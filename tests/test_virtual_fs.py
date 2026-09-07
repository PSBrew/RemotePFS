"""Regression coverage for virtual exFAT build and sector reads."""

from __future__ import annotations

import os
import pickle
import struct
import zlib

from remotepfs.config import parse
from remotepfs.exfat_builder import (
    _STATX_BTIME,
    _exfat_timestamp,
    _source_stat,
    _SourceStat,
    _Statx,
    build_exfat,
    scan_directory,
)
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


def test_file_and_directory_timestamps_use_source_metadata(tmp_path) -> None:
    """Encode source file and directory timestamps in exFAT entries."""
    source_dir = tmp_path / "folder"
    source_dir.mkdir()
    source_file = source_dir / "game.bin"
    source_file.write_bytes(b"payload")
    directory_time_ns = 1_700_000_000_123_000_000
    file_time_ns = 1_700_000_002_987_000_000
    os.utime(source_dir, ns=(directory_time_ns, directory_time_ns))
    os.utime(source_file, ns=(file_time_ns, file_time_ns))
    directory_stat = source_dir.stat()
    file_stat = source_file.stat()
    config = parse(
        f"""global:
  image_size_gib: 1
  cluster_size_kib: 64
  label: TEST
sources:
  - name: local
    protocol: nfs
    endpoint: local:/export
    mount_point: {tmp_path}
entries:
  - virtual_path: folder
    source: {source_dir}
    type: directory
"""
    )
    mapper = SectorMapper.from_layout(build_exfat(config))
    layout = mapper.layout

    def cluster_bytes(cluster: int) -> bytes:
        offset = (
            layout.partition_start_lba + layout.cluster_heap_offset + (cluster - 2) * layout.sectors_per_cluster
        ) * 512
        return mapper.read_bytes(offset, layout.sectors_per_cluster * 512)

    def assert_timestamp_fields(entry: bytes, source_stat: os.stat_result) -> None:
        created_date, created_time, created_increment = _exfat_timestamp(
            getattr(source_stat, "st_birthtime_ns", None) or source_stat.st_mtime_ns
        )
        modified_date, modified_time, modified_increment = _exfat_timestamp(source_stat.st_mtime_ns)
        accessed_date, accessed_time, _ = _exfat_timestamp(source_stat.st_atime_ns)
        assert struct.unpack_from("<H", entry, 8)[0] == created_time
        assert struct.unpack_from("<H", entry, 10)[0] == created_date
        assert struct.unpack_from("<H", entry, 12)[0] == modified_time
        assert struct.unpack_from("<H", entry, 14)[0] == modified_date
        assert struct.unpack_from("<H", entry, 16)[0] == accessed_time
        assert struct.unpack_from("<H", entry, 18)[0] == accessed_date
        assert entry[20] == created_increment
        assert entry[21] == modified_increment
        assert entry[22:25] == b"\x80\x80\x80"

    try:
        root = cluster_bytes(layout.root_dir_cluster)
        assert_timestamp_fields(root[96:128], directory_stat)
        nested_cluster = struct.unpack_from("<I", root, 96 + 32 + 20)[0]
        nested = cluster_bytes(nested_cluster)
        assert_timestamp_fields(nested[:32], file_stat)
    finally:
        mapper.close()


def test_exfat_timestamp_clamps_range_and_keeps_subsecond_precision() -> None:
    """Encode exFAT timestamp limits and 10 ms increments deterministically."""
    minimum = 315532800 * 1_000_000_000
    maximum = 4_354_819_199 * 1_000_000_000
    assert _exfat_timestamp(minimum - 1) == _exfat_timestamp(minimum)
    assert _exfat_timestamp(maximum + 1_000_000_000) == _exfat_timestamp(maximum + 999_000_000)
    assert _exfat_timestamp(1_700_000_001_230_000_000)[2] == 123


def test_statx_reader_returns_complete_snapshot_and_falls_back(monkeypatch, tmp_path) -> None:
    """Read one complete birth-time snapshot from statx and fall back on failure."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")

    def fake_statx(_dirfd, _path, _flags, _mask, result: _Statx) -> int:
        result.stx_mask = _STATX_BTIME | 0x07FF
        result.stx_size = 7
        result.stx_atime.tv_sec = 1_650_000_001
        result.stx_mtime.tv_sec = 1_650_000_002
        result.stx_btime.tv_sec = 1_650_000_000
        result.stx_btime.tv_nsec = 123_000_000
        return 0

    monkeypatch.setattr("remotepfs.exfat_builder._statx", fake_statx)
    snapshot = _source_stat(str(source))
    assert snapshot is not None
    assert snapshot.size_bytes == 7
    assert snapshot.accessed_ns == 1_650_000_001_000_000_000
    assert snapshot.modified_ns == 1_650_000_002_000_000_000
    assert snapshot.created_ns == 1_650_000_000_123_000_000
    monkeypatch.setattr("remotepfs.exfat_builder._statx", lambda *_args: -1)
    fallback = _source_stat(str(source))
    assert fallback is not None
    assert fallback.created_ns == fallback.modified_ns


def test_builder_uses_snapshot_birthtime_for_creation_field(monkeypatch, tmp_path) -> None:
    """Use one complete source snapshot for creation and modification fields."""
    source = tmp_path / "game.bin"
    source.write_bytes(b"payload")
    modified_ns = 1_700_000_000_000_000_000
    birth_ns = 1_600_000_000_000_000_000
    snapshot = _SourceStat(size_bytes=7, accessed_ns=modified_ns, modified_ns=modified_ns, created_ns=birth_ns)
    monkeypatch.setattr("remotepfs.exfat_builder._source_stat", lambda _path: snapshot)
    mapper = SectorMapper.from_layout(build_exfat(parse(_config(tmp_path, source.name))))
    try:
        layout = mapper.layout
        root_offset = (
            layout.partition_start_lba
            + layout.cluster_heap_offset
            + (layout.root_dir_cluster - 2) * layout.sectors_per_cluster
        ) * 512
        root = mapper.read_bytes(root_offset, layout.sectors_per_cluster * 512)
        entry = root[96:128]
        created = _exfat_timestamp(birth_ns)
        modified = _exfat_timestamp(modified_ns)
        assert struct.unpack_from("<HH", entry, 8) == (created[1], created[0])
        assert struct.unpack_from("<HH", entry, 12) == (modified[1], modified[0])
    finally:
        mapper.close()


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
