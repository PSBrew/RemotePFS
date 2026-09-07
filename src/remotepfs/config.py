"""Config system: TOML parsing and validation per spec 07 (config-system) and spec 05 (security limits).

Schema (spec 07):

    [global]
    image_size_gib = 512          # >= 1, <= 262144 (exFAT limit)
    cluster_size_kib = 64         # locked to 64 (PS5 / ShadowMountPlus)
    label = "REMOTEPFS"           # <= 11 chars, uppercase ASCII
    oem_name = "REMOTEPFS"        # exactly 8 chars, uppercase ASCII

    [[sources]]
    name / server / export / mount_point / nfs_options

    [[entries]]
    virtual_path / source / type ("file" | "directory")

Security limits (spec 05): path traversal rejection, max 10 000 entries.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MAX_CONFIG_BYTES = 1_048_576  # 1 MiB body limit (spec 05)
MAX_ENTRIES = 10_000  # spec 05 hard limit
MAX_VIRTUAL_PATH_LEN = 255  # exFAT limit

IMAGE_SIZE_GIB_MIN = 1
IMAGE_SIZE_GIB_MAX = 262_144  # 256 TiB, exFAT limit

FORBIDDEN_PATTERNS = ("..", "./", "~")


class ConfigError(Exception):
    """Config validation error with an optional TOML field path."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class SourceConfig:
    """One NFS mount source."""

    name: str
    server: str
    export: str
    mount_point: str
    nfs_options: str = ""


@dataclass(frozen=True)
class EntryConfig:
    """One virtual filesystem entry (file or directory in the exFAT root)."""

    virtual_path: str
    source: str
    type: Literal["file", "directory"]


@dataclass(frozen=True)
class Config:
    """Validated RemotePFS configuration."""

    image_size_gib: int
    cluster_size_kib: int
    label: str
    oem_name: str
    sources: list[SourceConfig] = field(default_factory=list)
    entries: list[EntryConfig] = field(default_factory=list)

    @property
    def image_size_bytes(self) -> int:
        """Virtual image size in bytes."""
        return self.image_size_gib * 1024**3

    @property
    def mount_points(self) -> list[str]:
        """Configured mount points, longest first (so prefix checks match the most specific source)."""
        return sorted((s.mount_point for s in self.sources), key=len, reverse=True)


