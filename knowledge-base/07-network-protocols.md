# 07 — Network Protocols for Low-Latency Remote File Access

> **RemotePFS context:** SBC (4 GB RAM) reads game files (10–100+ GB) from remote NAS
> over 2× bonded 1 GbE, presents them as a local USB block device to PS5. PS5 reads
> sequentially with some random access. Lowest possible latency for block-level reads
> over 1 GbE is the primary goal. Multi-tier cache: RAM (4 GB) → optional local HDD →
> remote NAS. Load balancing: Ethernet (1 Gbps, low latency) for foreground reads,
> Wi-Fi for background/prefetch. Network compression for non-already-compressed data.

---

## 1. Protocol Candidates Overview

| Protocol   | Transport | Access Model  | Linux Kernel Support | Maturity      | Overhead per Request |
|------------|-----------|---------------|----------------------|---------------|----------------------|
| SMB 3.1.1  | TCP/QUIC  | File-level    | ksmbd / Samba         | Very mature   | High (chatty, many round-trips for metadata) |
| NFS v4.1   | TCP       | File-level    | Native (in-kernel)   | Very mature   | Moderate (stateful, fewer round-trips than NFSv3) |
| iSCSI      | TCP       | Block-level   | Native (in-kernel)   | Very mature   | Low–moderate (SCSI CDB in TCP, no file semantics) |
| NBD        | TCP       | Block-level   | Native (in-kernel)   | Mature        | Very low (simple request/response header) |
| Custom UDP | UDP       | Block-level   | Custom (userspace or kernel) | N/A    | Lowest possible (no TCP overhead, no kernel stack traversal if bypassed) |

### Key distinction: File-level vs Block-level

- **File-level (SMB, NFS):** Server interprets a filesystem. Client sends
  `OPEN file → READ offset/length → CLOSE`. Server handles metadata, permissions,
  file locking, directory structure. Every open/read is a protocol round-trip with
  metadata overhead. The client's OS page cache and the server's page cache both
  participate, creating double-caching and coherence overhead.

- **Block-level (iSCSI, NBD, custom):** Server presents a raw block device. Client
  runs its own filesystem on top. Reads are simple `READ(block_offset, length)` with
  no metadata round-trips. No file locking, no permission checks over the wire. The
  block device is accessed as if it were local — exactly the model RemotePFS needs
  (presenting a USB block device to PS5).

**For RemotePFS, block-level access is strongly preferred.** The PS5 sees a block
device; there is no benefit to a file-level protocol's metadata. Block protocols
eliminate per-file open/close/lock round-trips entirely.

---

## 2. Protocol Deep Dive

### 2.1 SMB 3.1.1 (Server Message Block)

**History & versions:**
- SMB 1.0: extremely chatty, 100+ commands, 64 KB block limit, deprecated 2013.
- SMB 2.0 (2006): 19 commands, pipelining, compounding (multiple ops per request),
  durable handles, removed 64 KB block limit. Major chattiness reduction.
- SMB 2.1 (2009): new opportunistic locking (lease) mechanism.
- SMB 3.0 (2012): SMB Direct (RDMA), SMB Multichannel, end-to-end encryption
  (AES-128-CCM), transparent failover, scale-out file server.
- SMB 3.0.2 (2013): SMB1 optionally disabled.
- **SMB 3.1.1 (2015):** Pre-authentication integrity (SHA-512), AES-128-GCM
  encryption, secure dialect negotiation mandatory. Latest and most secure dialect.

**Transport:** TCP port 445. SMB over QUIC (UDP 443) added in Windows Server 2022
for encrypted remote access without VPN.

**Protocol overhead:**
- Each file access requires: negotiate → session setup → tree connect →
  create/open → read → close → tree disconnect. Even with compounding (SMB2+),
  each file open involves multiple round-trips.
