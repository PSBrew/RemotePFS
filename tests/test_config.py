"""Tests for remotepfs.config: YAML parsing and validation per spec 07."""

from __future__ import annotations

from typing import NoReturn

import pytest

import remotepfs.config as config_module
from remotepfs.config import ConfigError, load, parse, validate

VALID = """\
global:
  image_size_gib: 512
  cluster_size_kib: 64
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: nas1
    protocol: nfs
    endpoint: 192.168.1.100:/volume1/games
    mount_point: /mnt/nas1
    options: ro,hard
entries:
  - virtual_path: fps games
    source: /mnt/nas1/fpsgames/
    type: directory
  - virtual_path: game.iso
    source: /mnt/nas1/images/game.iso
    type: file
"""


def test_parse_valid_config() -> None:
    cfg = parse(VALID)
    assert cfg.image_size_gib == 512
    assert cfg.cluster_size_kib == 64
    assert cfg.label == "REMOTEPFS"
    assert cfg.oem_name == "REMOTEPF"
    assert cfg.usb_port == "auto"
    assert len(cfg.sources) == 1
    assert cfg.sources[0].name == "nas1"
    assert len(cfg.entries) == 2
    assert cfg.image_size_bytes == 512 * 1024**3


def test_prefetch_defaults_are_enabled() -> None:
    """Enable directory metadata and full FAT by default."""
    cfg = parse(VALID)
    assert cfg.prefetch.directory_metadata.enabled is True
    assert cfg.prefetch.fat.enabled is True
    assert cfg.prefetch.directory_metadata.refresh_interval_seconds == 300
    assert cfg.prefetch.fat.refresh_interval_seconds == 300


def test_prefetch_policy_is_validated() -> None:
    """Reject invalid nested prefetch types and intervals."""
    invalid_configs = (
        VALID + "prefetch:\n  fat:\n    enabled: 1\n",
        VALID + "prefetch:\n  fat:\n    refresh_interval_seconds: -1\n",
    )
    for text in invalid_configs:
        with pytest.raises(ConfigError, match="prefetch"):
            parse(text)


def test_full_fat_prefetch_policy_is_enabled() -> None:
    """Parse explicit full-FAT prefetch settings."""
    cfg = parse(VALID + "prefetch:\n  fat:\n    enabled: true\n    refresh_interval_seconds: 300\n")
    assert cfg.prefetch.fat.enabled is True


def test_nfs_options_require_standalone_read_only_flag() -> None:
    """Reject missing, writable, and substring lookalike read-only options."""
    for options in ("", "rw,hard", "xro,hard", "ro,rw"):
        bad = VALID.replace("options: ro,hard", f"options: {options}")
        with pytest.raises(ConfigError):
            parse(bad)


def test_cifs_source_requires_credentials_and_read_only_options() -> None:
    cifs = VALID.replace(
        "protocol: nfs\n    endpoint: 192.168.1.100:/volume1/games",
        "protocol: cifs\n    endpoint: //NAS_IP/games",
    ).replace(
        "options: ro,hard",
        "options: ro,vers=3.1.1\n    credentials_file: /etc/remotepfs/smb.credentials",
    )
    cfg = parse(cifs)
    assert cfg.sources[0].protocol == "cifs"
    assert cfg.sources[0].endpoint == "//NAS_IP/games"
    assert cfg.sources[0].credentials_file == "/etc/remotepfs/smb.credentials"
    for bad in (
        cifs.replace("    credentials_file: /etc/remotepfs/smb.credentials\n", ""),
        cifs.replace("options: ro,vers=3.1.1", "options: ro,rw,vers=3.1.1"),
        cifs.replace("credentials_file: /etc/remotepfs/smb.credentials", "credentials_file: smb.credentials"),
    ):
        with pytest.raises(ConfigError):
            parse(bad)


def test_future_protocol_identifier_is_preserved() -> None:
    future = VALID.replace(
        "protocol: nfs\n    endpoint: 192.168.1.100:/volume1/games",
        "protocol: https\n    endpoint: https://example.invalid/games",
    )
    cfg = parse(future)
    assert cfg.sources[0].protocol == "https"
    assert cfg.sources[0].endpoint == "https://example.invalid/games"


def test_parse_rejects_invalid_yaml() -> None:
    with pytest.raises(ConfigError, match="YAML parse error"):
        parse("global: [")


def test_recursion_error_is_reported_as_config_error(monkeypatch) -> None:
    def raise_recursion(*args: object, **kwargs: object) -> NoReturn:
        raise RecursionError("too deep")

    monkeypatch.setattr(config_module.yaml, "load", raise_recursion)
    with pytest.raises(ConfigError, match="nesting exceeds"):
        parse(VALID)


