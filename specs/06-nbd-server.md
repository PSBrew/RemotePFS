# Spec 06 - NBD Server (nbdkit Python Plugin)

## Overview

RemotePFS uses **nbdkit** to serve sectors from the virtual exFAT filesystem to the kernel's NBD driver. nbdkit handles all NBD protocol details — negotiation, structured replies, request limits, and concurrency. We implement only the data plane: a Python plugin that translates sector reads into NFS file reads via the sector mapper.

## Architecture

```
PS5 SCSI READ
    │
    v
g_mass_storage (file=/dev/nbd0, ro=1)
    │
    v
nbd0 block device (kernel)
    │
    v
nbdkit (--readonly --filter=blocksize --filter=cache)
    │   Unix socket at /run/remotepfs/nbd.sock
    v
Python plugin: remotepfs_nbd.py
    │   ├─ pread(h, buf, offset, flags) → fill buf
    │   ├─ extents(h, count, offset, flags) → extent list
    │   └─ block_size(h) → (512, 4096, 65536)
    v
nbdkit filters:
    │   ├─ blocksize: minblock=512, maxdata=64k
    │   └─ cache: cache-on-read=true, cache-max-size=1G
    v
SectorMapper → NFS files
```

## Why nbdkit

| Concern | Hand-rolled NBD | nbdkit |
|---------|----------------|--------|
| Protocol correctness | Full NBD spec to implement | Battle-tested by Red Hat |
| Structured replies | Must implement | Built-in |
| Concurrency | Must design thread model | nbdkit thread model |
| Request limits | Must enforce | Configurable (`--max-request`) |
| Memory safety | Python bytes handling | nbdkit allocates, plugin fills |
| Debugging | Custom diagnostics | `--verbose`, `--dump-plugin` |
| Zero-region fast path | Manual per-sector | `extents()` bulk reports zero regions |
| Block size enforcement | Must validate per-call | `blocksize` filter |
| Read cache | Must implement | `cache` filter, 1 GiB |
| Maintenance | Own code to maintain | Upstream maintained |

## nbdkit Python Plugin

