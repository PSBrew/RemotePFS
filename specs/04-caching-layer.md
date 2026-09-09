# 04 — Caching Layer

> **RemotePFS context:** SBC (4 GB RAM) caches virtual exFAT reads across two tiers:
> L1 nbdkit cache filter (temp file in $TMPDIR), L2 kernel page cache on NFS file reads.
> Metadata preloading warms critical sectors before PS5 USB gadget activation.

---

## 1. Architecture Overview

```
PS5 USB host
    │  SCSI READ(16)
    ▼
g_mass_storage  (file=/dev/nbd0, ro=1)
    │  block I/O
    ▼
/dev/nbd0  (kernel NBD client, nbd.ko)
    │  read_ahead_kb=4096
    │  AF_UNIX socket
    ▼
nbdkit --filter=blocksize --filter=cache
    │
    │  ┌─────────────────────────────────────┐
    │  │  blocksize filter (minblock=512,   │
    │  │  maxdata=65536)                    │
    │  │  Aligns reads, splits oversized.   │
    │  │  —                                                      │
    │  │  (requests aligned to 512 B, fragmented if >64 KiB)     │
    │  └──────────────┬──────────────────────┘
    │                 │
    │                 ▼
    │  ┌─────────────────────────────────────┐
    │  │  L1: nbdkit cache filter           │
    │  │  Temp-file cache (location: $TMPDIR)                 │
    │  │  cache-on-read=true                                 │
    │  │  Capacity bounded by cache-max-size; LRU eviction   │
    │  │  (see man nbdkit-cache-filter)                      │
    │  └──────────────┬──────────────────────┘
    │                 │ cache miss
    │                 ▼
    │  ┌─────────────────────────────────────┐
    │  │  Python plugin → SectorMapper      │
    │  │  Maps LBA → (NFS fd, offset)       │
    │  │  extents() → NBDKIT_EXTENT_ZERO   │
    │  └──────────────┬──────────────────────┘
    │                 │ os.pread(nfs_fd, offset, count)
    │                 ▼
    │  ┌─────────────────────────────────────┐
    │  │  L2: Linux Page Cache              │
    │  │  Kernel NFS client page cache.      │
    │  │  Automatic, no config needed.       │
    │  │  Backed by free RAM (~2–3 GiB).    │
    │  └──────────────┬──────────────────────┘
    │                 │ page cache miss
    │                 ▼
    │  NFS v4.1 RPC READ over TCP 2049
    │
    ▼
NAS (Synology / NFS server)
```

Two-tier design eliminates redundant work:

- **L1 (nbdkit cache)**: Absorbs repeated protocol-level reads. exFAT metadata
  sectors read many times per PS5 filesystem scan — cache hit avoids Python
  plugin call entirely.
- **L2 (page cache)**: Absorbs repeated file-level reads. If PS5 reads the same
  game asset twice or reads nearby sectors of a large file, the in-kernel page
  cache serves it without NFS round-trips.

---

## Metadata prefetch policy

Activation performs synchronous, bounded prefetch through NBD before USB bind.
Directory metadata and full FAT prefetch are enabled by default. Full FAT size
is proportional to virtual image size, but reads are split into 64 KiB requests
and the FAT is never expanded into a Python in-memory blob.

Configuration uses nested categories:

```yaml
prefetch:
  directory_metadata: {enabled: true, refresh_interval_seconds: 300}
  fat: {enabled: true, refresh_interval_seconds: 300}
```

The interval is reserved for future asynchronous incremental refresh. Current
nbdkit cache remains the only verified file-cache layer.

## 2. L1: nbdkit Cache Filter

### 2.1 Configuration

```bash
nbdkit \
    --unix /run/remotepfs/nbd.sock \
    --readonly \
    --filter=blocksize \
    --filter=cache \
    python /usr/lib/remotepfs/remotepfs_nbd.py \
    minblock=512 \
    maxdata=65536 \
    cache-min-block-size=262144 \
    cache-max-size=1073741824 \
    cache-on-read=true
```

**Filter stacking order:** nbdkit applies filters left-to-right, outermost first:

```
client → blocksize filter → cache filter → python plugin
```

- **blocksize** (outermost): `minblock=512` rounds reads to 512-byte alignment;
  `maxdata=65536` (64 KiB) fragments larger reads into multiple plugin requests.
  All requests reaching the cache are at least sector-aligned and at most 64 KiB.

