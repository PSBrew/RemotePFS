# RemotePFS Knowledge Base - Topic Index

> Use this index for quick orientation on each topic. For deeper research,
> open the linked `<NN>-<slug>.md` file in this directory.

## How to Use

- Start here to find the right topic fast.
- For details, open `<NN>-<slug>.md` in this directory.
- HTML reports were removed; the `.md` files in this directory are authoritative.
- For implementation specs, open `../specs/<NN>-<slug>.md` (from repo root: `specs/`).

---

## 01 - ShadowMountPlus
- What: Automated background auto-mounter for jailbroken PS5; scans USB paths, mounts exFAT/UFS/PFS images, registers titles.
- Relevance: Defines the exact USB presentation contract RemotePFS must satisfy: exFAT partition, 64KB clusters, 512-byte sectors, game files at image root, `/mnt/usbN` scan path.
- Gotchas: 10-second stability wait defers recently-modified sources; 15-second scan interval; read-only mount by default; LVD sector size must match cluster size or mount fails.
- Upstream: https://github.com/drakmor/ShadowMountPlus
- Internal: 01-shadowmountplus.md

## 02 - PS5 USB and exFAT Handling
- What: PS5 kernel USB mass storage detection, exFAT mount behavior, and external storage requirements.
- Relevance: Defines what USB descriptors, partition tables, and filesystem geometry the PS5 expects from a USB mass storage device.
- Gotchas: PS5 may enforce specific cluster sizes; USB enumeration timing matters; external drive format requirements.
- Upstream: https://www.playstation.com (PS5 system software documentation)
- Internal: 02-ps5-usb-exfat.md

## 03 - Radxa Cubie A7S
- What: Pocket-sized SBC with Allwinner A733 octa-core (2x A76 + 6x A55), 3 TOPS NPU, LPDDR5, GbE, WiFi 6.
- Relevance: Hardware platform for RemotePFS; provides USB OTG gadget mode, GbE for NAS access, and RAM for caching.
- Gotchas: Allwinner A733 Linux kernel support status; USB OTG controller type (musb/dwc2/dwc3); 4GB RAM budget for caching vs service.
- Upstream: https://docs.radxa.com/en/cubie/a7s
- Internal: 03-radxa-cubie-a7s.md

## 04 - Synology DS224+ NAS
- What: 2-bay NAS with Intel Celeron J4125, 2GB DDR4, 2x 1GbE LAN, DSM operating system.
- Relevance: Backend storage for game files; 2x bonded 1GbE provides ~2Gbps aggregate throughput; SMB/NFS protocol support.
- Gotchas: 2GB RAM limits NAS-side cache; J4125 AES-NI available for encryption; link aggregation modes affect real-world throughput.
- Upstream: https://www.synology.com/en-us/products/DS224+
- Internal: 04-synology-nas.md

## 05 - exFAT Filesystem Specification
- What: Microsoft exFAT filesystem structure: boot sector, FAT table, directory entries, cluster heap, allocation bitmap.
- Relevance: RemotePFS must emulate a valid exFAT partition on the USB gadget backing store; PS5 validates geometry before mounting.
- Gotchas: Cluster size must be 64KB for PS5 compatibility; sector size 512 bytes; partition alignment matters; UPCASE table required.
- Upstream: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
- Internal: 05-exfat-filesystem.md

## 06 - USB OTG Mass Storage Gadget Emulation
- What: Linux USB gadget framework (configfs) for emulating USB mass storage devices via mass_storage function.
- Relevance: Core mechanism for making the SBC appear as a USB flash drive to the PS5; handles SCSI command emulation and LUN backing.
- Gotchas: Backing store file must report correct virtual size; SCSI READ_CAPACITY must match exFAT geometry; removable media flag; performance tuning (queue depth, max_sectors).
- Upstream: https://docs.kernel.org/usb/gadget_configfs.html
- Internal: 06-usb-otg-gadget.md

## 07 - Network Protocols for Low-Latency File Access
- What: Comparison of SMB 3.1.1, NFS v4.1, iSCSI, NBD, and custom UDP protocols for block-level remote file access.
- Relevance: Determines how the SBC fetches game file blocks from the NAS; protocol choice directly impacts latency and throughput.
- Gotchas: SMB has higher overhead but rich features; NFS lower overhead but stateless; iSCSI/NBD are block-level; custom UDP may win for sequential reads.
- Upstream: Various (SMB3 spec, NFSv4.1 RFC 7530, NBD protocol, iSCSI RFC 3720)
- Internal: 07-network-protocols.md

## 08 - Multi-Tier Caching Strategies
- What: RAM (4GB) -> optional local HDD -> remote NAS cache hierarchy with LRU/LFU/ARC eviction and read-ahead prefetch.
- Relevance: Game files are 10-100+ GB but active reads are sequential; caching hot blocks in RAM reduces NAS round-trips.
- Gotchas: 4GB RAM must be split between service and cache; prefetch must not evict hot blocks; local HDD adds latency but saves bandwidth.
- Upstream: Linux page cache, dm-cache, zRAM documentation
- Internal: 08-caching-strategies.md

