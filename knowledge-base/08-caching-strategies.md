# 08 - Multi-Tier Caching Strategies

## Source

- Linux page cache: https://www.kernel.org/doc/Documentation/filesystems/vfs.txt
- dm-cache documentation: https://www.kernel.org/doc/Documentation/device-mapper/cache.txt
- zRAM documentation: https://www.kernel.org/doc/Documentation/blockdev/zram.txt
- ARC/L2ARC in ZFS (reference): https://openzfs.org/
- Linux memory management: https://www.kernel.org/doc/gorman/
- Researched: 2026-09-05

## Executive Summary

Game files are 10-100+ GB but active reads are sequential. RemotePFS has 4 GB RAM on the Cubie A7S to allocate between service and file cache. The cache strategy uses Linux page cache as primary (NFS reads populate page cache automatically), with optional dm-cache on local storage for a second tier. Prefetching sequential game file blocks ahead of the PS5'S read position reduces cache misses. The total cache budget should be ~3 GB for file cache, leaving ~1 GB for RemotePFS service process and kernel.

## Cache Hierarchy

```
PS5 Read Request
      |
      v
+------------------+
| L1: Page Cache   |  3 GB RAM (fastest, ~15-35 GB/s depending on LPDDR5 bus width)
| (in-memory VFS   |  Managed automatically by Linux kernel
|  cache for NFS)  |  LRU-like eviction with read-ahead
+------------------+
      | (miss)
      v
+------------------+
| L2: Local Storage |  Optional: onboard eMMC or USB HDD (slower, ~50-150 MB/s read)
| (dm-cache)        |  Managed by device-mapper cache target
|                   |  Write-back or write-through mode
+------------------+
      | (miss)
      v
+------------------+
| L3: NAS (NFS)    |  ~112 MB/s per 1 GbE link (~220 MB/s with bonded 2x)
| (network fetch)   |  The source of truth for all game files
+------------------+
```

## L1: Linux Page Cache

### How It Works

- Every NFS read populates the page cache transparently.
- The kernel evicts pages under memory pressure using a two-list (active/inactive) LRU algorithm.
- Read-ahead mechanism detects sequential access patterns and prefetches pages ahead of the current position.
- `fadvise()` can hint to kernel: `POSIX_FADV_SEQUENTIAL` increases read-ahead window for sequential access, `POSIX_FADV_WILLNEED` triggers prefetch, `POSIX_FADV_DONTNEED` drops pages from cache.

### Page Cache Sizing

With 4 GB total RAM on Cubie A7S:

```bash
# Reserve 1 GB for system, service process, kernel
# Allow page cache to use remaining ~3 GB
# System automatically reclaims under pressure

# Tune vm settings for caching:
sysctl vm.vfs_cache_pressure=50     # Default: 100. Lower = keep dentries longer
sysctl vm.swappiness=10             # No swap configured; set conservatively in case swap added later
sysctl vm.dirty_ratio=30            # Start writeback at 30% dirty (minimal impact: read-dominated workload)
sysctl vm.dirty_background_ratio=10 # Background writeback at 10% (same: read-dominated)
```

### Read-Ahead Tuning

```bash
# Increase read-ahead for sequential game file reads
# Default: 128 KB. Game files benefit from larger read-ahead.
blockdev --setra 2048 /dev/sda   # 2048 sectors = 1 MB read-ahead

# For NFS mounts, rsize=1048576 sets the NFS RPC payload size (1 MB per READ).
# NFS client read-ahead is managed by the kernel nfs_readahead logic, separate from rsize.
# blockdev --setra applies to local block devices, not NFS mounts.
```

### fadvise Integration (RemotePFS Logic)

RemotePFS can call `posix_fadvise()` to optimize caching:

```python
import os, ctypes

# Constants from fcntl.h
POSIX_FADV_SEQUENTIAL = 2
POSIX_FADV_WILLNEED = 3
POSIX_FADV_DONTNEED = 4

def prewarm_cache(fd, offset, length):
    """Tell kernel to preload region into page cache."""
    ctypes.CDLL("libc.so.6").posix_fadvise64(fd, offset, length, POSIX_FADV_WILLNEED)

def drop_cache(fd, offset, length):
    """Tell kernel region won't be needed again (free memory)."""
    ctypes.CDLL("libc.so.6").posix_fadvise64(fd, offset, length, POSIX_FADV_DONTNEED)
```

## L2: dm-cache (Optional Local Block Cache)

### When to Use

- When NAS is accessed over WiFi with high latency or variable bandwidth.
- When game files exceed 3 GB working set and cache thrashing is observed.
- When onboard eMMC (optional, up to 256 GB) can serve as a read cache.

### dm-cache Configuration

dm-cache requires a block-level origin device (e.g., iSCSI LUN or NBD export). It cannot cache an NFS mount directly, since dm-cache operates at the block layer, not the filesystem layer. If using NFS for file serving (the recommended RemotePFS architecture), dm-cache is not applicable unless the NAS also exports a block device via iSCSI/NBD. This section is documented for reference but not recommended for the initial RemotePFS release.