| Parameter               | Value          | Purpose                                           |
|-------------------------|----------------|---------------------------------------------------|
| `minblock`              | `512`          | Minimum block size and alignment. Reads padded to 512-byte boundary. Power of two, ≤ 64K. |
| `maxdata`               | `65536`        | Maximum read size 64 KiB. Larger reads are fragmented. Integer multiple of minblock. |
| `cache-min-block-size`  | `262144`       | Minimum block size used by the cache (256 KiB). Power of 2, ≥ 4096. Default is 64K. Larger values reduce metadata overhead at the cost of spatial waste. |
| `cache-max-size`        | `1073741824`   | 1 GiB cache capacity limit. When reached, blocks are evicted per high/low thresholds (95%/80%) until below the low threshold. Least recently used blocks discarded first. |
| `cache-on-read`         | `true`         | Populate cache on read miss. Any time a block is read from the plugin, it is saved in the cache (if space permits). |

### 2.2 Cache Behavior

With `cache-on-read=true`, every read from the plugin is stored in the cache
temp file. The cache divides data into blocks of at least `cache-min-block-size`
(256 KiB). When the cache reaches `cache-max-size` (1 GiB), eviction triggers
at the high threshold (95% = 972 MiB) and continues until below the low
threshold (80% = 819 MiB). Least recently used blocks are discarded first.

The default `cache=writeback` mode is harmless: the device is read-only
(`--readonly` on nbdkit, `ro=1` on the USB gadget), so no writes ever reach
the cache and no dirty pages exist.

**Rationale for blocksize-first:** Placing blocksize before cache ensures that
all requests arriving at the cache are sector-aligned (minblock=512) and any
larger client reads are fragmented to 64 KiB chunks (maxdata). Avoid assuming
specific cache entry sizing; see man nbdkit-cache-filter.

**Read path (blocksize → cache → plugin):**
1. NBD request arrives from nbd.ko.
2. Blocksize filter: rounds to 512-byte boundary; fragments if >64 KiB.
3. Cache filter: check for cached copy. If hit, return.
4. Miss: forward to Python plugin. Cache the result on return (space permitting).

This is critical for the virtual exFAT: Region 2 (file data clusters) is sparse.
Only clusters backing config-defined files are populated. All other clusters
(sectors 65536+) are unallocated. Without extents, nbdkit would call the Python
plugin for every unallocated-sector read, which would then return zeros. With
extents, the kernel NBD client never issues those reads — the zero region is
served from the kernel block layer without any nbdkit interaction.

The nbdkit cache filter must not cache zero extents. Unallocated regions are
infinite — caching them wastes 1 GiB on nothing. The cache filter only stores
data returned by real `pread()` calls (allocated sectors).

---

## 3. L2: Linux Page Cache (NFS)

### 3.1 Mechanism

The Python plugin reads NFS files via `os.pread(fd, offset, count)`. This
syscall passes through the kernel VFS layer, which checks the page cache:

- **Page cache hit:** Data already in RAM from a previous NFS read. Returned
  without any network I/O. Latency = memory copy (~microseconds).
- **Page cache miss:** Kernel NFS client issues `NFS4OP_READ` RPC over TCP 2049.
  Data is read into page cache, then copied to userspace. Latency = LAN RTT +
  NAS disk read.

The page cache is automatic — no configuration required beyond the NFS mount.

### 3.2 Page Cache Sizing

On a 4 GB SBC:

| Component               | Approximate RAM |
|-------------------------|-----------------|
| OS + systemd + SSH      | ~200 MiB        |
| nbdkit + Python plugin  | ~100 MiB        |
| Virtual exFAT metadata  | ~10 MiB         |
| **L1 nbdkit cache**     | **1 GiB**       |
| **Remaining (L2 page cache)** | **~2.7 GiB** |

The kernel automatically uses free RAM for the page cache. NFS-read game data
fills the remaining ~2.7 GiB. Under memory pressure, the kernel evicts clean
(read-only) page cache pages before swapping. Since RemotePFS is read-only
(`ro=1` on gadget, read-only NFS mount), all cached pages are clean and can be
dropped instantly without writeback.

### 3.3 NFS Mount Options Affecting Caching

