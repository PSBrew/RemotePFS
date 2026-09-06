# 13 - Future Features Catalog

## Source

- exFAT specification: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
- exFAT on-disk format (exfatprogs): https://deepwiki.com/exfatprogs/exfatprogs/6-exfat-on-disk-format
- PS5 exFAT image conversion: https://gist.github.com/indraAsLesmana/52f1f4d3e12d2f4dca1544166a6f88ec
- Linux FUSE documentation: https://www.kernel.org/doc/html/next/filesystems/fuse.html
- ShadowMountPlus source code (local): `../ShadowMountPlusDocsReview/`
- Researched: 2026-09-05

## Executive Summary

Four future features are cataloged here for architectural awareness: (1) metadata preloading, (2) alternative transport protocols, (3) extracted game folder mounting via FUSE, and (4) multi-network load balancing. **None are implemented in the initial RemotePFS design.** However, the service architecture must not preclude them. This document records the research, feasibility assessment, and architectural implications so that spec authors can design extension points now.

## Feature 1: Metadata Preloading (Hot Region Pinning)

### Concept

Most PS5 game files are `.exfat` images (single-file exFAT containers) or `.ppsa`-style compressed blobs. When ShadowMountPlus mounts one, it reads the exFAT metadata structures first. The SBC can preload these structures into RAM or pin them in the Linux page cache so the PS5 never waits for a network round-trip during initial mount.

### exFAT On-Disk Layout (What to Preload)

An exFAT volume has four regions (per Microsoft spec, Table 3):

| Region | Offset (sectors) | Size | Priority for Preload |
|--------|-----------------|------|----------------------|
| Main Boot Sector | 0 | 1 sector (512 bytes) | Critical |
| Main Extended Boot Sectors | 1 | 8 sectors | Critical |
| Main OEM Parameters | 9 | 1 sector | Critical |
| Main Reserved | 10 | 1 sector | Not required (reserved by spec) |
| Main Boot Checksum | 11 | 1 sector | Critical |
| FAT Region | FatOffset (typically sector 24+) | FatLength sectors | Critical |
| Cluster Heap (root dir) | ClusterHeapOffset | Variable (root dir only) | High |
| Cluster Heap (file data) | ClusterHeapOffset + N | Variable | No (lazy-load) |

**Total metadata to preload:** ~6 KiB (main boot region, sectors 0-11) + FAT region (typically 1-10 MiB for game volumes) + root directory cluster (1 cluster = 64 KiB) + allocation bitmap (~293 KiB per 154 GiB image).

### What to Pin

1. **Boot region** (sectors 0-11): 6 KiB (12 sectors). Contains FatOffset, FatLength, ClusterHeapOffset, ClusterCount, FirstClusterOfRootDirectory, BytesPerSectorShift, SectorsPerClusterShift. The PS5 kernel reads this first to identify the filesystem. The backup boot region (sectors 12-23) is redundant for healthy volumes and not included in preload scope.

2. **FAT (allocation table)**: Maps cluster chains. For a 154 GiB exFAT image with 512-byte sectors and 64 KiB clusters, the FAT has ~2.4 million entries (4 bytes each) = ~9.2 MiB. The PS5 reads this to traverse file cluster chains.

3. **Root directory cluster**: Contains directory entries for the top-level files. ShadowMountPlus scans for game files here. Typically 1-2 clusters (64-128 KiB).

4. **Allocation bitmap**: exFAT maintains a bitmap tracking free/used clusters. For ~2.4M clusters at 1 bit each = ~293 KiB. Located via the `BitMap` directory entry in the root directory (not at a fixed cluster). Pinning it avoids a network read during mount.

### Implementation Strategy (Future)

- **Option A: `madvise(MADV_WILLNEED)` on mmap'd regions.** Map the exFAT image file, call `madvise` on the boot + FAT + root directory byte ranges. Kernel initiates nonblocking readahead. Follow with `mincore()` to verify pages are resident before proceeding to UDC bind.

