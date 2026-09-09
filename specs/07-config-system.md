## Prefetch policy

RemotePFS prefetches filesystem metadata synchronously during activation, before NBD
connection and USB gadget binding. This pass is not a background worker.

```yaml
prefetch:
  directory_metadata:
    enabled: true
    refresh_interval_seconds: 300
  fat:
    enabled: true
    refresh_interval_seconds: 300
```

Both categories default to enabled with a five-minute refresh interval reserved
for future asynchronous refresh support. Full FAT warming uses bounded NBD reads
and does not materialize the FAT in RAM. Current prefetch reads through the
mapper and nbdkit cache; no parallel cache backend exists.

# Spec 07 - Config System & Sector Mapper

## Overview

The config system defines protocol-backed sources and virtual exFAT layout mappings. NFS and SMB/CIFS are supported in V1. Future providers such as HTTP(S), FTP, and torrent/P2P can reuse the generic source contract without changing virtual layout entries. The sector mapper translates NBD sector requests into source file reads.

## Configuration Format (YAML)

### File Location

`/etc/remotepfs/remotepfs.yaml`

### Schema

```yaml
# remotepfs.yaml — Virtual exFAT layout for RemotePFS

global:
  image_size_gib: 2047
  cluster_size_kib: 128
  label: REMOTEPFS
  oem_name: REMOTEPF
  usb_port: auto
# Protocol-backed sources
sources:
  - name: nas1
    protocol: nfs
    endpoint: 192.168.1.100:/volume1/games
    mount_point: /mnt/nas1
    read_only: true
    options: ro,nfsvers=4.1,nconnect=4,rsize=1048576,hard,noatime

  - name: nas2
    protocol: cifs
    endpoint: //192.168.1.101/moregames
    mount_point: /mnt/nas2
    read_only: true
    options: ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576
    credentials_file: /etc/remotepfs/nas2.credentials

entries:
  - virtual_path: fps games
    source: /mnt/nas1/fpsgames/
    type: directory

  - virtual_path: RPG Collection
    source: /mnt/nas1/rpg/
    type: directory

  - virtual_path: game.iso
    source: /mnt/nas2/images/ps5game.iso
    type: file

  - virtual_path: standalone.pkg
    source: /mnt/nas2/packages/mygame.pkg
    type: file
```

### Field Reference

#### `global`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image_size_gib` | u64 | 2047 | Virtual image size in GiB. Must be >= space needed for all entries + metadata and <= 2047 for the PS5-compatible MBR layout. |
| `cluster_size_kib` | u64 | 64 | Cluster size in KiB. Locked to 64 for PS5 compatibility (ShadowMountPlus). |
| `label` | str | "RemotePFS" | Volume label, uppercase, 11 chars max |
| `oem_name` | str | "REMOTEPFS" | OEM name, 8 chars |
| `usb_port` | str | `"auto"` | UDC selector. `auto` chooses deterministic fastest device-capable UDC; explicit value selects UDC by name. |
#### `sources[]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique source identifier |
| `protocol` | str | Yes | Provider identifier, such as `nfs` or `cifs` |
| `endpoint` | str | Yes | Provider endpoint. NFS uses `server:/export`; CIFS uses `//server/share` |
| `mount_point` | str | Yes in V1 | Local provider mount directory |
| `read_only` | bool | No | Defaults to `true`; `false` is rejected |
| `options` | str | No | Provider options. NFS/CIFS options must include standalone `ro` and reject `rw` |
| `credentials_file` | str | No | Absolute root-owned `0600` credential file for providers requiring credentials |

#### `entries[]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `virtual_path` | str | Yes | Path shown in exFAT root directory. Max 255 chars. |
| `source` | str | Yes | Provider path under one of the sources' mount points |
| `type` | str | Yes | `"file"` (single file) or `"directory"` (recursive scan) |

### Validation Rules

1. `image_size_gib` must be >= 1 and <= 2047 (PS5-compatible MBR partition limit)
2. `cluster_size_kib` must be exactly 128 (PS5 requirement)
3. `label`: length <= 11, uppercase ASCII only
4. `oem_name`: length == 8, uppercase ASCII only
5. `usb_port` must be `auto` or a non-empty UDC name without `/`
6. Automatic UDC selection must consider only device-capable controllers, rank by reported maximum speed, and use deterministic name ordering for ties
7. Selection must raise a clear error when no suitable UDC exists
8. Every `entries[].source` path must be under a valid `sources[].mount_point`
9. Every `entries[].virtual_path` must be unique (no duplicates)
10. Every `entries[].virtual_path` must not contain `/` (flat root directory in V1; subdirectories are collected under the virtual_path directory entry)
11. At least one `sources` and one `entries` item must exist
12. Source names must be unique
## Config Parsing & Validation

### ConfigManager

```python
class ConfigManager:
    """Parse, validate, and manage RemotePFS YAML config."""

    def __init__(self, config_path: str = "/etc/remotepfs/remotepfs.yaml"): ...

    def load(self) -> Config:  # Parse + validate from disk
        """Load and validate config from disk. Raises ConfigError on failure."""
        ...

    def validate(self, raw: dict) -> Config:
        """Validate a config dict (from API). Raises ConfigError on failure."""
        ...

    def reload(self) -> Config:  # Re-read from disk
        """Reload config file. Raises ConfigError on failure (current config preserved)."""
        ...


class Config:
    """Validated config object."""

    image_size_gib: int
    cluster_size_kib: int
    label: str
    oem_name: str
    usb_port: str
    sources: list[SourceConfig]
    entries: list[EntryConfig]


class SourceConfig:
    name: str
    protocol: str
    endpoint: str
    mount_point: str
    read_only: bool = True
    options: str = ""
    credentials_file: str | None = None


class EntryConfig:
    virtual_path: str
    source: str
    type: Literal["file", "directory"]
```

