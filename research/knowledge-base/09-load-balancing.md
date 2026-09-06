# 09 - Load Balancing Ethernet + WiFi

## Source

- Linux bonding documentation: https://www.kernel.org/doc/Documentation/networking/bonding.txt
- MPTCP (Multipath TCP): https://www.mptcp.dev/
- Linux routing policy: iproute2 documentation
- tc (traffic control): https://man7.org/linux/man-pages/man8/tc.8.html
- ethtool: https://man7.org/linux/man-pages/man8/ethtool.8.html
- Researched: 2026-09-05

## Executive Summary

Cubie A7S has both Gigabit Ethernet and WiFi 6 (802.11ax). Ethernet serves foreground game reads (low latency, consistent). WiFi 6 handles background tasks: prefetching, NAS management traffic, system updates. Linux bonding with LACP (mode 4) aggregates the NAS's 2x1GbE ports. Policy-based routing directs traffic by type. MPTCP enables simultaneous use of both interfaces for a single connection if needed.

## Hardware Capabilities

### Cubie A7S Network Interfaces

| Interface | Type | Speed | Latency | Reliability |
|-----------|------|-------|---------|-------------|
| eth0 | Gigabit Ethernet | 1 Gbps | <1 ms | Excellent |
| wlan0 | WiFi 6 (802.11ax) | Up to 600 Mbps typical | 2-10 ms | Variable |
| 2x1 GbE (NAS) | Bonded Ethernet | ~2 Gbps aggregate | <1 ms | Excellent |

### NAS Side (Synology DS224+)

The NAS has 2x1GbE ports. Linux bonding on the SBC does not directly control the NAS. Options:

1. **LACP (mode 4 / 802.3ad)**: Requires managed switch supporting LACP, but provides true bandwidth aggregation.
- All links in the aggregation group must have the same speed and duplex (both 1 Gbps full-duplex). Mixing speeds will cause LACP to refuse to aggregate.
2. **Balance-xor (mode 2)**: No switch support needed. Traffic distributed by MAC hash.
3. **Round-robin (mode 0)**: Transmit packets round-robin across both interfaces. Can cause out-of-order delivery.

For a simple home network without a managed switch:
- **Use balance-xor (mode 2) on the NAS** (configurable in Synology DSM).
- Or **configure NAS with one port dedicated to SBC, one to rest of LAN**.
- Or **use NFS nconnect=4 on the SBC** to multiply TCP sessions (kernel distributes across both NAS IPs if using multipath).

## Traffic Segmentation

### Policy-Based Routing

Route foreground game reads over Ethernet (eth0), background traffic over WiFi (wlan0):

```bash
# Add a routing table for WiFi traffic
echo "100 wifi" >> /etc/iproute2/rt_tables

# Route management / prefetch traffic via wlan0
ip rule add fwmark 1 table wifi priority 100
ip route add default via 192.168.1.1 dev wlan0 table wifi

# Default route stays via eth0 for game reads
ip route add default via 192.168.1.1 dev eth0

# Mark-specific routing with iptables:
# Mark SSH and prefetch traffic (port 22222) for WiFi
iptables -t mangle -A OUTPUT -p tcp --dport 22222 -j MARK --set-mark 1
ip rule add fwmark 1 table wifi
```

### Traffic Classes

| Traffic Type | Interface | Priority | Protocol |
|-------------|-----------|----------|----------|
| Game reads (foreground) | eth0 | High | NFS (port 2049) |
| Prefetch reads | wlan0 | Low | NFS |
| NAS management | wlan0 | Low | HTTP/HTTPS |
| System updates | wlan0 | Lowest | HTTP/HTTPS/FTP |
| USB gadget control | Local | N/A | configfs sysfs |

## MPTCP (Multipath TCP)

MPTCP enables a single TCP connection to use multiple paths simultaneously:

```bash
# Enable MPTCP (if kernel supports it)
sysctl net.mptcp.enabled=1

# NFS uses kernel sockets, not userspace sockets. Standard MPTCP userspace
# tooling (mptcpize, LD_PRELOAD) cannot enable MPTCP for NFS. Kernel-level
# MPTCP for NFS would require kernel patches or explicit IPPROTO_MPTCP socket
# creation in the NFS client code, which is not available in mainline kernels.
# MPTCP is NOT applicable to NFS without kernel modifications.
#
# MPTCP also requires both endpoints to support it. If the NAS kernel lacks
# MPTCP, connections silently fall back to plain TCP with no multipath benefit.
# Verify Synology DSM kernel MPTCP support before attempting.

# Experimental; not recommended as primary strategy
```

