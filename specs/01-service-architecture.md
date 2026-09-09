# 01 — Service Architecture

> **RemotePFS context:** SBC (4 GB RAM) presents NAS game files to PS5 as a USB
> mass storage device via NBD + nbdkit Python plugin with virtual exFAT.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                              PS5                                     │
│  SCSI READ/WRITE over USB 3.0 (USSX cable / Rear USB-C port)         │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ USB 3.0
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     g_mass_storage                                   │
│  file=/dev/nbd0, ro=1, stall=1, nofua=1, forced_eject=1,           │
│  idVendor=0x1d6b, idProduct=0x0104                                 │
│  (linux-usb-gadgets, dwc3 driver, USB Device (Gadget) controller)    │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ block I/O
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     /dev/nbd0                                        │
│  Kernel NBD client (nbd.ko). Forwards block I/O to nbdkit socket.   │
│  read_ahead_kb=4096, queue_depth=32.                                 │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ AF_UNIX socket
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     nbdkit (server)                                  │
│  nbdkit -U /run/remotepfs/nbd.sock --readonly                         │
│         --filter=blocksize --filter=cache                             │
│         python /usr/lib/remotepfs/remotepfs_nbd.py                    │
│         minblock=512 maxdata=65536 maxlen=4M                          │
│         cache-min-block-size=262144 cache-max-size=1G                 │
│                                                                      │
│  Filters (applied in order):                                         │
│    1. blocksize: Enforces `minblock=512`, `maxdata=65536`. Aligns/pads        │
│       small reads; fragments oversized requests into 64 KiB chunks.            │
│    2. cache: Temp-file cache (`$TMPDIR`), `cache-on-read=true`. Capacity       │
│       bounded by `cache-max-size`. LRU eviction (see nbdkit-cache-filter).     │
│                                                                      │
│  Plugin: Python 3.11+ plugin implementing:                            │
│    - pread(h, buf, offset, flags): Forward to SectorMapper.              │
│    - extents(h, count, offset, flags): Query sparse/hole map from    │
│      SectorMapper (optimizes PS5 reads, avoids reading zeros).       │
│    - get_size(h): Return total virtual exFAT size (computed from     │
│      config at compile time).                                         │
│    - cache  (bool): Enabled (nbdkit cache filter active).            │
│    - thread_model(): Return nbdkit.THREAD_MODEL_SERIALIZE_REQUESTS.        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ pread(h, buf, offset, flags)
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     SectorMapper                                      │
│  Virtual exFAT block device built from YAML config.                  │
│  Maps LBA ranges → (protocol source, file_offset) tuples.            │
│                                                                      │
│  Sectors:                                                            │
│    Region 0     [    0 –   511]: MBR + GPT (partition table stub)    │
│    Region 1     [  512 – 65535]: exFAT VBR + FAT + allocation bitmap │
│    Region 2     [65536 –  ...]: File data clusters                   │
│                                                                      │
│  Region 2 is sparse — only sectors backing config-defined files       │
│  are populated. Holes return zeroes (or NBDKIT_EXTENT_ZERO).         │
│                                                                      │
│  Metadata (Regions 0–1) is pre-built at config compile time and      │
│  stored in memory. ExFAT structures (VBR, FAT, bitmap, root dir,     │
│  upcase table) computed from config tree.                            │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ os.pread(source fd, file_offset)
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│              Protocol Source Access Layer                             │
│  Open file descriptors to mounted protocol sources.                  │
│  V1: Linux NFS client or CIFS client mounting SMB 3.1.1.             │
│  Read-only options and source-specific page cache remain active.     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Breakdown

### 2.1 Config System

Manages virtual filesystem layout. Single YAML config maps virtual paths
to protocol-backed source paths. V1 supports multiple NFS and SMB sources;
future providers can reuse the source contract.

```yaml
global:
  image_size_gib: 2047
  cluster_size_kib: 128
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: nas1
    protocol: cifs
    endpoint: //192.168.1.100/games
    mount_point: /mnt/nas1
    read_only: true
    options: ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576
    credentials_file: /etc/remotepfs/nas1.credentials
entries:
  - virtual_path: Elden Ring
    source: /mnt/nas1/elden-ring/
    type: directory
```

**Two-phase deployment model (V1):**

1. `POST /api/config/compile`: Validates config, builds virtual exFAT metadata
   (FAT, bitmap, directory tree), computes total size. Returns generation ID
   and validation report. No interruption to active UDC session.
2. `POST /api/config/activate`: Atomically swaps active generation. Restarts
   nbdkit with new plugin state. Rebinds UDC if currently bound.

