"""Regression coverage for virtual exFAT build and sector reads."""

from __future__ import annotations

import pickle
import struct
import zlib

from remotepfs.config import parse
from remotepfs.exfat_builder import build_exfat, scan_directory
from remotepfs.sector_mapper import SectorMapper


def _config(tmp_path, filename: str) -> str:
    return f"""global:
  image_size_gib: 1
  cluster_size_kib: 64
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: local
    protocol: nfs
    endpoint: 127.0.0.1:/exports
    mount_point: {tmp_path}
    options: ro
entries:
  - virtual_path: {filename}
    source: {tmp_path / filename}
    type: file
"""


def test_build_exfat_and_mapper_reads_file_and_metadata(tmp_path) -> None:
    source = tmp_path / "game.bin"
    source.write_bytes(b"RemotePFS" + b"x" * 1000)
    mapper = SectorMapper.from_layout(build_exfat(parse(_config(tmp_path, source.name))))
    try:
        assert mapper.read_bytes(0, 512)[510:512] == b"\x55\xaa"
        vbr = mapper.layout.metadata[mapper.layout.partition_start_lba]
        assert vbr[3:11] == b"EXFAT   "
        assert mapper.layout.metadata[mapper.layout.partition_start_lba + 12] == vbr
        assert (
            mapper.layout.metadata[mapper.layout.partition_start_lba + 23]
            == mapper.layout.metadata[mapper.layout.partition_start_lba + 11]
        )
        mapping = mapper.layout.file_mappings[0]
        file_offset = (
            mapper.layout.partition_start_lba * 512
            + mapper.layout.cluster_heap_offset * 512
            + (mapping.start_cluster - 2) * mapper.layout.cluster_size_bytes
        )
        assert mapper.read_bytes(file_offset, 9) == b"RemotePFS"
        assert mapper.read_bytes(file_offset + source.stat().st_size + 100, 16) == b"\0" * 16
    finally:
        mapper.close()


def test_build_exfat_writes_crc_valid_gpt(tmp_path) -> None:
    """Build valid primary and backup GPT metadata for host partition parsers."""
    source = tmp_path / "file.bin"
    source.write_bytes(b"payload")
    mapper = SectorMapper.from_layout(build_exfat(parse(_config(tmp_path, source.name))))
    try:
        total_sectors = mapper.layout.total_sectors
        primary = mapper.layout.metadata[1]
        backup = mapper.layout.metadata[total_sectors - 1]
        entries = b"".join(mapper.layout.metadata[index] for index in range(2, 34))
        entries_crc = zlib.crc32(entries) & 0xFFFFFFFF
        partition_start = struct.unpack_from("<Q", entries, 32)[0]
        partition_end = struct.unpack_from("<Q", entries, 40)[0]
        assert partition_start == mapper.layout.partition_start_lba
        assert partition_end == total_sectors - 34
        primary_vbr = mapper.layout.metadata[partition_start]
        expected_partition_length = partition_end - partition_start + 1
        assert struct.unpack_from("<Q", primary_vbr, 72)[0] == expected_partition_length
        backup_start = partition_start + 12
        backup_vbr = mapper.layout.metadata[backup_start]
        assert struct.unpack_from("<Q", backup_vbr, 72)[0] == expected_partition_length
        assert mapper.layout.metadata[partition_start + 11][:4] != b"\0\0\0\0"
        assert mapper.layout.metadata[backup_start + 11][:4] != b"\0\0\0\0"

        for header, current_lba, backup_lba, entries_lba in (
            (primary, 1, total_sectors - 1, 2),
            (backup, total_sectors - 1, 1, total_sectors - 33),
        ):
            assert header[:8] == b"EFI PART"
            assert struct.unpack_from("<Q", header, 24)[0] == current_lba
            assert struct.unpack_from("<Q", header, 32)[0] == backup_lba
            assert struct.unpack_from("<Q", header, 72)[0] == entries_lba
            assert struct.unpack_from("<I", header, 88)[0] == entries_crc
            header_size = struct.unpack_from("<I", header, 12)[0]
            stored_crc = struct.unpack_from("<I", header, 16)[0]
            check = bytearray(header[:header_size])
            struct.pack_into("<I", check, 16, 0)
            assert zlib.crc32(check) & 0xFFFFFFFF == stored_crc
    finally:
        mapper.close()


def test_mapper_state_round_trip(tmp_path) -> None:
    source = tmp_path / "file.bin"
    source.write_bytes(b"payload")
    mapper = SectorMapper.from_layout(build_exfat(parse(_config(tmp_path, source.name))))
    restored = SectorMapper.from_state(mapper.to_state())
    try:
        mapping = restored.layout.file_mappings[0]
        offset = (
            restored.layout.partition_start_lba * 512
            + restored.layout.cluster_heap_offset * 512
            + (mapping.start_cluster - 2) * restored.layout.cluster_size_bytes
        )
        assert restored.read_bytes(offset, 7) == b"payload"
    finally:
        mapper.close()
        restored.close()


