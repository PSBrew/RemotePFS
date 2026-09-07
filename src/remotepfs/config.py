"""Config system: YAML parsing and validation per spec 07 and spec 05."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from .consts import DEFAULT_CIFS_OPTIONS, DEFAULT_NFS_OPTIONS

MAX_CONFIG_BYTES = 1_048_576  # 1 MiB body limit (spec 05)
MAX_ENTRIES = 10_000  # spec 05 hard limit
MAX_VIRTUAL_PATH_LEN = 255  # exFAT limit

IMAGE_SIZE_GIB_MIN = 1
IMAGE_SIZE_GIB_MAX = 262_144  # 256 TiB, exFAT limit

FORBIDDEN_PATTERNS = ("..", "./", "~")


class ConfigError(Exception):
    """Config validation error with an optional YAML field path."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class _StrictLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and duplicate mapping keys."""

    def compose_node(self, parent: Any, index: Any) -> Node:
        """Reject aliases before PyYAML expands referenced nodes."""
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ConstructorError(None, None, "YAML aliases are not supported", event.start_mark)
        return super().compose_node(parent, index)


def _construct_mapping(loader: yaml.Loader, node: MappingNode, deep: bool = False) -> dict[object, object]:
    """Construct a mapping while rejecting duplicate keys."""
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(None, None, f"duplicate YAML key: {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class SourceConfig:
    """One protocol-backed source."""

    name: str
    protocol: str
    endpoint: str
    mount_point: str
    read_only: bool = True
    options: str = ""
    credentials_file: str | None = None


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
    usb_port: str = "auto"

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


def _validate_global(raw: dict[str, object]) -> tuple[int, int, str, str, str]:
    """Validate global settings; return image, cluster, labels, and USB port."""
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

    usb_port = raw.get("usb_port", "auto")
    if not isinstance(usb_port, str) or not usb_port or "/" in usb_port:
        raise ConfigError(
            "global.usb_port: must be 'auto' or a non-empty UDC name",
            field="global.usb_port",
        )
    return image_size_gib, cluster_size_kib, label, oem_name, usb_port


def _validate_sources(raw: list[object]) -> list[SourceConfig]:
    """Validate registered protocol sources; names must be unique."""
    if not raw:
        raise ConfigError("at least one [[sources]] entry is required", field="sources")
    sources: list[SourceConfig] = []
    names: set[str] = set()
    for i, item in enumerate(raw):
        field = f"sources[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{field}: must be a table", field=field)
        name = _validate_str(item.get("name"), f"{field}.name")
        if name in names:
            raise ConfigError(f"{field}.name: duplicate source name '{name}'", field=f"{field}.name")
        names.add(name)
        protocol = item.get("protocol", "nfs")
        if (
            not isinstance(protocol, str)
            or not protocol
            or not protocol.replace("+", "").replace("-", "").replace(".", "").isalnum()
        ):
            raise ConfigError(f"{field}.protocol: must be a protocol identifier", field=f"{field}.protocol")
        endpoint = _validate_str(item.get("endpoint"), f"{field}.endpoint")
        if protocol == "nfs" and ":" not in endpoint:
            raise ConfigError(f"{field}.endpoint: NFS endpoint must be server:/export", field=f"{field}.endpoint")
        if protocol == "cifs" and not endpoint.startswith("//"):
            raise ConfigError(f"{field}.endpoint: CIFS endpoint must be //server/share", field=f"{field}.endpoint")
        mount_point = _validate_str(item.get("mount_point"), f"{field}.mount_point")
        if not mount_point.startswith("/"):
            raise ConfigError(f"{field}.mount_point: must be absolute", field=f"{field}.mount_point")
        _validate_no_traversal(mount_point, f"{field}.mount_point")
        read_only = item.get("read_only", True)
        if read_only is not True:
            raise ConfigError(f"{field}.read_only: must remain true", field=f"{field}.read_only")
        if "options" not in item:
            options = DEFAULT_NFS_OPTIONS if protocol == "nfs" else DEFAULT_CIFS_OPTIONS if protocol == "cifs" else ""
        else:
            options = item["options"]
        if not isinstance(options, str):
            raise ConfigError(f"{field}.options: must be a string", field=f"{field}.options")
        credentials_file = item.get("credentials_file")
        if credentials_file is not None:
            if not isinstance(credentials_file, str) or not credentials_file:
                raise ConfigError(f"{field}.credentials_file: must be a string", field=f"{field}.credentials_file")
            if not credentials_file.startswith("/"):
                raise ConfigError(f"{field}.credentials_file: must be absolute", field=f"{field}.credentials_file")
            _validate_no_traversal(credentials_file, f"{field}.credentials_file")
        if protocol == "cifs" and not credentials_file:
            raise ConfigError(
                f"{field}.credentials_file: required for cifs sources", field=f"{field}.credentials_file"
            )
        if protocol in {"nfs", "cifs"}:
            option_tokens = {token.strip() for token in options.split(",")}
            if "ro" not in option_tokens or "rw" in option_tokens:
                raise ConfigError(
                    f"{field}.options: must contain standalone 'ro' and must not contain 'rw'",
                    field=f"{field}.options",
                )
        sources.append(
            SourceConfig(
                name=name,
                protocol=protocol,
                endpoint=endpoint,
                mount_point=mount_point,
                read_only=read_only,
                options=options,
                credentials_file=credentials_file,
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
    """Validate parsed YAML data and return a Config."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping", field="<root>")

    g = raw.get("global")
    if not isinstance(g, dict):
        raise ConfigError("global section is required", field="global")
    image_size_gib, cluster_size_kib, label, oem_name, usb_port = _validate_global(g)

    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list):
        raise ConfigError("sources sequence is required", field="sources")
    sources = _validate_sources(sources_raw)

    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise ConfigError("entries sequence is required", field="entries")
    entries = _validate_entries(entries_raw, sorted((s.mount_point for s in sources), key=len, reverse=True))
    return Config(
        image_size_gib=image_size_gib,
        cluster_size_kib=cluster_size_kib,
        label=label,
        oem_name=oem_name,
        sources=sources,
        entries=entries,
        usb_port=usb_port,
    )


def parse(text: str) -> Config:
    """Parse and validate a YAML config string."""
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ConfigError(f"config exceeds 1 MiB ({MAX_CONFIG_BYTES} bytes)", field="<body>")
    try:
        raw = yaml.load(text, Loader=_StrictLoader)
    except RecursionError as exc:
        raise ConfigError("YAML nesting exceeds parser limits") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error: {exc}") from exc
    return validate(raw)


def load(path: str) -> Config:
    """Load, parse, and validate a YAML config file from disk."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    return parse(text)