```bash
# Setup dm-cache with eMMC as cache device
# Requires: eMMC partitions for metadata + cache data, iSCSI/NBD LUN as origin
# /dev/mmcblk0p1 = eMMC metadata partition, /dev/mmcblk0p2 = eMMC cache data
# /dev/nas-lun = iSCSI LUN from NAS (origin, slow)

# Step 1: Create cache device with correct dmsetup table format:
#   cache <metadata> <cache> <origin> <block_size> <#feature_args> [<feature>]* <policy> <#policy_args> [policy_args]*
dmsetup create games-cache --table "0 $(blockdev --getsz /dev/nas-lun) \
  cache /dev/mmcblk0p1 /dev/mmcblk0p2 /dev/nas-lun 1024 0 mq 0"

# block_size=1024 sectors (512 KB), 0 feature args, policy=mq, 0 policy args
# Step 2: Use /dev/mapper/games-cache as backing for USB gadget
```

### Cache Policy

dm-cache supports multiple policies:
- **mq** (multi-queue): Default, good general-purpose.
- **smq** (stochastic multi-queue): Better for large caches with many blocks.
- **cleaner**: Special-purpose maintenance policy for flushing dirty blocks during decommissioning. Not a running cache policy.

For RemotePFS:
```bash
# Sequential read pattern, write-through mode (no writes to cache on NAS)
# Use mq policy with sequential threshold tweaked
```

### Limitations

- dm-cache operates at block level, not file level — can't leverage file semantics.
- Write-back risks data loss on eMMC failure (not an issue for read-only game cache).
- Adds kernel overhead (another I/O layer between exFAT and NFS).
- Likely unnecessary if NFS page cache is sufficient.

## L3: NAS (NFS)

- Accessed only on L1 and L2 cache misses.
- Bonded 2x1GbE provides ~220 MB/s aggregate throughput.
- NFS `nconnect=4` opens 4 parallel TCP connections, which LACP can distribute across bonded 2x1GbE links for aggregate throughput.

## Prefetching Strategy

Game files are read sequentially. RemotePFS can prefetch blocks ahead:

```python
class PrefetchManager:
    """Prefetch sequential blocks ahead of PS5 read position."""

    def __init__(self, cache_size_mb=4):
        self.prefetch_window = cache_size_mb * 1024 * 1024  # 4 MB ahead
        self.fd = None

    def on_read(self, offset: int, length: int):
        """After a PS5 read, prefetch the next window."""
        prefetch_offset = offset + length
        prefetch_length = min(self.prefetch_window,
                               self.file_size - prefetch_offset)
        if prefetch_length > 0:
            # Non-blocking prefetch via fadvise
            posix_fadvise(self.fd, prefetch_offset, prefetch_length,
                          POSIX_FADV_WILLNEED)

    def set_file(self, fd, file_size: int):
        self.fd = fd
        self.file_size = file_size
        # Enable kernel sequential read-ahead
        posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)
```

## Memory Budget

| Component | RAM | Notes |
|-----------|-----|-------|
| RemotePFS service | 128 MB | Python process heap (fadvise + configfs management) |
| Kernel + system | 512 MB | Kernel, drivers, systemd |
| Page cache (L1) | 3 GB | Automatic via NFS reads |
| **Total** | **4 GB** | Cubie A7S base model |

For 8 GB or 16 GB Cubie A7S variants:
- Scale page cache proportionally: 7 GB or 15 GB.
- No upper limit — more cache = fewer NAS round trips.

## Cache Hit Rate Expectations

For sequential game reads with a working set larger than cache:
- **Worse case**: cache thrashing, hit rate near 0%.
- **Best case** (fits in cache): 100%.
- **Typical** for PS5 games (large sequential files):
  - Active file cache: the game file being read benefits from read-ahead.
  - Re-reads (loading screens, fast travel): hit when within L1 window.
  - Prefetch ahead: 4 MB window ahead of current position (see PrefetchManager).

## When to Avoid Caching

- **O_DIRECT** reads: bypass page cache entirely, go directly to NFS.
  - Use only if measuring shows cache pollution from one-shot reads.
  - Otherwise, use `POSIX_FADV_DONTNEED` after reading transient data.

## Relevance to RemotePFS

1. **Primary cache is Linux page cache** (L1). Zero code, maximum performance.
2. **fadvise integration** (prefetch + drop) in RemotePFS service process.
3. **Read-ahead tuning** via `blockdev --setra` and `POSIX_FADV_SEQUENTIAL`.
4. **L2 local cache** (dm-cache on eMMC) is optional, added only if profiling shows need.
5. **Memory budget**: 3 GB for cache on base model, leaving 1 GB for service.
6. **No custom caching framework needed.** Kernel provides everything.

## References

1. Linux page cache: https://www.kernel.org/doc/gorman/html/understand/understand013.html
2. dm-cache: https://www.kernel.org/doc/Documentation/device-mapper/cache.txt
3. posix_fadvise man page: https://man7.org/linux/man-pages/man2/posix_fadvise.2.html
4. blockdev man page: https://man7.org/linux/man-pages/man8/blockdev.8.html
