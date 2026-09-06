# 11 - Latency Optimization Techniques

## Source

- Linux kernel networking: https://www.kernel.org/doc/Documentation/networking/
- TCP man page: https://man7.org/linux/man-pages/man7/tcp.7.html
- ethtool: https://man7.org/linux/man-pages/man8/ethtool.8.html
- sendfile/splice: https://man7.org/linux/man-pages/man2/sendfile.2.html
- irqbalance, taskset: https://man7.org/linux/man-pages/man1/taskset.1.html
- Linux kernel USB gadget documentation
- Google BBR congestion control
- Researched: 2026-09-05

## Executive Summary

End-to-end latency from the NAS to the PS5 game process must be minimal to avoid loading stutters. The data path has multiple stages: NAS disk read, NAS kernel, network (Gigabit Ethernet), SBC kernel receive, NFS client, page cache lookup, exFAT emulation layer, f_mass_storage buffer, USB bus, PS5 USB host, PS5 exFAT driver, PS5 game read. Each stage adds latency. This article catalogs optimization techniques at each layer, starting with the highest-impact (cheapest) wins.

## Latency Budget (Target)

Goal: Sustained read latency under 10 ms for 4 KB random reads, under 50 ms for the user-perceived loading experience.

```
Stage                         Latency (typical)     Latency (tuned)
─────────────────────────────────────────────────────────────────
NAS disk seek                   5-10 ms              5 ms (SSD)
NAS kernel + NFS server         0.1-0.5 ms           0.1 ms
Network (1 GbE)                 0.1-0.3 ms           0.1 ms
SBC kernel receive + NFS        0.1-0.5 ms           0.1 ms
Page cache lookup               0.001 ms             cache hit: ~0 ms
exFAT emulation layer           0.01-0.1 ms          0.01 ms
f_mass_storage buffer copy      0.01-0.05 ms         0.01 ms
USB 3.1 Gen2 bus                0.01 ms              0.01 ms
PS5 USB host + exFAT driver     0.1-0.5 ms           0.1 ms
PS5 game read                   1-5 ms               1-5 ms
─────────────────────────────────────────────────────────────────
Total (cache miss)              7-17 ms              7-11 ms
Total (cache hit)               1-6 ms               1-3 ms
```

## Layer-by-Layer Optimizations

### 1. NAS Side: Synology DS224+

```bash
# NFS server tuning (/etc/nfs.conf or DSM UI)
nfsd.threads=16               # Increase NFS worker threads

# Note: DS224+ has NO M.2 NVMe slots and NO PCIe expansion. The only way to
# get SSD performance is 2.5" SATA SSDs in the 2 drive bays (reduces storage
# capacity). For HDD-based DS224+, disk seek latency is 5-10 ms.
# async export option risks data loss on power failure; for read-heavy
# workloads, sync (default) is fine. Do not use async.
```

**Disk read latency**: Without SSD, game files on HDD experience 5-10 ms seek. With 2.5" SATA SSDs in drive bays: ~0.1 ms seek, but storage capacity reduced. Consider a NAS model with M.2 slots (e.g., DS423+) for SSD caching without sacrificing drive bays.

### 2. Network: TCP Tuning

```bash
# /etc/sysctl.conf on SBC (Cubie A7S)

# Disable Nagle's algorithm (TCP_NODELAY) for NFS
# NFS already sets TCP_NODELAY internally

# Congestion control: BBR (not critical on LAN, but won't hurt)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Increase socket buffer sizes (reduces stalls on large reads)
net.core.rmem_max = 16777216        # 16 MiB
net.core.wmem_max = 16777216        # 16 MiB
net.ipv4.tcp_rmem = 4096 131072 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.tcp_fastopen = 1    # Client only (SBC is NFS client, not server)

# Disable TCP slow start after idle
net.ipv4.tcp_slow_start_after_idle = 0

# Increase backlog
net.core.netdev_max_backlog = 5000
net.core.somaxconn = 4096
```

### 3. Ethernet: Interrupt Coalescing

```bash
# ethtool tuning for low latency (tradeoff: more CPU for lower latency)
# Default eth0 coalescing parameters are tuned for throughput, not latency

ethtool -C eth0 rx-usecs 8        # Reduce RX coalesce from 20-60 to 8µs
ethtool -C eth0 tx-usecs 8        # Reduce TX coalesce
ethtool -C eth0 rx-frames 1       # Interrupt after 1 frame
ethtool -C eth0 adaptive-rx off   # Disable adaptive (variable latency)
ethtool -C eth0 adaptive-tx off
```

**Tradeoff**: Lower coalesce = lower latency but higher CPU (more interrupts). With A76 cores, this is acceptable.

### 4. CPU Affinity

```bash
# Pin USB gadget interrupt + NFS worker to fast A76 cores
# Pin background tasks to A55 cores

# Identify IRQ numbers
cat /proc/interrupts | grep -E 'eth0|usb|dwc3'

# Pin network IRQ to CPU 0-1 (A76 cores)
echo 3 > /proc/irq/$(cat /proc/interrupts | grep eth0 | awk -F: '{print $1}' | head -1 | xargs)/smp_affinity

# Pin RemotePFS service to A76 cores
taskset -c 0,1 remotepfs-service

# Leave A55 cores (2-7) for background tasks
# irqbalance can be disabled or configured to prefer A55
```