- For **sequential large-file reads** (RemotePFS's main workload): after the initial
  open, SMB read requests are relatively efficient with large read sizes (1 MB+
  with SMB 2+). The overhead is dominated by the initial open/close, not the data
  transfer itself.
- **SMB signing** (default in SMB 3.1.1) adds HMAC-SHA-256 overhead per packet.
  **SMB encryption** (AES-128-GCM) adds further per-packet crypto overhead.

**Throughput on 1 GbE:**
- Theoretical max: ~114 MB/s (940 Mbps after TCP/IP overhead).
- SMB 3.1.1 with signing: ~100–110 MB/s achievable on 1 GbE with jumbo frames (MTU 9000).
- SMB encryption enabled: ~80–100 MB/s (AES-GCM crypto overhead on low-power CPUs).
- On an SBC with a weak CPU, SMB signing + encryption overhead can be significant,
  dropping throughput below 80 MB/s.

**SMB Multichannel:** Aggregates multiple NICs. With 2× bonded 1 GbE, SMB
Multichannel can use both NICs for aggregate throughput if they are separate
interfaces (not a LACP bond). If bonded via LACP at L2, SMB sees one interface.
SMB Multichannel requires separate IP addresses per NIC.

**SMB compression (SMB 3.1.1):** Compresses data during transfer. Uses XPRESS
(LZ77), XPRESS Huffman, LZNT1, PATTERN_V1, or LZ4 (Server 2025+). Beneficial for
compressible data but **game files are often already compressed** (PS5 PKG/PFS
format uses internal compression), so SMB compression adds CPU overhead with
little ratio gain. Should be disabled for already-compressed game data.

**Suitability for RemotePFS:**
- ❌ File-level access — unnecessary metadata overhead for block device use case.
- ❌ Signing + encryption overhead on SBC's weak CPU.
- ❌ Requires Samba or ksmbd on the NAS (server-side).
- ✅ Very mature, well-understood, excellent Linux support via cifs-utils/Samba.
- ⚠️ Could work for initial file copy (NAS → SBC local cache), but not ideal for
  latency-sensitive block device reads.

**Verdict:** Not recommended for RemotePFS block device path. Suitable only as
a fallback for file-level prefetch from NAS.

---

### 2.2 NFS v4.1 (Network File System)

**History & versions:**
- NFSv2 (1989, RFC 1094): stateless, UDP only, 8 KB transfer limit, 2 GB file limit.
- NFSv3 (1995, RFC 1813): TCP support, async writes, 64-bit file sizes/offsets.
- NFSv4 (2000, RFC 3010): stateful, integrated security (RPCSEC-GSS),
  firewall-friendly (single port 2049), compound operations (multiple RPCs per
  request).
- **NFSv4.1 (2010, RFC 5661):** Session trunking (multipathing), pNFS (parallel
  NFS — separates metadata from data, allowing direct client-to-data-server access).
- NFSv4.2 (2016, RFC 7862): server-side clone/copy, application I/O advice,
  sparse files, space reservation.

**Transport:** TCP port 2049 (single port, firewall-friendly). NFSv4 is stateful —
maintains open file state, leases, sessions.

**Protocol overhead:**
- NFS uses ONC RPC (SunRPC) with XDR encoding. Each operation has RPC header
  overhead (~40 bytes for RPC + XDR).
- NFSv4.1 uses **compound operations** — multiple ops (OPEN, READ, CLOSE) can be
  batched in a single RPC, reducing round-trips vs NFSv3.
- NFSv4.1 **sessions** provide a stateful session with exactly-once semantics,
  reducing retransmission overhead.
- For sequential reads: NFSv4.1 with `rsize`/`wsize` = 1 MB (default on modern Linux)
  achieves good throughput with minimal round-trips after file is open.
- NFS read-ahead (prefetch) is built into the Linux NFS client — it detects
  sequential access patterns and issues asynchronous read-ahead requests.

**Throughput on 1 GbE:**
- ~100–112 MB/s achievable with `rsize=1048576` and jumbo frames.
- NFS has slightly less per-packet overhead than SMB (simpler protocol, no signing
  by default).
- On SBCs, NFS CPU overhead is lower than SMB (no mandatory signing/encryption).

**pNFS (Parallel NFS):** NFSv4.1's pNFS allows the client to read data directly from
data servers, bypassing the metadata server for bulk data. This is relevant for
clustered storage but **overkill for RemotePFS** (single NAS). pNFS requires
pNFS-enabled server (e.g., NFS-ganesha with pNFS, or GPFS/Lustre).

**Caching integration:**
- NFS client uses the Linux page cache for read caching. `fscache` (FS-Cache)
  can be used to add a persistent local disk cache (cached NFS) — relevant for
  RemotePFS's optional local HDD tier.
- NFS `actimeo` / `noac` controls attribute caching timeout. For a read-only
  game file mount, long cache timeouts are fine.
- NFS `lookupcache` controls directory entry caching.

**Suitability for RemotePFS:**
- ❌ File-level access — same metadata overhead concern as SMB.
- ✅ Lower CPU overhead than SMB on SBC (no mandatory signing/encryption).
- ✅ Native in-kernel Linux client (no userspace daemon needed).
- ✅ fscache support for persistent local HDD cache tier.
- ✅ Built-in read-ahead for sequential access.
- ⚠️ Still requires file-level mount — not a block device.

**Verdict:** Better than SMB for the file-level NAS access path (lower overhead,
native kernel client, fscache for HDD tier). Still not ideal for the block device
path. Recommended for the **NAS → local HDD prefetch** path if file-level access
is needed, but NBD/iSCSI preferred for the hot path.

---

### 2.3 iSCSI (Internet Small Computer System Interface)

**Overview:** Carries SCSI commands (CDBs) over TCP/IP. Presents a remote block
device as a local SCSI disk. Block-level — no filesystem semantics over the wire.
The client (initiator) formats and manages its own filesystem on the LUN.

**Transport:** TCP ports 860 and 3260. iSCSI encapsulates SCSI CDBs in Protocol Data
Units (PDUs), which are carried over TCP.

**Protocol overhead:**
- iSCSI PDU header: 48 bytes (Basic Header Segment). Data follows immediately.
- SCSI READ command CDB: 16 bytes (for READ_16) — very compact.
- Per-read overhead: ~64 bytes header for a request that can carry up to 1 MB of
  data. At 1 MB read size, header overhead is 0.006% — negligible.
- TCP overhead: 20 bytes IP header + 20 bytes TCP header = 40 bytes per segment. With
  jumbo frames (MTU 9000), effective TCP overhead is ~1.6% of payload.
- No file-level metadata round-trips. Block reads are stateless from the protocol's
  perspective — just `READ(lba, length) → data`.

**Throughput on 1 GbE:**
- ~110–114 MB/s achievable with jumbo frames and large I/O sizes.
- Software iSCSI initiator uses host CPU for TCP processing. On a weak SBC CPU,
  TCP interrupt processing can be a bottleneck at line rate.
- iSCSI does not have built-in compression or encryption overhead (unlike SMB 3.1.1
  with mandatory signing). IPSec can be added at the network layer if needed.

**1 GbE configuration best practices:**
1. **Jumbo frames (MTU 9000):** Enable on both NICs and switch. Reduces per-byte
   TCP/IP header overhead by ~5.5×. Critical for iSCSI throughput on 1 GbE.
   Both initiator and target NICs + all switches in path must agree on MTU.
2. **Dedicated VLAN / subnet:** Isolate iSCSI traffic from other network traffic to
   prevent congestion. A dedicated VLAN on the bonded 1 GbE pair is ideal.
3. **Multipathing (MPIO):** Use Linux `multipath` with two iSCSI sessions (one per
   NIC) for load balancing and failover. Round-robin or least-pending I/O path
   selector. This effectively doubles bandwidth if the NAS target has two IPs.
4. **Large queue depth:** Set `queue_depth` appropriately (default 32 on Linux).
   Higher queue depth allows more in-flight I/O, improving latency hiding.
5. **TCP parameters:** `tcp_window_scaling=1`, large socket buffer sizes
   (`net.core.rmem_max`, `net.core.wmem_max`), `tcp_no_metrics_save=1`.
6. **irqbalance / interrupt coalescing:** Enable NIC interrupt coalescing to reduce
   CPU interrupt overhead at the cost of slightly higher latency per packet.

**Block vs file:**
- iSCSI presents a raw block device. The SBC would format it with ext4/exFAT/F2FS
  and present it to PS5 via USB gadget.
- This is the **natural fit** for RemotePFS: the PS5 already expects a block device,
  and iSCSI provides one with minimal protocol overhead.

**Security:** CHAP authentication (cleartext challenge-response, not encrypted).
No built-in encryption. IPSec can encrypt at network layer. For a LAN deployment
behind a firewall, cleartext iSCSI is acceptable.

**Suitability for RemotePFS:**
- ✅ Block-level — perfect match for block device presentation to PS5.
- ✅ Low protocol overhead (48-byte PDU header, no metadata round-trips).
- ✅ Native in-kernel Linux initiator (open-iscsi).
- ✅ MPIO for 2× 1 GbE utilization.
- ✅ No mandatory signing/encryption overhead.
- ⚠️ TCP processing on SBC CPU can be a bottleneck at line rate.
- ⚠️ No built-in compression (would need to layer on top or use a compressed
  block device like ZFS with compression on the NAS target side).

**Verdict:** Strong candidate. Native kernel support, low overhead, block-level.
Best "off-the-shelf" protocol for RemotePFS block device path.

---

### 2.4 NBD (Network Block Device)

**Overview:** Linux NBD protocol (originally 1997, formally documented 2011).
Forwards a block device from a server to a client over TCP. The client kernel
driver creates a virtual block device (`/dev/nbd0`); all I/O to that device is
forwarded to the server, which accesses storage via a conventional filesystem
interface.

**Architecture:**
- **Client:** In-kernel module (`nbd.ko`). Presents `/dev/nbdN` block device.
  Kernel forwards bio requests to the NBD client, which sends them over TCP.
- **Server:** Userspace daemon (`nbd-server`). Receives requests, reads/writes
  the backing file or device, returns data. Can serve from a file, a partition,
  or a full device.

**Protocol:**
- NBD protocol is very simple: request header (28 bytes for NBD_CMD_READ) +
  data. Response header (4 bytes reply + 4 bytes error) + data.
- Commands: `NBD_CMD_READ`, `NBD_CMD_WRITE`, `NBD_CMD_FLUSH`, `NBD_CMD_TRIM`,
  `NBD_CMD_BLOCK_STATUS`.
- No authentication, no encryption, no multipathing, no session management.
  The simplest possible block-over-TCP protocol.
- Connection setup: client connects to server TCP port, server sends an
  8-byte magic + export info, then request/response loop begins.

**Protocol overhead:**
- Request header: 28 bytes (magic, flags, handle, offset, length, command).
- For a 1 MB read: 28 bytes request + 4+4 bytes response header = 36 bytes
  overhead per 1 MB = 0.003%. Even lower than iSCSI.
- No TCP-level overhead beyond standard (40 bytes IP+TCP per segment).
- No SCSI CDB encoding overhead (iSCSI's 48-byte PDU header is larger than
  NBD's 28-byte request).

**Throughput on 1 GbE:**
- NBD can saturate 1 GbE (~112 MB/s) with large I/O sizes.
- The userspace `nbd-server` is single-threaded per export by default but
  can be configured for multi-connection. For sequential reads, a single
  connection saturates 1 GbE easily.
- Kernel NBD client has minimal overhead — bio requests are forwarded directly
  to the TCP socket. The kernel block layer handles merging, read-ahead, and
  queuing.

**Advantages over iSCSI for RemotePFS:**
- Simpler protocol, lower per-request overhead (28 vs 48 bytes).
- No SCSI complexity — just block reads/writes.
- Userspace server can be customized (e.g., add compression, caching,
  prefetch logic directly in the server).
- The NBD server can be a thin custom daemon that reads from NAS files and
  serves block data — this is the **natural integration point for RemotePFS**.

**Disadvantages vs iSCSI:**
- No multipathing (single TCP connection per NBD device). To use 2× 1 GbE,
  would need two NBD exports + `dm-multipath` or `md` (RAID0/mirror) on top.
- No standard security (no CHAP). Must rely on network isolation.
- No built-in flow control beyond TCP. If the server can't keep up, TCP
  backpressure handles it.
- Less mature ecosystem than iSCSI (fewer management tools, less enterprise
  adoption).

**NBD with multi-connection (Linux 5.10+):**
- Modern Linux NBD client supports multiple connections to the same export
  (`nbdcfi` / `nbd-client -N`). The kernel can distribute I/O across
  connections for parallelism.
- However, for sequential reads, a single connection with large read-ahead
  is sufficient to saturate 1 GbE.

**Caching integration:**
- NBD client uses the Linux block layer's read-ahead (`read_ahead_kb` sysfs
  parameter). Setting it high (e.g., 4096 KB) improves sequential read
  throughput by prefetching.
- The block device can be fronted by `dm-cache` or `bcache` for the
  optional local HDD cache tier — exactly the RemotePFS multi-tier model.
- The userspace NBD server can implement its own RAM cache for hot blocks.

**Suitability for RemotePFS:**
- ✅ Block-level — perfect for block device presentation.
- ✅ Lowest protocol overhead of any standard protocol.
- ✅ Customizable server — can integrate compression, RAM cache, NAS prefetch.
- ✅ Native in-kernel Linux client.
- ✅ Works with `dm-cache` / `bcache` for multi-tier caching.
- ⚠️ No multipathing (but can be worked around with dm-multipath or custom server).
- ⚠️ No security (network isolation required).

**Verdict:** Best fit for RemotePFS. The NBD server can be a custom daemon that
integrates the multi-tier cache (RAM → HDD → NAS) and load balancing (Ethernet
for foreground, Wi-Fi for prefetch) directly. The NBD client is in-kernel, giving
the lowest latency path from PS5 USB read → block device → network → NAS.

---

### 2.5 Custom UDP-Based Protocol

**Rationale:** TCP adds overhead that is unnecessary for a controlled, single-link,
low-latency storage path on a LAN:

- **TCP handshake:** 3-way handshake (~1.5× RTT) before first data. For a
  long-lived connection (RemotePFS), this is amortized away — not a major factor.
- **TCP ACK overhead:** TCP sends ACKs for received data (delayed ACKs, every other
  segment by default). On 1 GbE with jumbo frames, this is ~7800 ACKs/sec at line
  rate. Each ACK is a small packet that consumes CPU + bus cycles.
- **TCP head-of-line blocking:** If a single segment is lost, TCP stalls all
  subsequent data until retransmission. On a clean LAN (0.001% packet loss), this
  is rare but the stall latency is ~RTT + retransmit_timeout. For block reads,
  head-of-line blocking is rarely an issue on a healthy LAN.
- **TCP congestion control:** Slow start + AIMD can throttle throughput after a
  single loss event. On a dedicated LAN with no congestion, this is suboptimal —
  the protocol "leaves bandwidth on the table" after any minor packet loss.
- **Kernel socket traversal:** Even with TCP, data goes: app → kernel socket
  buffer → NIC. A custom UDP protocol can use `recvmmsg`/`sendmmsg` for batched
  syscalls, or `AF_XDP` / `DPDK` for kernel bypass, eliminating the kernel
  socket traversal entirely.

**Custom UDP protocol design for RemotePFS:**
```
Request:  [magic(4)] [request_id(4)] [cmd(1)] [offset(8)] [length(4)]  = 21 bytes
Response: [magic(4)] [request_id(4)] [status(1)] [data...]             = 9 bytes + data
```
- No connection setup — fire-and-forget request, server responds to source IP/port.
- Request ID for matching responses (idempotent retransmit on timeout).
- No flow control needed — the server knows the client's read pattern is
  sequential and can pre-push data (server-side prefetch).
- Jumbo frames (MTU 9000) allow ~8960 bytes payload per UDP datagram.
- For a 1 MB read: 1 request (21 bytes) + 112 response datagrams (each ~8960
  bytes payload + 9 bytes header) = ~1005 bytes total overhead per 1 MB = 0.1%.

**Kernel bypass options:**
- **AF_XDP (XDP sockets):** Linux 4.18+. Zero-copy packet processing in a
  userspace BPF program attached to the NIC. Very low latency, no syscall
  per packet. Suitable for custom UDP protocol on SBC.
- **DPDK:** Full kernel bypass, requires hugepages, dedicated NIC port. Heavy
  for an SBC but possible. Overkill for RemotePFS.
- **io_uring:** Linux 5.1+. Async I/O for sockets — batched send/recv with
  minimal syscall overhead. Not kernel bypass but dramatically reduces
  per-syscall overhead. Good middle ground.

**Can it beat SMB/NFS for sequential game file reads?**
- **Against SMB 3.1.1:** Yes, decisively. SMB's per-file open/close/metadata
  overhead is irrelevant for a custom block protocol. No signing/encryption
  overhead. On an SBC, the CPU savings from not doing SMB signing + AES-GCM
  encryption alone could be 10–30% of CPU time at line rate.
- **Against NFS v4.1:** Yes, but by a smaller margin. NFSv4.1 is efficient
  for sequential reads after file open, but still has ONC RPC overhead (XDR
  encoding, compound operations, session management). A custom UDP protocol
  eliminates all of this. The margin is maybe 5–15% throughput improvement
  and lower latency on a weak SBC CPU.
- **Against iSCSI:** Marginal improvement. iSCSI is already block-level and
  efficient. The custom UDP protocol saves the TCP ACK overhead and SCSI
  CDB encoding (~48 bytes per PDU vs ~21 bytes per custom request). On 1 GbE,
  the throughput improvement is ~5–10%. The latency improvement is more
  significant: no TCP retransmit stall, no congestion control backoff, no
  ACK processing. On a clean LAN, latency is dominated by wire propagation
  (~0.5 ms for 100m CAT6) + NIC processing + kernel stack traversal. A
  custom UDP protocol with AF_XDP or io_uring can shave 20–50 µs per
  request by avoiding the TCP stack.
- **Against NBD:** Similar to iSCSI — marginal throughput improvement, more
  significant latency improvement. NBD over TCP has the same TCP overhead.
  A custom UDP protocol with the same request/response model but no TCP
  overhead can save ~5–10% throughput and reduce per-request latency by
  avoiding TCP ACK processing and head-of-line blocking.

**Practical concern:** On 1 GbE, the bottleneck is wire speed (114 MB/s), not
protocol overhead. The throughput difference between any protocol is <10%.
The **latency** difference matters more: every µs saved in protocol processing
is a µs the PS5 doesn't wait for data. For a sequential read workload, the
first read of a new file/region incurs the full RTT; subsequent reads are
prefetched. Protocol latency affects the first-read latency and the
prefetch window efficiency.

**Load balancing with UDP:**
- A custom protocol can trivially send foreground reads over Ethernet (low
  latency) and background prefetch over Wi-Fi — just use two sockets on two
  interfaces. No multipathing complexity.
- TCP multipathing (iSCSI MPIO, SMB Multichannel) requires both paths to be
  TCP and have matching MTU. A custom UDP protocol can use heterogeneous
  transports with different latency/throughput characteristics.

**Reliability:**
- UDP is unreliable — packets can be lost, reordered, or duplicated. For a
  block read protocol, this is manageable: the client sends a request with
  a request_id, the server responds with the same request_id. If the response
  doesn't arrive within a timeout, the client retransmits. Idempotent reads
  make this safe.
- On a clean LAN (0.001% or less packet loss), retransmits are rare. The
  timeout can be aggressive (1–2 ms) since RTT on a LAN is < 1 ms.
- For writes (less relevant for RemotePFS read-only use case), need
  write-ahead logging or sync writes to handle retransmit safety.

**Suitability for RemotePFS:**
- ✅ Lowest possible latency (no TCP overhead, potential kernel bypass).
- ✅ Simplest protocol — can integrate caching, load balancing, compression
  directly.
- ✅ Heterogeneous transport (Ethernet + Wi-Fi) trivial.
- ⚠️ Must implement reliability (retransmit, dedup) — more dev effort.
- ⚠️ No ecosystem support — debugging tools, monitoring, etc. must be custom.
- ⚠️ On 1 GbE, throughput gain is marginal (<10%); latency gain is more
  significant but still small (<50 µs per request).

**Verdict:** Theoretically optimal for latency, but the practical gain over
NBD on 1 GbE is small (<50 µs per request, <10% throughput). The
development effort (reliability, debugging, testing) may not justify the
gain. **Recommended approach: use NBD with a custom server backend that
implements the caching/compression/prefetch logic.** If latency measurements
show NBD's TCP overhead is a bottleneck, a custom UDP protocol layered on
the same server logic is a natural evolution.

---

## 3. SMB Direct (SMB over RDMA) — Feasibility on 1 GbE

### What is SMB Direct?
SMB Direct uses RDMA (Remote Direct Memory Access) to transfer data directly
from the server's memory to the client's memory, bypassing the CPU and OS
network stack entirely. Zero-copy, zero-CPU data transfer.

### Requirements
- **RDMA-capable NIC:** Three types exist:
  - **iWARP:** RDMA over TCP/IP. Works on standard Ethernet. (Deprecated —
    Mellanox/iWARP vendors have largely moved to RoCE.)
  - **InfiniBand:** Dedicated fabric, not Ethernet. Highest performance but
    requires InfiniBand switches and HCAs.
  - **RoCE (RDMA over Converged Ethernet):** RDMA over standard Ethernet.
    RoCEv2 encapsulates RDMA in UDP/IP. Requires a **RoCE-capable NIC**
    (e.g., Mellanox ConnectX-3+, Broadcom, Chelsio).
- **Both client and server** must have RDMA-capable NICs.
- **No minimum link speed** is defined by the protocol, but in practice:
  - RoCE is typically deployed on **10 GbE or faster** (25/40/100 GbE).
  - 1 GbE RoCE NICs **exist but are extremely rare** — the market for 1 GbE
    RDMA is negligible. RoCE NICs are almost universally 10+ GbE.
  - InfiniBand is always ≥10 Gbps (typically 25/40/56/100/200 Gbps).
  - iWARP NICs at 1 GbE existed historically (Chelsio, NetXen) but are
    discontinued and unsupported.

### Feasibility on 1 GbE
1. **Hardware availability:** There are essentially no current 1 GbE RoCE
   NICs on the market. RoCE NICs start at 10 GbE (Mellanox ConnectX-3,
   CX-4, CX-5, CX-6). Finding a 1 GbE RDMA NIC for an SBC is not practical.
2. **Cost:** Even if a 1 GbE RoCE NIC existed, it would cost more than a
   10 GbE RoCE NIC due to lack of volume. SBCs (Radxa, Raspberry Pi, etc.)
   typically do not have PCIe slots for RoCE NICs — most use USB or SoC-
   integrated Ethernet.
3. **SBC compatibility:** The SBC's SoC Ethernet controller is almost
   certainly not RDMA-capable. Adding a RoCE NIC requires a PCIe slot
   (not available on most SBCs) or a USB-to-RoCE adapter (does not exist
   commercially).
4. **Benefit at 1 GbE:** Even if RDMA were available on 1 GbE, the benefit
   is CPU offload, not throughput (throughput is capped by 1 GbE wire
   speed). On an SBC with a weak CPU, CPU offload could help, but the
   cost/complexity of adding RoCE hardware is not justified at 1 GbE
   speeds. The CPU overhead of TCP on 1 GbE is manageable even on weak
   SBCs (~10–20% of a single core at line rate with jumbo frames).

### Conclusion
**SMB Direct (RDMA) is not feasible on 1 GbE for RemotePFS.** No practical
hardware exists. If 10+ GbE were available, SMB Direct would be excellent
for CPU offload and latency reduction, but at 1 GbE the TCP overhead is
manageable and RDMA hardware is unavailable.

---

## 4. Protocol Comparison Summary

| Dimension              | SMB 3.1.1          | NFS v4.1           | iSCSI               | NBD                  | Custom UDP           |
|------------------------|--------------------|--------------------|---------------------|----------------------|----------------------|
| Access model           | File-level         | File-level         | Block-level         | Block-level          | Block-level          |
| Transport              | TCP (or QUIC)      | TCP                | TCP                 | TCP                  | UDP                  |
| Per-request header     | ~100+ bytes (SMB2) | ~60 bytes (RPC+XDR)| 48 bytes (PDU)      | 28 bytes (request)   | ~21 bytes            |
| Metadata round-trips   | Many (open/close)  | Moderate (compound)| None (block)        | None (block)         | None (block)         |
| Signing overhead       | Yes (mandatory)    | No (optional)      | No (CHAP only)     | No                   | No                   |
| Encryption overhead    | Yes (AES-128-GCM)  | No (optional)      | No (IPSec optional) | No                   | No                   |
| Multipathing           | SMB Multichannel   | Session trunking   | MPIO (native)       | Multi-conn (5.10+)   | Custom (trivial)     |
| 1 GbE throughput       | ~90–110 MB/s       | ~100–112 MB/s      | ~110–114 MB/s       | ~110–114 MB/s        | ~110–114 MB/s        |
| Latency (first read)   | High (open + read) | Moderate (open)    | Low (block read)    | Very low (block read)| Lowest (no TCP)      |
| Caching integration    | OS page cache      | fscache + page cache | OS page cache + bcache/dm-cache | bcache/dm-cache + custom server | Full custom         |
| Compression            | SMB compression    | No                 | No (ZFS on target)  | Custom server        | Custom               |
| Linux kernel support   | Samba / ksmbd      | Native             | Native (open-iscsi) | Native (nbd.ko)      | Custom               |
| SBC CPU overhead       | High (signing+crypto)| Low–moderate     | Moderate (TCP)      | Low–moderate (TCP)   | Lowest (no TCP)      |
| Maturity               | Very mature        | Very mature        | Very mature         | Mature               | N/A (custom)         |
| Dev effort             | None (use Samba)   | None (use NFS)     | None (use open-iscsi)| Low (nbd-server cfg) | High (build from scratch) |

---

## 5. Recommendation for RemotePFS

### Primary protocol: NBD with custom server backend

**Rationale:**
1. **Block-level is the correct model.** RemotePFS presents a USB block device to
   the PS5. The PS5 issues block reads. File-level protocols (SMB, NFS) add
   unnecessary metadata overhead. Block protocols (NBD, iSCSI) map directly to
   the PS5's read pattern.

2. **NBD has the lowest protocol overhead** of any standard block protocol (28-byte
   request header vs iSCSI's 48-byte PDU). On 1 GbE, this doesn't matter for
   throughput (wire speed is the bottleneck), but it matters for latency: fewer
   bytes per request = less serialization/deserialization CPU time.

3. **Custom NBD server = integration point.** The NBD server is a userspace daemon
   that reads from a backing store. For RemotePFS, the backing store is the NAS.
   The server can implement:
   - **Multi-tier cache (RAM → HDD → NAS):** Check RAM cache → check local HDD
     cache (via bcache/dm-cache or manual file-based cache) → fetch from NAS
     (via NFS, SMB, or raw TCP) → return data. The server controls the cache
     hierarchy and can prefetch aggressively.
   - **Load balancing:** Foreground (PS5-initiated) reads go over Ethernet (low
     latency). Background prefetch goes over Wi-Fi (higher latency, no impact on
     foreground). The server manages two NIC sockets and routes requests
     accordingly.
   - **Compression:** If data fetched from NAS is compressible (not game files —
     which are already compressed), compress before storing in RAM cache or
     transmitting over Wi-Fi (bandwidth-limited).
   - **Read-ahead/prefetch:** Detect sequential access pattern, prefetch ahead of
     the PS5's read position by N MB (configurable, e.g., 256 MB) using the Wi-Fi
     path, stage data in RAM cache. When PS5 reads ahead, data is already in RAM —
     zero latency.

4. **NBD client is in-kernel.** The path from PS5 USB read → block device → NBD
   client → TCP socket → NBD server is entirely in-kernel on the client side.
   No userspace context switch for the hot path (client side). The server is
   userspace but can use `io_uring` for efficient I/O.

5. **dm-cache / bcache for HDD tier.** If the local HDD cache is a separate device,
   `bcache` or `dm-cache` can layer it between the NBD device and the RAM cache
   (via the server). Or, simpler: the custom NBD server manages the HDD cache
   directly via `pread`/`pwrite` to a cache file.

### Secondary protocol: iSCSI (fallback)

If the custom NBD server approach proves too complex or has stability issues,
iSCSI is the fallback:
- Same block-level model, native kernel support.
- MPIO for 2× 1 GbE utilization.
- More mature ecosystem (open-iscsi, tgt, LIO target).
- Caching via bcache/dm-cache on the iSCSI device.
- Disadvantage: no custom server logic — caching/prefetch/load-balancing must
  be implemented at the block layer (bcache/dm-cache) rather than in a custom
  server.

### Not recommended for block device path:
- **SMB 3.1.1:** File-level, high overhead, signing/encryption CPU cost on SBC.
- **NFS v4.1:** File-level. Better than SMB but still wrong access model.
- **SMB Direct (RDMA):** Not feasible on 1 GbE (no hardware).

### Protocol for NAS access (server-side):
The NBD server (on the SBC) reads game files from the NAS. This path can use:
- **NFS v4.1** (recommended): Low overhead, native kernel, fscache for HDD-tier
  if needed. Good for sequential reads of large files.
- **SMB 3.1.1** (alternative): If the NAS only exports SMB. Disable signing/
  encryption if possible on the LAN to reduce SBC CPU overhead.
- **Raw TCP / custom** (best, if NAS supports it): If a simple block-serving
  daemon runs on the NAS, the SBC can read game files as raw blocks with zero
  protocol overhead. This is the ultimate optimization but requires NAS-side
  software.

### Custom UDP protocol (future optimization):
If latency measurements show that NBD's TCP overhead is the bottleneck (i.e.,
per-request latency is dominated by TCP ACK processing rather than wire
propagation), a custom UDP protocol can be layered on top of the same server
backend. This is a **future optimization**, not the initial implementation.
The NBD server's caching/prefetch/load-balancing logic is reusable regardless
of transport.

---

## 6. Caching Integration Notes

### Multi-tier cache architecture (NBD server perspective):

```
PS5 USB read → SBC NBD client (kernel) → TCP → SBC NBD server (userspace)
                                                    ↓
                                              [RAM cache: 4 GB]
                                                    ↓ miss
                                              [Local HDD cache]
                                                    ↓ miss
                                              [NAS fetch via NFS/SMB/TCP]
                                                    ↓
                                              Prefetch ahead on Wi-Fi
```

- **RAM cache (4 GB):** LRU cache of recently-read blocks. 4 GB can hold ~40
  blocks of 100 MB each, or ~64K blocks of 64 KB each. For sequential reads,
  a sliding window of ~1–2 GB of read-ahead data fits in RAM. The remaining
  2–3 GB is for the OS, NBD server, and active data.

- **Local HDD cache:** Persistent cache of fetched game data. Survives reboots.
  Populated by background prefetch over Wi-Fi. Size = HDD free space. On
  next access, data is served from HDD (faster than NAS over 1 GbE if the
  HDD is SATA SSD or fast HDD, comparable if it's a slow HDD). `bcache` or
  `dm-cache` can manage this, or the custom NBD server manages it directly.

- **NAS fetch:** The fallback when data is in neither RAM nor HDD cache.
  Fetched via NFS v4.1 (recommended) over the 1 GbE Ethernet path for
  foreground reads, or via Wi-Fi for background prefetch.

### Read-ahead / prefetch strategy:
- **Sequential detection:** NBD server monitors read pattern. If offset
  increases monotonically, switch to prefetch mode: fetch ahead of the
  current read position by N MB (configurable, e.g., 256 MB) using the Wi-Fi
  path (background) or Ethernet (if Wi-Fi is saturated).
- **Random access:** If reads are non-sequential, disable prefetch (random
  access pattern = prefetch wastes bandwidth). Serve from RAM cache only.
- **Game file characteristics:** PS5 game files are typically read
  sequentially (streaming assets) with some random access (loading specific
  assets). A hybrid approach: prefetch sequentially but keep random-access
  blocks in RAM cache.

---

## 7. Load Balancing Notes

### Ethernet (1 GbE, 2× bonded) — foreground path:
- 2× 1 GbE bonded (LACP/802.3ad) provides 2 Gbps aggregate but a single
  TCP connection is limited to 1 Gbps (LACP hashes by flow).
- For the NBD TCP connection: a single connection = 1 Gbps. To use both
  NICs, either:
  - Two NBD connections (one per NIC IP) + dm-multipath, or
  - A custom server with two TCP sockets (one per NIC IP), distributing
    requests across both.
- The bonded pair can be used as a single interface for simplicity; the
  custom server can open two sockets on two IPs for parallelism.

### Wi-Fi — background/prefetch path:
- Wi-Fi (802.11ac/ax) provides 400–1200 Mbps theoretical, ~200–400 Mbps
  practical at range. Sufficient for background prefetch.
- Wi-Fi has higher latency (~2–10 ms) than Ethernet (~0.5 ms). Not suitable
  for foreground reads but fine for prefetch (data is ready before PS5
  needs it).
- The NBD server uses Wi-Fi only for prefetch requests that don't need
  low latency. The Ethernet path handles foreground (latency-sensitive)
  reads.

### Traffic shaping:
- Foreground (Ethernet): NBD client reads → server checks cache → if miss,
  fetch from NAS over Ethernet with priority.
- Background (Wi-Fi): Server-initiated prefetch → fetch from NAS over Wi-Fi
  → stage in RAM/HDD cache.
- The server must ensure Wi-Fi prefetch doesn't block or delay Ethernet
  foreground fetches. Thread pools or async I/O (`io_uring`) handle this.

---

## 8. 1 GbE Configuration Best Practices (All Protocols)

1. **Jumbo frames (MTU 9000):** Enable on SBC NIC, NAS NIC, and all switches.
   Reduces TCP/IP header overhead by ~5.5× and interrupt rate by ~5.5×.
   Critical for any protocol on 1 GbE.

2. **TCP tuning:**
   ```
   net.ipv4.tcp_window_scaling = 1
   net.core.rmem_max = 16777216
   net.core.wmem_max = 16777216
   net.ipv4.tcp_rmem = 4096 87380 16777216
   net.ipv4.tcp_wmem = 4096 65536 16777216
   net.ipv4.tcp_no_metrics_save = 1
   net.ipv4.tcp_congestion_control = cubic  # or bbr if available
   ```

3. **Interrupt coalescing:** Enable on SBC NIC (`ethtool -C eth0 rx-usecs 50`).
   Reduces CPU interrupt overhead at cost of ~50 µs added latency per packet.
   Acceptable for sequential reads; may hurt random-access latency.

4. **IRQ affinity / RPS:** Pin NIC IRQs to specific cores. Enable RPS
   (Receive Packet Steering) to distribute softirq across cores. On a
   multi-core SBC, this prevents one core from being saturated by network
   softirq.

5. **Socket buffer sizes:** Large socket buffers (16 MB) prevent TCP
   flow control from throttling throughput when the receiver is slow to
   process (e.g., during cache miss + NAS fetch).

6. **Read-ahead (`blockdev --setra`):** For NBD/iSCSI block devices, set
   `read_ahead_kb` to 4096+ for sequential read workloads. The kernel
   prefetches into page cache.

---

## 9. Sources

- [Wikipedia: iSCSI](https://en.wikipedia.org/wiki/ISCSI) — iSCSI protocol,
  initiator/target/LUN, security, implementations.
- [Wikipedia: Network Block Device](https://en.wikipedia.org/wiki/Network_block_device)
  — NBD protocol, architecture, client/server model.
- [Wikipedia: Server Message Block](https://en.wikipedia.org/wiki/Server_Message_Block)
  — SMB history, versions (1.0 → 3.1.1), SMB2 compounding/pipelining, SMB Direct,
  SMB Multichannel, SMB over QUIC.
- [Wikipedia: Network File System](https://en.wikipedia.org/wiki/Network_File_System)
  — NFS history, v2/v3/v4/v4.1/v4.2, pNFS, session trunking.
- [Microsoft Learn: SMB features](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-feature-descriptions)
  — SMB Direct (RDMA) requirements (iWARP, InfiniBand, RoCE), SMB compression
  algorithms, SMB encryption (AES-128-GCM/CCM), SMB over QUIC.
- [StarWind: What is iSCSI](https://www.starwindsoftware.com/blog/what-is-iscsi/)
  — iSCSI performance, jumbo frames, multipathing, iSER (RDMA), vs Fibre Channel.
- [Enterprise Storage Forum: What is iSCSI](https://www.enterprisestorageforum.com/hardware/what-is-iscsi-and-how-does-it-work/)
  — iSCSI PDU structure, performance on various Ethernet speeds, limitations.