| Option         | Effect on Caching                                     |
|----------------|-------------------------------------------------------|
| `rsize=1048576`| Max read size 1 MiB. Larger reads fill page cache in bigger chunks, reducing per-byte overhead. |
| `noac`         | Disable attribute cache. Each file access re-validates attributes. Not needed for read-only game files — prefer long `actimeo` instead. |
| `lookupcache=pos`| Cache positive dentries (successful lookups). Avoids re-looking-up game file paths on repeat access. |
| `hard`         | Retry indefinitely on NAS outage. Blocked reads stall PS5 but prevent I/O errors. |
| `ro`           | Read-only mount. All page cache pages are clean, eviction is instant. |

---

## 4. Metadata Preloading (V1)

### 4.1 Purpose

When the PS5 first detects the USB extended storage device, it issues a flurry
of small reads: partition table scan, exFAT VBR validation, FAT traversal, root
directory enumeration, up-case table load. If these reads miss both caches and
hit the NAS, the PS5 sees multi-millisecond latency per sector, causing a slow
initial filesystem scan (ShadowMountPlus takes seconds instead of milliseconds).

Preloading eliminates this cold-start penalty. Before UDC bind, the RemotePFS
service connects `nbdsh` to the nbdkit AF_UNIX socket and reads all known-hot
LBA ranges. These reads flow through the full NBD stack — nbdkit → cache filter
→ blocksize → plugin → SectorMapper → (for file data) NFS pread() → Linux page
cache. Result: **both** L1 (nbdkit cache filter) and L2 (Linux page cache for
file-backed regions) are warmed before the PS5 ever connects.

**Why nbdsh, not raw NFS pread():** Reading NFS files directly (`os.pread(nfs_fd,
...)`) would warm only the Linux page cache (L2). The nbdkit cache filter (L1)
would remain cold, since it only sees NBD protocol requests. Using `nbdsh` (or
`nbdcopy` from `/dev/null`) against the nbdkit Unix socket ensures every read
traverses the full NBD protocol path, populating both tiers.

### 4.2 What Gets Preloaded

SectorMapper exposes a `get_hot_ranges()` method that returns a list of
`(offset, length)` tuples for all metadata regions and top-level directory
clusters. Only these ranges are preloaded — not the entire virtual device.
Unallocated regions (holes) are skipped because `extents()` marks them as
`NBDKIT_EXTENT_ZERO`.

| Region              | LBA Range             | Size        | Content                        |
|---------------------|-----------------------|-------------|--------------------------------|
| exFAT primary boot region | 0 – 11                | ~6 KiB      | Superfloppy VBR, OEM parameters, and filesystem geometry |
| exFAT backup boot region  | 12 – 23               | ~6 KiB      | Backup boot region and checksum sectors |
| FAT (allocation)    | FAT region (variable) | ~9.2 MiB    | Cluster allocation chains. Size depends on virtual device image_size_gib and cluster_size_kib. |
| Root directory      | First data clusters   | Variable    | Directory entries for all top-level game folders. |
| Up-case table       | Up-case table region  | 128 KiB    | Full uncompressed Unicode mapping for all UTF-16 code units. |

Total preload: depends on image_size_gib and cluster_size_kib. Example: **~35–40 MiB** for a 512 GiB image with 64 KiB
clusters and ~50 games.

### 4.3 Preload Sequence

```
1. Config compiled (POST /api/config/compile):
   - SectorMapper built with complete LBA → backing-store map.
   - SectorMapper.get_hot_ranges() emits (offset, length) tuples for:
     - Regions 0 (MBR/GPT boot sectors)
     - Region 1 (exFAT metadata: VBR, FAT, bitmap, root dir, up-case table)
     - Region 2 top-level directory clusters (first cluster of each game dir)
   - Metadata stored in-memory (pre-built buffers for Regions 0–1).

2. nbdkit started with new plugin state, NBD client NOT yet connected.
   nbdkit is listening on /run/remotepfs/nbd.sock.

3. nbdsh connects directly to nbdkit Unix socket:
     nbdsh -u "nbd+unix:///?socket=/run/remotepfs/nbd.sock" \
           -c "h.pread(...)" for each hot range
   ├─ Each pread() traverses: nbdsh → Unix socket → nbdkit → cache filter
   │  → blocksize → python plugin → SectorMapper
   ├─ For metadata (Regions 0–1): SectorMapper returns pre-built buffer.
   │  No NFS round-trip. Data cached in L1.
   ├─ For file data (Region 2 dir clusters): SectorMapper does
   │  os.pread(nfs_fd, offset, count) → fills L2 page cache → then L1.
   ├─ For unallocated ranges: skipped (extents zero-region fast path).
   └─ Cache filter (cache-on-read=true) caches each response.

4. nbdsh disconnects. L1 cache now holds all hot metadata + directory entries.
   L2 page cache holds NFS-backed file data for top-level directory clusters.

5. NBD client connected (nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0), then UDC bound.
   - PS5 initial filesystem scan reads VBR, FAT, root dir, up-case table.
   - All metadata reads are L1 cache hits.
   - Top-level directory enumeration reads are L1 hits (preloaded clusters).
   - PS5 sees sub-millisecond latency for the entire initial scan.
```

