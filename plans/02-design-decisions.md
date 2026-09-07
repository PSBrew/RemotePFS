# RemotePFS Design Decisions

Document finalized 2026-09-05. Records architectural decisions made during the design phase. Each decision includes context, reasoning, and consequences.

> Migrated from PSBrew/RemotePFS-Research (`plans/02-design-decisions.md`) on 2026-09-05 and
> updated to match the canonical specs (`specs/01-08`). Where a decision evolved during the
> NBD + virtual exFAT pivot, the current decision is stated and the change is noted. The specs
> remain authoritative for detail; this record explains why.

## DD-01: NBD as Block Device Backend

**Decision:** Use NBD (Network Block Device) over Unix socket as the block device backend for `g_mass_storage`, served by nbdkit with a Python plugin. Rejected FUSE + losetup and ublk for V1.

*Update (2026-09-05): the NBD server is nbdkit (external process, Python plugin), not a hand-rolled NBD server inside `remotepfsd`. See spec 06.*

**Context:** `g_mass_storage` (`f_mass_storage`) needs a regular file or block device for its LUN backing store. The virtual exFAT filesystem must be backed by NFS-mounted game files — we cannot pre-copy multi-gigabyte games to local storage.

**Options considered:**

| Option | Viable | Reason |
|--------|--------|--------|
| Regular file on NFS | No | `g_mass_storage` reads the file directly; can't intercept reads to translate sectors to NFS file offsets |
| FUSE filesystem + losetup | No | Loop driver may use `splice_read`/`sendpage` paths that FUSE doesn't implement reliably. Fragile in kernel context. |
| NBD (Unix socket) | Yes | Purpose-built: serves sector reads from userspace via a simple protocol. Mature, well-documented. |
| ublk (io_uring) | Deferred to V2 | Faster but requires kernel 6.0+ and `CONFIG_BLK_DEV_UBLK`. Verify Radxa BSP 6.6 config before adopting. |
| Custom kernel module | No | Maximum complexity for V1. Unnecessary when NBD exists. |
| Hand-rolled NBD userspace server | No (superseded by nbdkit) | Protocol correctness, structured replies, and request-limit handling already solved by nbdkit; we implement only `pread()` and `extents()`. |

**Consequences:**
- nbdkit runs as a separate process, serving sectors over the Unix socket `/run/remotepfs/nbd.sock` to `/dev/nbd0`
- `g_mass_storage` LUN file = `/dev/nbd0`
- Protocol overhead: ~10-50 us per read request (Unix socket round-trip). Negligible vs NFS latency (~1-10 ms).
- Requires `CONFIG_BLK_DEV_NBD` in kernel config (likely present in Radxa BSP 6.6)
- Read-only enforced at three layers: nbdkit `--readonly`, `nbd-client -r`, and `g_mass_storage` `ro=1` (triple enforcement, spec 05)

## DD-02: Virtual exFAT Filesystem (Not Pre-Made Images)

**Decision:** Build a virtual exFAT filesystem in memory at startup, mapping virtual paths to NFS source files. Rejected pre-made `.exfat` image files on NAS.

**Context:** User wanted to expose NAS folder contents directly, not require games to be pre-packaged as `.exfat` container files. ShadowMountPlus scans the exFAT filesystem and auto-detects game folders (directories with `param.sfo`) and image files (`.exfat`, `.ffpfs`, `.ffpkg`, `.ffpfsc`).

**Options considered:**

| Option | Viable | Reason |
|--------|--------|--------|
| Pre-made `.exfat` images on NAS | Rejected | Requires pre-packaging games, single-image-at-a-time switching, unbind/rebind cycle |
| Virtual exFAT (in-memory build) | Yes | All games visible simultaneously, no pre-packaging, config-driven layout |
| FUSE filesystem mounted directly | No | `g_mass_storage` needs block device, not mounted filesystem |

**Consequences:**
- exFAT metadata generated in memory at startup: boot sector (12 sectors), FAT (cluster chains), directory entries
- Read-only exFAT — no write path, no allocation bitmap updates, no FAT modifications
- exFAT spec followed: 64 KiB clusters (per ShadowMountPlus), 512-byte sectors (LVD), standard directory entry format
- Directory recursion: `type=directory` config entries trigger recursive scan of NFS source, building subdirectory exFAT entries
- Large directories possible — many games means large root directory, FAT grows proportionally

## DD-03: Protocol-Backed Config-Driven Virtual Layout (YAML)

**Decision:** Use YAML config file (`/etc/remotepfs/remotepfs.yaml`) defining
generic protocol-backed sources and virtual path mappings. V1 supports NFS and
SMB 3.1.1 through Linux's CIFS client. Future HTTP(S), FTP, and torrent/P2P
providers can reuse the source contract.

**Context:** User wanted customizable virtual filesystem layout: map virtual
paths to remote folders, support files and directories, and use multiple
servers. Config must support hot reload without service restart.

```yaml
global:
  image_size_gib: 2048
  cluster_size_kib: 64
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
  - virtual_path: fps games
    source: /mnt/nas1/fpsgames/
    type: directory
```