## 09 - Load Balancing Ethernet + WiFi
- What: Policy-based routing, LACP bonding, MPTCP, and Linux traffic control for splitting foreground (ethernet) and background (wifi) traffic.
- Relevance: Ethernet (1Gbps, low latency) serves foreground game reads; WiFi 6 handles prefetch/background downloads from NAS.
- Gotchas: WiFi latency is higher and variable; MPTCP support requires kernel config; bonding requires switch support for LACP.
- Upstream: Linux bonding, MPTCP, tc/man pages
- Internal: 09-load-balancing.md

## 10 - Network-Level Compression
- What: zstd, lz4, zlib/deflate, and brotli compression for reducing network transfer size.
- Relevance: Game files may contain uncompressed assets; compression can reduce bandwidth usage and improve effective throughput.
- Gotchas: Already-compressed data (e.g., Oodle/Kraken in game files) wastes CPU with no ratio benefit; compression adds latency for small reads.
- Upstream: zstd, lz4, zlib documentation
- Internal: 10-network-compression.md

## 11 - Latency Optimization Techniques
- What: TCP tuning (BBR, TCP_NODELAY), kernel buffer sizing, zero-copy I/O, interrupt coalescing, CPU affinity, USB transfer tuning.
- Relevance: End-to-end latency from NAS -> network -> SBC cache -> USB -> PS5 must be low enough to avoid game stuttering.
- Gotchas: TCP_NODELAY increases overhead for small packets; BBR may not help on LAN; USB transfer size affects latency/throughput tradeoff.
- Upstream: Linux kernel networking documentation, TCP tuning guides
- Internal: 11-latency-optimization.md

## 12 - MkPFS Conventions and Project Style
- What: PSBrew org coding style, project organization, and documentation conventions from the MkPFS reference project.
- Relevance: RemotePFS must follow the same conventions for consistency: Python 3.11+, uv, Ruff, pytest, Google docstrings, Conventional Commits.
- Gotchas: No em dashes in prose; explicit type annotations on all locals; `PFS` uppercase in class names, lowercase in snake_case; no `from __future__ import annotations` by default.
- Upstream: https://github.com/PSBrew/MkPFS
- Internal: 12-mkpfs-conventions.md

## 13 - Future Features Catalog
- What: Metadata preloading (exFAT hot region pinning), alternative transport protocols (FTP/SFTP/HTTP/BitTorrent), and extracted game folder mounting via FUSE.
- Relevance: Informs architectural extension points (transport backend abstraction, source provider interface, metadata cache layer) so specs can design for future features without implementing them.
- Gotchas: Metadata preload requires parsing exFAT boot sector fields to compute byte ranges; FUSE adds per-read context switch overhead; BitTorrent only viable for multi-SBC mesh; extracted folder mounting requires custom exFAT FUSE filesystem.
- Upstream: exFAT spec (Microsoft), FUSE (kernel.org), PS5 dumping scene (GitHub)
- Internal: 13-future-features.md

## 14 - Docker Deployment on Linux SBCs
- What: Docker image deployment for RemotePFS user space with host-kernel NBD, NFS, ConfigFS, and USB UDC prerequisites.
- Relevance: Evaluates whether Docker improves ARM SBC portability without hiding board-specific USB gadget and kernel requirements.
- Gotchas: `--privileged` exposes broad host authority; `--device` and `CAP_SYS_ADMIN` may still fail for NBD/ConfigFS; Docker Desktop on macOS cannot validate SBC UDC behavior; host cleanup is required after container restart.
- Upstream: https://docs.docker.com/build/building/multi-platform/
- Internal: 14-docker-sbc-deployment.md

---

## Cross-Reference Matrix

| Topic | Depends On | Informs |
|-------|-----------|---------|
| 01-ShadowMountPlus | 02-PS5-USB, 05-exFAT | 04-USB-gadget, all specs |
| 02-PS5-USB-exFAT | 05-exFAT | 01-SMP, 06-USB-gadget |
| 03-RadxA7S | - | 06-USB-gadget, 08-caching, 11-latency |
| 04-SynologyNAS | - | 07-protocols, 08-caching |
| 05-exFAT | - | 02-PS5, 06-USB-gadget |
| 06-USB-gadget | 05-exFAT, 03-RadxA7S | 01-SMP, specs |
| 07-protocols | 04-SynologyNAS | 08-caching, 11-latency |
| 08-caching | 03-RadxA7S, 07-protocols | 11-latency, specs |
| 09-loadbalancing | 03-RadxA7S | 07-protocols, 11-latency |
| 10-compression | 07-protocols | 11-latency |
| 11-latency | 07-protocols, 08-caching, 09-loadbalancing | specs |
| 12-mkpfs | - | all specs, project structure |
| 13-future | 05-exFAT, 07-protocols | specs (extension points) |
| 14-docker-sbc | 03-RadxA7S, 06-USB-gadget, 07-protocols | deployment docs, container packaging |