### ConfigError

```python
class ConfigError(Exception):
    """Config validation error with user-facing message."""

    def __init__(self, message: str, field: str | None = None):
        self.message = message
        self.field = field  # YAML path: "entries[0].source"
```

## Sector Mapper

The sector mapper translates NBD sector numbers to source file reads. It is built once at startup (or on config reload) from the validated config and the directory scan results.

### SectorMapper

```python
class SectorMapper:
    """Map virtual exFAT sector numbers to (source, offset) pairs."""

    def __init__(self, exfat_layout: ExfatLayout): ...

    def map(self, sector: int) -> SectorMapping:
        """Return the source mapping for a given sector number."""
        ...

    @property
    def total_sectors(self) -> int:
        """Total sectors in the virtual image."""
        ...


class SectorMapping:
    """Result of mapping a sector number."""

    kind: Literal["metadata", "file", "zero"]
    # For kind == "metadata":
    #   Data is from in-memory exFAT metadata buffer
    # For kind == "file":
    #   Data is from source_fd at byte offset
    source_fd: int | None = None  # Open file descriptor
    offset: int | None = None  # Byte offset within source file
    # For kind == "zero":
    #   Data is 512 zero bytes (unmapped sector)
```

### Mapping Algorithm

```
Input: sector (0-indexed sector number in virtual exFAT image)

1. Determine which exFAT region sector falls in:
   a. Boot region (sectors 0-11): kind="metadata", index into boot sector buffer
   b. Backup boot region (sectors image_size_sectors-12 to image_size_sectors-1): same as boot
   c. FAT region: kind="metadata", index into FAT buffer
   d. Data region (cluster 2+): Compute cluster_number, then:
      - Look up cluster in cluster_chain table
      - Each cluster maps to a contiguous byte range in a source file
      - Compute byte offset within that file
      - kind="file", source_fd, offset
   e. Unmapped sector: kind="zero"

2. Return SectorMapping
```

### Cluster Chain Table

Built during exFAT metadata generation:

```python
class ClusterChain:
    """A contiguous range of clusters mapped to a source file."""

    start_cluster: int  # First cluster in chain (exFAT cluster number)
    count_clusters: int  # Number of clusters
    source_path: str  # NFS path
    source_fd: int  # Open file descriptor (os.O_RDONLY)
    source_offset: int  # Byte offset within source file (0 for file entries)
```

### File Descriptor Management

Source files are opened with `os.open(path, os.O_RDONLY)` during startup/mapping. FDs are kept open for the lifetime of the service. This avoids per-read `open()` syscall overhead and is safe because:

- All NFS mounts are `hard` — if the NAS goes away, reads block until it returns
- No writes happening, so no data corruption risk
- FD leak impossible: each file opened once, closed only on service stop or config reload

## Directory Scanning (type=directory entries)

When `type = "directory"`, the sector mapper recursively scans the source NFS directory and builds exFAT directory entries for all files and subdirectories within.

### Scan Logic

```python
def scan_directory(source_path: str, virtual_parent: str) -> list[VirtualEntry]:
    """Scan NFS directory and return exFAT directory entries.

    source_path: NFS directory path (e.g., "/mnt/nas1/fpsgames/")
    virtual_parent: Virtual parent directory name (e.g., "fps games")
    """
    entries = []
    for entry in sorted(os.scandir(source_path), key=lambda e: e.name):
        if entry.is_dir():
            # ShadowMountPlus: looks for param.sfo in subdirs to detect game folders
            entries.append(
                VirtualEntry(
                    virtual_path=f"{virtual_parent}/{entry.name}",
                    source_path=entry.path,
                    is_directory=True,
                )
            )
            entries.extend(scan_directory(entry.path, f"{virtual_parent}/{entry.name}"))
        elif entry.is_file():
            entries.append(
                VirtualEntry(
                    virtual_path=f"{virtual_parent}/{entry.name}",
                    source_path=entry.path,
                    is_directory=False,
                    file_size=entry.stat().st_size,
                )
            )
    return entries
```

Each `VirtualEntry` becomes one exFAT directory entry with an associated `ClusterChain` for file data.

### exFAT Directory Name Rules

- Virtual directory names: truncated to 255 bytes (exFAT limit)
- Special characters: no filtering (exFAT supports UCS-2, so far broader than ASCII)
- Invalid exFAT chars: `/` is not allowed in directory entry names (replaced with `_` if found in source)
- Upper/lower case preserved (exFAT is case-insensitive but case-preserving)
- Unicode normalization: not performed (filesystem provides raw bytes)

## Config Hot-Reload Sequence

1. Validate new config (without applying)
2. If invalid: return errors, keep current config running
3. Acquire exclusive lock (stop accepting NBD requests)
4. Unbind UDC (PS5 sees disconnect)
5. Unmount NFS sources not in new config
6. Mount new NFS sources
7. Scan new directory entries
8. Rebuild sector mapper with new cluster chain table
9. Atomically swap NBD server's mapper reference
10. Rebind UDC (PS5 sees new device)
11. Release lock

Total disruption time: ~5-15 seconds. ShadowMountPlus re-scans after 10s stability wait + 15s scan interval = ~25s before games reappear on PS5.

## References

- YAML specification: https://yaml.org/spec/
- DD-02: Virtual exFAT Filesystem
- DD-03: Config-Driven Virtual Layout
- DD-04: Config Hot-Reload
- KB 01: ShadowMountPlus (game detection via param.sfo + image file extensions)
