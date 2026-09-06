# 01 — Project Roadmap

> **RemotePFS V1:** SBC exposes NAS game files to PS5 as a virtual exFAT USB mass
> storage device via NBD + nbdkit Python plugin. Config-driven, no `.exfat` image
> files, no loopback mounts.

---

## 1. Architecture Reference

This roadmap implements the architecture defined in:

| Spec | Title                                      | Key Decisions                                    |
|------|--------------------------------------------|--------------------------------------------------|
| 01   | Service Architecture                       | NBD + virtual exFAT + config-driven layout       |
| 02   | Protocol Choice                            | NFS v4.1 for NAS, NBD over AF_UNIX for local     |
| 03   | USB Gadget Configuration                   | g_mass_storage with /dev/nbd0, ConfigFS setup    |
| 04   | Caching Layer                              | Two-tier: nbdkit cache + Linux page cache        |
| 05   | Security Model                             | Host OS hardening, socket permissions, read-only |
| 06   | NBD Server (nbdkit Python Plugin)          | Python plugin, filter chain, bring-up script     |
| 07   | Config System                              | TOML schema, validation, mount mapping           |
| 08   | HTTP API                                   | Two-phase compile/activate; PUT/reload wrappers; status/games/health/eject  |

Design decisions document at `plans/02-design-decisions.md`.

---

## 2. Phases

### Phase 1 — Prerequisites

**Goal:** SBC ready for development and deployment.

**Tasks:**

1. Install system packages:
   - `nbdkit`, `nbdkit-plugin-python3`, `nbd-client`
   - `nfs-common` (NFS client)
   - `python3` (≥3.11), `python3-tomli` (or stdlib `tomllib`)
   - `python3-fastapi` (HTTP API), `python3-uvicorn` (ASGI server), `python3-pydantic` (models)

2. Verify kernel modules available:
   - `nbd`, `nfs`, `nfsv4`, `dwc3` (or SBC-equivalent UDC driver)
   - `libcomposite`, `usb_f_mass_storage`

3. Mount NFS exports (or configure fstab):
   ```bash
   mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
       <nas_ip>:/exports/games /mnt/nfs/games
   ```

4. Create directory structure:
   - `/etc/remotepfs/` — config
   - `/var/lib/remotepfs/generations/` — compiled generations
   - `/run/remotepfs/` — runtime sockets
   - `/usr/lib/remotepfs/` — application code

5. Clone repository, set up Python virtual environment.

**Deliverable:** SBC with all dependencies installed, NFS mounts verified, repo ready.

**Estimated effort:** 2–3 hours.

---

### Phase 2 — Config System & Virtual exFAT Builder

**Goal:** Parse TOML config, validate, compile into virtual exFAT metadata structures.

**Spec reference:** [07 — Config System](../specs/07-config-system.md), [06 — NBD Server](../specs/06-nbd-server.md).

**Tasks:**

1. **TOML config schema** (`/etc/remotepfs/remotepfs.conf`):
   - `[global]`: image_size_gib, cluster_size_kib (locked 64), label (uppercase, 11 chars), oem_name
   - `[[sources]]`: name, server, export, mount_point, nfs_options
   - `[[entries]]`: virtual_path (flat, no `/`), source (under mount_point), type ("file"|"directory")

2. **Config compiler** (`config_compiler.py`):
   - Validate TOML syntax and field constraints
   - Verify NFS source directories exist and are accessible
   - Build directory tree from `[[entries]]`
   - Verify `param.sfo` exists in each game directory (probe, not required)
   - Assign file IDs, compute cluster chains

3. **Virtual exFAT builder** (`sector_mapper.py`):
   - Build exFAT structures in memory: VBR, FAT, allocation bitmap, root directory, upcase table
   - Compute total virtual device size from config
   - Build LBA → (NFS fd, file_offset) mapping for file data regions
   - Mark unpopulated regions as holes (NBDKIT_EXTENT_ZERO)

4. **Generation management:**
   - Assign UUID to each compilation
   - Store compiled generation to `/var/lib/remotepfs/generations/<uuid>/`
   - Serialize SectorMapper state for fast activation

5. **Atomic generation swap:**
   - Compile new generation without interrupting active one
   - Swap via `/var/lib/remotepfs/active` symlink