def test_scan_directory_returns_sorted_recursive_entries(tmp_path) -> None:
    """Scan directory helper returns stable recursive entries."""
    (tmp_path / "z.txt").write_bytes(b"z")
    (tmp_path / "a.txt").write_bytes(b"aa")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.txt").write_bytes(b"bbb")

    entries = scan_directory(str(tmp_path), virtual_parent="games")

    assert [(entry.virtual_path, entry.is_directory, entry.size_bytes) for entry in entries] == [
        ("games/a.txt", False, 2),
        ("games/nested", True, 0),
        ("games/nested/b.txt", False, 3),
        ("games/z.txt", False, 1),
    ]


def test_directory_checksums_and_bitmap_cover_allocated_clusters(tmp_path) -> None:
    """Generated directory sets and allocation bitmap agree with FAT layout."""
    source = tmp_path / "game.bin"
    source.write_bytes(b"payload")
    mapper = SectorMapper.from_layout(build_exfat(parse(_config(tmp_path, source.name))))
    layout = mapper.layout
    sectors = layout.sectors_per_cluster

    def cluster_bytes(cluster: int) -> bytes:
        start = (layout.partition_start_lba + layout.cluster_heap_offset + (cluster - 2) * sectors) * 512
        return mapper.read_bytes(start, sectors * 512)

    try:
        root = cluster_bytes(layout.root_dir_cluster)
        offset = 0
        allocated: set[int] = set()
        while root[offset]:
            entry_type = root[offset]
            if entry_type == 0x85:
                secondary_count = root[offset + 1]
                entry_set = root[offset : offset + (secondary_count + 1) * 32]
                checksum = 0
                for index, byte in enumerate(entry_set):
                    if index < 32 and index in (2, 3):
                        continue
                    checksum = (((checksum & 1) << 15) | (checksum >> 1)) + byte
                    checksum &= 0xFFFF
                assert struct.unpack_from("<H", entry_set, 2)[0] == checksum
                stream = entry_set[32:64]
                name_units = stream[3]
                assert stream[1] == 1
                name_bytes = b"".join(entry_set[index + 2 : index + 32] for index in range(64, len(entry_set), 32))[
                    : name_units * 2
                ]
                entry_name = name_bytes.decode("utf-16-le")
                expected_hash = 0
                for byte in entry_name.upper().encode("utf-16-le"):
                    expected_hash = (((expected_hash & 1) << 15) | (expected_hash >> 1)) + byte
                    expected_hash &= 0xFFFF
                assert struct.unpack_from("<H", stream, 4)[0] == expected_hash
                first_cluster = struct.unpack_from("<I", stream, 20)[0]
                data_length = struct.unpack_from("<Q", stream, 24)[0]
                allocated.add(first_cluster)
                if struct.unpack_from("<H", entry_set, 4)[0] & 0x10:
                    assert data_length > 0
                else:
                    assert data_length == source.stat().st_size
                offset += (secondary_count + 1) * 32
            elif entry_type == 0x82:
                upcase_cluster = struct.unpack_from("<I", root, offset + 20)[0]
                upcase_length = struct.unpack_from("<Q", root, offset + 24)[0]
                upcase_data = mapper.read_bytes(
                    (
                        layout.partition_start_lba
                        + layout.cluster_heap_offset
                        + (upcase_cluster - 2) * layout.sectors_per_cluster
                    )
                    * 512,
                    upcase_length,
                )
                checksum = 0
                for byte in upcase_data:
                    checksum = ((checksum >> 1) | ((checksum & 1) << 31)) + byte
                    checksum &= 0xFFFFFFFF
                assert struct.unpack_from("<I", root, offset + 4)[0] == checksum
                assert upcase_length == layout.upcase_length
                offset += 32
            else:
                offset += 32
        bitmap = cluster_bytes(layout.bitmap_cluster)
        expected = {layout.root_dir_cluster, layout.bitmap_cluster, layout.upcase_cluster, *allocated}
        for cluster in expected:
            bit = cluster - 2
            assert bitmap[bit // 8] & (1 << (bit % 8))
    finally:
        mapper.close()


def test_large_image_keeps_fat_and_bitmap_lazy(tmp_path) -> None:
    """Large images use compact allocation ranges and lazy metadata."""
    source = tmp_path / "file.bin"
    source.write_bytes(b"payload")
    config = parse(_config(tmp_path, source.name).replace("image_size_gib: 1", "image_size_gib: 10240"))
    mapper = SectorMapper.from_layout(build_exfat(config))
    restored = SectorMapper.from_state(pickle.loads(pickle.dumps(mapper.to_state())))
    try:
        assert restored.layout.bitmap_length > 1024 * 1024
        assert len(pickle.dumps(restored.to_state())) < 2 * 1024 * 1024
        fat_offset = (restored.layout.partition_start_lba + restored.layout.fat_offset) * 512
        assert restored.read_bytes(fat_offset, 8) == b"\xf8\xff\xff\xff\xff\xff\xff\xff"
        bitmap_offset = (
            restored.layout.partition_start_lba
            + restored.layout.cluster_heap_offset
            + (restored.layout.bitmap_cluster - 2) * restored.layout.sectors_per_cluster
        ) * 512
        assert restored.read_bytes(bitmap_offset, 1) == b"\xff"
    finally:
        mapper.close()
        restored.close()
