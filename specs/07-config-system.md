# Spec 07 - Config System & Sector Mapper

## Overview

The config system defines NFS mount sources and virtual exFAT layout mappings. The sector mapper translates NBD sector requests into NFS file reads using this configuration. Together they form the core translation layer: "PS5 reads sector N" → "read byte range [X, X+512) from NFS file Y".

## Configuration Format (TOML)

### File Location

`/etc/remotepfs/remotepfs.conf`

### Schema

```toml
# remotepfs.conf — Virtual exFAT layout for RemotePFS

# Global settings
[global]
image_size_gib = 2048          # Virtual exFAT image size (GiB)
cluster_size_kib = 64          # Cluster size (KiB) — must match ShadowMountPlus: 64
label = "RemotePFS"            # Volume label (11 chars max, uppercase)
oem_name = "REMOTEPFS"         # OEM name (8 chars)

# NFS mount sources
[[sources]]
name = "nas1"
server = "192.168.1.100"
export = "/volume1/games"
mount_point = "/mnt/nas1"
nfs_options = "nfsvers=4.1,nconnect=4,rsize=1048576,wsize=1048576,hard,noatime"

[[sources]]
name = "nas2"
server = "192.168.1.101"
export = "/volume1/moregames"
mount_point = "/mnt/nas2"
nfs_options = "nfsvers=4.1,nconnect=4,rsize=1048576,wsize=1048576,hard,noatime"

# Virtual layout entries
[[entries]]
virtual_path = "fps games"     # PS5 shows this as directory name (max 255 chars)
source = "/mnt/nas1/fpsgames/" # NFS path relative to mount point
type = "directory"             # Recursively scan and include subdirs/files

[[entries]]
virtual_path = "RPG Collection"
source = "/mnt/nas1/rpg/"
type = "directory"

[[entries]]
virtual_path = "game.iso"
source = "/mnt/nas2/images/ps5game.iso"
type = "file"                  # Single file; virtual_path treated as leaf filename

[[entries]]
virtual_path = "standalone.pkg"
source = "/mnt/nas2/packages/mygame.pkg"
type = "file"
```

### Field Reference

#### `[global]`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image_size_gib` | u64 | 2048 | Virtual image size in GiB. Must be >= space needed for all entries + metadata. |
| `cluster_size_kib` | u64 | 64 | Cluster size in KiB. Locked to 64 for PS5 compatibility (ShadowMountPlus). |
| `label` | str | "RemotePFS" | Volume label, uppercase, 11 chars max |
| `oem_name` | str | "REMOTEPFS" | OEM name, 8 chars |

#### `[[sources]]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique identifier for this source |
| `server` | str | Yes | NFS server IP or hostname |
| `export` | str | Yes | NFS export path on server |
| `mount_point` | str | Yes | Local mount directory (created if missing) |
| `nfs_options` | str | No | Extra mount options (appended to defaults) |

#### `[[entries]]`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `virtual_path` | str | Yes | Path shown in exFAT root directory. Max 255 chars. |
| `source` | str | Yes | NFS source path (must be under one of the sources' mount points) |
| `type` | str | Yes | `"file"` (single file) or `"directory"` (recursive scan) |

### Validation Rules

1. `image_size_gib` must be >= 1 and <= 262144 (256 TiB, exFAT limit)
2. `cluster_size_kib` must be exactly 64 (PS5 requirement)
3. `label`: length <= 11, uppercase ASCII only
4. `oem_name`: length == 8, uppercase ASCII only
5. Every `entries[].source` path must be under a valid `sources[].mount_point`
6. `entries[].virtual_path` must be unique (no duplicates)
7. `entries[].virtual_path` must not contain `/` (flat root directory in V1; subdirectories are collected under the virtual_path directory entry)
8. At least one `[[sources]]` and one `[[entries]]` must exist
9. Source names must be unique

## Config Parsing & Validation

### ConfigManager

```python
class ConfigManager:
    """Parse, validate, and manage RemotePFS TOML config."""

    def __init__(self, config_path: str = "/etc/remotepfs/remotepfs.conf"):
        ...

    def load(self) -> Config:           # Parse + validate from disk
        """Load and validate config from disk. Raises ConfigError on failure."""
        ...

    def validate(self, raw: dict) -> Config:
        """Validate a config dict (from API). Raises ConfigError on failure."""
        ...

    def reload(self) -> Config:         # Re-read from disk
        """Reload config file. Raises ConfigError on failure (current config preserved)."""
        ...

class Config:
    """Validated config object."""
    image_size_gib: int
    cluster_size_kib: int
    label: str
    oem_name: str
    sources: list[SourceConfig]
    entries: list[EntryConfig]

class SourceConfig:
    name: str
    server: str
    export: str
    mount_point: str
    nfs_options: str

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
        self.field = field    # TOML path: "entries[0].source"
```

## Sector Mapper

The sector mapper translates NBD sector numbers to source file reads. It is built once at startup (or on config reload) from the validated config and the directory scan results.

### SectorMapper

```python
class SectorMapper:
    """Map virtual exFAT sector numbers to (source, offset) pairs."""

    def __init__(self, exfat_layout: ExfatLayout):
        ...

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
    source_fd: int | None = None       # Open file descriptor
    offset: int | None = None          # Byte offset within source file
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
    start_cluster: int       # First cluster in chain (exFAT cluster number)
    count_clusters: int      # Number of clusters
    source_path: str         # NFS path
    source_fd: int           # Open file descriptor (os.O_RDONLY)
    source_offset: int       # Byte offset within source file (0 for file entries)
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
            entries.append(VirtualEntry(
                virtual_path=f"{virtual_parent}/{entry.name}",
                source_path=entry.path,
                is_directory=True,
            ))
            entries.extend(scan_directory(entry.path, f"{virtual_parent}/{entry.name}"))
        elif entry.is_file():
            entries.append(VirtualEntry(
                virtual_path=f"{virtual_parent}/{entry.name}",
                source_path=entry.path,
                is_directory=False,
                file_size=entry.stat().st_size,
            ))
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

- TOML specification: https://toml.io/en/v1.0.0
- exFAT directory entry format: Microsoft exFAT Revision 1.00
- DD-02: Virtual exFAT Filesystem
- DD-03: Config-Driven Virtual Layout
- DD-04: Config Hot-Reload
- KB 01: ShadowMountPlus (game detection via param.sfo + image file extensions)
