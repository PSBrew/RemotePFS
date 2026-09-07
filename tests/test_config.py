"""Tests for remotepfs.config: TOML parsing + validation per spec 07."""

from __future__ import annotations

import pytest

from remotepfs.config import ConfigError, load, parse, validate

VALID = """\
[global]
image_size_gib = 512
cluster_size_kib = 64
label = "REMOTEPFS"
oem_name = "REMOTEPF"

[[sources]]
name = "nas1"
server = "192.168.1.100"
export = "/volume1/games"
mount_point = "/mnt/nas1"
nfs_options = "ro,hard"

[[entries]]
virtual_path = "fps games"
source = "/mnt/nas1/fpsgames/"
type = "directory"

[[entries]]
virtual_path = "game.iso"
source = "/mnt/nas1/images/game.iso"
type = "file"
"""


def test_parse_valid_config() -> None:
    cfg = parse(VALID)
    assert cfg.image_size_gib == 512
    assert cfg.cluster_size_kib == 64
    assert cfg.label == "REMOTEPFS"
    assert cfg.oem_name == "REMOTEPF"
    assert len(cfg.sources) == 1
    assert cfg.sources[0].name == "nas1"
    assert len(cfg.entries) == 2
    assert cfg.image_size_bytes == 512 * 1024**3


def test_parse_rejects_invalid_toml() -> None:
    with pytest.raises(ConfigError, match="TOML parse error"):
        parse("[global]\nimage_size_gib = ")


def test_parse_rejects_oversized_body() -> None:
    big = '[global]\nlabel = "' + "A" * 2_000_000 + '"\n'
    with pytest.raises(ConfigError, match="1 MiB"):
        parse(big)


def test_cluster_size_locked_to_64() -> None:
    bad = VALID.replace("cluster_size_kib = 64", "cluster_size_kib = 32")
    with pytest.raises(ConfigError, match="exactly 64"):
        parse(bad)


def test_label_rules() -> None:
    for bad_label in ("remotepfs", "THIRTEENCHARS"):
        bad = VALID.replace('label = "REMOTEPFS"', f'label = "{bad_label}"')
        with pytest.raises(ConfigError, match="label"):
            parse(bad)


def test_oem_name_must_be_8_chars() -> None:
    bad = VALID.replace('oem_name = "REMOTEPF"', 'oem_name = "TOOLONG"')
    with pytest.raises(ConfigError, match="exactly 8"):
        parse(bad)


def test_image_size_bounds() -> None:
    for size in ("0", "262145"):
        bad = VALID.replace("image_size_gib = 512", f"image_size_gib = {size}")
        with pytest.raises(ConfigError, match="image_size_gib"):
            parse(bad)


def test_virtual_path_with_slash_rejected() -> None:
    bad = VALID.replace('virtual_path = "game.iso"', 'virtual_path = "nested/game"')
    with pytest.raises(ConfigError, match="must not contain '/'"):
        parse(bad)


def test_duplicate_virtual_path_rejected() -> None:
    dup = VALID.replace('virtual_path = "game.iso"', 'virtual_path = "fps games"')
    with pytest.raises(ConfigError, match="duplicate virtual path"):
        parse(dup)


def test_source_must_be_under_mount_point() -> None:
    bad = VALID.replace('source = "/mnt/nas1/images/game.iso"', 'source = "/etc/passwd"')
    with pytest.raises(ConfigError, match="under a configured mount_point"):
        parse(bad)


def test_path_traversal_rejected() -> None:
    bad = VALID.replace('source = "/mnt/nas1/images/game.iso"', 'source = "/mnt/nas1/../etc/passwd"')
    with pytest.raises(ConfigError, match="path traversal"):
        parse(bad)


def test_duplicate_source_name_rejected() -> None:
    dup = (
        VALID
        + """
[[sources]]
name = "nas1"
server = "10.0.0.2"
export = "/x"
mount_point = "/mnt/nas2"
"""
    )
    with pytest.raises(ConfigError, match="duplicate source name"):
        parse(dup)


def test_missing_sources_or_entries_rejected() -> None:
    no_entries = "[global]\nlabel = 'X'\n"
    with pytest.raises(ConfigError):
        parse(no_entries)


def test_entry_type_must_be_file_or_directory() -> None:
    bad = VALID.replace('type = "file"', 'type = "symlink"')
    with pytest.raises(ConfigError, match="must be 'file' or 'directory'"):
        parse(bad)


def test_mount_points_sorted_by_length() -> None:
    cfg = parse(VALID)
    assert cfg.mount_points == ["/mnt/nas1"]


def test_validate_dict_directly() -> None:
    cfg = validate(
        {
            "global": {"image_size_gib": 1, "oem_name": "REMOTEPF"},
            "sources": [{"name": "s", "server": "10.0.0.1", "export": "/e", "mount_point": "/mnt/x"}],
            "entries": [
                {"virtual_path": "a", "source": "/mnt/x/a", "type": "file"},
            ],
        }
    )
    assert cfg.entries[0].virtual_path == "a"


def test_load_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load(str(tmp_path / "nope.conf"))