def test_duplicate_yaml_keys_rejected() -> None:
    duplicate = VALID.replace(
        "    endpoint: 192.168.1.100:/volume1/games\n", "    endpoint: first:/games\n    endpoint: second:/games\n"
    )
    with pytest.raises(ConfigError, match="duplicate YAML key"):
        parse(duplicate)


def test_yaml_aliases_rejected() -> None:
    aliased = """\
global: &defaults
  image_size_gib: 512
  cluster_size_kib: 64
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: nas1
    protocol: nfs
    endpoint: 192.168.1.100:/volume1/games
    mount_point: /mnt/nas1
    options: ro,hard
entries:
  - virtual_path: game.iso
    source: /mnt/nas1/images/game.iso
    type: file
  - <<: *defaults
"""
    with pytest.raises(ConfigError, match="aliases are not supported"):
        parse(aliased)


def test_parse_rejects_oversized_body() -> None:
    big = "global:\n  label: '" + "A" * 2_000_000 + "'\n"
    with pytest.raises(ConfigError, match="1 MiB"):
        parse(big)


def test_cluster_size_locked_to_64() -> None:
    bad = VALID.replace("cluster_size_kib: 64", "cluster_size_kib: 32")
    with pytest.raises(ConfigError, match="exactly 64"):
        parse(bad)


def test_label_rules() -> None:
    for bad_label in ("remotepfs", "THIRTEENCHARS"):
        bad = VALID.replace("label: REMOTEPFS", f"label: {bad_label}")
        with pytest.raises(ConfigError, match="label"):
            parse(bad)


def test_oem_name_must_be_8_chars() -> None:
    bad = VALID.replace("oem_name: REMOTEPF", "oem_name: TOOLONG")
    with pytest.raises(ConfigError, match="exactly 8"):
        parse(bad)


def test_image_size_bounds() -> None:
    for size in ("0", "262145"):
        bad = VALID.replace("image_size_gib: 512", f"image_size_gib: {size}")
        with pytest.raises(ConfigError, match="image_size_gib"):
            parse(bad)


def test_virtual_path_with_slash_rejected() -> None:
    bad = VALID.replace("virtual_path: game.iso", "virtual_path: nested/game")
    with pytest.raises(ConfigError, match="must not contain '/'"):
        parse(bad)


def test_duplicate_virtual_path_rejected() -> None:
    dup = VALID.replace("virtual_path: game.iso", "virtual_path: fps games")
    with pytest.raises(ConfigError, match="duplicate virtual path"):
        parse(dup)


def test_source_must_be_under_mount_point() -> None:
    bad = VALID.replace("source: /mnt/nas1/images/game.iso", "source: /etc/passwd")
    with pytest.raises(ConfigError, match="under a configured mount_point"):
        parse(bad)


def test_path_traversal_rejected() -> None:
    bad = VALID.replace("source: /mnt/nas1/images/game.iso", "source: /mnt/nas1/../etc/passwd")
    with pytest.raises(ConfigError, match="path traversal"):
        parse(bad)


def test_duplicate_source_name_rejected() -> None:
    duplicate_source = """  - name: nas1
    protocol: nfs
    endpoint: 10.0.0.2:/x
    mount_point: /mnt/nas2
    options: ro
"""
    dup = VALID.replace("options: ro,hard\nentries:", f"options: ro,hard\n{duplicate_source}entries:", 1)
    with pytest.raises(ConfigError, match="duplicate source name"):
        parse(dup)


def test_missing_sources_or_entries_rejected() -> None:
    no_entries = "global:\n  label: X\n"
    with pytest.raises(ConfigError):
        parse(no_entries)


def test_entry_type_must_be_file_or_directory() -> None:
    bad = VALID.replace("type: file", "type: symlink")
    with pytest.raises(ConfigError, match="must be 'file' or 'directory'"):
        parse(bad)


def test_mount_points_sorted_by_length() -> None:
    cfg = parse(VALID)
    assert cfg.mount_points == ["/mnt/nas1"]


def test_validate_dict_directly() -> None:
    cfg = validate(
        {
            "global": {"image_size_gib": 1, "oem_name": "REMOTEPF"},
            "sources": [
                {
                    "name": "s",
                    "protocol": "nfs",
                    "endpoint": "10.0.0.1:/e",
                    "mount_point": "/mnt/x",
                    "options": "ro",
                }
            ],
            "entries": [{"virtual_path": "a", "source": "/mnt/x/a", "type": "file"}],
        }
    )
    assert cfg.entries[0].virtual_path == "a"


def test_load_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load(str(tmp_path / "nope.yaml"))