```python
# remotepfs_nbd.py — nbdkit Python plugin for RemotePFS

import nbdkit
import os

API_VERSION = 2

# Set at startup via nbdkit config keys
image_size = 0
sector_mapper = None


def config(key: str, value: str) -> None:
    """Handle config keys from nbdkit command line."""
    ...


def config_complete() -> None:
    """Called after all config keys processed."""
    ...


# ---------- Size & block info ----------


def get_size(h) -> int:
    """Return virtual image size in bytes."""
    return image_size


def block_size(h) -> tuple[int, int, int]:
    """Return (minimum, preferred, maximum) I/O sizes.

    512    — exFAT logical sector size (matching PS5 512-byte LVD)
    4096   — Linux page size (efficient page cache alignment)
    65536  — 64 KiB max (matches NFS rsize, USB cluster reads)
    """
    return (512, 4096, 65536)


def is_rotational(h) -> bool:
    """Not rotational — virtual image served from RAM + NFS."""
    return False


# ---------- Capabilities ----------


def can_write(h) -> bool:
    return False


def can_trim(h) -> bool:
    return False


def can_zero(h) -> bool:
    return False


def can_flush(h) -> bool:
    return False


def can_fua(h) -> int:
    """No Forced Unit Access support — read-only."""
    return nbdkit.FUA_NONE


def can_multi_conn(h) -> bool:
    return False


def can_extents(h) -> bool:
    """Enable extents for zero-region bulk reporting."""
    return True


def can_cache(h) -> int:
    """nbdkit cache filter handles all caching; plugin delegates."""
    return nbdkit.CACHE_NONE


def can_fast_zero(h) -> bool:
    return False


# ---------- Thread model ----------


def thread_model() -> int:
    """Single-threaded: PS5 USB gadget is synchronous request-response."""
    return nbdkit.THREAD_MODEL_SERIALIZE_REQUESTS


# ---------- Data plane ----------


def pread(h, buf: memoryview, offset: int, flags: int) -> None:
    """Fill buf with len(buf) bytes from virtual image at byte offset.

    Called by nbdkit (possibly after blocksize filter splits/aligns).
    Data is written directly into buf (not returned).
    """
    nbytes = len(buf)
    end = offset + nbytes

    if end > image_size:
        raise nbdkit.Error("pread: beyond image size")

    # Fast path: contiguous metadata region (boot sectors, FAT)
    if sector_mapper.is_metadata_region(offset, nbytes):
        sector_mapper.read_metadata(offset, buf)
        return

    # General path: sector-by-sector mapping
    sector_start = offset // 512
    sector_end = (end + 511) // 512

    pos = 0
    for sector in range(sector_start, sector_end):
        mapping = sector_mapper.map(sector)
        chunk_start = max(offset + pos, sector * 512)
        chunk_end = min(end, (sector + 1) * 512)
        chunk_size = chunk_end - chunk_start

        if mapping.kind == "file":
            file_off = mapping.offset + (chunk_start - sector * 512)
            buf[pos : pos + chunk_size] = os.pread(mapping.source_fd, chunk_size, file_off)
        elif mapping.kind == "metadata":
            meta_off = chunk_start - sector * 512
            buf[pos : pos + chunk_size] = mapping.metadata[meta_off : meta_off + chunk_size]
        elif mapping.kind == "zero":
            buf[pos : pos + chunk_size] = b"\x00" * chunk_size

        pos += chunk_size


def extents(h, count: int, offset: int, flags: int) -> list:
    """Return list of (offset, length, type) extents for range [offset, offset+count).

    Eliminates per-sector round-trips for zero-mapped regions (unmapped space
    between games, slack space). The kernel NBD client skips I/O for large
    zero regions reported here.

    Returns:
        List of (offset_in_query, length, type) tuples.
        type: 0 = data, nbdkit.EXTENT_ZERO = zero (kernel synthesizes, no I/O)
    """
    extents = []
    sector_start = offset // 512
    sector_end = (offset + count + 511) // 512

    current_type = None
    current_start = None
    current_len = 0

    for sector in range(sector_start, sector_end):
        mapping = sector_mapper.map(sector)
        etype = nbdkit.EXTENT_ZERO if mapping.kind == "zero" else 0

        if etype == current_type:
            current_len += 512
        else:
            if current_type is not None:
                extents.append((current_start, current_len, current_type))
            current_type = etype
            current_start = sector * 512
            current_len = 512

    if current_type is not None:
        extents.append((current_start, current_len, current_type))

    return extents


def close(h) -> None:
    pass
```

### Error Handling

```python
# Underlying I/O error → nbdkit.Error (maps to NBD_EIO)
try:
    buf[pos : pos + chunk_size] = os.pread(fd, chunk_size, file_off)
except OSError as e:
    raise nbdkit.Error(repr(e))

# Beyond image size → handled by plugin check + nbdkit framework
# Invalid mapping → debug log + zero fill (graceful degradation)
```

## nbdkit Invocation

```bash
nbdkit \
    -U /run/remotepfs/nbd.sock \
    --pidfile /run/remotepfs/nbdkit.pid \
    --unix-mode=0600 \
    --exit-with-parent \
    --threads 1 \
    --max-request 98304 \
    --readonly \
    --filter=blocksize \
    --filter=cache \
    python remotepfs_nbd.py \
    image_size=2199023255552 \
    mapper_state=/run/remotepfs/mapper.state
```
| Flag | Value | Purpose |
|------|-------|---------|
| `-U` | `/run/remotepfs/nbd.sock` | Unix socket path |
| `--pidfile` | `/run/remotepfs/nbdkit.pid` | Write PID file for lifecycle management |
| `--unix-mode` | `0600` | Socket file permissions (service user-only access) |
| `--user` | — | Managed by systemd `User=`. Omit in invocation. |
| `--group` | — | Managed by systemd `Group=`. Omit in invocation. |
| `--exit-with-parent` | — | nbdkit exits when parent process terminates |
| `--threads` | 1 | Single-threaded (synchronous protocol) |
| `--max-request` | 98304 (96 KiB) | Cap request size |
| `--readonly` | — | Enforce read-only at protocol level |
| `--filter=blocksize` | (see below) | Block alignment, size enforcement |
| `--filter=cache` | (see below) | Read cache (temporary file in `$TMPDIR`) |