### 4.4 Timing

Preloading happens at two points:

1. **Cold start:** After nbdkit starts, before first UDC bind. nbdsh reads
   example ~35–40 MiB (512 GiB image, 64 KiB clusters) through a local Unix socket — dominated by cache filter bookkeeping (~150 ms).
   NFS round-trips for file-backed clusters add ~50–100 ms (LAN RTT).
   Total preload: ~200–250 ms.

2. **Config reload:** After nbdkit restarts with new generation, before UDC
   rebind. Same nbdsh preload sequence with new SectorMapper. Old cache is
   discarded (nbdkit process restart = fresh cache). Preload cost is identical
   to cold start.

### 4.5 What Is NOT Preloaded

- **Unallocated clusters:** extents() returns `NBDKIT_EXTENT_ZERO` for holes.
  The kernel NBD client (and nbdsh) skip zero regions. No cache pollution.
- **Deep subdirectories:** Only top-level directory clusters are preloaded.
  Nested directories (game subfolders) are warmed lazily on first PS5 access.
- **Game file data:** Bulk game assets (multi-GB) are not preloaded. Would
  overflow the 1 GiB L1 cache and evict critical metadata. Game data is served
  from L2 page cache (populated on first read) or NAS.
---

## 5. nbd0 Queue Tuning

### 5.1 read_ahead_kb

The kernel NBD client supports read-ahead via the block layer. Set after NBD
client connection:

```bash
echo 4096 > /sys/block/nbd0/queue/read_ahead_kb
```

| Parameter       | Value  | Rationale                                        |
|-----------------|--------|--------------------------------------------------|
| `read_ahead_kb` | `4096` | 4 MB read-ahead. When PS5 issues sequential reads (game loading), the kernel block layer issues speculative reads ahead of the current position. At 4 MB, this saturates the nbdkit pipeline and hides NFS latency. |

**Trade-off:** Higher values waste cache on data the PS5 may not read. Lower
values under-utilize available bandwidth. 4 MB is a balance: 64× the typical
nbdkit read size (64 KiB after blocksize filter), enough to keep the pipeline
full.

### 5.2 Other Queue Parameters

```bash
# Max sectors per request (max I/O size, in KiB)
echo 64 > /sys/block/nbd0/queue/max_sectors_kb    # 64 KiB (matches blocksize maxdata=65536)

# Nominal I/O scheduler (none for virtual device — no seek cost)
echo none > /sys/block/nbd0/queue/scheduler

# Queue depth (number of concurrent I/O requests)
echo 32 > /sys/block/nbd0/queue/nr_requests
```

| Parameter           | Value    | Rationale                                        |
|---------------------|----------|--------------------------------------------------|
| `max_sectors_kb`    | `64`     | 64 KiB max per request. Matches blocksize filter's `maxdata=65536`. Prevents oversized requests that blocksize would split anyway. |
| `scheduler`         | `none`   | No I/O scheduler. Virtual device has no seek latency — merging adjacent requests adds CPU overhead with no benefit. |
| `nr_requests`       | `32`     | 32 in-flight requests. Enough to pipeline NFS reads without overwhelming the SBC's CPU. |


**Note:** These are block device queue parameters — not NFS, not nbdkit.
`/sys/block/nbd0/queue/*` tuning only affects the kernel's NBD client I/O
scheduling. NFS-level tuning (rsize, nconnect) is covered in spec 02.

---

## 6. Cache Invalidation

