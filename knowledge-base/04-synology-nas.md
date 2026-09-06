# Synology DS224+ NAS — Hardware & Protocol Research

> Research target for RemotePFS project: evaluate DS224+ as backend storage for PS5 game files over SMB/NFS.

---

## 1. Hardware Specifications

### 1.1 CPU

| Field | Value |
|---|---|
| **CPU Model** | Intel Celeron J4125 |
| **Architecture** | 64-bit x86 (Goldmont Plus / Gemini Lake Refresh) |
| **Cores / Threads** | 4 / 4 (no Hyper-Threading) |
| **Base Clock** | 2.0 GHz |
| **Burst Clock** | 2.7 GHz |
| **Cache** | 4 MB L2 (shared) |
| **TDP** | 10 W |
| **Lithography** | 14 nm |
| **Launch Date** | Q4 2019 |
| **Marketing Status** | Discontinued (Intel ARK); still actively sold by Synology |

**Sources:** Intel ARK ([specifications](https://www.intel.com/content/www/us/en/products/sku/197305/intel-celeron-processor-j4125-4m-cache-up-to-2-70-ghz/specifications.html)), TechPowerUp ([J4125](https://www.techpowerup.com/cpu-specs/celeron-j4125.c3280)), Synology DS224+ product page & datasheet.

### 1.2 AES-NI & Hardware Encryption

| Feature | Status |
|---|---|
| **Intel AES-NI** | **Yes** — confirmed on Intel ARK spec page ("Intel AES New Instructions: Yes") |
| **Instruction Set Extensions** | SSE4.2 |
| **Intel Secure Key** | Yes |
| **Intel SGX** | Yes (with Intel ME) |
| **VT-x / VT-d / EPT** | Yes / Yes / Yes |

**AES-NI significance for RemotePFS:** J4125 supports hardware-accelerated AES via AES-NI and SSE4.2. DSM leverages this for:

- **Shared folder encryption** — AES-256 CBC mode
- **Volume encryption** — AES-XTS-Plain64 mode
- **SMB transport encryption** — SMB3 encryption (AES-GCM on capable clients)
- **SSH hardware-accelerated ciphers** — DSM uses SSH hardware accelerated ciphers
- **HTTPS/TLS** — built-in AES-NI hardware encryption engine

AES-NI offloads AES operations to dedicated silicon, preventing CPU bottleneck during encrypted transfers. Without AES-NI, enabling SMB3 encryption or shared-folder encryption on J4125 would cause significant throughput degradation.

**Sources:** Intel ARK (AES-NI: Yes), Synology DSM 7.2 spec page (Security: "Built-in AES-NI hardware encryption engine"), Synology DS224+ product page (Hardware Encryption Engine: checked).

### 1.3 Memory

| Field | Value |
|---|---|
| **Pre-installed** | 2 GB DDR4 non-ECC |
| **Memory Slots** | 1 (SO-DIMM) |
| **Max Memory** | 6 GB (2 GB + 4 GB Synology D4NS01-4G) |
| **Memory Type** | DDR4 non-ECC SO-DIMM, up to 2400 MT/s |

> **Note:** Synology warns non-Synology memory modules may void warranty and affect reliability.

**Source:** Synology DS224+ product page & datasheet.

### 1.4 Storage

| Field | Value |
|---|---|
| **Drive Bays** | 2 x 3.5" SATA HDD or 2.5" SATA SSD |
| **Hot Swappable** | Yes |
| **RAID Types** | Synology Hybrid RAID (SHR), Basic, JBOD, RAID 0, RAID 1 |
| **Max Single Volume** | 108 TB |
| **Max Internal Volumes** | 64 |
| **File Systems** | Internal: Btrfs, ext4; External: Btrfs, ext4, ext3, FAT32, NTFS, HFS+, exFAT |
| **SSD TRIM** | Yes |
| Storage Efficiency | Yes (compression on Btrfs; deduplication requires Synology SSDs and specific NAS models) |

**Source:** Synology DS224+ product page, datasheet, DSM 7.2 spec page.

### 1.5 Networking

| Field | Value |
|---|---|
| **LAN Ports** | 2 x RJ-45 1GbE |
| **MTU Limit** | 1500 (per port) |
| **Wake on LAN/WAN** | Yes |
| **Link Aggregation** | Yes (4 modes — see Section 3) |
| **SMB3 Multichannel** | Yes (x86, DSM 7.1.1+, SMB Service 4.15) |
| **NFS 4.1 Multipathing** | Yes |

**Source:** Synology DS224+ product page ("MTU value of 1GbE LAN port has a limit of 1500"), DSM 7.2 spec page.

### 1.6 External Ports

| Port | Count | Notes |
|---|---|---|
| **USB 3.2 Gen 1** | 2 | One front (with Copy button), one rear |
| **USB Copy** | Yes | Front port supports one-touch copy |

### 1.7 Power & Environment

| Field | Value |
|---|---|
| **Power Supply** | 60 W external adapter |
| **Power Consumption (Access)** | 14.69 W |
| **Power Consumption (HDD Hibernation)** | 4.41 W |
| **AC Input** | 100V-240V, 50/60 Hz single phase |
| **Operating Temp** | 0C to 40C (32F-104F) |
| **Operating Humidity** | 8%-80% RH |
| **Noise Level** | 22 dB(A) |
| **System Fan** | 1 x 92 mm (Cool/Quiet/Full-Speed modes) |

**Source:** Synology DS224+ product page & datasheet.

### 1.8 Physical

| Field | Value |
|---|---|
| **Form Factor** | Desktop |
| **Dimensions (HxWxD)** | 165 x 108 x 232.2 mm |
| **Weight** | 1.3 kg (without drives) |

---

## 2. DSM Software Platform

### 2.1 DSM Overview

DSM (DiskStation Manager) is Synology's Linux-based NAS operating system. Current generation: DSM 7.2 / 7.3 / 7.4 (selectable on spec page). DS224+ ships with DSM 7.x.

### 2.2 Supported File Protocols

| Protocol | Supported | Notes |
|---|---|---|
| **SMB1 (CIFS)** | Yes | Legacy; not recommended for security |
| **SMB2** | Yes | |
| **SMB3** | Yes | DSM's "SMB3" = **SMB 3.1.1** (see note below) |
| **NFSv3** | Yes | |
| **NFSv4** | Yes | TCP only |
| **NFSv4.1** | Yes | Model-dependent; DS224+ supports it |
| **AFP** | Yes | Legacy macOS; deprecated by Apple |
| **FTP** | Yes | FTP, FTPS, SFTP |
| **WebDAV** | Yes | |
| **iSCSI** | Yes | Max 2 targets, 4 LUNs on DS224+ |
| **rsync** | Yes | v3.1.2, SSH encryption supported |

**Critical SMB 3.1.1 note from DSM spec:**

> "The minimum SMB protocol cannot be set to the SMB3. As SMB3 on DSM refers to **SMB 3.1.1**, setting SMB3 as the minimum SMB protocol will prevent client devices supporting earlier SMB3 versions from accessing Synology NAS via the SMB protocol."

This confirms DSM's SMB3 implementation is actually SMB 3.1.1 — the latest version, supporting preauthentication integrity, AES-GCM encryption, and AES-CCM.

### 2.3 SMB Advanced Features

| Feature | Status |
|---|---|
| **SMB3 Multichannel** | Yes (DSM 7.1.1+, SMB Service 4.15, x86 only) |
| **SMB3 Transport Encryption** | Yes (configurable) |
| **Server Signing** | Yes |
| **SMB Durable Handles** | Yes |
| **Opportunistic Locking** | Yes (SMB2 file leasing, SMB3 directory leasing) |
| **Windows ACL** | Yes (up to 200 explicit permissions) |
| **Previous Versions** | Yes (Windows integration) |
| **Large MTU** | Yes |
| **Server-side Copy** | Yes (Windows) |
| **File Fast Clone** | Yes (Btrfs only) |
| **SMB3 Multichannel + Link Aggregation** | **Mutually exclusive** — cannot be enabled concurrently |
| **RDMA** | Not supported |

> **Performance caveat:** "Enabling transport encryption mode or server signing may reduce read/write performance during SMB file transfer." — DSM spec.

### 2.4 NFS Features

| Feature | Status |
|---|---|
| **NFS v2/v3/v4/v4.1** | Yes |
| **NFS 4.1 Multipathing** | Yes |
| **Kerberos Security** | Yes (NFS Kerberized sessions) |
| **Custom Service Ports** | Yes |
| **UDP Packet Size Settings** | Yes |
| **NFSv4 Limitation** | TCP only (no UDP) |

### 2.5 Encryption & Security

| Feature | Mode |
|---|---|
| **Shared Folder Encryption** | AES-256 CBC |
| **Volume Encryption** | AES-XTS-Plain64 |
| **SMB Encryption** | SMB3 transport encryption (AES-GCM/CCM) |
| **FTP over SSL/TLS** | Yes |
| **SFTP** | Yes |
| **rsync over SSH** | Yes |
| **HTTPS** | Configurable cipher suite, TLS 1.1/1.2/1.3 |
| **Firewall** | Yes (GeoIP rules, DDoS protection) |
| **Auto Block** | Yes (login attempt limiting) |
| **Let's Encrypt** | Yes (auto-renewal) |
| **2-Step Verification** | Yes |
| **AES-NI Engine** | Built-in hardware encryption engine |

### 2.6 Concurrent Connections (DS224+ specific)

| Protocol | Max Concurrent |
|---|---|
| SMB/NFS/AFP/FTP | 500 | Total concurrent connections |
| **SMB (FSCT-based)** | 5 connections (Microsoft FSCT HomeFolder test) |

> **Note:** Datasheet states max concurrent SMB/NFS/AFP/FTP connections = 500 with default 2 GB RAM. Expandable to 1,500 with 6 GB RAM installed.

---

## 3. Link Aggregation Modes

DSM supports 4 link aggregation modes for combining the 2 x 1GbE ports:

### 3.1 Mode Summary

| Mode | Switch Requirement | Hashing | Fault Tolerance | Use Case |
|---|---|---|---|---|
| **Adaptive Load Balancing (ALB)** | None (unmanaged switch OK) | Outbound traffic distribution | Yes | Simple setups, no managed switch |
| **IEEE 802.3ad Dynamic (LACP)** | Managed switch with LACP support | L2/L2+L3 hash | Yes | Best for multi-client, standard |
| **Balance XOR** | None | XOR hash | Yes | Basic load distribution |
| **Active/Standby** | None | Active link only, standby failover | Yes | Pure redundancy, no throughput gain |

### 3.2 Adaptive Load Balancing (ALB)

- **No switch support required** — works with any unmanaged switch
- Distributes **outbound** traffic across both links
- Inbound traffic uses standard ARP resolution
- Provides fault tolerance: if one link fails, traffic continues on the other
- Best when no managed switch is available

### 3.3 IEEE 802.3ad Dynamic Link Aggregation (LACP / Balance-TCP)

- **Requires managed switch** with IEEE 802.3ad LACP support
- Both inbound and outbound traffic load-balanced
- If multiple switches used, they must be stackable and properly configured
- Industry standard, best compatibility with enterprise switches
- Provides fault tolerance and maximum aggregate throughput

### 3.4 Balance XOR

- Uses XOR hash on MAC addresses to distribute traffic
- No special switch support required
- Provides fault tolerance

### 3.5 Active/Standby

- Only one port active at a time
- Other port is standby, activated on failure
- No throughput aggregation — pure redundancy
- No switch support required

### 3.6 Link Aggregation vs SMB3 Multichannel

> **Key constraint:** SMB3 Multichannel and Link Aggregation **cannot be enabled concurrently** on DSM.

**SMB3 Multichannel** is a different mechanism:

- Operates at the SMB protocol layer (application level)
- Client discovers multiple NICs on server and opens multiple TCP connections
- Does not require switch support — works with any switch
- Only benefits SMB traffic (not NFS, AFP, etc.)
- Requires x86 platform, DSM 7.1.1+, SMB Service 4.15
- Client OS support: Windows 8+, Windows Server 2012+, macOS 11.3+

**Link Aggregation** operates at the network bond layer (L2):

- Transparent to all protocols (SMB, NFS, AFP, etc.)
- Requires switch support for LACP mode
- Benefits all traffic types

For RemotePFS using NFS or mixed protocols, **Link Aggregation (LACP) is preferred** since SMB3 Multichannel only benefits SMB and is mutually exclusive with Link Aggregation.

---

## 4. Throughput Analysis for RemotePFS

### 4.1 Theoretical 1GbE Limits

| Configuration | Theoretical Max | Practical Max |
|---|---|---|
| **Single 1GbE** | 1,000 Mbps = ~119 MB/s | ~110-115 MB/s |
| **2 x 1GbE bonded (LACP)** | 2,000 Mbps = ~238 MB/s | ~220-230 MB/s (aggregate) |
| **Single client, single stream** | Limited to single link (~115 MB/s) | LACP hashes by flow; single TCP stream = one link |

> **Critical:** LACP does **not** give 2x speed to a single TCP connection. It distributes **multiple flows** across both links. A single file transfer over SMB/NFS will use one link (~115 MB/s max). Multiple concurrent clients/streams can achieve aggregate ~230 MB/s.

### 4.2 SMB Throughput

| Scenario | Expected Throughput |
|---|---|
| **SMB single stream, 1GbE** | ~105-115 MB/s (read), ~100-110 MB/s (write) |
| **SMB with encryption (AES-NI)** | ~90-110 MB/s (AES-NI minimizes overhead) |
| **SMB with encryption (no AES-NI)** | ~50-70 MB/s (CPU bottleneck) |
| **SMB3 Multichannel, 2x1GbE** | ~200-230 MB/s aggregate (multiple TCP connections) |
| **SMB over LACP, single client** | ~105-115 MB/s (single flow on one link) |
| **SMB over LACP, multi-client** | ~220-230 MB/s aggregate across clients |

### 4.3 NFS Throughput

| Scenario | Expected Throughput |
|---|---|
| **NFSv4.1 single stream, 1GbE** | ~100-115 MB/s |
| **NFSv4.1 with Kerberos (krb5p)** | ~60-80 MB/s (encryption overhead) |
| **NFSv4.1 multipathing, 2x1GbE** | ~200-230 MB/s (if client supports multipathing) |
| **NFS over LACP, single client** | ~100-115 MB/s (single flow) |
| **NFS over LACP, multi-client** | ~220-230 MB/s aggregate |

### 4.4 Protocol Overhead Comparison

| Protocol | Overhead | Best For |
|---|---|---|
| **SMB 3.1.1** | Moderate (~2-5% protocol overhead) | Windows clients, mixed environments, file locking |
| **NFS v4.1** | Low (native to Unix, lightweight) | Linux/Unix clients, raw throughput, low-latency |
| **AFP** | High (deprecated, legacy) | Legacy macOS only — avoid |
| **iSCSI** | Low (block-level, no filesystem overhead) | Block storage, VM disks |

### 4.5 CPU Impact

J4125 (4-core, 2.0/2.7 GHz, 10 W TDP) is the bottleneck for encrypted transfers:

| Operation | CPU Impact |
|---|---|
| **Unencrypted SMB/NFS** | Low — ~10-20% CPU on J4125 at ~115 MB/s |
| **SMB3 encryption (AES-NI)** | Low-Moderate — ~20-35% CPU, AES-NI offloads crypto |
| **SMB3 encryption (software)** | High — ~60-80% CPU without AES-NI |
| **Shared folder encryption** | Moderate — ~30-50% CPU with AES-NI |
| **NFS Kerberos krb5p** | Moderate-High — encryption per packet |
| **NFS Kerberos krb5i (integrity only)** | Low-Moderate — integrity check only |
| **NFS Kerberos krb5 (auth only)** | Low — auth at mount, data unencrypted |

---

## 5. RemotePFS Relevance

### 5.1 Use Case: PS5 Game File Backend

DS224+ as backend storage for PS5 game files over network:

| Factor | Assessment |
|---|---|
| **2 x 1GbE bonded** | Aggregate ~230 MB/s across multiple flows; single flow limited to ~115 MB/s |
| **2-bay RAID 1** | Read performance benefits from RAID 1 (dual read); write = single disk speed |
| **SMB 3.1.1** | Full protocol support; AES-NI enables encryption with minimal overhead |
| **NFS v4.1** | Lower overhead than SMB; suitable for Linux-based access patterns |
| **Btrfs** | Data integrity (checksums, self-healing), snapshots for versioning |
| **AES-NI** | Critical for encrypted shared folders — hardware acceleration prevents CPU bottleneck |
| **2 GB RAM** | Constraint — adequate for file serving; expand to 6 GB if running multiple packages |
| **10 W TDP CPU** | Low power, but limited headroom for heavy concurrent encryption + dedup |

### 5.2 Protocol Selection for RemotePFS

**SMB 3.1.1 recommended** for RemotePFS:

- Full Windows ACL support
- AES-NI accelerated encryption
- SMB3 Multichannel can use both 1GbE links (but not with Link Aggregation)
- Server-side copy, durable handles, file leasing
- Directory leasing for faster directory enumeration

**NFS v4.1 alternative** if:

- Client is Linux-based
- Minimal protocol overhead desired
- NFS 4.1 multipathing available on client
- No need for Windows ACL semantics

### 5.3 Link Aggregation Strategy

For RemotePFS with 2 x 1GbE:

1. **If single client, SMB only:** Use **SMB3 Multichannel** (bypasses Link Aggregation, uses both links for SMB)
2. **If single client, NFS or mixed protocols:** Use **LACP (802.3ad)** with managed switch — but single-flow throughput stays ~115 MB/s
3. **If multiple clients:** Use **LACP (802.3ad)** — aggregate throughput scales with concurrent clients
4. **If no managed switch:** Use **Adaptive Load Balancing (ALB)** — no switch support needed, outbound load balancing + failover
5. **If pure redundancy needed:** Use **Active/Standby** — no throughput gain, but link failover

### 5.4 Bottleneck Analysis

| Component | Bottleneck? | Limit |
|---|---|---|
| **1GbE links (2x)** | Yes — primary bottleneck | ~230 MB/s aggregate, ~115 MB/s per flow |
| **J4125 CPU** | No (unencrypted); Yes (heavy encryption + concurrent) | 4-core 2.7 GHz burst handles ~230 MB/s unencrypted easily |
| **2 GB RAM** | No (file serving); Yes (if running multiple DSM packages) | Expandable to 6 GB |
| **SATA HDD (2-bay)** | Yes (if HDD) — ~150-200 MB/s per drive | SSD eliminates disk bottleneck |
| **RAID 1** | Balanced — read = 2x disk, write = 1x disk | |
| **MTU 1500** | Minor — no jumbo frames (DS224+ 1GbE MTU limit = 1500) | Small efficiency loss vs 9000 MTU |

### 5.5 Limitations for RemotePFS

1. **No 2.5GbE/10GbE** — DS224+ only has 2 x 1GbE ports; no PCIe slots for NIC upgrades
2. **MTU capped at 1500** — no jumbo frame support on 1GbE ports (per Synology note)
3. **2 GB RAM stock** — adequate for file serving but tight if running containers, surveillance, etc.
4. **J4125 discontinued** — Intel lists it as discontinued; Synology still ships DS224+ with it
5. **No RDMA** — SMB3 Multichannel does not support RDMA
6. **SMB3 Multichannel XOR Link Aggregation** — must choose one, not both
7. **2-bay limit** — only RAID 0 or RAID 1; no RAID 5/6 redundancy with 2 bays

---

## 6. Sources

| Source | URL |
|---|---|
| Synology DS224+ Product Page | https://www.synology.com/en-us/products/DS224+ |
| Synology DS224+ Datasheet (PDF) | https://global.download.synology.com/download/Document/Hardware/DataSheet/DiskStation/24-year/DS224+/enu/DS224+_Data_Sheet_enu.pdf |
| Synology DSM 7.2 Tech Specs | https://www.synology.com/en-us/dsm/7.2/software_spec/dsm |
| Synology Performance Page | https://www.synology.com/en-us/products/performance |
| Synology Link Aggregation KB | https://kb.synology.com/en-global/DSM/help/DSM/AdminCenter/connection_network_linkaggr |
| Synology SMB3 Multichannel KB | https://kb.synology.com/en-us/DSM/tutorial/smb3_multichannel_link_aggregation |
| Intel J4125 ARK Specs | https://www.intel.com/content/www/us/en/products/sku/197305/intel-celeron-processor-j4125-4m-cache-up-to-2-70-ghz/specifications.html |
| TechPowerUp J4125 | https://www.techpowerup.com/cpu-specs/celeron-j4125.c3280 |

---

## 7. Key Findings Summary

1. **CPU:** Intel Celeron J4125, 4-core/4-thread, 2.0/2.7 GHz, 10 W TDP, 14 nm. Discontinued by Intel but still in active DS224+ production.

2. **AES-NI:** **Confirmed supported.** Intel ARK lists "Intel AES New Instructions: Yes." DSM security spec references "built-in AES-NI hardware encryption engine." Critical for encrypted SMB/shared folders without throughput collapse.

3. **RAM:** 2 GB DDR4 non-ECC stock, expandable to 6 GB (1 SO-DIMM slot). Max 6 GB total.

4. **LAN:** 2 x 1GbE RJ-45. MTU limited to 1500 (no jumbo frames). Wake on LAN/WAN supported.

5. **Link Aggregation:** 4 modes — Adaptive Load Balancing (no switch needed), IEEE 802.3ad LACP (managed switch), Balance XOR, Active/Standby. **SMB3 Multichannel and Link Aggregation are mutually exclusive.**

6. **SMB 3.1.1:** DSM's "SMB3" is actually SMB 3.1.1 (confirmed in DSM spec limitations text). Supports transport encryption (AES-GCM/CCM), server signing, durable handles, directory leasing, multichannel. SMB3 Multichannel available on x86 (DS224+ qualifies).

7. **NFS v4.1:** Supported on DS224+. NFS 4.1 multipathing supported. NFSv4 limited to TCP. Kerberos security (krb5/krb5i/krb5p) supported.

8. **Max Volume:** 108 TB single volume; 64 internal volumes; 2 bays (RAID 0/1/SHR/Basic/JBOD).

9. **Power:** 14.69 W access, 4.41 W hibernation, 60 W PSU. 22 dB(A) noise.

10. **RemotePFS fit:** 2x1GbE bonded gives ~230 MB/s aggregate (multi-flow). Single-flow limited to ~115 MB/s. AES-NI enables encrypted SMB with minimal overhead. Btrfs provides data integrity. J4125 adequate for file serving but limited headroom for heavy concurrent workloads.