### blocksize Filter

```
minblock=512     — 512-byte sector minimum (exFAT sector size)
maxdata=65536    — 64 KiB max read (match NFS rsize, page-friendly)
maxlen=134217728 — 128 MiB max request (nbdkit default, not constraining)
```

This filter ensures all I/O requests from the kernel NBD client are aligned to 512-byte boundaries and no larger than 64 KiB — matching USB mass-storage and ShadowMountPlus expectations (512-byte LVD sectors, 64 KiB cluster reads).

### cache Filter

```
cache-on-read=true         — Cache sectors as they are read
cache-max-size=1073741824  — 1 GiB max cache (comfortable on 4 GiB SBC)
```

The cache filter sits between the kernel NBD client and our plugin. All reads hit the cache first; only cache misses reach `pread()`. Benefits:

- Re-reads of exFAT metadata (boot sector, FAT, root dir) are cache hits after first PS5 enumeration
- Frequently accessed game regions (level headers, asset tables) stay cached
- 1 GiB cache holds ~16 seconds of sustained 64 MB/s game streaming
- Cache stored in a temporary file (`$TMPDIR`) (separate from Linux page cache)

## Plugin Communication

The Python plugin accesses sector mapper state via a pickled state file set at nbdkit startup.

```python
# In remotepfsd (before spawning nbdkit):
import pickle

state = {
    "image_size": mapper.total_bytes,
    "region_table": mapper.region_table,
}
state_path = "/run/remotepfs/mapper.state"
with open(state_path, "wb") as f:
    pickle.dump(state, f)
```

The nbdkit plugin loads this in `config()`:

```python
# In remotepfs_nbd.py:
def config(key, value):
    if key == "mapper_state":
        with open(value, "rb") as f:
            state = pickle.load(f)
        global image_size, region_table
        image_size = state["image_size"]
        region_table = state["region_table"]
```

**Alternative (V2):** Unix socket IPC for live updates without nbdkit restart. Deferred.

## End-to-End Attach Flow

### Bring Up (cold start)

```bash
# 1. Compile config, build virtual exFAT layout, pickle mapper state
remotepfs compile /etc/remotepfs/remotepfs.yaml
# → gen_id=abc123, image_size=154000000000, file_count=47

# 2. Metadata preload (pread() warming before bind — V1 scope)
remotepfs-ctl preload abc123    # Example ~35–40 MiB (512 GiB image, 64 KiB clusters) into caches
# 3. Start nbdkit server
remotepfs-ctl serve abc123
# nbdkit forks to background, socket ready

# 4. Connect kernel NBD client (read-only)
nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0

# 5. Wait for device readiness (avoid race before gadget bind)
while [ "$(cat /sys/block/nbd0/size)" -le 0 ] 2>/dev/null; do sleep 0.1; done
# Verify: blockdev --getsize64 /dev/nbd0 > 0

# 6. Point USB gadget LUN at /dev/nbd0
echo /dev/nbd0 > /sys/kernel/config/usb_gadget/g1/functions/mass_storage.0/lun.0/file
echo 1 > /sys/kernel/config/usb_gadget/g1/functions/mass_storage.0/lun.0/ro

# 7. Bind UDC — PS5 sees USB mass storage device
echo <udc_name> > /sys/kernel/config/usb_gadget/g1/UDC
```

### Eject (PS5 rescan with new config)

```bash
# 1. Unbind gadget — PS5 sees disconnect, stops I/O
echo "" > /sys/kernel/config/usb_gadget/g1/UDC

# 2. Disconnect NBD client first (clean protocol close), then stop nbdkit
nbd-client -d /dev/nbd0 2>/dev/null
kill $(cat /run/remotepfs/nbdkit.pid)

# 3. Compile new config, activate (atomic generation swap)
remotepfs compile /etc/remotepfs/remotepfs.yaml
# → gen_id=def456
remotepfs-ctl activate def456
# Under the hood: pickle new state → spawn new nbdkit → connect nbd-client

# 4. Wait for nbd0 device readiness (sized and ready)
while [ "$(cat /sys/block/nbd0/size)" -le 0 ] 2>/dev/null; do sleep 0.1; done

# 5. Metadata preload for new generation
remotepfs-ctl preload def456

# 6. Rebind UDC — PS5 rescans, sees new filesystem
echo <udc_name> > /sys/kernel/config/usb_gadget/g1/UDC
```