**Memory footprint:** ~1 MB per 1000 game files for exFAT metadata. At 4 GB RAM, easily holds metadata for thousands of files.

**Deliverable:** Config compiler that validates TOML and produces a ready-to-activate generation with serialized SectorMapper state.

**Estimated effort:** 8–12 hours.

---

### Phase 3 — NBD Server (nbdkit Plugin)

**Goal:** nbdkit Python plugin serves virtual exFAT as a local block device over AF_UNIX socket.

**Spec reference:** [06 — NBD Server](../specs/06-nbd-server.md), [02 — Protocol Choice (Decision B)](../specs/02-protocol-choice.md).

**Tasks:**

1. **Python plugin** (`remotepfs_nbd.py`):
   - `pread(h, buf, offset, flags)`: Delegate to SectorMapper
   - `extents(h, count, offset, flags)`: Query SectorMapper for hole/zero regions
   - `get_size(h)`: Return total virtual exFAT size
   - `thread_model()`: Return `nbdkit.THREAD_MODEL_SERIALIZE_REQUESTS`
   - `cache` (bool): Enabled
   - Load active generation on startup

2. **Filter chain** (order matters):
   - `--filter=blocksize`: Sector-align and cap request size, `minblock=512 maxdata=65536 maxlen=4M`
   - `--filter=cache`: Temp-file cache (`$TMPDIR`), `cache-on-read=true`, bounded by `cache-max-size`; LRU

3. **Bring-up script:**
   ```bash
   modprobe nbd
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
       python /usr/lib/remotepfs/remotepfs_nbd.py \
       image_size=<computed> \
       mapper_state=<path> &
   nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
   ```

4. **Lifecycle management:**
   - Start nbdkit with correct filter chain
   - Connect kernel NBD client
   - Graceful restart: stop old nbdkit, disconnect NBD, start new, reconnect
   - Health check: process liveness, socket existence, device size > 0

5. **systemd service** (`remotepfs.service`):
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

**Deliverable:** Working `/dev/nbd0` block device backed by nbdkit + Python plugin, systemd service for lifecycle management.

**Estimated effort:** 6–10 hours.

---

### Phase 4 — HTTP API

**Goal:** REST API on localhost for config management, status monitoring, and device eject.

**Spec reference:** [08 — HTTP API](../specs/08-http-api.md).

**Tasks:**

1. **Endpoints:**

   | Method | Path                    | Description                                    |
   |--------|-------------------------|------------------------------------------------|
   | POST   | `/api/config/compile`   | Validate + compile TOML → generation ID         |
   | POST   | `/api/config/activate`  | Atomic swap to new generation                   |
   | GET    | `/api/status`           | Active generation, device size, bind state, game count |
   | POST   | `/api/eject`            | Gracefully unbind UDC (PS5 sees device removal) |

2. **`POST /api/config/compile`:**
   - Accept TOML config in request body
   - Validate + compile (Phase 2)
   - Return `{ generation_id, entry_count, size_bytes, warnings[], errors[] }`

3. **`POST /api/config/activate`:**
   - Accept `{ generation_id }`
   - Atomically swap active generation:
     1. Unbind UDC (if bound)
     2. Disconnect NBD client
     3. Stop nbdkit
     4. Start nbdkit with new generation
     5. Reconnect NBD client
     6. Rebind UDC
   - Return `{ active_generation_id, status }`

4. **`GET /api/status`:**
   - Return: `{ active_generation_id, device_size, bound, udc, game_count, uptime }`

5. **`POST /api/eject`:**
   - Unbind UDC gracefully (via `forced_eject`)
   - Disconnect NBD
   - Stop nbdkit (optional: keep running for re-bind)

6. **Server:** FastAPI + Pydantic + uvicorn on `localhost:8080` (Unix socket optional: `/run/remotepfs/api.sock`).

**Deliverable:** HTTP API server, integrated with `remotepfs` CLI, config hot-reload via two-phase compile+activate.

**Estimated effort:** 6–8 hours.

---

### Phase 5 — USB Gadget Bind

**Goal:** Bind `/dev/nbd0` to `g_mass_storage` USB gadget, PS5 detects as extended storage.

**Spec reference:** [03 — USB Gadget Configuration](../specs/03-usb-gadget-config.md).