def _validate_str(value: object, field_name: str, *, required: bool = True) -> str:
    """Require a non-empty string field."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field_name}: must be a non-empty string", field=field_name)
    return value


def _validate_no_traversal(path: str, field_name: str) -> None:
    """Reject path traversal patterns per spec 05 section 6.3."""
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in path:
            raise ConfigError(f"{field_name}: path traversal pattern '{pattern}' rejected", field=field_name)


def _validate_global(raw: dict[str, object]) -> tuple[int, int, str, str]:
    """Validate the [global] table; return (image_size_gib, cluster_size_kib, label, oem_name)."""
    image_size_gib = raw.get("image_size_gib", 2048)
    if not isinstance(image_size_gib, int) or isinstance(image_size_gib, bool):
        raise ConfigError("global.image_size_gib: must be an integer", field="global.image_size_gib")
    if not IMAGE_SIZE_GIB_MIN <= image_size_gib <= IMAGE_SIZE_GIB_MAX:
        raise ConfigError(
            f"global.image_size_gib: must be between {IMAGE_SIZE_GIB_MIN} and {IMAGE_SIZE_GIB_MAX}",
            field="global.image_size_gib",
        )

    cluster_size_kib = raw.get("cluster_size_kib", 64)
    if cluster_size_kib != 64:
        raise ConfigError(
            "global.cluster_size_kib: must be exactly 64 (PS5 requirement)",
            field="global.cluster_size_kib",
        )

    label = raw.get("label", "REMOTEPFS")
    if not isinstance(label, str) or not label.isascii() or len(label) > 11 or label != label.upper():
        raise ConfigError("global.label: must be uppercase ASCII, at most 11 chars", field="global.label")

    oem_name = raw.get("oem_name", "REMOTEPF")
    if not isinstance(oem_name, str) or not oem_name.isascii() or len(oem_name) != 8 or oem_name != oem_name.upper():
        raise ConfigError("global.oem_name: must be uppercase ASCII, exactly 8 chars", field="global.oem_name")
    return image_size_gib, cluster_size_kib, label, oem_name


def _validate_sources(raw: list[object]) -> list[SourceConfig]:
    """Validate [[sources]] array; names must be unique."""
    if not raw:
        raise ConfigError("at least one [[sources]] entry is required", field="sources")
    sources: list[SourceConfig] = []
    names: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"sources[{i}]: must be a table", field=f"sources[{i}]")
        name = _validate_str(item.get("name"), f"sources[{i}].name")
        if name in names:
            raise ConfigError(f"sources[{i}].name: duplicate source name '{name}'", field=f"sources[{i}].name")
        names.add(name)
        server = _validate_str(item.get("server"), f"sources[{i}].server")
        export = _validate_str(item.get("export"), f"sources[{i}].export")
        mount_point = _validate_str(item.get("mount_point"), f"sources[{i}].mount_point")
        if not mount_point.startswith("/"):
            raise ConfigError(f"sources[{i}].mount_point: must be absolute", field=f"sources[{i}].mount_point")
        _validate_no_traversal(mount_point, f"sources[{i}].mount_point")
        nfs_options = item.get("nfs_options", "")
        if not isinstance(nfs_options, str):
            raise ConfigError(f"sources[{i}].nfs_options: must be a string", field=f"sources[{i}].nfs_options")
        sources.append(
            SourceConfig(
                name=name,
                server=server,
                export=export,
                mount_point=mount_point,
                nfs_options=nfs_options,
            )
        )
    return sources


def _validate_entries(raw: list[object], mount_points: list[str]) -> list[EntryConfig]:
    """Validate [[entries]] array per spec 07 rules 5-7 and spec 05 limits."""
    if not raw:
        raise ConfigError("at least one [[entries]] entry is required", field="entries")
    if len(raw) > MAX_ENTRIES:
        raise ConfigError(f"entries: {len(raw)} entries exceeds the maximum of {MAX_ENTRIES}", field="entries")
    entries: list[EntryConfig] = []
    paths: set[str] = set()
    for i, item in enumerate(raw):
        field = f"entries[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{field}: must be a table", field=field)
        virtual_path = _validate_str(item.get("virtual_path"), f"{field}.virtual_path")
        if "/" in virtual_path:
            raise ConfigError(
                f"{field}.virtual_path: must not contain '/' (flat root in V1)",
                field=f"{field}.virtual_path",
            )
        if len(virtual_path.encode("utf-8")) > MAX_VIRTUAL_PATH_LEN:
            raise ConfigError(f"{field}.virtual_path: exceeds 255 bytes", field=f"{field}.virtual_path")
        if virtual_path in paths:
            raise ConfigError(
                f"{field}.virtual_path: duplicate virtual path '{virtual_path}'",
                field=f"{field}.virtual_path",
            )
        paths.add(virtual_path)
        _validate_no_traversal(virtual_path, f"{field}.virtual_path")

        source = _validate_str(item.get("source"), f"{field}.source")
        _validate_no_traversal(source, f"{field}.source")
        if not any(source == mp or source.startswith(mp + "/") for mp in mount_points):
            raise ConfigError(
                f"{field}.source: must be under a configured mount_point",
                field=f"{field}.source",
            )

        entry_type = item.get("type")
        if entry_type not in ("file", "directory"):
            raise ConfigError(
                f"{field}.type: must be 'file' or 'directory', got {entry_type!r}",
                field=f"{field}.type",
            )
        entries.append(EntryConfig(virtual_path=virtual_path, source=source, type=entry_type))
    return entries


def validate(raw: dict[str, object]) -> Config:
    """Validate a config dict (from TOML or JSON) and return a Config.

    Args:
        raw: Parsed TOML/JSON config dict with ``global``, ``sources`` and
            ``entries`` keys.

    Returns:
        Validated Config object.

    Raises:
        ConfigError: On the first validation failure, with a field path.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config must be a TOML table", field="<root>")

    g = raw.get("global")
    if not isinstance(g, dict):
        raise ConfigError("[global] section is required", field="global")
    image_size_gib, cluster_size_kib, label, oem_name = _validate_global(g)

    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list):
        raise ConfigError("[[sources]] array is required", field="sources")
    sources = _validate_sources(sources_raw)

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise ConfigError("[[entries]] array is required", field="entries")
    entries = _validate_entries(entries_raw, sorted((s.mount_point for s in sources), key=len, reverse=True))
    return Config(
        image_size_gib=image_size_gib,
        cluster_size_kib=cluster_size_kib,
        label=label,
        oem_name=oem_name,
        sources=sources,
        entries=entries,
    )


def parse(text: str) -> Config:
    """Parse and validate a TOML config string.

    Args:
        text: TOML config contents.

    Returns:
        Validated Config object.

    Raises:
        ConfigError: On TOML syntax errors or validation failures.
    """
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ConfigError(f"config exceeds 1 MiB ({MAX_CONFIG_BYTES} bytes)", field="<body>")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML parse error: {exc}") from exc
    return validate(raw)


def load(path: str) -> Config:
    """Load, parse, and validate a config file from disk.

    Args:
        path: Path to the TOML config file (default /etc/remotepfs/remotepfs.conf).

    Returns:
        Validated Config object.

    Raises:
        ConfigError: On unreadable file, TOML errors, or validation failures.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return parse(text)