### 5. Kernel-Space I/O (Copy Minimization)

```bash
# f_mass_storage does all I/O in-kernel (no userspace copies)
# but it does NOT use sendfile/splice zero-copy. There is one
# kernel-internal copy: page cache -> f_mass_storage 16 KiB buffer -> USB DMA.
```

**Key insight**: If the backing file for f_mass_storage is on an NFS mount, reads go: NFS page cache -> kernel VFS -> f_mass_storage buffer -> USB. One kernel-internal copy (page cache to 16 KiB buffer). The page cache acts as L1 cache transparently. This is NOT zero-copy (sendfile/splice), but it avoids userspace copies entirely.

### 6. I/O Scheduler

```bash
# Use noop/none scheduler for solid-state backing (eMMC, image file)
# None for NVMe, mq-deadline for SATA SSD
echo none > /sys/block/mmcblk0/queue/scheduler

# If backing file is on ext4 filesystem, use:
echo noop > /sys/block/loop0/queue/scheduler
```

### 7. USB Gadget Tuning

```bash
# Chunk size: 16 KiB (FSG_BUFLEN=16384 in storage_common.h on 6.6 BSP).
# I/O processed in 16 KiB chunks with double buffering; no fixed per-command cap.
# Not tunable via configfs or module parameters. For larger transfers, consider
# f_tcm (target framework) gadget instead, which supports larger transfer sizes.

# Stall enabled (per kernel docs, required for proper SCSI error handling)
echo 1 > /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/stall

# No FUA (skip forced cache flush on every write; safe for read-only LUN)
echo 1 > /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/lun.0/nofua

### 8. Kernel Preemption

```bash
# Use a low-latency kernel (CONFIG_PREEMPT or PREEMPT_RT)
# BSP kernel may not have this — check config
zcat /proc/config.gz | grep CONFIG_PREEMPT   # or: zgrep CONFIG_PREEMPT /proc/config.gz
echo "Note: /proc/config.gz requires CONFIG_IKCONFIG_PROC in kernel"

# If available, enable
# Change kernel command line:
# preempt=full
```

## Monitoring Latency

```bash
# NFS operation latency (per-mount)
cat /proc/self/mountstats
# Shows per-operation counts and latencies

# Network latency to NAS
ping -c 100 192.168.1.100 | tail -1

# USB gadget SCSI command latency
# USB gadget debugging (dynamic_debug, not tracepoints)
echo "file f_mass_storage.c +p" > /sys/kernel/debug/dynamic_debug/control
cat /sys/kernel/debug/dynamic_debug/control | grep f_mass

# Disk I/O latency
iostat -x 1 /dev/loop0
```

## Quick Wins (Highest Impact First)

1. **SSD on NAS** (5-10 ms savings per cache miss) — 2.5" SATA SSDs in DS224+ drive bays (reduces capacity) or NAS model with M.2 slots.
2. **NFS rsize=1048576** (fewer round trips for large sequential reads) — already set.
3. **nconnect=4** (parallel TCP for bonded links) — already set.
4. **Ethernet interrupt coalescing** (0.02-0.05 ms savings per packet) — ethtool -C.
5. **CPU affinity** (prevents cache migration, variable latency) — taskset.
6. **BBR congestion control** (marginal benefit on LAN; may add jitter on sub-ms RTT, profile before committing) — sysctl.
7. **TCP fast open** (eliminates 3-way handshake on reconnect) — sysctl.
8. **Kernel preemption** (depends on BSP kernel config) — check.

## Relevance to RemotePFS

1. **SSD on NAS** is the single highest-impact optimization (eliminates HDD seek times). DS224+ requires 2.5" SATA SSDs in drive bays.
2. **TCP tuning** via sysctl: buffer sizes, BBR, fast open, slow start.
3. **ethtool coalescing** reduces interrupt-driven latency by 20-40 us.
4. **CPU affinity** pins service to fast A76 cores, background to A55.
5. **Kernel-space I/O** — f_mass_storage avoids userspace copies (one kernel-internal copy to 16 KiB buffer).
6. **USB gadget** chunk size 16 KiB (FSG_BUFLEN=16384; not tunable via configfs), stall=1, nofua=1.
7. **Monitor** via mountstats, dynamic_debug, iostat, ping.
8. **Don't over-optimize early**: Profile first, then apply targeted fixes. The initial NFS-based setup may already meet latency budgets.

## References

1. Linux TCP tuning: https://man7.org/linux/man-pages/man7/tcp.7.html
2. ethtool: https://man7.org/linux/man-pages/man8/ethtool.8.html
3. Google BBR: https://github.com/google/bbr
4. sendfile/splice: https://man7.org/linux/man-pages/man2/sendfile.2.html
5. Linux USB gadget: https://docs.kernel.org/usb/mass-storage.html
6. irqbalance: https://github.com/Irqbalance/irqbalance
7. Synology SSD Cache: https://kb.synology.com/en-global/DSM/help/DSM/StorageManager/ssd_cache
