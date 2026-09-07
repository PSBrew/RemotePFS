"""Build deterministic, read-only exFAT metadata from :mod:`remotepfs.config`.

Builder keeps metadata in sector-sized buffers and leaves file payloads on their
source paths.  This makes state cheap to pickle and permits lazy source opens.
"""

from __future__ import annotations

import hashlib
import os
import struct
import uuid
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

from .consts import (
    BOOT_REGION_SECTORS,
    BOOT_REGION_TOTAL_SECTORS,
    CLUSTER_SIZE_BYTES,
    ENTRY_BITMAP,
    ENTRY_FILE,
    ENTRY_FILENAME,
    ENTRY_STREAM,
    ENTRY_UPCASE,
    ENTRY_VOLUME_LABEL,
    EXFAT_BOOT_SIGNATURE,
    FAT_EOC,
    FAT_FIRST_RESERVED,
    PARTITION_START_LBA,
    SECTOR_SIZE,
    SECTORS_PER_CLUSTER,
    UPCASE_TABLE_BYTES,
)


def stable_file_id(virtual_path: str) -> int:
    """Return stable positive 31-bit ID derived from virtual path."""

    digest = hashlib.sha256(virtual_path.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little") & 0x7FFFFFFF


@dataclass(frozen=True)
class FileMapping:
    """Contiguous cluster range backed by one source file."""

    start_cluster: int
    cluster_count: int
    source_path: str
    size_bytes: int
    virtual_path: str


@dataclass(frozen=True)
class VirtualEntry:
    """One deterministic virtual tree entry."""

    virtual_path: str
    source_path: str
    name: str
    is_directory: bool
    size_bytes: int
    first_cluster: int
    cluster_count: int
    file_id: int
    parent_path: str


@dataclass
class ExfatLayout:
    """Serializable exFAT geometry, metadata, and lazy file mappings."""

    total_bytes: int
    total_sectors: int
    partition_start_lba: int
    sectors_per_cluster: int
    cluster_size_bytes: int
    fat_offset: int
    fat_length: int
    cluster_heap_offset: int
    cluster_count: int
    root_dir_cluster: int
    bitmap_cluster: int
    bitmap_length: int
    upcase_cluster: int
    upcase_length: int
    metadata: dict[int, bytes] = field(default_factory=dict)
    file_mappings: tuple[FileMapping, ...] = ()
    entries: tuple[VirtualEntry, ...] = ()
    hot_ranges: tuple[tuple[int, int], ...] = ()

    def to_state(self) -> dict[str, object]:
        """Return plain pickle-safe state with no open descriptors."""

        return {
            "total_bytes": self.total_bytes,
            "total_sectors": self.total_sectors,
            "partition_start_lba": self.partition_start_lba,
            "sectors_per_cluster": self.sectors_per_cluster,
            "cluster_size_bytes": self.cluster_size_bytes,
            "fat_offset": self.fat_offset,
            "fat_length": self.fat_length,
            "cluster_heap_offset": self.cluster_heap_offset,
            "cluster_count": self.cluster_count,
            "root_dir_cluster": self.root_dir_cluster,
            "bitmap_cluster": self.bitmap_cluster,
            "bitmap_length": self.bitmap_length,
            "upcase_cluster": self.upcase_cluster,
            "upcase_length": self.upcase_length,
            "metadata": dict(self.metadata),
            "file_mappings": tuple(self.file_mappings),
            "entries": tuple(self.entries),
            "hot_ranges": tuple(self.hot_ranges),
        }

    @classmethod
    def from_state(cls, state: dict[str, object]) -> ExfatLayout:
        """Recreate layout from :meth:`to_state` output."""

        return cls(**state)  # type: ignore[arg-type]


@dataclass
class _Node:
    """Internal tree node used while assigning clusters."""

    name: str
    virtual_path: str
    source_path: str
    is_directory: bool
    size_bytes: int = 0
    children: list[_Node] = field(default_factory=list)
    first_cluster: int = 0
    cluster_count: int = 0


class ExfatBuilder:
    """Build virtual exFAT metadata from validated configuration."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.total_bytes = config.image_size_bytes
        self.total_sectors = self.total_bytes // SECTOR_SIZE
        if self.total_bytes % SECTOR_SIZE:
            raise ValueError("image size must be a multiple of 512 bytes")
        if self.total_sectors <= PARTITION_START_LBA + BOOT_REGION_TOTAL_SECTORS:
            raise ValueError("image is too small for exFAT partition")

    def build(self) -> ExfatLayout:
        """Build metadata, FAT, bitmap, directory sets, and file mappings."""

        roots = self._build_tree()
        fat_offset, fat_length, cluster_heap_offset, cluster_count = self._geometry()
        next_cluster = 2
        # Reserve directory clusters first, so directory and metadata reads stay hot.
        for node in self._walk(roots):
            node.cluster_count = self._directory_cluster_count(node) if node.is_directory else 0
            if node.is_directory:
                node.first_cluster = next_cluster
                next_cluster += node.cluster_count
        bitmap_length = (cluster_count + 7) // 8
        bitmap_clusters = max(1, (bitmap_length + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)
        bitmap_cluster = next_cluster
        next_cluster += bitmap_clusters
        upcase_cluster = next_cluster
        upcase_clusters = max(1, (UPCASE_TABLE_BYTES + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)
        next_cluster += upcase_clusters

        mappings: list[FileMapping] = []
        for node in self._walk(roots):
            if not node.is_directory:
                size = node.size_bytes
                node.cluster_count = max(1, (size + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)
                node.first_cluster = next_cluster
                next_cluster += node.cluster_count
                mappings.append(
                    FileMapping(node.first_cluster, node.cluster_count, node.source_path, size, node.virtual_path)
                )
        if next_cluster - 2 > cluster_count:
            raise ValueError("configured files do not fit virtual image")

        fat = bytearray(
            self._fat_bytes(
                cluster_count, mappings, roots, bitmap_cluster, bitmap_clusters, upcase_cluster, upcase_clusters
            )
        )
        metadata: dict[int, bytes] = {}
        self._put_boot_regions(
            metadata, fat_offset, fat_length, cluster_heap_offset, cluster_count, roots[0].first_cluster
        )
        self._put_sectors(metadata, PARTITION_START_LBA + fat_offset, fat, fat_length)

        bitmap = bytearray(bitmap_length)
        for cluster in range(2, next_cluster):
            bitmap[(cluster - 2) // 8] |= 1 << ((cluster - 2) % 8)
        self._put_cluster_bytes(metadata, cluster_heap_offset, bitmap_cluster, bytes(bitmap))

        upcase = self._upcase_table()
        self._put_cluster_bytes(metadata, cluster_heap_offset, upcase_cluster, upcase)

        # Directory entries refer to bitmap/upcase clusters, so render after all assignments.
        root = roots[0]
        root_bytes = self._directory_bytes(
            root, root is roots[0], bitmap_cluster, bitmap_length, upcase_cluster, len(upcase)
        )
        self._put_cluster_bytes(metadata, cluster_heap_offset, root.first_cluster, root_bytes)
        for node in self._walk(root.children):
            if node.is_directory:
                self._put_cluster_bytes(
                    metadata,
                    cluster_heap_offset,
                    node.first_cluster,
                    self._directory_bytes(node, False, bitmap_cluster, bitmap_length, upcase_cluster, len(upcase)),
                )

        self._put_mbr_gpt(metadata)
        entries = tuple(self._entry_record(node, parent_path="") for node in self._walk(root.children))
        hot_ranges = self._ranges(metadata)
        return ExfatLayout(
            total_bytes=self.total_bytes,
            total_sectors=self.total_sectors,
            partition_start_lba=PARTITION_START_LBA,
            sectors_per_cluster=SECTORS_PER_CLUSTER,
            cluster_size_bytes=CLUSTER_SIZE_BYTES,
            fat_offset=fat_offset,
            fat_length=fat_length,
            cluster_heap_offset=cluster_heap_offset,
            cluster_count=cluster_count,
            root_dir_cluster=root.first_cluster,
            bitmap_cluster=bitmap_cluster,
            bitmap_length=bitmap_length,
            upcase_cluster=upcase_cluster,
            upcase_length=len(upcase),
            metadata=metadata,
            file_mappings=tuple(mappings),
            entries=entries,
            hot_ranges=hot_ranges,
        )

    def _build_tree(self) -> list[_Node]:
        """Scan configured sources in stable lexical order."""

        root = _Node("", "", "", True)
        for entry in sorted(self.config.entries, key=lambda item: item.virtual_path):
            node = _Node(entry.virtual_path, entry.virtual_path, entry.source, entry.type == "directory")
            if node.is_directory:
                self._scan(node)
            else:
                try:
                    node.size_bytes = os.stat(node.source_path).st_size
                except OSError:
                    node.size_bytes = 0
            root.children.append(node)
        return [root]

    def _scan(self, parent: _Node) -> None:
        """Recursively scan directory without following symlinks."""

        try:
            items = sorted(os.scandir(parent.source_path), key=lambda item: item.name)
        except OSError:
            return
        for item in items:
            if item.is_dir(follow_symlinks=False):
                child = _Node(item.name, f"{parent.virtual_path}/{item.name}", item.path, True)
                parent.children.append(child)
                self._scan(child)
            elif item.is_file(follow_symlinks=False):
                try:
                    size = item.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                parent.children.append(_Node(item.name, f"{parent.virtual_path}/{item.name}", item.path, False, size))

    def _walk(self, nodes: Iterable[_Node]) -> Iterable[_Node]:
        """Yield nodes depth-first with stable ordering."""

        for node in nodes:
            yield node
            if node.is_directory:
                yield from self._walk(node.children)

    @staticmethod
    def _directory_cluster_count(node: _Node) -> int:
        """Return clusters required by directory entry set."""

        count = (
            (3 if node.virtual_path == "" else 0)
            + 1
            + sum(1 + (len(child.name.encode("utf-16-le")) // 2 + 14) // 15 for child in node.children)
        )
        return max(1, (count * 32 + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)

    def _geometry(self) -> tuple[int, int, int, int]:
        """Compute self-consistent FAT and cluster heap geometry."""

        partition_sectors = self.total_sectors - PARTITION_START_LBA
        fat_offset = BOOT_REGION_TOTAL_SECTORS
        cluster_count = 0
        fat_length = 1
        for _ in range(8):
            cluster_count = (partition_sectors - fat_offset - fat_length) // SECTORS_PER_CLUSTER
            new_length = max(1, (cluster_count * 4 + SECTOR_SIZE - 1) // SECTOR_SIZE)
            if new_length == fat_length:
                break
            fat_length = new_length
        return fat_offset, fat_length, fat_offset + fat_length, cluster_count

    def _fat_bytes(
        self,
        cluster_count: int,
        mappings: list[FileMapping],
        roots: list[_Node],
        bitmap_cluster: int,
        bitmap_clusters: int,
        upcase_cluster: int,
        upcase_clusters: int,
    ) -> bytes:
        """Build FAT entries for every allocated contiguous chain."""

        fat = bytearray(self._geometry()[1] * SECTOR_SIZE)
        struct.pack_into("<II", fat, 0, FAT_FIRST_RESERVED, FAT_EOC)
        ranges: list[tuple[int, int]] = [(bitmap_cluster, bitmap_clusters), (upcase_cluster, upcase_clusters)]
        ranges.extend((node.first_cluster, node.cluster_count) for node in self._walk(roots) if node.is_directory)
        ranges.extend((item.start_cluster, item.cluster_count) for item in mappings)
        for start, count in ranges:
            for cluster in range(start, start + count - 1):
                struct.pack_into("<I", fat, cluster * 4, cluster + 1)
            struct.pack_into("<I", fat, (start + count - 1) * 4, FAT_EOC)
        return bytes(fat)

    def _put_boot_regions(
        self,
        metadata: dict[int, bytes],
        fat_offset: int,
        fat_length: int,
        heap_offset: int,
        cluster_count: int,
        root_cluster: int,
    ) -> None:
        """Write primary and backup exFAT boot sectors with checksums."""

        partition_sectors = self.total_sectors - PARTITION_START_LBA
        boot = bytearray(BOOT_REGION_SECTORS * SECTOR_SIZE)
        struct.pack_into("<3s8s", boot, 0, b"\xeb\x76\x90", b"EXFAT   ")
        boot[11:64] = b"\0" * 53
        struct.pack_into("<Q", boot, 64, PARTITION_START_LBA)
        struct.pack_into("<Q", boot, 72, partition_sectors)
        struct.pack_into("<I", boot, 80, fat_offset)
        struct.pack_into("<I", boot, 84, fat_length)
        struct.pack_into("<I", boot, 88, heap_offset)
        struct.pack_into("<I", boot, 92, cluster_count)
        struct.pack_into("<I", boot, 96, root_cluster)
        serial = int.from_bytes(hashlib.sha256(self.config.label.encode("ascii")).digest()[:4], "little")
        struct.pack_into("<I", boot, 100, serial)
        struct.pack_into("<H", boot, 104, 0x0100)
        struct.pack_into("<H", boot, 106, 0)
        boot[108:113] = bytes((9, 7, 1, 0x80, 0))
        boot[510:512] = EXFAT_BOOT_SIGNATURE
        checksum = self._boot_checksum(boot)
        checksum_sector = struct.pack("<I", checksum) * (SECTOR_SIZE // 4)
        region = boot + checksum_sector
        primary = PARTITION_START_LBA
        backup = PARTITION_START_LBA + BOOT_REGION_SECTORS
        for index in range(BOOT_REGION_SECTORS):
            metadata[primary + index] = bytes(region[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE])
            metadata[backup + index] = bytes(region[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE])

    @staticmethod
    def _boot_checksum(boot: bytes) -> int:
        """Calculate exFAT rotate-right boot checksum."""
        value = 0
        for offset, byte in enumerate(boot[: 11 * SECTOR_SIZE]):
            if offset in (106, 107, 112):
                continue
            value = ((value >> 1) | ((value & 1) << 31)) + byte
            value &= 0xFFFFFFFF
        return value

    @staticmethod
    def _put_sectors(metadata: dict[int, bytes], start_sector: int, data: bytes, sector_count: int) -> None:
        """Store zero-padded sector-aligned metadata."""

        for index in range(sector_count):
            begin = index * SECTOR_SIZE
            metadata[start_sector + index] = data[begin : begin + SECTOR_SIZE].ljust(SECTOR_SIZE, b"\0")

    def _put_cluster_bytes(self, metadata: dict[int, bytes], heap_offset: int, cluster: int, data: bytes) -> None:
        """Store data at cluster heap location, padding to whole clusters."""

        start = PARTITION_START_LBA + heap_offset + (cluster - 2) * SECTORS_PER_CLUSTER
        padded = data.ljust(max(SECTOR_SIZE, ((len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE), b"\0")
        self._put_sectors(metadata, start, padded, (len(padded) + SECTOR_SIZE - 1) // SECTOR_SIZE)

    def _upcase_table(self) -> bytes:
        """Build deterministic compact upcase table payload."""

        # Table length mandated by exFAT. Identity values cover common ASCII and
        # Latin codepoints; remaining bytes are reserved zeroes.
        table = bytearray(UPCASE_TABLE_BYTES)
        for codepoint in range(min(UPCASE_TABLE_BYTES // 2, 0x1000)):
            upper = ord(chr(codepoint).upper()[0]) if chr(codepoint).upper() else codepoint
            struct.pack_into("<H", table, codepoint * 2, upper & 0xFFFF)
        return bytes(table)

    def _directory_bytes(
        self,
        node: _Node,
        is_root: bool,
        bitmap_cluster: int,
        bitmap_length: int,
        upcase_cluster: int,
        upcase_length: int,
    ) -> bytes:
        """Encode directory entry sets and set checksums."""

        entries: list[bytes] = []
        if is_root:
            label = self.config.label.encode("utf-16-le")
            item = bytearray(32)
            item[0] = ENTRY_VOLUME_LABEL
            item[1] = min(len(label) // 2, 11)
            item[2 : 2 + min(len(label), 22)] = label[:22]
            entries.append(bytes(item))
            bitmap = bytearray(32)
            bitmap[0] = ENTRY_BITMAP
            bitmap[1] = 0
            struct.pack_into("<I", bitmap, 20, bitmap_cluster)
            struct.pack_into("<Q", bitmap, 24, bitmap_length)
            entries.append(bytes(bitmap))
            upcase = bytearray(32)
            upcase[0] = ENTRY_UPCASE
            struct.pack_into("<I", upcase, 4, zlib.crc32(self._upcase_table()) & 0xFFFFFFFF)
            struct.pack_into("<I", upcase, 20, upcase_cluster)
            struct.pack_into("<Q", upcase, 24, upcase_length)
            entries.append(bytes(upcase))
        for child in sorted(node.children, key=lambda item: item.name):
            entries.extend(self._file_entry_set(child))
        entries.append(bytes(32))
        result = b"".join(entries)
        return result.ljust(
            max(32, ((len(result) + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES) * CLUSTER_SIZE_BYTES), b"\0"
        )

    def _file_entry_set(self, node: _Node) -> list[bytes]:
        """Encode one file or directory entry set."""

        encoded = node.name.encode("utf-16-le")[: 2 * 255]
        units = len(encoded) // 2
        filename_count = max(1, (units + 14) // 15)
        primary = bytearray(32)
        primary[0] = ENTRY_FILE
        primary[1] = filename_count + 1
        struct.pack_into("<H", primary, 4, 0x10 if node.is_directory else 0x20)
        stream = bytearray(32)
        stream[0] = ENTRY_STREAM
        stream[3] = units
        struct.pack_into("<H", stream, 4, self._name_hash(encoded))
        struct.pack_into("<I", stream, 20, node.first_cluster)
        struct.pack_into("<Q", stream, 24, 0 if node.is_directory else node.size_bytes)
        chunks = [encoded[index : index + 30].ljust(30, b"\0") for index in range(0, len(encoded), 30)] or [b"\0" * 30]
        result = [bytes(primary), bytes(stream)]
        result.extend(bytes(bytearray([ENTRY_FILENAME]) + chunk + b"\0") for chunk in chunks)
        raw = b"".join(result)
        checksum = self._entry_checksum(raw)
        primary[2:4] = struct.pack("<H", checksum)
        return [bytes(primary), *result[1:]]

    @staticmethod
    def _name_hash(encoded: bytes) -> int:
        """Calculate deterministic exFAT-style name hash."""

        value = 0
        for byte in encoded:
            value = ((value << 15) | (value >> 1)) & 0xFFFF
            value = (value + byte) & 0xFFFF
        return value

    @staticmethod
    def _entry_checksum(data: bytes) -> int:
        """Calculate directory entry set checksum."""

        value = 0
        for index, byte in enumerate(data):
            if index in (2, 3):
                byte = 0
            value = ((value << 15) | (value >> 1)) + byte
            value &= 0xFFFF
        return value

    def _entry_record(self, node: _Node, parent_path: str) -> VirtualEntry:
        """Convert internal node into public immutable record."""

        return VirtualEntry(
            virtual_path=node.virtual_path,
            source_path=node.source_path,
            name=node.name,
            is_directory=node.is_directory,
            size_bytes=node.size_bytes,
            first_cluster=node.first_cluster,
            cluster_count=node.cluster_count,
            file_id=stable_file_id(node.virtual_path),
            parent_path=parent_path,
        )

    def _put_mbr_gpt(self, metadata: dict[int, bytes]) -> None:
        """Write a protective MBR and deterministic GPT partition metadata."""
        total_sectors = self.total_sectors
        mbr = bytearray(SECTOR_SIZE)
        mbr[446 + 4] = 0xEE
        struct.pack_into(
            "<II", mbr, 446 + 8, PARTITION_START_LBA, min(total_sectors - PARTITION_START_LBA, 0xFFFFFFFF)
        )
        mbr[510:512] = EXFAT_BOOT_SIGNATURE
        metadata[0] = bytes(mbr)

        partition_type = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7").bytes_le
        partition_guid = uuid.uuid5(uuid.NAMESPACE_DNS, "remotepfs").bytes_le
        entry = bytearray(128)
        entry[:16] = partition_type
        entry[16:32] = partition_guid
        struct.pack_into("<QQ", entry, 32, PARTITION_START_LBA, total_sectors - 1)
        name = "RemotePFS".encode("utf-16le")
        entry[56 : 56 + len(name)] = name
        entries = bytes(entry).ljust(32 * SECTOR_SIZE, b"\0")
        metadata[2] = entries[:SECTOR_SIZE]
        for index in range(1, 32):
            metadata[2 + index] = entries[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE]
        header = bytearray(SECTOR_SIZE)
        header[:8] = b"EFI PART"
        struct.pack_into("<II", header, 8, 0x00010000, 92)
        struct.pack_into("<QQQQ", header, 24, 1, total_sectors - 1, 34, total_sectors - 34)
        header[56:72] = uuid.uuid5(uuid.NAMESPACE_DNS, "remotepfs-header").bytes_le
        struct.pack_into("<I", header, 80, 2)
        struct.pack_into("<I", header, 84, 128)
        struct.pack_into("<I", header, 88, 128)
        struct.pack_into("<I", header, 16, 0)
        struct.pack_into("<I", header, 16, zlib.crc32(header[:92]) & 0xFFFFFFFF)
        metadata[1] = bytes(header)

    @staticmethod
    def _ranges(metadata: dict[int, bytes]) -> tuple[tuple[int, int], ...]:
        """Collapse contiguous metadata sectors into hot byte ranges."""

        result: list[tuple[int, int]] = []
        for sector in sorted(metadata):
            if result and result[-1][0] + result[-1][1] == sector * SECTOR_SIZE:
                start, length = result[-1]
                result[-1] = (start, length + SECTOR_SIZE)
            else:
                result.append((sector * SECTOR_SIZE, SECTOR_SIZE))
        return tuple(result)


def build_exfat(config: Config) -> ExfatLayout:
    """Build exFAT layout from validated config."""

    return ExfatBuilder(config).build()


def scan_directory(source_path: str, virtual_parent: str = "") -> list[VirtualEntry]:
    """Scan directory deterministically, returning lightweight entry records.

    This helper does not allocate clusters; use :func:`build_exfat` for a complete
    filesystem. Missing or unreadable directories produce an empty list.
    """

    from .config import Config

    config = Config(1, 64, "REMOTEPFS", "REMOTEPF", entries=[])
    builder = ExfatBuilder(config)
    root = _Node(virtual_parent, virtual_parent, source_path, True)
    builder._scan(root)
    return [builder._entry_record(node, virtual_parent) for node in builder._walk(root.children)]


__all__ = [
    "ExfatBuilder",
    "ExfatLayout",
    "FileMapping",
    "VirtualEntry",
    "build_exfat",
    "scan_directory",
    "stable_file_id",
]
