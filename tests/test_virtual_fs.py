"""Regression coverage for virtual exFAT build and sector reads."""

from __future__ import annotations

from remotepfs.config import parse
from remotepfs.exfat_builder import build_exfat, scan_directory
from remotepfs.sector_mapper import SectorMapper


def _config(tmp_path, filename: str) -> str:
    return f'''[global]
image_size_gib = 1
cluster_size_kib = 64
label = "REMOTEPFS"
oem_name = "REMOTEPF"

[[sources]]
name = "local"
server = "127.0.0.1"
export = "/exports"
mount_point = "{tmp_path}"

[[entries]]
virtual_path = "{filename}"
source = "{tmp_path / filename}"
type = "file"
'''


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
