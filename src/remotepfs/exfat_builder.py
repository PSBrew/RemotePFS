"""Build virtual exFAT metadata and lazy file mappings."""

from __future__ import annotations

import calendar
import ctypes
import datetime
import hashlib
import logging
import os
import struct
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
from .consts import (
    BOOT_REGION_SECTORS,
    BOOT_REGION_TOTAL_SECTORS,
    CLUSTER_HEAP_ALIGNMENT_SECTORS,
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

EXFAT_MIN_TIMESTAMP_SECONDS = calendar.timegm((1980, 1, 1, 0, 0, 0))
EXFAT_MAX_TIMESTAMP_SECONDS = calendar.timegm((2107, 12, 31, 23, 59, 59))
MBR_PARTITION_TYPE = 0x07


class _StatxTimestamp(ctypes.Structure):
    """Linux statx timestamp layout."""

    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    """Linux statx structure through kernel-defined 256-byte size."""

    _fields_ = [
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("stx_mnt_id", ctypes.c_uint64),
        ("stx_dio_mem_align", ctypes.c_uint32),
        ("stx_dio_offset_align", ctypes.c_uint32),
        ("stx_subvol", ctypes.c_uint64),
        ("stx_atomic_write_unit_min", ctypes.c_uint32),
        ("stx_atomic_write_unit_max", ctypes.c_uint32),
        ("stx_atomic_write_segments_max", ctypes.c_uint32),
        ("stx_dio_read_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 9),
    ]


@dataclass(frozen=True)
class _SourceStat:
    """One consistent source metadata snapshot."""

    size_bytes: int
    accessed_ns: int
    modified_ns: int
    created_ns: int


_AT_FDCWD = -100
_AT_SYMLINK_NOFOLLOW = 0x100
_STATX_BASIC_STATS = 0x07FF
_STATX_ATIME = 0x0020
_STATX_BTIME = 0x0800
_STATX_REQUIRED = 0x0240


def _load_statx() -> Callable[..., int] | None:
    """Load libc statx on Linux, or return None on unsupported platforms."""
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx = libc.statx
        statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
        statx.restype = ctypes.c_int
        return statx
    except (AttributeError, OSError):
        return None


_statx = _load_statx()


def _source_stat(path: str) -> _SourceStat | None:
    """Read one complete source metadata snapshot."""
    if _statx is not None:
        result = _Statx()
        try:
            status = _statx(
                _AT_FDCWD,
                os.fsencode(path),
                _AT_SYMLINK_NOFOLLOW,
                _STATX_BASIC_STATS | _STATX_BTIME,
                result,
            )
        except OSError:
            status = -1
        if status == 0 and result.stx_mask & _STATX_REQUIRED == _STATX_REQUIRED:
            modified_ns = result.stx_mtime.tv_sec * 1_000_000_000 + result.stx_mtime.tv_nsec
            created_ns = (
                result.stx_btime.tv_sec * 1_000_000_000 + result.stx_btime.tv_nsec
                if result.stx_mask & _STATX_BTIME
                else modified_ns
            )
            return _SourceStat(
                size_bytes=result.stx_size,
                accessed_ns=(
                    result.stx_atime.tv_sec * 1_000_000_000 + result.stx_atime.tv_nsec
                    if result.stx_mask & _STATX_ATIME
                    else modified_ns
                ),
                modified_ns=modified_ns,
                created_ns=created_ns,
            )
    try:
        source_stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return _SourceStat(
        size_bytes=source_stat.st_size,
        accessed_ns=source_stat.st_atime_ns,
        modified_ns=source_stat.st_mtime_ns,
        created_ns=getattr(source_stat, "st_birthtime_ns", None) or source_stat.st_mtime_ns,
    )


def _exfat_timestamp(timestamp_ns: int) -> tuple[int, int, int]:
    """Encode Unix nanoseconds as exFAT date, time, and 10 ms increment."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    if seconds < EXFAT_MIN_TIMESTAMP_SECONDS:
        seconds, nanoseconds = EXFAT_MIN_TIMESTAMP_SECONDS, 0
    elif seconds > EXFAT_MAX_TIMESTAMP_SECONDS:
        seconds, nanoseconds = EXFAT_MAX_TIMESTAMP_SECONDS, 999_000_000
    timestamp = datetime.datetime.fromtimestamp(seconds, datetime.UTC)
    date = ((timestamp.year - 1980) << 9) | (timestamp.month << 5) | timestamp.day
    time = (timestamp.hour << 11) | (timestamp.minute << 5) | (timestamp.second // 2)
    increment = (timestamp.second % 2) * 100 + nanoseconds // 10_000_000
    return date, time, increment


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
    allocated_ranges: tuple[tuple[int, int], ...] = ()

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
            "allocated_ranges": tuple(self.allocated_ranges),
        }

    @classmethod
    def from_state(cls, state: dict[str, object]) -> ExfatLayout:
        """Recreate layout from :meth:`to_state` output."""
        if "allocated_ranges" not in state:
            raise ValueError("mapper state lacks compact allocation ranges; rebuild required")
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
    created_ns: int = 0
    modified_ns: int = 0
    accessed_ns: int = 0


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
        bitmap_length = (cluster_count + 7) // 8
        bitmap_clusters = max(1, (bitmap_length + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)
        bitmap_cluster = 2
        upcase_cluster = bitmap_cluster + bitmap_clusters
        upcase_clusters = max(1, (UPCASE_TABLE_BYTES + CLUSTER_SIZE_BYTES - 1) // CLUSTER_SIZE_BYTES)
        next_cluster = upcase_cluster + upcase_clusters
        # Reserve bitmap and upcase extents before directory allocation.
        for node in self._walk(roots):
            node.cluster_count = self._directory_cluster_count(node) if node.is_directory else 0
            if node.is_directory:
                node.first_cluster = next_cluster
                next_cluster += node.cluster_count

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
        allocated_ranges = self._allocated_ranges(
            mappings, roots, bitmap_cluster, bitmap_clusters, upcase_cluster, upcase_clusters
        )
        metadata: dict[int, bytes] = {}
        self._put_boot_regions(
            metadata, fat_offset, fat_length, cluster_heap_offset, cluster_count, roots[0].first_cluster
        )
        upcase = self._upcase_table()
        root = roots[0]
        root_bytes = self._directory_bytes(
            root, root is roots[0], bitmap_cluster, bitmap_length, upcase_cluster, len(upcase)
        )
        self._put_cluster_bytes(metadata, cluster_heap_offset, root.first_cluster, root_bytes)
        self._put_cluster_bytes(metadata, cluster_heap_offset, upcase_cluster, upcase)
        for node in self._walk(root.children):
            if node.is_directory:
                self._put_cluster_bytes(
                    metadata,
                    cluster_heap_offset,
                    node.first_cluster,
                    self._directory_bytes(node, False, bitmap_cluster, bitmap_length, upcase_cluster, len(upcase)),
                )

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
            allocated_ranges=allocated_ranges,
        )

    def _build_tree(self) -> list[_Node]:
        """Scan configured sources in stable lexical order."""
        root = _Node("", "", "", True)
        for entry in sorted(self.config.entries, key=lambda item: item.virtual_path):
            node = _Node(entry.virtual_path, entry.virtual_path, entry.source, entry.type == "directory")
            if node.is_directory:
                self._scan(node)
            else:
                source_stat = _source_stat(node.source_path)
                if source_stat is not None:
                    node.size_bytes = source_stat.size_bytes
                    self._apply_source_stat(node, source_stat)
            root.children.append(node)
        return [root]

    @staticmethod
    def _apply_source_stat(node: _Node, source_stat: _SourceStat) -> None:
        """Copy one source metadata snapshot into an internal tree node."""
        if not node.is_directory:
            node.size_bytes = source_stat.size_bytes
        node.created_ns = source_stat.created_ns
        node.modified_ns = source_stat.modified_ns
        node.accessed_ns = source_stat.accessed_ns

    def _scan(self, parent: _Node, source_stat: _SourceStat | None = None) -> None:
        """Recursively scan directory without following symlinks."""
        try:
            items = sorted(os.scandir(parent.source_path), key=lambda item: item.name)
        except OSError as error:
            logging.getLogger(__name__).warning("cannot scan directory %s: %s", parent.source_path, error)
            return
        if source_stat is None:
            source_stat = _source_stat(parent.source_path)
        if source_stat is not None:
            self._apply_source_stat(parent, source_stat)
        for item in items:
            if item.is_dir(follow_symlinks=False):
                child = _Node(item.name, f"{parent.virtual_path}/{item.name}", item.path, True)
                child_stat = _source_stat(child.source_path)
                if child_stat is not None:
                    self._apply_source_stat(child, child_stat)
                parent.children.append(child)
                self._scan(child, child_stat)
            elif item.is_file(follow_symlinks=False):
                child = _Node(item.name, f"{parent.virtual_path}/{item.name}", item.path, False)
                child_stat = _source_stat(child.source_path)
                if child_stat is not None:
                    self._apply_source_stat(child, child_stat)
                parent.children.append(child)

    @staticmethod
    def _allocated_ranges(
        mappings: list[FileMapping],
        roots: list[_Node],
        bitmap_cluster: int,
        bitmap_clusters: int,
        upcase_cluster: int,
        upcase_clusters: int,
    ) -> tuple[tuple[int, int], ...]:
        """Return allocated cluster runs without expanding them per cluster."""
        ranges = [(bitmap_cluster, bitmap_clusters), (upcase_cluster, upcase_clusters)]
        ranges.extend(
            (node.first_cluster, node.cluster_count) for node in ExfatBuilder._walk_static(roots) if node.is_directory
        )
        ranges.extend((item.start_cluster, item.cluster_count) for item in mappings)
        return tuple(sorted(ranges))

    @staticmethod
    def _walk_static(nodes: list[_Node]) -> list[_Node]:
        """Walk nodes for static allocation calculations."""
        result: list[_Node] = []
        stack = list(nodes)
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(reversed(node.children))
        return result

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
        """Compute self-consistent FAT and aligned cluster heap geometry."""
        partition_sectors = self.total_sectors - PARTITION_START_LBA
        fat_offset = 2048
        cluster_count = 0
        fat_length = SECTORS_PER_CLUSTER
        cluster_heap_offset = fat_offset + fat_length
        for _ in range(8):
            cluster_heap_offset = (
                (fat_offset + fat_length + CLUSTER_HEAP_ALIGNMENT_SECTORS - 1) // CLUSTER_HEAP_ALIGNMENT_SECTORS
            ) * CLUSTER_HEAP_ALIGNMENT_SECTORS
            cluster_count = (partition_sectors - cluster_heap_offset) // SECTORS_PER_CLUSTER
            new_length = max(1, (cluster_count * 4 + SECTOR_SIZE - 1) // SECTOR_SIZE)
            new_length = ((new_length + SECTORS_PER_CLUSTER - 1) // SECTORS_PER_CLUSTER) * SECTORS_PER_CLUSTER
            if new_length == fat_length:
                break
            fat_length = new_length
        return fat_offset, fat_length, cluster_heap_offset, cluster_count

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
        boot = bytearray((BOOT_REGION_SECTORS - 1) * SECTOR_SIZE)
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
        boot[108:113] = bytes((9, 8, 1, 0x80, 0xFF))
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

    @staticmethod
    def _simple_upper(codepoint: int) -> int:
        """Return one-to-one Unicode uppercase mapping for one UTF-16 unit."""

        upper = chr(codepoint).upper()
        return ord(upper) if len(upper) == 1 and ord(upper) <= 0xFFFF else codepoint

    def _upcase_table(self) -> bytes:
        """Build uncompressed exFAT upcase table for all UTF-16 code units."""
        values = [self._upcase_codepoint(codepoint) for codepoint in range(0x10000)]
        return struct.pack("<65536H", *values)

    def _upcase_codepoint(self, codepoint: int) -> int:
        """Return exFAT-compatible uppercase mapping for one UTF-16 unit."""

        return self._simple_upper(codepoint)

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
            struct.pack_into("<I", upcase, 4, self._rotate_checksum(self._upcase_table()))
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
        self._put_timestamps(primary, node)
        stream = bytearray(32)
        stream[0] = ENTRY_STREAM
        stream[3] = units
        struct.pack_into("<H", stream, 4, self._name_hash(encoded))
        data_length = node.cluster_count * CLUSTER_SIZE_BYTES if node.is_directory else node.size_bytes
        stream[1] = 1
        struct.pack_into("<Q", stream, 8, data_length)
        struct.pack_into("<I", stream, 20, node.first_cluster)
        struct.pack_into("<Q", stream, 24, data_length)
        chunks = [encoded[index : index + 30].ljust(30, b"\0") for index in range(0, len(encoded), 30)] or [b"\0" * 30]
        result = [bytes(primary), bytes(stream)]
        result.extend(bytes((ENTRY_FILENAME, 0)) + chunk for chunk in chunks)
        raw = b"".join(result)
        checksum = self._entry_checksum(raw)
        primary[2:4] = struct.pack("<H", checksum)
        return [bytes(primary), *result[1:]]

    @staticmethod
    def _put_timestamps(primary: bytearray, node: _Node) -> None:
        """Write exFAT creation, modification, and access timestamps."""
        create_date, create_time, create_increment = _exfat_timestamp(node.created_ns)
        modified_date, modified_time, modified_increment = _exfat_timestamp(node.modified_ns)
        accessed_date, accessed_time, _ = _exfat_timestamp(node.accessed_ns)
        struct.pack_into("<HH", primary, 8, create_time, create_date)
        struct.pack_into("<HH", primary, 12, modified_time, modified_date)
        struct.pack_into("<HH", primary, 16, accessed_time, accessed_date)
        primary[20] = create_increment
        primary[21] = modified_increment
        primary[22:25] = b"\x80\x80\x80"

    def _name_hash(self, encoded: bytes) -> int:
        """Calculate exFAT name hash using uppercase UTF-16 code units."""

        value = 0
        for index in range(0, len(encoded), 2):
            codepoint = struct.unpack_from("<H", encoded, index)[0]
            upper = self._upcase_codepoint(codepoint)
            for byte in struct.pack("<H", upper):
                value = (((value & 1) << 15) | (value >> 1)) + byte
                value &= 0xFFFF
        return value

    @staticmethod
    def _rotate_checksum(data: bytes) -> int:
        """Calculate exFAT rotate-right checksum over all bytes."""

        value = 0
        for byte in data:
            value = ((value >> 1) | ((value & 1) << 31)) + byte
            value &= 0xFFFFFFFF
        return value

    @staticmethod
    def _entry_checksum(data: bytes) -> int:
        """Calculate directory entry set checksum."""

        value = 0
        for index, byte in enumerate(data):
            if index < 32 and index in (2, 3):
                continue
            value = (((value & 1) << 15) | (value >> 1)) + byte
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

    config = Config(1, 128, "REMOTEPFS", "REMOTEPF", entries=[])
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