Three cache layers exist between PS5 read and NAS disk:

| Layer | Location          | Populated By         | Invalidation Method                  |
|-------|-------------------|----------------------|--------------------------------------|
| L1    | nbdkit cache filter (userspace) | NBD reads via nbdsh or PS5 | nbdkit process restart |
| BDC   | Kernel block device page cache for /dev/nbd0 | Block I/O from g_mass_storage or pread() on /dev/nbd0 | `nbd-client -d` destroys /dev/nbd0 (page cache evicted with device). Alternative: `blockdev --flushbufs /dev/nbd0` before disconnect. |
| L2    | Kernel NFS page cache | `os.pread(nfs_fd, ...)` in plugin | Survives nbdkit restart. Partially reusable if NFS file mapping unchanged. |

### 6.1 On Config Reload

When a new config generation is activated (`POST /api/config/activate`):

```
1. Unbind UDC                    → PS5 sees device removal
2. blockdev --flushbufs /dev/nbd0 → Purge kernel block device page cache
                                     for /dev/nbd0 (stale blocks from old
                                     generation). Belt-and-suspenders:
                                     disconnect also clears this.
3. nbd-client -d /dev/nbd0       → Disconnect NBD client. /dev/nbd0 removed.
                                     Kernel page cache for old nbd0 evicted.
4. kill nbdkit                   → nbdkit process exits. L1 cache destroyed.
5. Start new nbdkit              → Fresh process, empty L1 cache.
                                     Listening on /run/remotepfs/nbd.sock.
6. nbdsh preload                 → Connect nbdsh to Unix socket, read hot
                                     ranges. Warms L1 (nbdkit cache) and L2
                                     (NFS page cache for file-backed clusters).
7. nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0 → Reconnect.
                                     Fresh /dev/nbd0, no stale blocks.
8. Set nbd0 queue params         → read_ahead_kb=4096, max_sectors_kb=64
9. Bind UDC                      → PS5 sees device insertion, rescans.
                                     All metadata reads hit pre-warmed L1.
```

**Why `blockdev --flushbufs` before disconnect:** `nbd-client -d` destroys the
NBD device, which implicitly evicts its page cache. However, adding explicit
`blockdev --flushbufs /dev/nbd0` before disconnect is belt-and-suspenders: it
guarantees no stale block data from the old SectorMapper survives in the kernel
block layer, even if the disconnect races with in-flight I/O.

**L2 survival across restart:** The kernel NFS page cache survives nbdkit
restart (it belongs to the NFS mount, not nbdkit). Unchanged games (same
virtual path → same NFS file) benefit from warm L2 after activation. New or
remapped games miss L2 and warm on first PS5 access.

### 6.2 No Runtime Cache Coherence Needed

- **NFS files are read-only.** No other process writes to them. No cache
  invalidation for writes.
- **Config changes restart nbdkit.** No need for partial cache invalidation or
  selective eviction. Full restart = correct by construction.
- **NAS-side changes** (new game files added to NFS export) require config
  recompilation and activation. Without activation, PS5 does not see new files.
---

## 7. Monitoring

### 7.1 nbd0 Block Device Stats

```bash
# Cumulative I/O statistics for /dev/nbd0
cat /sys/block/nbd0/stat
# Fields: read_ios read_merges read_sectors read_ticks
#         write_ios write_merges write_sectors write_ticks
#         in_flight io_ticks time_in_queue

# Read-ahead effectiveness
cat /sys/block/nbd0/queue/read_ahead_kb

# Queue depth utilization
cat /sys/block/nbd0/queue/nr_requests
```

Key metrics for the HTTP API `/api/status` endpoint:

| Metric                 | Source                         | Meaning                                          |
|------------------------|--------------------------------|--------------------------------------------------|
| `nbd0_read_sectors`    | `/sys/block/nbd0/stat` field 3 | Total sectors read by PS5. Cumulative counter.   |
| `nbd0_read_ticks`      | `/sys/block/nbd0/stat` field 4 | Total ms spent reading. Latency indicator.       |
| `nbd0_in_flight`       | `/sys/block/nbd0/stat` field 9 | Current in-flight I/Os. Pipeline saturation.     |
| `nbd0_read_ahead_kb`   | `/sys/block/nbd0/queue/`       | Current read-ahead setting.                      |