**Alternatives considered:**

- JSON: verbose for hand-edited nested layouts.
- INI: weak support for repeated source and entry records.
- TOML: good syntax, but YAML is user preference and supports future provider-specific fields.

**Consequences:**

- YAML parser dependency (`PyYAML`) is required by service.
- Config never contains credential values; CIFS references external mode-0600 files.
- `protocol`, `endpoint`, `mount_point`, `read_only`, `options`, and optional
  `credentials_file` form generic source contract.
- Provider-specific validation and mounting remain isolated in provider managers.

## DD-04: Config Hot-Reload Contract (Two-Phase API)

**Decision:** Canonical V1 contract uses two endpoints: `POST /api/config/compile`
then `POST /api/config/activate`. This preserves a side-effect-free validation
boundary before activation. `PUT /api/config` and `POST /api/config/reload` are
convenience wrappers that perform the same compile/activate sequence internally.
See spec 08 for full API.

**Reload sequence (spec 08):**
1. Parse + validate new config → `Config` object
2. Build `Generation(gen=N+1, config=config)` — fully inert. Mount new NFS sources (retain old mounts during build), scan directories, assign stable file IDs (SHA256 of `virtual_path` truncated to 32 bits), build `SectorMapper`, precompute metadata buffer
3. Unbind UDC → PS5 sees disconnect
4. Unmount old NFS sources not in new config
5. Pickle new generation state to `/run/remotepfs/mapper.state`
6. Restart nbdkit with new state
7. Reconnect nbd-client (`nbd-client -U ... -r`) to new nbdkit socket, poll `/sys/block/nbd0/size > 0`
8. Atomically swap `_active_gen = new_gen`
9. Rebind UDC → PS5 sees new device
10. Cleanup: close old gen's orphaned fds, free old gen memory

**Consequences:**
- Single API call replaces two-phase compile + activate; simpler client integration
- Unbind/rebind UDC still necessary — can't swap exFAT metadata while PS5 is mid-read
- PS5 disruption is ~5-15 seconds (unbind + rebuild + rebind + ShadowMountPlus stability wait)
- Not a "live" swap — this is a clean disconnect/reconnect, not transparent
- Validation failures never touch the serving generation; current gen keeps serving
- Concurrent reload attempts guarded by `asyncio.Lock`; second attempt returns 423 `RELOAD_IN_PROGRESS`
- The active generation is immutable; in-flight reads finish against the old generation

## DD-05: Single Active USB Device, Multiple Games

**Decision:** V1 exposes a single USB gadget LUN with a single virtual exFAT filesystem containing all configured games. All games visible simultaneously. No LUN switching.

**Context:** Original plan was one `.exfat` image per LUN, switching via symlink + UDC rebind. With virtual exFAT, all games appear in the same filesystem. ShadowMountPlus scans and detects all of them.

**Consequences:**
- Simpler: no game switching API needed
- PS5 sees all games at once — ShadowMountPlus handles discovery
- Config changes (add/remove games) still require the two-phase deploy + UDC unbind/rebind cycle (DD-04)
- Single USB device, single LUN: `g_mass_storage` configuration is straightforward

## DD-06: Metadata Preloading in V1

**Decision:** Include `pread()`-based metadata warming in V1 scope. Warm the nbdkit cache (L1) and the NFS page cache (L2) with exFAT boot sectors, FAT, and root directory entries before UDC bind, using nbdsh/nbdcopy against the Unix socket with target ranges from `SectorMapper.get_hot_ranges()` (example ~35–40 MiB for 512 GiB image, 64 KiB clusters).


**Rationale:** The PS5 mount sequence reads boot sector, FAT, and root directory immediately after USB enumeration. Without preloading, those first reads are NFS round-trips (~1-10 ms each) during partition scan. With warming, the PS5 initial scan is entirely cache hits (~50 ms).

**Implementation:** After nbdkit start and nbd-client connect, before UDC bind: issue sequential `pread()` calls for hot LBAs (MBR, GPT, exFAT VBR, FAT, allocation bitmap, root directory, upcase table) through the full nbdkit chain. Skippable via `--no-preload` for development (spec 08/roadmap phase 6).

## DD-07: HTTP API on localhost

**Decision:** The `remotepfsd` service exposes an HTTP API on `127.0.0.1` for config management and status monitoring. No game switching endpoint (all games visible simultaneously per DD-05).

*Update (2026-09-05): framework is FastAPI + Pydantic v2 + uvicorn (ASGI); superseded the earlier stdlib `http.server`/aiohttp sketch. See spec 08.*

**Endpoints:**
- `PUT /api/config` — validate + apply config in one step (hot-reload, returns 200 or 422)
- `POST /api/config/reload` — reload config from disk (equivalent to PUT with file contents)
- `GET /api/config` — current active config as JSON
- `GET /api/status` — service state, NFS mounts, NBD status, UDC status, game count
- `GET /api/health` — liveness check
- `GET /api/games` — virtual filesystem entries visible to PS5
- `POST /api/eject` — graceful UDC unbind