- **Option B: Explicit `readahead()` syscall.** Call `readahead(fd, offset, count)` on the metadata regions before binding the file to the USB gadget. Initiates readahead in the background and returns immediately (may block briefly on filesystem metadata reads). Does not guarantee pages are resident — subsequent `pread()` into a discard buffer confirms residency.


- **Option C: Synchronous `pread()` warming.** Open the file, `pread()` each metadata byte range into a fixed scratch buffer (e.g., 1 MiB) in a loop. Blocks until pages are faulted into page cache. Simplest way to guarantee residency before UDC bind. Overhead: ~9-10 MiB of warm-up reads per game at startup.

- **Option D: Custom FUSE layer.** Intercept reads; serve metadata from in-memory cache, data regions from NFS. Most flexible, most complex.

**Recommended for initial architecture:** Hybrid of Options A/B + C. Issue `readahead()` or `mmap()` + `madvise(MADV_WILLNEED)` on the boot/FAT/root-directory byte ranges (nonblocking, initiates async I/O), then verify residency with a synchronous `pread()` of the last page in each range (or `mincore()` if the file is mmap'd) before binding to UDC. For the ~9-10 MiB metadata package of a typical 154 GiB game image, this takes milliseconds over NFS. `g_mass_storage` opens the path independently via configfs when `lun.0/file` is written — page cache is per-inode, so preloaded pages are shared regardless of which fd is used. Pages are likely retained absent memory pressure but may be evicted under stress; the service should monitor residency (`mincore()` or `pread()` probe) and re-preload if pages are reclaimed.


The spec must define:
- A **metadata region map** for each `.exfat` image: byte offsets of boot, FAT, root directory, allocation bitmap.
- A **preload phase** in the service startup: before `g_mass_storage` binds the file, preload these regions.
- **Page cache is per-inode**, not per-fd. `g_mass_storage` opens the path independently when configfs writes `lun.0/file`. Preload by opening the same path and issuing `readahead()` or `posix_fadvise(POSIX_FADV_WILLNEED)` before binding to UDC. No shared fd is required or possible.

### Boot Sector Fields to Parse for Preload

| Field | Offset (bytes) | Size | Purpose |
|-------|---------------|------|---------|
| FatOffset | 80 | 4 | Sector offset of FAT start |
| FatLength | 84 | 4 | Number of sectors in FAT |
| ClusterHeapOffset | 88 | 4 | Sector offset of cluster heap |
| ClusterCount | 92 | 4 | Total clusters in heap |
| FirstClusterOfRootDirectory | 96 | 4 | Cluster number of root dir |
| BytesPerSectorShift | 108 | 1 | log2(bytes per sector) |
| SectorsPerClusterShift | 109 | 1 | log2(sectors per cluster) |
| NumberOfFats | 110 | 1 | Usually 1 |

From these, compute:
- `bytes_per_sector = 1 << BytesPerSectorShift`
- `sectors_per_cluster = 1 << SectorsPerClusterShift`
- `bytes_per_cluster = 1 << (BytesPerSectorShift + SectorsPerClusterShift)`
- FAT byte range: `FatOffset << BytesPerSectorShift` to `(FatOffset + FatLength) << BytesPerSectorShift`
- Root directory byte range: `(ClusterHeapOffset << BytesPerSectorShift) + ((FirstClusterOfRootDirectory - 2) * bytes_per_cluster)` to `+ bytes_per_cluster`
- Allocation bitmap: located via the `BitMap` directory entry in the root directory (not at a fixed cluster); scan root directory for the bitmap entry to find `FirstClusterOfBitMap` and `BitMapLength`

### Size Estimate

For a typical 154 GiB PS5 game `.exfat` image (per the gist):
- Sector size: 512 bytes (BytesPerSectorShift = 9)
- Cluster size: 64 KiB (SectorsPerClusterShift = 7, i.e., 128 sectors per cluster)
- Cluster count: ~2.4 million
- FAT size: ~9.2 MiB (2.4M entries x 4 bytes)
- Root directory: 1 cluster = 64 KiB
- Allocation bitmap: ~293 KiB (2.4M bits / 8)
- **Total metadata to preload: ~9-10 MiB per game image**

With 16 GiB RAM on the Radxa Cubie A7S, preloading metadata for 10-20 games simultaneously is trivially feasible (~100-200 MiB).

## Feature 2: Alternative Transport Protocols (FTP, P2P/Torrent)

### Concept

RemotePFS currently uses NFS v4.1 over the LAN to fetch game files from the Synology NAS. Future versions could support alternative protocols for different deployment scenarios:

1. **FTP/SFTP**: For remote access over WAN (slow, high-latency). Synology DSM has built-in FTP server. Linux `curlftpfs` (FUSE) can mount FTP as a filesystem. Slow for game streaming but viable for downloading games to local cache.

2. **HTTP/HTTPS**: For CDN-style distribution. A web server on the NAS serves game files; the SBC downloads on demand. Better for initial acquisition than streaming.

3. **BitTorrent/P2P**: For distributing game images across multiple SBCs in a mesh. Each SBC seeds game data to others. Useful in multi-PS5 setups (e.g., game cafes). Adds significant complexity (libtorrent/Transmission integration, piece hashing, peer management).

### Feasibility Assessment

| Protocol | Use Case | Complexity | Integration Path | Viability |
|----------|----------|------------|-----------------|-----------|
| FTP/SFTP | Remote download, WAN access | Low | `curlftpfs` FUSE mount, or `lftp` mirror to local cache | High for downloads, low for streaming |
| HTTP/HTTPS | CDN-style distribution | Low | `curl`/`wget` download, or HTTP FUSE mount | High for downloads, medium for streaming |
| WebDAV | Remote filesystem access | Low | `davfs2` FUSE mount | Medium (higher latency than NFS) |
| BitTorrent | Multi-SBC mesh distribution | High | `libtorrent-rasterbar` C++ library, `transmission-daemon` | Medium (useful only for multi-PS5 setups) |
| SFTP | Secure remote access | Low | `sshfs` FUSE mount | Medium (encrypted, CPU overhead) |

### Architectural Implication

The spec must define a **transport abstraction layer** so the core file-serving logic is decoupled from the network protocol. Initial implementation hardcodes NFS, but the interface should be:

```python
class FileBackend(Protocol):
    def open(self, path: str) -> FileHandle: ...
    def pread(self, handle: FileHandle, offset: int, size: int) -> bytes: ...
    def close(self, handle: FileHandle) -> None: ...
    def stat(self, path: str) -> FileStat: ...
    def list_dir(self, path: str) -> list[str]: ...
```

`FileHandle` is opaque; `pread` may short-read only at EOF or error; concurrent `pread` on the same handle is safe. NFS is the first backend. FTP, HTTP, and BitTorrent are future backends implementing the same interface. The USB gadget layer does not know or care which backend is used.

### NOT Recommended for Initial Release

- FTP/SFTP: Too slow for game streaming (single TCP connection, no parallelism, chatty control channel).
- BitTorrent: Complexity not justified for single-NAS, single-SBC deployment. Only viable for multi-SBC mesh.
- HTTP: Viable as a download mechanism, not for random-access streaming.

**Recommendation:** NFS v4.1 for streaming. FTP/HTTP as optional download-to-cache mechanisms. BitTorrent deferred indefinitely.

## Feature 3: Extracted Game Folder Mounting (FUSE Virtualization)

### Concept

PS5 games are stored as `.exfat` image files. ShadowMountPlus mounts these images and presents them to the PS5. But what if the NAS has extracted game folders (loose files in `PPSAxxxxx-app0/`) instead of `.exfat` images? RemotePFS could:

1. Mount the extracted folder via FUSE.
2. Create a virtual exFAT image in memory (or backed by a sparse file).
3. Present the virtual image to the USB gadget.

The PS5 sees a standard exFAT USB device. RemotePFS translates exFAT sector reads into file reads from the extracted folder.

### How It Would Work

```
NAS (extracted game folder)
  |
  v
SBC: FUSE filesystem
  |  - Reads exFAT metadata from in-memory virtual boot sector + FAT
  |  - Translates cluster reads -> file reads from the extracted folder
  |  - Exposes a virtual .exfat block device
  v
g_mass_storage (USB gadget)
  |
  v
PS5 (ShadowMountPlus)
```

### Implementation Approach

1. **Virtual exFAT builder**: At startup, scan the extracted game folder, compute total size, create a virtual exFAT filesystem layout (boot sector, FAT, root directory with a single file entry pointing to the game data).

2. **FUSE filesystem**: Implement a FUSE filesystem that serves the virtual exFAT image. Metadata (boot, FAT, root dir) is generated in-memory. Data regions are read from the actual game files on the NAS via NFS.

3. **Loopback + gadget**: Mount the FUSE filesystem, create a loop device from it, bind the loop device to `g_mass_storage`.

### Feasibility Assessment

- **Complexity**: High. Requires implementing a custom FUSE filesystem that speaks exFAT sector-level semantics.
- **Performance**: FUSE adds a kernel-userspace context switch per read. For 1 MiB reads, overhead is ~10-50 us per call. Acceptable for game streaming.
- **Alternative**: Use `mkfs.exfat` to create a real `.exfat` image on the NAS from the extracted folder, then mount it normally. Simpler but requires pre-processing on the NAS.
- **Another alternative**: Use `exfat-fuse` to mount a real exFAT image file. If the NAS already has `.exfat` images, this is unnecessary.

### Architectural Implication

The spec must define a **source abstraction** so the service can accept either:
- A `.exfat` image file (current design), or
- An extracted game folder (future feature).

The source abstraction determines how the file descriptor passed to `g_mass_storage` is obtained:

- `.exfat` image: open the file directly, pass fd to gadget.
- Extracted folder: FUSE virtual filesystem creates a virtual block device, pass that fd to gadget.

**Recommendation:** Defer to v2. The initial architecture should handle `.exfat` images only. The FUSE virtualization layer is a significant complexity addition that should be designed after the core service is working. However, the spec should define the extension point (source provider interface) so the FUSE layer can be added later without re-architecting.

### PS5 Game File Structure (From Research)

From the gist and PS5 hacking scene research:

- PS5 games are dumped as folders named `PPSAxxxxx-app0/` (e.g., `PPSA07631-app0/`).
- Inside: `sce_sys/` directory with `param.sfo`, `icon0.png`, `nptitle.dat`, `npbind.dat`.
- Game data files are large (10-150 GiB per game).
- To create a `.exfat` image: `truncate -s <size>G game.exfat && mkfs.exfat -n "PPSAxxxxx" game.exfat && mount -o loop game.exfat /mnt && rsync -rltDHh --ignore-errors --progress ./PPSAxxxxx-app0/ /mnt/`.
- ShadowMountPlus expects the `.exfat` file at the root of the USB device, not inside any subdirectory.

## Feature 4: Multi-network Load Balancing

### Goals

Aggregate bandwidth across multiple NICs/uplinks; provide resilience to single-link failure; maintain stable latency for game I/O.

### Assumptions

Multiple network interfaces on the SBC (e.g., eth0 + wlan0, or dual 1 GbE). Same backend object accessible via multi-source HTTP mirrors or a single NFS/HTTP origin reachable through multiple routes.

### Modes

- **Multi-source HTTP**: Parallel range requests (`Range: bytes=N-M`) to mirrored endpoints. Each link fetches a different chunk of the same file.
- **Single origin, multi-link**: Multiple TCP connections bound to different interfaces via `SO_BINDTODEVICE`. MPTCP if both endpoints support it (caveat: NFS over MPTCP has compatibility issues; see KB 09).

### Scheduler

Weighted round-robin by EWMA throughput and in-flight bytes. Demote slow or degraded links; promote healthy links. If the remote server is the bottleneck (all links saturate at same throughput), fall back to single best link.

### Striping

Per-range chunking (1-4 MiB) with per-link readahead windows. Failed chunks retried on another link. File offset semantics preserved (no reordering visible to USB gadget layer).

### Health Checks and Failover

Per-link probes (RTT, throughput, error rate). Circuit-breaker per link: fast failover on consecutive failures; cooldown before re-probing. Adaptive path selection: if remote is bottleneck, use single lowest-latency link for game I/O and relegate background cache preloading to higher-latency links.

### Backpressure

Cap in-flight requests per link. Adjust weights based on queue depth and RTT. Throttle USB gadget read responses if all links are saturated.

### Metrics and Telemetry

Per-link throughput, RTT, error rate, circuit-breaker state. Export via Prometheus or equivalent. Surface in service dashboard for debugging.

### Architectural Implication

The spec must define a **link aggregator** layer between `FileBackend` and the transport. `FileBackend.pread()` routes through the aggregator, which selects a link per request. The aggregator is transparent to the USB gadget layer. Initial implementation uses a single link (no aggregator); the interface must be designed so the aggregator can be inserted without changing `FileBackend` callers.

## Relevance to RemotePFS Architecture

All four features share a common architectural requirement: **abstraction layers that separate concerns**.

1. **Metadata preloading** requires the service to understand exFAT on-disk layout and cache metadata regions before the PS5 reads them.
2. **Alternative transports** require a backend abstraction so NFS can be swapped for FTP/HTTP/torrent.
3. **Extracted folder mounting** requires a source abstraction so `.exfat` files can be swapped for extracted folders via FUSE.
4. **Multi-network load balancing** requires a link aggregator abstraction so multiple NICs/uplinks can be combined transparently.

The spec should define these interfaces without implementing them:

```
TransportBackend (abstract)
  - NFSBackend (implemented)
  - FTPBackend (future)
  - HTTPBackend (future)

SourceProvider (abstract)
  - ExfatImageProvider (implemented)
  - ExtractedFolderProvider (future, requires FUSE)

MetadataCache (implemented)
  - Preload exFAT metadata regions at startup
  - Serve metadata reads from RAM
  - Fall through to transport backend for data reads
```

## References

1. exFAT specification: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
2. exFAT on-disk format: https://deepwiki.com/exfatprogs/exfatprogs/6-exfat-on-disk-format
3. PS5 exFAT image conversion: https://gist.github.com/indraAsLesmana/52f1f4d3e12d2f4dca1544166a6f88ec
4. Linux FUSE: https://www.kernel.org/doc/html/next/filesystems/fuse.html
5. File transfer protocol comparison: https://en.wikipedia.org/wiki/Comparison_of_file_transfer_protocols
6. PS5 game dumping: https://wololo.net/2023/12/02/ps5-release-ps5-game-file-dumper-complete-game-dumping-utility/
7. ShadowMountPlus source: `../ShadowMountPlusDocsReview/`
8. NookieAI/PS5-Vault: https://github.com/NookieAI/PS5-Vault
9. Linux network bonding: https://www.kernel.org/doc/html/latest/networking/bonding.html
10. MPTCP documentation: https://www.kernel.org/doc/html/latest/networking/mptcp/index.html
11. Linux traffic control (tc): https://tldp.org/HOWTO/Adv-Routing-HOWTO/lartc.qdisc.classic.html
12. SO_BINDTODEVICE (setsockopt(2)): https://man7.org/linux/man-pages/man2/setsockopt.2.html
13. readahead(2): https://man7.org/linux/man-pages/man2/readahead.2.html
14. madvise(2) / MADV_WILLNEED: https://man7.org/linux/man-pages/man2/madvise.2.html
15. mincore(2): https://man7.org/linux/man-pages/man2/mincore.2.html
16. posix_fadvise(2) / POSIX_FADV_WILLNEED: https://man7.org/linux/man-pages/man2/posix_fadvise.2.html