### 7.2 NFS Page Cache Stats

```bash
# System-wide page cache usage
cat /proc/meminfo | grep -E '^(Cached|Buffers|MemTotal|MemAvailable):'

# Per-NFS-mount I/O stats (if using nfsstat)
nfsstat -m    # NFS mount stats (per-server RPC counts, retransmits)
```

| Metric                 | Source                         | Meaning                                          |
|------------------------|--------------------------------|--------------------------------------------------|
| `Cached`               | `/proc/meminfo`                | Total page cache size (includes NFS + everything else). ~2.5 GiB typical. |
| `MemAvailable`         | `/proc/meminfo`                | RAM available for new allocations (including reclaimable cache). |
| NFS `retrans`          | `nfsstat -m`                   | NFS RPC retransmissions. Non-zero = NAS congestion or packet loss. |

### 7.3 nbdkit Cache Filter Stats

nbdkit cache filter can be configured to log cache statistics (hit rate, size,
evictions) via the `cache-log` parameter or by querying nbdkit's control socket.
V1 monitoring reads nbdkit's stderr output for cache stats if `--verbose` is
set.

```bash
nbdkit --readonly --filter=blocksize --filter=cache \\
       --verbose \\
       python /usr/lib/remotepfs/remotepfs_nbd.py \\
       cache-max-size=1G cache-on-read=true 2>/var/log/remotepfs/nbdkit.log
```

---

## 8. Performance Expectations

| Scenario                              | L1 Hit | L2 Hit | NFS Miss | Expected Latency     | Throughput |
|---------------------------------------|--------|--------|----------|----------------------|------------|
| PS5 partition scan (metadata)         | 100%   | —      | —        | <0.1 ms              | N/A (small)|
| PS5 directory listing (cached dir)    | 100%   | —      | —        | <0.1 ms              | N/A        |
| Game file read, first access          | miss   | miss   | hit      | 2–5 ms (LAN RTT + NAS) | ~112 MB/s |
| Game file read, same block repeated   | hit    | —      | —        | <0.1 ms              | >500 MB/s (local) |
| Game file read, nearby block (page cache) | miss | hit   | —        | <0.5 ms (kernel copy) | ~400 MB/s   |
| Unallocated sector read (extents)     | —      | —      | —        | <0.01 ms (kernel)    | N/A        |

**Notes:**

- **L1 hit path** uses filesystem I/O on a temporary cache file in `$TMPDIR`.
  Involves a read() syscall; performance depends on the backing filesystem
  and mount options. Measure on target hardware; no fixed latency guaranteed.
- **L2 hit latency** is kernel page cache lookup + copy_to_user. A syscall to
  `pread()` + memory copy. 2–5× slower than L1 hit but still sub-millisecond.
- **NFS miss latency** is LAN round-trip + NAS disk read. GbE RTT is ~0.2 ms
  (local switch), NAS disk seek is 2–10 ms depending on HDD vs SSD.
  Throughput-limited by 1 GbE line rate (~112 MB/s) after pipeline fills.
- **Extents fast path** avoids all layers. The kernel NBD client sees
  `NBD_REPLY_TYPE_BLOCK_STATUS` with `NBD_STATE_ZERO` and returns zeros without
  issuing `NBD_CMD_READ`. Zero plugin, zero cache, zero NFS interaction.
- Preloading ensures the first PS5 scan path (metadata + directory entries)
  follows the L1 hit row, not the NFS miss row.

### 8.1 Cold Start Latency Budget

For a cold boot with preloading:

| Step                                  | Time    | Notes                                    |
|---------------------------------------|---------|------------------------------------------|
| nbdkit start + plugin init            | ~500 ms | Python import, SectorMapper build        |
| nbdsh metadata preload (9–10 MiB)     | ~250 ms | nbdsh on Unix socket, warms L1 + L2      |
| NBD client connect                    | ~50 ms  | nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0 |
| UDC bind                              | ~100 ms | ConfigFS gadget setup                    |
| PS5 partition scan (all L1 hit)       | ~50 ms  | ShadowMountPlus probes exFAT             |
| **Total cold start to PS5 ready**     | **~950 ms** |                                      |
Without preloading, the PS5 partition scan would trigger NFS misses (~2–5 ms
each), adding seconds to the startup for the hundreds of small metadata reads
involved in exFAT validation + directory enumeration.

