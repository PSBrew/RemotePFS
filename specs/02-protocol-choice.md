# 02 — Protocol Choice

> **RemotePFS context:** Two distinct protocol decisions govern the data path:
> (a) NAS access: how the SBC reads game files from the NAS over the LAN.
> (b) Local block device: how the virtual exFAT block device is served to the
> USB gadget layer on the SBC itself. These are separate concerns with separate
> constraints.

---

## 1. Decision A: NAS Access — NFS v4.1

The SBC must read game file data from the NAS over the LAN. The chosen protocol
is NFS v4.1.

### 1.1 Rationale

- **Native in-kernel Linux client.** No userspace daemon required. Mount via
  `mount -t nfs4` directly. The kernel NFS client integrates with the Linux page
  cache, meaning recently-read file data is cached transparently in the SBC's
  free RAM.

- **Low CPU overhead.** NFS has no mandatory signing or encryption (unlike SMB
  3.1.1). On a resource-constrained SBC, per-packet crypto overhead at 1 GbE
  line rate would consume a significant fraction of available CPU. NFS avoids
  this entirely.

- **Compound operations.** NFSv4.1 batches multiple RPCs (OPEN, READ, CLOSE)
  into a single compound request, reducing round-trips versus NFSv3. For
  sequential large-file reads, compound operations plus `rsize=1048576` achieve
  ~112 MB/s on 1 GbE.

- **`nconnect` mount option.** Linux NFS client supports multiple TCP connections
  per mount (`nconnect=N`). With 2× bonded 1 GbE, `nconnect=2` can utilize both
  NICs for aggregate throughput on a single mount.

- **Hard mount.** `-o hard` ensures the NFS client retries indefinitely if the
  NAS is temporarily unreachable. Reads block rather than returning I/O errors,
  which is the correct behaviour for a read-only game file use case — the PS5
  sees a transient stall, not a broken device.

### 1.2 Mount Configuration

```bash
mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
    nas.example.com:/exports/games /mnt/nas/games
```

| Option           | Value       | Purpose                                       |
|------------------|-------------|-----------------------------------------------|
| `ro`             | —           | Read-only. Game files are never modified.     |
| `hard`           | —           | Retry indefinitely on NAS outage.             |
| `nconnect`       | 2           | Two TCP connections per mount, one per NIC.   |
| `rsize`          | 1048576     | 1 MB read size, matches NFS max.              |
| `noatime`        | —           | Skip access-time updates, reduce metadata ops.|
| `nosuid`         | —           | Ignore setuid/setgid bits (security hardening).|
| `nodev`          | —           | Do not interpret device files on this mount.  |
| `noexec`         | —           | Prevent direct execution from this mount.     |

### 1.3 Why Not SMB 3.1.1 for NAS Access

- Mandatory signing and optional encryption add CPU overhead on the SBC.
- File-level access with per-open/close metadata round-trips.
- Requires Samba or ksmbd server-side on the NAS.
- Higher protocol overhead per request versus NFS.

SMB is usable as a fallback if the NAS only exports SMB, but NFS is preferred.

### 1.4 Why Not iSCSI for NAS Access

iSCSI would require the NAS to export a raw block device (LUN), not individual
files. This forces one of two undesirable models:

1. **Single pre-built image file on NAS, exported as iSCSI LUN.** This is the
   old architecture (file-backed `.exfat` image). It precludes dynamic virtual
   exFAT construction from arbitrary NAS folder contents and makes config-driven
   virtual path mapping impossible.

2. **Cluster filesystem on NAS (e.g., OCFS2, GFS2) shared with SBC.** The SBC
   would mount the cluster filesystem directly and build the virtual exFAT from
   it. This requires a cluster-aware filesystem on both NAS and SBC, adding
   significant complexity for zero benefit over NFS file-level access.

NFS is the correct choice: the NAS exports files, the SBC mounts them, and the
virtual exFAT builder reads file data on demand via the NFS mount.

---

## 2. Decision B: Local Block Device — NBD via nbdkit

The virtual exFAT filesystem is served to the Linux USB gadget subsystem as a
local block device. The chosen protocol is NBD (Network Block Device), used
**entirely on the local SBC** — no network transport involved.

### 2.1 Why a Local Block Device Is Required

The Linux `g_mass_storage` USB gadget driver requires a backing file or block
device via the configfs `file=` parameter:

```
/sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/lun.0/file
```

While this parameter can point to a regular file, using a block device is
strongly preferred:

- **File-backed LUNs use the kernel's file I/O path.** Reads go through the
  page cache, adding a copy. The kernel must serialize I/O through the inode,
  and `splice`/`sendpage` from file-backed storage is fragile.