**MPTCP for RemotePFS**: Not applicable to NFS without kernel modifications. NFS `nconnect=4` achieves similar parallelism without MPTCP complexity. Use MPTCP only if single-session parallelization is needed and both endpoints support it.

## NAS Link Aggregation Configuration

### Option A: LACP (requires managed switch)

```bash
# On Synology DSM:
# Control Panel > Network > Network Interface > Bond
# Mode: IEEE 802.3ad (LACP)
```

SBC side: No LACP needed on SBC (single eth0). NFS `nconnect=4` opens 4 parallel TCP connections, which LACP can distribute across both NAS links via the switch's hash distribution. Distribution depends on switch hash policy; L4 (src+dst port) hashing recommended to minimize collisions. With nconnect=4 and 2 links, some unevenness is possible but statistically unlikely with L4 hashing.

### Option B: Balance XOR (no managed switch needed)

```bash
# On Synology DSM:
# Mode: Balance XOR (Layer 2+3)
```

Packets distributed by MAC/IP hash. Multiple TCP sessions from `nconnect` benefit from distribution.

### Option C: Two Separate IPs (simplest, most reliable)

Assign each NAS NIC a different IP:
- eth0: 192.168.1.100
- eth1: 192.168.1.101

```bash
# On SBC - mount two NFS exports over different paths:
mount -t nfs -o nfsvers=4.1,nconnect=2,rsize=1048576 192.168.1.100:/volume1/games /mnt/nas-games-1
mount -t nfs -o nfsvers=4.1,nconnect=2,rsize=1048576 192.168.1.101:/volume1/games /mnt/nas-games-2
```

RemotePFS service stripes reads across both mounts for ~2 Gbps aggregate.

## QoS / Traffic Control

Prioritize game read traffic over background:

```bash
# Rate-limit background WiFi traffic to 100 Mbps
tc qdisc add dev wlan0 root handle 1: htb default 12
tc class add dev wlan0 parent 1: classid 1:1 htb rate 100mbit ceil 600mbit
tc class add dev wlan0 parent 1: classid 1:12 htb rate 100mbit ceil 600mbit

# Mark game read traffic (NFS port 2049) with DSCP CS6
# SBC is NFS client: match --dport 2049 (outbound to NAS), not --sport
iptables -t mangle -A OUTPUT -p tcp --dport 2049 -j DSCP --set-dscp-class CS6

# Map DSCP to HTB class via tc filter
tc filter add dev wlan0 parent 1: protocol ip prio 1 u32 match ip dscp 48 flowid 1:1

## Recommended Configuration for RemotePFS

```
[PS5] <--USB 3.1 Gen2--> [Cubie A7S]
                              |
                    eth0: 1 GbE (foreground game reads)
                              |
                    +---------+---------+
                    |                   |
              NAS eth0            NAS eth1
              192.168.1.100       192.168.1.101
                    | (bonded or separate)
                    |
              [Synology DS224+]

                    wlan0: WiFi 6 (background)
                         |
                    (prefetch, mgmt)
```

**SBC routing:**
- eth0: 192.168.1.200, default route for NFS (port 2049).
- wlan0: 192.168.1.201, route for background traffic.
- NFS mounted via eth0 using `nconnect=4`.
- If NAS has two IPs: mount both, stripe reads in RemotePFS service.

## Relevance to RemotePFS

1. **Foreground/background split**: Ethernet = game reads, WiFi = prefetch + management.
2. **NFS nconnect=4** multiplies TCP sessions for parallel throughput across bonded NAS links.
3. **policy-based routing** isolates traffic by type.
4. **TC rate limiting** prevents background traffic from starving game reads.
5. **NAS bonding** handled in Synology DSM UI; SBC only needs aggressive NFS nconnect.
6. **No MPTCP needed** for initial implementation; revisit if profiling shows need.

## References

1. Linux bonding: https://www.kernel.org/doc/Documentation/networking/bonding.txt
2. MPTCP: https://www.mptcp.dev/
3. iproute2: https://wiki.linuxfoundation.org/networking/iproute2
4. tc: https://man7.org/linux/man-pages/man8/tc.8.html
5. Synology Bonding: https://kb.synology.com/en-global/DSM/help/DSM/AdminCenter/connection_network_linkagg