**Tasks:**

1. **ConfigFS setup:**
   - Create gadget directory structure in `/sys/kernel/config/usb_gadget/remotepfs/`
   - Set USB descriptors: idVendor, idProduct, bcdDevice, bcdUSB, strings

2. **LUN configuration:**
   - `file=/dev/nbd0`, `ro=1`, `nofua=1`, `stall=1`, `forced_eject=1`

3. **UDC detection and bind:**
   - Auto-detect UDC: `ls /sys/class/udc/ | head -1`
   - Bind: `echo <udc> > /sys/kernel/config/usb_gadget/remotepfs/UDC`
   - Verify: `cat /sys/kernel/config/usb_gadget/remotepfs/UDC` is non-empty

4. **Gadget manager** (`gadget_manager.py`):
   - `bind()`: Full setup sequence
   - `unbind()`: Graceful teardown (echo "" > UDC, remove ConfigFS entries)
   - `status()`: Check bind state, UDC name
   - Idempotent: skip steps already done

5. **End-to-end attach flow:**
   ```
   nbdkit start → nbd-client connect → ConfigFS create → LUN param set → UDC bind
   ```
   Verify at each step before proceeding.

6. **Teardown sequence** (reverse order):
   ```
   UDC unbind → ConfigFS remove → nbd-client disconnect → nbdkit stop
   ```

**Deliverable:** PS5 detects RemotePFS as USB extended storage device with virtual exFAT filesystem.

**Estimated effort:** 4–6 hours.

---

### Phase 6 — Metadata Preloading

**Goal:** Warm nbdkit cache with critical exFAT metadata before UDC bind to eliminate NFS round-trips during PS5's initial partition scan.

**Spec reference:** [04 — Caching Layer](../specs/04-caching-layer.md) (Metadata Preloading §4).

**Tasks:**

1. **Identify hot LBAs:**
   - MBR (LBA 0)
   - GPT header + entries (LBA 1–33)
   - ExFAT VBR (LBA 2048)
   - FAT region (all cluster entries)
   - Allocation bitmap
   - Root directory entries
   - Upcase table

2. **Preloading strategy:**
   - After nbdkit start, before UDC bind
   - Issue `pread()` calls for all hot LBAs through full nbdkit chain
   - Cache filter intercepts and caches each read
   - Sequential pread to avoid thrashing nbdkit's LRU

3. **Verification:**
   - After preloading, verify nbdkit cache hit rate for metadata LBAs
   - Measure PS5 initial scan time with vs. without preloading

4. **Integration:**
   - Automatic in `remotepfs serve` startup sequence
   - Skippable via `--no-preload` flag for development

**Deliverable:** PS5 initial partition scan completes entirely from nbdkit cache, zero NFS round-trips for metadata.

**Estimated effort:** 3–5 hours.

---

### Phase 7 — End-to-End Testing

**Goal:** Verify full stack works end to end with real PS5 hardware.

**Spec reference:** Cross-cutting all specs.

**Tasks:**

1. **SBC-side verification:**
   - nbdkit starts without errors, `/dev/nbd0` reports correct size
   - ConfigFS gadget binds successfully
   - HTTP API responds on all endpoints
   - Config hot-reload works: compile new config, activate, verify new generation active
   - Metadata preloading completes within timeout

2. **PS5 recognition:**
   - PS5 detects USB extended storage on boot and on hot-plug
   - ShadowMountPlus scans and detects game directories (param.sfo present)
   - Games appear in PS5 home screen

3. **Game mount:**
   - Launch a game from the virtual exFAT
   - Verify game loads and plays normally
   - Monitor NBD I/O stats for expected throughput

4. **Virtual filesystem verification:**
   - All config-defined games visible in ShadowMountPlus
   - Directory structure matches TOML config
   - No stale/missing entries

5. **Error handling:**
   - NAS disconnect: verify NFS hard mount blocks gracefully (no I/O errors)
   - nbdkit restart: verify PS5 recovers after re-bind
   - Invalid config: verify compile rejects with clear error

6. **Performance baseline:**
   - Sequential read throughput over NBD (target: ≥100 MB/s)
   - PS5 game load time vs. USB SSD baseline
   - nbdkit cache hit rate during game play