- **Block devices use the kernel's block I/O path.** Reads go through the block
  layer, which supports efficient zero-copy `splice` into the USB gadget's
  scatter-gather list. No intermediate page cache copy.

- **The virtual exFAT is not a static file.** It is generated dynamically from
  NAS folder contents by the Python plugin. There is no pre-existing file to
  point `file=` at.

### 2.2 Why NBD (Local)

NBD provides a kernel block device (`/dev/nbd0`) backed by a userspace server.
For RemotePFS, the userspace server is **nbdkit** with a custom Python plugin.

```
/dev/nbd0 → nbdkit (userspace) → Python plugin → SectorMapper → NFS NAS
              ↑ UNIX socket (no TCP)
```

**Key properties:**

- **nbdkit handles the NBD protocol.** The plugin only needs to implement two
  methods: `pread()` (read sectors at an offset) and `extents()` (report sparse
  regions). nbdkit manages connection setup, request dispatch, error handling,
  and the kernel device registration.

- **UNIX domain socket, not TCP.** nbdkit listens on a local UNIX socket
  (`-U /run/remotepfs/nbd.sock`). No network stack traversal. The kernel NBD
  client connects to the socket, and all I/O stays within the SBC.

- **nbdkit filters are composable middleware.** The `--filter=blocksize`
  filter ensures all I/O is aligned to the virtual exFAT block size (4 KB).
  The `--filter=cache` filter provides a 1 GiB read cache in nbdkit, reducing
  repeated calls into the Python plugin for hot sectors.

- **Battle-tested.** NBD has been in the Linux kernel since 1997. The nbdkit
  project is maintained by Red Hat and used in production virtualization
  stacks (libguestfs, virt-v2v). The `nbd.ko` kernel module is present on
  essentially every Linux distribution.

### 2.3 Why Not FUSE + loop

An alternative would be to implement the virtual exFAT as a FUSE filesystem and
then create a loop device from a sparse file on that FUSE mount:

```
Python FUSE daemon → FUSE mount → sparse file → losetup → /dev/loopN → g_mass_storage
```

This is rejected for several reasons:

- **`splice`/`sendpage` from FUSE is unreliable.** The kernel's ability to do
  zero-copy I/O from a FUSE-backed file through a loop device to the USB gadget
  depends on FUSE writeback caching and the `splice_read` file operation. These
  paths are fragile, kernel-version-dependent, and known to cause data
  corruption or hangs under heavy I/O.

- **Double userspace round-trip.** A read from the PS5 goes: USB gadget →
  loop device → FUSE kernel module → Python FUSE daemon (userspace) → response
  back through kernel → loop → USB gadget. The NBD path has a single userspace
  round-trip: USB gadget → nbd.ko → nbdkit (userspace) → response.

- **Sparse file size.** The virtual exFAT must report a fixed size to the PS5
  (e.g., 1 TiB). A sparse file of this size on a FUSE filesystem requires the
  FUSE daemon to handle `fallocate` and `lseek` beyond EOF correctly, which
  adds complexity.

### 2.4 Why Not ublk

Linux `ublk` (userspace block device, kernel 6.0+) is a newer alternative that
allows userspace block device implementations via `io_uring`. It is a candidate
for V2 optimization but not chosen for V1:

- **Kernel version requirement.** ublk requires Linux 6.0+. Many SBC
  distributions (Armbian, Raspberry Pi OS, Radxa images) ship older kernels.
  NBD works on Linux 2.6+.

- **Less battle-tested.** ublk is a 2022 addition. NBD has 25+ years of
  production use. For a reliability-critical path (PS5 game loading), maturity
  matters.

- **`io_uring` complexity.** While ublk's `io_uring` path can offer lower
  latency than NBD's socket-based path, the performance difference on a local
  UNIX socket at 4 KB block sizes is negligible. The benchmark can be revisited
  for V2.

### 2.5 Startup Sequence

```bash
# 1. Load kernel module
modprobe nbd

# 2. Start nbdkit with Python plugin
nbdkit \
    --foreground \
    --unix /run/remotepfs/nbd.sock \
    --readonly \
    --filter=blocksize \
    --filter=cache \
    python /usr/lib/remotepfs/remotepfs_nbd.py \
    cache-min-block-size=262144 \
    cache-max-size=1073741824 \
    cache-on-read=true

# 3. Connect kernel NBD client to UNIX socket via nbd-client
nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0

# 4. /dev/nbd0 is now a live block device backed by the Python plugin
```

---

## 3. Plugin API (nbdkit Python v2)

RemotePFS implements the nbdkit Python plugin API v2 (see spec 06):