---

## 9. Design Decisions

### 9.1 Why nbdkit cache, not a custom Python cache

- **Correctness:** nbdkit cache filter is maintained by Red Hat, battle-tested
  in libguestfs/virt-v2v. It handles LRU eviction, block alignment, concurrent
  access (SERIALIZE_REQUESTS thread model), and cache coherency correctly. A custom Python
  dict-based cache would need to replicate all of this.
- **Zero plugin overhead on hit:** Cache filter runs before the Python plugin.
  A cache hit never enters Python. A Python-level cache would still pay the
  nbdkit → plugin dispatch cost (C → Python → C) on every read, even hits.
- **Integration with blocksize filter:** Cache filter sits outside blocksize.
  Reads are cached after alignment (512-byte minimum) and size bounding
  (64 KiB maximum). This gives the cache filter consistent request shapes:
  same LBA range always maps to the same `(offset, count)` tuple, maximizing
  hit rate.

- exFAT metadata reads from the PS5 are dominated by 512-byte and 4096-byte
  reads (single sectors, directory entries). These are well below 256 KiB.
- Sequential game data reads are 128 KiB–1 MB (PS5 game filesystem block size).
  A 256 KiB threshold means metadata reads are cached, game data reads are not
  (cached at L2 instead).
- The 1 GiB L1 cache fits approximately 4096 blocks of 256 KiB. This is enough
  for all metadata plus hot game data if desired.
- Tuning: if game data hit rate is too low, reduce `cache-min-block-size` to `131072`
  (128 KiB) to allow some game data into L1. Trade RAM for hit rate.

### 9.3 Why preloading over lazy warming

- **PS5 timeout sensitivity.** The PS5 USB mass storage class driver has
  timeouts on SCSI commands. If the initial partition scan takes too long
  (hundreds of NFS round-trips at 2–5 ms each), the PS5 may report the device
  as "unsupported" or "corrupted." Preloading eliminates this risk.
- **Deterministic startup time.** Preloading is a fixed ~150 ms operation.
  Without it, startup time depends on NFS RTT, NAS load, and PS5 request pattern
  — unpredictable by minutes.
- **Low cost.** 9–10 MiB is <1% of the SBC's 4 GB RAM. The preload time
  (~150 ms) is dominated by cache filter bookkeeping, not data movement.

### 9.4 Why nbdkit restart for invalidation

- **Simplicity.** No partial invalidation protocol. No cache tags, no generation
  IDs, no version checks. Kill process → start new process = all state reset.
- **Correctness.** A partial invalidation bug (stale cache entry not evicted)
  could serve old FAT entries after a config change, causing the PS5 to read
  wrong file data. Restart eliminates this class of bug entirely.
- **Restart cost is acceptable.** nbdkit startup + Python plugin init + NBD
  reconnect + metadata preload + UDC rebind = ~800 ms. The PS5 sees a brief
  device removal/insertion. Config reloads are infrequent (once per session when
  adding/removing games).

---

## 10. Future Extensions (Out of V1 Scope)

### 10.1 Cache Warm-Up on UDC Bind Trigger

Detect the PS5's initial partition scan heuristic (pattern of small metadata
reads) and defer nbdkit cache population until the PS5 actually connects. This
avoids warming the cache if the gadget is never bound. Not needed for V1 —
preload cost is negligible.

### 10.2 L1 Cache Size Auto-Tuning

Adjust `cache-max-size` dynamically based on `MemAvailable` from `/proc/meminfo`.
If the system has more free RAM, grow L1. If under memory pressure, shrink.
Requires nbdkit cache filter runtime resize support (not available in current
nbdkit versions).

### 10.3 Persistent L1 Cache Across Restarts

Serialize the cache filter state (if supported by nbdkit) so that nbdkit
restart on config reload can preserve hot cache entries that remain valid.
### 10.4 Prefetch Heuristics

Monitor PS5 read patterns and issue speculative pread() calls for likely-next
sectors (e.g., next sequential range in a large game file). Parallelize over
Wi-Fi interface to avoid consuming GbE bandwidth needed for foreground reads.
Requires the nbdkit python plugin to expose a background prefetch hook.

---

*Conforms to mkpfs conventions: Python 3.11+, Google docstrings, Ruff
line-length=119, Conventional Commits.*