**Deliverable:** Passing end-to-end test report with PS5 recognition, game mount, and performance baseline.

**Estimated effort:** 8–12 hours (includes PS5 testing time).

---

### Phase 8 — Hardening & Documentation

**Goal:** Production-ready error handling, logging, and documentation.

**Spec reference:** All specs.

**Tasks:**

1. **Error handling hardening:**
   - Graceful degradation on partial NFS mount failure
   - nbdkit crash recovery (systemd `Restart=always`)
   - Config validation with actionable error messages
   - UDC bind failure diagnosis (missing module, wrong UDC name)
   - NBD device timeout and reconnect

2. **Logging:**
   - Structured logging (JSON to journald)
   - Log levels: DEBUG (development), INFO (normal ops), WARN (recoverable), ERROR (fatal)
   - Key events: nbdkit start/stop, UDC bind/unbind, config compile/activate, generation swap

3. **CLI polish:**
   - `remotepfs serve`: Full startup with progress output
   - `remotepfs shutdown`: Graceful teardown
   - `remotepfs status`: Human-readable status
   - `remotepfs compile <remotepfs.conf>`: Offline config validation

4. **README and user docs:**
   - Hardware prerequisites (SBC model, USB cable, power)
   - Installation steps
   - TOML config reference with examples
   - Troubleshooting guide (common errors + solutions)
   - NAS setup guide (NFS export configuration)

5. **Security review:**
   - HTTP API bound to localhost only (no external access)
   - nbdkit socket permissions (root-only)
   - NFS exports read-only
   - Config file permissions

**Deliverable:** Production-ready service with comprehensive error handling, logging, and user documentation.

**Estimated effort:** 6–8 hours.

---

## 3. Milestone Table

| Phase | Milestone                        | Dependencies | Effort   | Deliverable                                      |
|-------|----------------------------------|-------------|----------|--------------------------------------------------|
| 1     | Prerequisites                    | None        | 2–3 hr   | SBC ready, deps installed, NFS mounts verified   |
| 2     | Config System & Virtual exFAT    | Phase 1     | 8–12 hr  | TOML → exFAT metadata compiler, generation mgmt  |
| 3     | NBD Server (nbdkit)              | Phase 2     | 6–10 hr  | /dev/nbd0 served by nbdkit + Python plugin       |
| 4     | HTTP API                         | Phase 2     | 6–8 hr   | Config hot-reload API, status endpoint            |
| 5     | USB Gadget Bind                  | Phase 3     | 4–6 hr   | PS5 detects virtual exFAT as USB storage         |
| 6     | Metadata Preloading              | Phase 3     | 3–5 hr   | Zero NFS round-trips for PS5 initial scan        |
| 7     | End-to-End Testing               | Phase 5, 6  | 8–12 hr  | PS5 recognition, game mount, perf baseline       |
| 8     | Hardening & Documentation        | Phase 7     | 6–8 hr   | Production-ready service, user docs              |

**Total estimated effort:** 43–64 hours.

---

## 4. Risk Register

