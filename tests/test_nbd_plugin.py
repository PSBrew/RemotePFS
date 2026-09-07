"""Tests for nbdkit Python API v2 plugin behavior without nbdkit installed."""

from __future__ import annotations

import ast
import errno
import pickle
from pathlib import Path

import pytest

from remotepfs import remotepfs_nbd
from remotepfs.config import parse
from remotepfs.exfat_builder import build_exfat
from remotepfs.sector_mapper import SectorMapper


def _mapper(tmp_path) -> SectorMapper:
    source = tmp_path / "game.bin"
    source.write_bytes(b"plugin-data")
    config = parse(
        f"""global:
  image_size_gib: 1
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: s
    protocol: nfs
    endpoint: x:/e
    mount_point: {tmp_path}
    options: ro
entries:
  - virtual_path: game.bin
    source: {source}
    type: file
"""
    )
    return SectorMapper.from_layout(build_exfat(config))


def test_plugin_loads_state_and_reports_read_only_contract(tmp_path) -> None:
    mapper = _mapper(tmp_path)
    state_path = tmp_path / "mapper.state"
    state_path.write_bytes(pickle.dumps(mapper.to_state()))
    remotepfs_nbd.image_size = 0
    remotepfs_nbd.sector_mapper = None
    remotepfs_nbd.config("cache-on-read", "true")
    remotepfs_nbd.config("mapper_state", str(state_path))
    remotepfs_nbd.config_complete()
    assert remotepfs_nbd.get_size() == mapper.total_bytes
    assert remotepfs_nbd.block_size() == (512, 4096, 65536)
    assert remotepfs_nbd.can_write() is False
    assert remotepfs_nbd.can_trim() is False
    assert remotepfs_nbd.thread_model() == remotepfs_nbd.nbdkit.THREAD_MODEL_SERIALIZE_REQUESTS
    assert remotepfs_nbd.open(True) is None
    with pytest.raises(OSError, match="read-only"):
        remotepfs_nbd.open(False)
    remotepfs_nbd.close(None)
    mapper.close()


def test_plugin_pread_fills_buffer(tmp_path) -> None:
    mapper = _mapper(tmp_path)
    remotepfs_nbd.image_size = mapper.total_bytes
    remotepfs_nbd.sector_mapper = mapper
    mapping = mapper.layout.file_mappings[0]
    offset = mapper._file_start_sector(mapping) * 512
    buffer = bytearray(11)
    remotepfs_nbd.pread(None, memoryview(buffer), offset, 0)
    assert bytes(buffer) == b"plugin-data"
    mapper.close()


def test_plugin_pread_reports_source_errno_to_nbdkit(monkeypatch) -> None:
    """Report missing source files through nbdkit and raise OSError."""
    errors: list[int] = []

    class MissingSource:
        def read(self, offset: int, buffer: memoryview) -> None:
            raise FileNotFoundError(errno.ENOENT, "missing NFS source")

    monkeypatch.setattr(remotepfs_nbd.nbdkit, "set_error", errors.append, raising=False)
    remotepfs_nbd.image_size = 512
    remotepfs_nbd.sector_mapper = MissingSource()

    with pytest.raises(OSError) as raised:
        remotepfs_nbd.pread(None, memoryview(bytearray(512)), 0, 0)

    assert raised.value.errno == errno.ENOENT
    assert errors == [errno.ENOENT]


def test_plugin_extents_reports_zero_holes(tmp_path) -> None:
    mapper = _mapper(tmp_path)
    remotepfs_nbd.image_size = mapper.total_bytes
    remotepfs_nbd.sector_mapper = mapper
    extents = remotepfs_nbd.extents(None, 4096, 0, 0)
    assert extents
    assert extents[0][2] == 0
    mapper.close()


def test_plugin_import_chain_is_python39_parseable() -> None:
    """Keep files imported by Debian Bullseye nbdkit Python 3.9-compatible."""
    package_dir = Path(__file__).parents[1] / "src" / "remotepfs"
    for module_name in ("remotepfs_nbd.py", "sector_mapper.py", "exfat_builder.py", "consts.py"):
        module_path = package_dir / module_name
        ast.parse(module_path.read_text(), filename=str(module_path), feature_version=(3, 9))