**Consequences:**
- FastAPI + Pydantic v2 + uvicorn (ASGI); async handlers, CPU-bound generation builds via `run_in_threadpool`
- Localhost-only binding (`127.0.0.1:8080`, no external exposure)
- No authentication (trusted local system)
- Concurrent reload attempts return 423 `RELOAD_IN_PROGRESS` (DD-04)

## DD-08: Python 3.11+ with uv, Following mkpfs Conventions

**Decision:** Implementation language is Python 3.11+. Package management via uv. Linting via Ruff (line-length=119). Testing via pytest. Documentation via Google docstrings. Conventional Commits.

**Rationale:** Matches mkpfs conventions. Python is suitable for the service layer (config compiler, HTTP API, NFS mount management, nbdkit plugin). Performance-critical path (NBD sector serving) is simple enough that Python's speed is adequate — the bottleneck is NFS latency, not Python overhead. nbdkit's C framework handles cache-hit requests without Python.

**Consequences:**
- `pyproject.toml` with uv configuration
- Ruff with `line-length = 119` (matching mkpfs)
- pytest for testing, including integration tests with NBD
- Google-style docstrings
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`)

## DD-09: Implementation Repository Structure

**Decision:** Private repo `PSBrew/RemotePFS` with code, specs, and plans. Research repo `PSBrew/RemotePFS-Research` keeps KB articles and reports as frozen research artifacts.

*Update (2026-09-05): specs and plans now live ONLY in the implementation repo; the research repo retains KB articles, reports, and research state.*

**Repository layout:**

```
PSBrew/RemotePFS (private)
├── README.md
├── docs/                     # User docs (roadmap phase 8)
├── specs/                    # 01-08, canonical
├── plans/
│   ├── 01-project-roadmap.md
│   └── 02-design-decisions.md
├── src/remotepfs/
│   ├── config.py              — YAML validation and config model
│   ├── exfat_builder.py       — exFAT metadata generation
│   ├── sector_mapper.py       — sector offset to source file mapping
│   ├── remotepfs_nbd.py       — nbdkit Python plugin (API v2)
│   ├── nbdkit_manager.py      — dedicated nbdkit unit lifecycle
│   ├── nfs_manager.py         — NFS mount/unmount management
│   ├── gadget_manager.py      — USB gadget ConfigFS management
│   ├── preloader.py           — metadata warming via NBD
│   ├── api.py                 — FastAPI application
│   └── service.py             — privileged orchestration loop
├── config/
│   ├── remotepfs.yaml.example
│   ├── remotepfs.service
│   └── remotepfs-nbdkit.service
├── tests/
├── pyproject.toml
└── .gitignore
```

**Consequences:**
- Implementation repo is the single home for specs and plans (research repo copies removed 2026-09-05)
- Research repo KB articles + reports stay frozen
- Implementation repo is the active development repo; no code exists yet (layout is planned, per roadmap)
## DD-09a: Privileged orchestration and unprivileged nbdkit split

The initial single-unit model could not mount NFS, attach `/dev/nbd0`, and
write USB ConfigFS while also running nbdkit as `remotepfs-nbd`. v0.0.1 keeps
those responsibilities separate: `remotepfs.service` runs orchestration as
root, while `remotepfs-nbdkit.service` owns the socket and runs nbdkit as
`remotepfs-nbd:remotepfs`. The orchestrator hands off
`/var/lib/remotepfs/mapper.state` as `root:remotepfs`, mode `0640`.


## DD-10: V1 Scope Summary

| Feature | V1 | V2+ |
|---------|-----|-----|
| Multi-source NFS mounts | Config-driven | — |
| NBD server | nbdkit + Python plugin, Unix socket, ro | ublk optimization |
| Virtual exFAT builder | In-memory, ro, single LUN | — |
| Config-driven virtual layout | YAML, file + directory | — |
| Config hot-reload | Two-phase compile/activate | — |
| Metadata preloading | pread() warming via NBD (example ~35–40 MiB for 512 GiB/64 KiB) before UDC bind | — |
| USB gadget (BOT) | Single LUN, ro | UAS (f_tcm) |
| HTTP API | FastAPI + Pydantic v2 + uvicorn, localhost | Web UI |
| Alternative transport backends | — | FTP/HTTP/BitTorrent |
| Extracted folder mounting | — | FUSE virtual image |
| Multi-network load balancing | — | Link aggregator |
| Multiple simultaneous LUNs | — | Multi-game LUNs |

## References

- Canonical specs: `specs/01-08` in this repository
- Roadmap: `plans/01-project-roadmap.md` (8 phases)
- Research repo: `PSBrew/RemotePFS-Research` (KB 00-13, reports, frozen artifacts)
- mkpfs: `PSBrew/MkPFS` (conventions reference)
- ShadowMountPlus source: verified DD-02 (detects directories + image files)
- NBD protocol: https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md
- nbdkit Python plugin: https://libguestfs.org/nbdkit-python-plugin.1.html
- exFAT specification: Microsoft exFAT Revision 1.00 (available via docs.microsoft.com)