`PUT /api/config` and `POST /api/config/reload` are convenience wrappers that
combine compile + activate into a single call (see spec 08). All endpoints
are listed in [spec 08 — HTTP API](08-http-api.md). Config schema and
validation rules are in [spec 07 — Config System](07-config-system.md).

### 2.2 Virtual exFAT Builder (SectorMapper)

Builds a virtual exFAT filesystem from compiled YAML config. No `.exfat`
file is ever created; filesystem exists as in-memory metadata plus
protocol source mappings.

**Responsibilities:**

- Compute exFAT structures: VBR, FAT (allocation table), allocation bitmap,
  root directory, upcase table.
- Assign cluster chains for each config-defined file/directory.
- Build a sector address map: `[LBA → (source fd, file_offset, byte_count)]`.
- Provide `pread()` entry point that resolves any LBA to metadata or source data.
- Provide `extents()` for hole/sparse mapping and `NBDKIT_EXTENT_ZERO`.

**PS5 exFAT compatibility:** The volume is presented as a superfloppy with the
exFAT VBR at LBA 0, 512-byte sectors, and 128 KiB clusters. The UPCASE table
is written uncompressed as 131,072 bytes. A PS5 mount failure may report
`UVFAT_copyupcasetable` when this table is missing, compressed, or malformed.

**Memory footprint:** ExFAT metadata is proportional to number of files.
Rough estimate: ~1 MB per 1000 game files. At 4 GB RAM, easily holds
metadata for thousands of files.

### 2.3 nbdkit Server Manager

Lifecycle management for the nbdkit process serving the NBD device.

**Responsibilities:**

- Start/stop nbdkit with correct filter chain and plugin path.
- Bind to AF_UNIX socket at `/run/remotepfs/nbd.sock`.
- Connect kernel NBD client (`nbd-client -u /run/remotepfs/nbd.sock -R -L /dev/nbd0` on Debian nbd 3.18).
- Handle graceful restart: stop old nbdkit, disconnect NBD client, start new
  nbdkit with updated plugin state, reconnect NBD client.
- Health checking: monitor nbdkit process, verify socket liveness.

**Filter chain (order matters):**

| Position | Filter      | Purpose                                           |
|----------|-------------|---------------------------------------------------|
| 1 (outer)| blocksize   | Enforce `minblock=512`; fragment reads >64 KiB (`maxdata=65536`). |
| 2        | cache       | Temp-file cache (`$TMPDIR`), `cache-on-read=true`, bounded by `cache-max-size`; LRU eviction. |
| 3 (inner)| python      | Plugin → SectorMapper → NFS.                       |

### 2.4 USB Gadget Manager

Manages the `g_mass_storage` USB gadget via ConfigFS.

**Responsibilities:**

- Create gadget directory structure in `/sys/kernel/config/usb_gadget/`.
- Configure USB descriptor strings (manufacturer, product, serial).
- Bind `g_mass_storage` function with `file=/dev/nbd0, ro=1, stall=1, nofua=1`.
- Bind UDC (USB Device Controller) to activate gadget.
- Unbind UDC gracefully before config changes.
- Handle PS5 eject request (detected via UDC event or explicit API call).

**UDC bind sequence (cold start):**

```
1. mount -t configfs none /sys/kernel/config
2. mkdir /sys/kernel/config/usb_gadget/remotepfs
3. echo 0x1d6b > .../idVendor   # Linux Foundation (default descriptor)
4. echo 0x0104 > .../idProduct  # Generic composite/mass storage gadget
5. echo "RemotePFS" > .../strings/0x409/manufacturer
6. echo "PS5 Extended Storage" > .../strings/0x409/product
7. mkdir .../functions/mass_storage.0
8. echo /dev/nbd0 > .../functions/mass_storage.0/lun.0/file
9. echo 1 > .../functions/mass_storage.0/lun.0/ro
10. ln -s .../functions/mass_storage.0 .../configs/c.1/
11. echo <udc_name> > .../UDC
```

**UDC rebind (config reload):**

```
1. echo "" > .../UDC                         # unbind
2. nbd-client -d /dev/nbd0                   # disconnect NBD
3. kill nbdkit; start new nbdkit             # new config
4. nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0  # reconnect NBD
5. echo <udc_name> > .../UDC                 # rebind
```

See [spec 03 — USB Gadget Configuration](03-usb-gadget-config.md) for full
detail.

### 2.5 HTTP API

REST API on `localhost:8080` for config management and status monitoring.

**Endpoints:**

| Method | Path                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| POST   | `/api/config/compile`   | Validate + compile YAML config → generation ID  |
| POST   | `/api/config/activate`  | Atomically swap active generation               |
| GET    | `/api/status`           | Current state (bound/unbound, game count, etc.) |
| POST   | `/api/eject`            | Gracefully unbind UDC (PS5 eject simulation)    |