| Risk                                    | Likelihood | Impact | Mitigation                                                       |
|-----------------------------------------|-----------|--------|------------------------------------------------------------------|
| `nbd` module missing from BSP kernel    | Medium    | High   | Verify before SBC selection. Fallback: custom kernel build or module backport. |
| Config validation failures on edge cases| Medium    | Medium | Comprehensive test suite for config compiler. Fuzz TOML inputs.  |
| nbdkit Python plugin performance        | Low       | Medium | Python overhead minimal (I/O bound). Profile if needed; rewrite hotspot in C plugin if critical. |
| `dwc3` / UDC driver missing or wrong mode | Low    | High   | Verify SBC supports USB gadget mode before selection. Test with simple gadget first. |
| PS5 SCSI command incompatibility        | Medium    | Medium | Test with `stall=1`. Unknown commands cause stall, not crash. Monitor dmesg during testing. |
| NFS mount hangs under load              | Low       | Medium | `hard` mount is correct behavior (blocks, doesn't error). Tune `timeo` and `retrans` if stalls are too long. |
| ConfigFS race conditions                | Low       | Low    | Serialize all gadget operations through gadget manager. Idempotent setup. |
| nbdkit cache thrashing under random I/O | Low       | Low    | 1 GiB cache is generous. Random I/O unlikely for PS5 game reads (mostly sequential). |
| PS5 ShadowMountPlus scan misses games   | Medium    | High   | Verify param.sfo present in every game directory. Test with known-good game dump structure. |

---

## 5. Deliverables

| Deliverable                        | Phase | Format                                                |
|------------------------------------|-------|-------------------------------------------------------|
| Config compiler + SectorMapper     | 2     | Python modules (`config_compiler.py`, `sector_mapper.py`) |
| nbdkit Python plugin               | 3     | Python (`remotepfs_nbd.py`)                           |
| nbdkit bring-up script + systemd   | 3     | Bash + systemd unit (`remotepfs.service`)             |
| HTTP API server                    | 4     | Python (`api_server.py`)                              |
| USB gadget manager                 | 5     | Python (`gadget_manager.py`)                          |
| Metadata preloader                 | 6     | Python (integrated in startup sequence)               |
| CLI entry point                    | 8     | Python (`/usr/bin/remotepfs`)                         |
| End-to-end test report             | 7     | Markdown in `reports/e2e-test-YYYY-MM-DD.md`          |
| User documentation                 | 8     | README.md, TOML config reference                      |
| Performance baseline report        | 7     | Markdown in `reports/perf-baseline.md`                |

---

## 6. End-to-End Attach Flow

Full startup sequence from cold boot to PS5 recognition (ref: spec 06):

```
1.  modprobe nbd g_mass_storage
2.  Mount NFS exports (fstab or manual)
3.  Load config from /etc/remotepfs/remotepfs.conf
4.  Compile config → SectorMapper (build exFAT metadata in memory)
5.  Start nbdkit:
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
          python /usr/lib/remotepfs/remotepfs_nbd.py \
          image_size=<computed> \
          mapper_state=<path>
6.  Connect NBD client:
      nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
7.  Tune block device:
      echo 4096 > /sys/block/nbd0/queue/read_ahead_kb
      echo 256  > /sys/block/nbd0/queue/nr_requests
8.  Metadata preloading: pread() all hot LBAs through nbdkit cache
9.  Configure USB gadget via ConfigFS:
      idVendor, idProduct, strings, LUN params (file=/dev/nbd0, ro=1)
10. Bind UDC → PS5 detects USB extended storage
11. Start HTTP API on localhost:8080
```

Config reload (hot-swap):

```
1. POST /api/config/compile (new TOML) → generation ID
2. POST /api/config/activate { generation_id }
   a. Unbind UDC
   b. Disconnect NBD
   c. Stop nbdkit
   d. Start nbdkit with new generation
   e. Reconnect NBD
   f. Metadata preloading
   g. Rebind UDC → PS5 sees media change
```

---

## 7. Future Features (Deferred)

These features are explicitly out of V1 scope. They are recorded here for visibility
but have no implementation timeline.

| Feature                        | Rationale for Deferral                                        |
|--------------------------------|---------------------------------------------------------------|
| Alternative NAS transports (SMB, iSCSI) | NFS v4.1 is the correct V1 choice. SMB adds crypto overhead. iSCSI is incompatible with virtual exFAT model. |
| Multi-network load balancing   | Single bonded 2×1 GbE is sufficient for V1. Multi-connection NFS (`nconnect`) already provides NIC-level parallelism. |
| ublk optimization              | Requires Linux 6.0+. Most SBC distributions ship older kernels. Performance difference negligible over AF_UNIX. |
| Hot-swap multiple games        | POST /api/eject + /api/activate provides full config reload. Individual game add/remove without full reload is future work. |
| FUSE-based virtual exFAT       | Subsumed by NBD + nbdkit architecture. NBD is simpler, faster, avoids FUSE context switches. |
| Prometheus metrics             | Planned but not blocking V1. Basic health checks via HTTP API `/api/status`. |
| udev-based auto-bind           | systemd service handles startup. udev rule is optional optimization. |
| Wi-Fi background prefetch      | Second NIC for speculative read-ahead. Requires separate nbdkit plugin/filter. |

---

*Conforms to RemotePFS conventions: Python 3.11+, Google docstrings, Ruff line-length=119, Conventional Commits.*