### Tear Down (reverse order)

```bash
# 1. Unbind gadget
echo "" > /sys/kernel/config/usb_gadget/g1/UDC

# 2. Unlink LUN file
echo "" > /sys/kernel/config/usb_gadget/g1/functions/mass_storage.0/lun.0/file

# 3. Disconnect NBD client
nbd-client -d /dev/nbd0

# 4. Stop nbdkit
kill $(cat /run/remotepfs/nbdkit.pid)

# 5. Clean up
rm /run/remotepfs/nbd.sock /run/remotepfs/mapper.state
```

## Lifecycle

```
Startup:  ConfigManager.load() → build ExfatLayout → build SectorMapper
          → pickle state → spawn nbdkit (--readonly, filters)
          → plugin loads state → nbdkit binds socket
          → nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
          → echo /dev/nbd0 > LUN file, ro=1
          → echo <udc> > UDC → PS5 enumerates

Running:  PS5 reads → nbd0 → [cache filter] → [blocksize filter] → pread()
          → SectorMapper → NFS

Reload:   echo "" > UDC → kill nbdkit → build new Generation → pickle state
          → spawn nbdkit → nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0 → echo <udc> > UDC

Shutdown: echo "" > UDC → echo "" > LUN file → nbd-client -d /dev/nbd0
          → kill nbdkit → clean up sockets/state
```

## nbdkit Installation on SBC

```bash
# Debian/Ubuntu (Radxa Armbian)
sudo apt install nbdkit nbdkit-plugin-python3 nbd-client

# Verify
nbdkit --dump-plugin python
# Expected: python_version=3.11
nbdkit --version
# Expected: nbdkit 1.30+
```

### Dependency Check

| Package | Required | Purpose |
|---------|----------|---------|
| `nbdkit` | Yes | NBD server binary |
| `nbdkit-plugin-python3` | Yes | Python plugin support |
| `nbd-client` | Yes | Connect /dev/nbd0 to nbdkit socket |
| `python3` (>= 3.11) | Yes | Plugin runtime |

All packages available in Debian Bookworm/arm64 (Armbian base) and Ubuntu 24.04.

## Performance

| Metric | Value | Notes |
|--------|-------|-------|
| nbdkit overhead per request | ~2-10 us | C framework, no Python for cache hits |
| `pread()` overhead (cache miss) | ~10-30 us | Python dict lookups |
| `extents()` overhead | ~50 us | Typical for 1 MiB query range |
| cache filter hit latency | — | Depends on cache file backing store and filesystem. Measure on target. |
| NFS `pread()` latency | context-dependent | Dominant factor on cache miss |
| Total per-request (cache miss) | context-dependent | NFS dominates |
| Zero-region throughput (extents) | ~10 GB/s | Synthesized, no I/O |
| Max throughput (64 KiB req) | ~120 MB/s | GbE-limited |

## Monitoring

```python
# Metrics via HTTP API endpoint
nbd.connections = 0 | 1
nbd.requests_total = counter
nbd.bytes_served = counter
nbd.read_errors = counter
nbd.extent_queries_total = counter
nbd.cache_hit_rate = float  # from nbdkit cache filter stats
```

`remotepfsd` monitors nbdkit process health: restarts on crash (within config reload). Process exit codes: 0 = clean shutdown, non-zero = crash → log + restart if not shutting down.

## References

- nbdkit Python plugin docs: https://libguestfs.org/nbdkit-python-plugin.1.html
- nbdkit blocksize filter: https://libguestfs.org/nbdkit-blocksize-filter.1.html
- nbdkit cache filter: https://libguestfs.org/nbdkit-cache-filter.1.html
- nbdkit manual: https://libguestfs.org/nbdkit.1.html
- nbdkit source (examples): https://gitlab.com/nbdkit/nbdkit/-/tree/master/plugins/python
- DD-01: NBD as Block Device Backend
- KB 06: USB OTG / Gadget Mode (f_mass_storage buffer/chunking)