See [spec 08 — HTTP API](08-http-api.md) for full detail.

### 2.6 systemd Service

Single systemd service (`remotepfs.service`) manages the entire lifecycle.

```ini
[Unit]
Description=RemotePFS — Virtual exFAT over NBD for PS5
After=network-online.target nfs-client.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/remotepfs serve
ExecStop=/usr/bin/remotepfs shutdown
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

The `remotepfs` CLI binary handles: config loading, nbdkit startup, NBD
client connection, UDC binding, HTTP API server, and graceful shutdown.

---

## 3. Data Flow

### 3.1 PS5 Game Read (hot path)

```
PS5 issues SCSI READ(16) for LBA X, length L
  │
  ▼
g_mass_storage translates SCSI READ → block I/O on /dev/nbd0 (lun.0/file)
  │
  ▼
nbd.ko sends NBD_CMD_READ(offset=X*512, length=L*512) over AF_UNIX socket
  │
  ▼
nbdkit receives NBD request:
  blocksize filter: align to 512-byte boundary; split if length > maxdata (65536)
  blocksize filter → cache filter → python plugin .pread(h, buf, offset, flags)
  │
  ▼
plugin.pread() → SectorMapper.resolve(offset)
  │
  ▼
SectorMapper looks up LBA range:
  ├─ Region 0 (MBR/GPT): return pre-built buffer ↔ 512 bytes
  ├─ Region 1 (exFAT metadata): return pre-built buffer ↔ ~32 MB
  ├─ Region 2 (file data): (source fd, file_offset) → os.pread(fd, count, file_offset)
  └─ Hole (unmapped): return zeroes + mark extent as NBDKIT_EXTENT_ZERO
  │
  ▼
os.pread() on NFS-mounted file descriptor:
  ├─ Linux page cache hit → return from kernel RAM (Tier 2)
  └─ Page cache miss → NFS RPC READ over TCP 2049 → NAS disk
  │
  ▼
Data flows back up: NFS → page cache → SectorMapper → plugin → cache filter
(populate on miss) → blocksize → nbdkit response → nbd.ko → g_mass_storage
→ USB → PS5.
```

### 3.2 Config Reload (game switch)

```
1. User edits `/etc/remotepfs/remotepfs.yaml` (add/remove sources or entries)
2. POST `/api/config/compile`
   ├─ Validate YAML syntax + field constraints
   ├─ Verify mounted protocol sources and source files
   ├─ Build virtual exFAT metadata (FAT, bitmap, directories)
   ├─ Compute total size, assign cluster chains
   ├─ Return generation ID (UUID) + validation report
   └─ Store compiled generation in /var/lib/remotepfs/generations/<id>/
3. POST /api/config/activate { generation_id: "<id>" }
   ├─ Unbind UDC (echo "" > .../UDC)
   ├─ Disconnect NBD client (nbd-client -d /dev/nbd0)
   ├─ Signal nbdkit to stop
   ├─ Start nbdkit with new plugin state (loads new SectorMapper)
   ├─ Reconnect NBD client (nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0)
   ├─ Bind UDC (echo <udc> > .../UDC)
   └─ Return success + active generation ID
```

### 3.3 Metadata Preloading (V1)

```
Before UDC bind (cold start or after activate):
  1. SectorMapper computes list of "hot" LBAs:
     - MBR (LBA 0)
     - GPT header + entries (LBA 1–33)
     - ExFAT VBR (LBA 2048)
     - FAT region (all clusters)
     - Allocation bitmap
     - Root directory entries
     - Upcase table
  2. Issue pread() calls for these LBAs through the full nbdkit chain.
     Cache filter intercepts and caches them.
  3. Subsequent PS5 reads for these sectors during initial partition
     scan hit nbdkit cache (Tier 1), avoiding NFS round-trips.