- `pread(h, buf, offset, flags)` — fill `buf` with data at `offset`
- `extents(h, count, offset, flags)` — report sparse/zero extents
- `get_size(h)` — return virtual device size
- `block_size(h)` — return tuple `(512, 4096, 65536)` (min, preferred, max)
- `thread_model()` — `nbdkit.THREAD_MODEL_SERIALIZE_REQUESTS`

SectorMapper maps `(offset, length)` to `(NFS fd, file offset)` and performs
`os.pread()` on NAS files. Unallocated regions are reported via `extents()` as
holes/zeros so the kernel can skip reading them.

---

## 4. End-to-End Data Path

```
PS5 USB host
    │  SCSI READ(16) command
    ▼
g_mass_storage (kernel, UDC)
    │  file=/dev/nbd0, ro=1
    ▼
/dev/nbd0 (kernel NBD client, nbd.ko)
    │  NBD_CMD_READ over UNIX socket
    ▼
nbdkit (userspace)
    │  ──filter=cache (1 GiB LRU)
    │  ──filter=blocksize (align to 4 KB)
    │  ──plugin=python
    ▼
Python plugin.pread(offset, size)
    │
    ▼
SectorMapper.resolve(offset, size)
    │  Maps block offset → virtual path → NFS file + file offset
    ▼
os.pread(nfs_fd, file_offset, size)
    │  Linux page cache (NFS client)
    │  If cache hit: zero-copy return from RAM
    │  If cache miss: NFS READ over TCP to NAS
    ▼
NAS filesystem
    │  ext4/ZFS/btrfs → disk
    ▼
Data returned reverse path: NAS → NFS → page cache → pread() → plugin →
nbdkit → nbd.ko → /dev/nbd0 → mass_storage → PS5
```

**Caching layers (two-tier):**

1. **nbdkit cache filter (1 GiB).** LRU cache in a temporary file (`$TMPDIR`).
   Satisfies repeated reads of hot sectors without invoking the Python plugin.
   Bounded by `cache-max-size` (see spec 04).

2. **Linux page cache (NFS client).** The kernel caches NFS file data
   transparently. Since RemotePFS reads game files via `pread()` on NFS-mounted
   file descriptors, all read data passes through the page cache. On a 4 GB
   SBC, the page cache can use most free RAM (typically 2–3 GB after OS and
   application overhead).

---

## 5. Why NBD Is Not Used for NAS Access

The old architecture considered NBD as the NAS access protocol (NBD over TCP to
a remote nbd-server). This is explicitly rejected in the new architecture:

- **NBD over TCP to NAS would require a block device on the NAS.** The NAS would
  need to serve a single block image (the old `.exfat` file model). This
  precludes dynamic virtual exFAT construction from NAS folder contents.

- **NBD has no built-in security.** No authentication, no encryption. Over a
  LAN this is manageable but requires network isolation. For local use (UNIX
  socket) this is irrelevant — only root can connect to the socket.

- **NBD over TCP has no multipathing.** A single NBD connection is bound to one
  TCP flow. NFS `nconnect` provides multi-connection support natively.

NBD is used **locally only** — between the kernel NBD client and nbdkit on the
same SBC, over a UNIX domain socket. NAS access uses NFS v4.1.

---

## 6. Summary

| Decision              | Protocol      | Transport          | Scope           |
|-----------------------|---------------|--------------------|-----------------|
| NAS access            | NFS v4.1      | TCP (1 GbE LAN)    | Remote (SBC→NAS)|
| Local block device    | NBD           | UNIX socket        | Local (SBC)     |

The two protocols serve fundamentally different roles. NFS provides file-level
access to game data on the NAS with native kernel integration, page caching, and
multi-connection throughput. NBD provides a local kernel block device backed by
a userspace plugin, enabling the virtual exFAT to be served to the USB gadget
without fragile FUSE+loop layering.

---

## 7. Sources

- [RFC 5661: NFSv4.1](https://datatracker.ietf.org/doc/html/rfc5661) — Compound
  operations, sessions, pNFS, session trunking.
- [Linux NFS client: nconnect](https://www.kernel.org/doc/html/latest/filesystems/nfs/nfs-client.html) —
  `nconnect` mount option for multiple TCP connections.
- [nbdkit documentation](https://libguestfs.org/nbdkit.1.html) — Plugin API,
  filter architecture, Python plugin support.
- [Linux NBD kernel documentation](https://www.kernel.org/doc/html/latest/admin-guide/blockdev/nbd.html) —
  NBD kernel module, multi-connection support (5.10+).
- [Linux USB gadget: mass_storage](https://www.kernel.org/doc/html/latest/usb/mass-storage.html) —
  `file=` parameter, configfs interface, LUN configuration.
- [ublk: userspace block device](https://www.kernel.org/doc/html/latest/block/ublk.html) —
  Linux 6.0+ userspace block driver via `io_uring`. Future optimization candidate.