```

---

## 4. Kernel Modules

Required kernel modules (loaded at service start):

| Module          | Purpose                                           |
|-----------------|---------------------------------------------------|
| `nbd`           | NBD client. Creates `/dev/nbd0` block device.     |
| `nfs`           | NFS client. Mounts NAS exports.                   |
| `nfsv4`         | NFSv4 protocol support.                           |
| `dwc3`          | USB 3.0 DRD controller driver (Radxa A7S).        |
| `libcomposite`  | ConfigFS-based USB gadget framework.              |
| `usb_f_mass_storage` | g_mass_storage function driver.              |

Modules **not** required (removed from V0 design):

| Module    | Reason                                              |
|-----------|-----------------------------------------------------|
| `loop`    | No loopback mounts. NBD replaces `/dev/loop0`.      |
| `exfat`   | No `.exfat` file to mount. Virtual exFAT in memory. |

---

## 5. Dependencies

### 5.1 System Packages (Debian/Ubuntu)

| Package                   | Purpose                                   |
|---------------------------|-------------------------------------------|
| `nbdkit`                  | NBD server with plugin + filter support   |
| `nbdkit-plugin-dev`       | Python plugin development headers         |
| `nbdkit-plugin-python3`   | nbdkit Python 3 plugin                    |
| `nbd-client`              | Kernel NBD client connector               |
| `nfs-common`              | NFS client utilities (mount.nfs)          |
| `python3` (≥3.11)        | Application runtime                       |
| `python3-yaml` (≥6.0)       | YAML parsing                            |
| `python3-fastapi`           | HTTP API framework (FastAPI + Pydantic)    |
| `python3-uvicorn`           | ASGI server for FastAPI                    |

### 5.2 Python Dependencies

| Package    | Purpose                        |
|------------|--------------------------------|
| `nbdkit`   | Python bindings for nbdkit API |

---

## 6. Filesystem Layout

```
/etc/remotepfs/
  remotepfs.yaml             # Active config (symlink or direct file)

/var/lib/remotepfs/
  generations/
    <uuid>/                # Compiled generation
      remotepfs.yaml         # Normalized config copy
      sector_map.pickle     # Serialized SectorMapper state
      metadata.bin          # Pre-built exFAT metadata (Regions 0–1)
      validation.json       # Compile validation report
  active -> generations/<uuid>/   # Symlink to active generation

/run/remotepfs/
  nbd.sock                 # nbdkit AF_UNIX socket
  api.sock                  # HTTP API AF_UNIX socket (optional)

/usr/lib/remotepfs/
  remotepfs_nbd.py          # nbdkit Python plugin
  sector_mapper.py          # Virtual exFAT SectorMapper
  config_compiler.py        # YAML → exFAT metadata compiler
  source_layer.py            # Protocol source access abstraction
  gadget_manager.py         # USB gadget ConfigFS interface
  api_server.py             # HTTP API server

/usr/bin/
  remotepfs                 # CLI entry point

/etc/systemd/system/
  remotepfs.service         # systemd unit
```

---

## 7. Startup Sequence

```
1. systemd starts remotepfs.service
2. Load YAML config from `/etc/remotepfs/remotepfs.yaml`
3. Mount configured NFS or CIFS sources if not already mounted:
     mount -t cifs -o ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576,credentials=/etc/remotepfs/nas1.credentials //192.168.1.100/games /mnt/nas1
4. Compile config → SectorMapper (build exFAT metadata in memory)
5. Start nbdkit with cache + blocksize filters + python plugin
6. Connect NBD client:
     nbd-client -u /run/remotepfs/nbd.sock -R -L /dev/nbd0
7. Set NBD read-ahead:
     echo 4096 > /sys/block/nbd0/queue/read_ahead_kb
8. Metadata preloading: pread() all hot LBAs through nbdkit cache
9. Configure USB gadget via ConfigFS (idVendor, idProduct, strings)
10. Set LUN file: echo /dev/nbd0 > .../lun.0/file
11. Bind UDC: echo <udc_name> > .../UDC
12. Start HTTP API server on localhost:8080
13. PS5 detects USB extended storage, ShadowMountPlus scans exFAT
```

---

## 8. Shutdown Sequence

```
1. Graceful: POST /api/eject
   ├─ Unbind UDC
   ├─ Disconnect NBD client
   ├─ Stop nbdkit
   ├─ Unmount NFS exports
   └─ Stop HTTP API server

2. Hard: systemctl stop remotepfs
   ├─ SIGTERM → graceful shutdown
   └─ SIGKILL after 10s timeout
```

---

## 9. Future Extensions (Out of V1 Scope)

### 9.1 SectorMapper Persistence
Serialize compiled SectorMapper to disk for instant cold start (no recompile
on reboot). Currently recompiles each boot — fast enough (sub-second) but
could cache across restarts.

### 9.2 Wi-Fi Background Prefetch
Use a second NIC (Wi-Fi) for background read-ahead of game data. Separate
nbdkit plugin or filter that issues speculative preads over Wi-Fi to warm
the cache before PS5 requests.

### 9.3 FUSE Virtual exFAT
FUSE-based virtual exFAT was the original V0 design (before nbdkit). It is
**subsumed by V1**: the nbdkit Python plugin + SectorMapper replaces FUSE
entirely. NBD block device model is simpler, faster, and avoids FUSE's
userspace context switch overhead for every filesystem operation.

---

*Conforms to mkpfs conventions: Python 3.11+, Google docstrings, Ruff
line-length=119, Conventional Commits.*
