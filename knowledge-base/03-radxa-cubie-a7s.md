# Radxa Cubie A7S — Hardware Research for RemotePFS

> Research date: 2026-09-05
> Sources: docs.radxa.com/en/cubie/a7s, cnx-software.com (Feb 2026), cnx-software.com (Dec 2024)

---

## 1. Overview

The Radxa Cubie A7S is a compact (51 x 51 mm) SBC built on the
Allwinner A733 octa-core SoC. It sits between the Cubie A7A
(credit-card size) and Cubie A7Z (Pi Zero size) in Radxa's Allwinner
A733 lineup. Key draws for RemotePFS: dual USB-C OTG ports, Gigabit
Ethernet, up to 16 GB LPDDR5, and PCIe Gen3 via FFC.

|Field|Value|
|---|---|
|Board|Radxa Cubie A7S|
|SoC|Allwinner A733 (12 nm)|
|Form factor|51 x 51 mm|
|Price|$25-35 (4/6/8 GB configs)|
|OS|Android 13, Debian (Radxa OS), Armbian (planned)|

---

## 2. SoC — Allwinner A733

### 2.1 CPU

|Cluster|Cores|Architecture|Max Clock|
|---|---|---|---|
|Big|2|Arm Cortex-A76|2.00 GHz|
|Little|6|Arm Cortex-A55|1.79-1.8 GHz|
|RT|1|RISC-V E902|~200 MHz|

- big.LITTLE arrangement; 8-core total (excluding RISC-V).
- RISC-V E902 real-time core runs FreeRTOS; no public dev resources.
- Antutu ~320,000 (Teclast P50Ai tablet reference).

### 2.2 GPU

- Imagination Technologies BXM-4-64 MC1
- OpenGL ES 3.2, OpenCL 3.0, Vulkan 1.3
- Mid-range circa 2020; slower than Mali-G57 MC2; UI-grade, not gaming.

### 2.3 VPU (Video Processing)

|Decode|Encode|
|---|---|
|8Kp24 H.265 / VP9 / AVS2|4Kp30 H.265 / H.264|
|No AV1 decode|—|

### 2.4 NPU (AI Accelerator)

- Optional, up to 3 TOPS
- Three SoC variants:
  - A733MX-HN3: NPU + HDMI
  - A733MX-N3X: NPU, no HDMI
  - A733MX-HXX: HDMI, no NPU

### 2.5 Memory Interface

- 32-bit LPDDR4 / LPDDR4x / LPDDR5
- Up to 16 GB RAM (LPDDR5 @ 4800 MT/s on Cubie A7S)
- 192 KB SRAM + 512 KB shared SRAM on-chip

### 2.6 Storage Interfaces

|Interface|Spec|
|---|---|
|UFS|2-lane UFS 3.0, up to 1 TB|
|eMMC|5.0 / 5.1|
|SD|SD 2.0 / 3.0|
|SDIO|2.0 / 3.0|
|Octal SPI|yes|

### 2.7 SoC I/O Summary

|Category|Details|
|---|---|
|USB|1x USB 3.1 Gen2 DRD (10 Gbps), 1x USB 2.0 Host, 1x USB 2.0 DRD|
|PCIe|1-lane PCIe 3.0 DM (8 Gbps)|
|Ethernet|GMAC (Gigabit)|
|Display|HDMI 2.0b (4Kp60), RGB 1080p60, LVDS 1080p60, eDP 1.4b / DP 1.4 (4Kp60), 2x 4-lane MIPI DSI (4Kp45)|
|Camera|2x parallel CSI (up to 3264x4224), 4+4+2-lane MIPI CSI (2.0 Gbit/s/lane)|
|Audio|5x I2S|
|Low-speed|9x UART, 3x 10-channel PWM (100 MHz), 5x SPI (100 MHz), 16x I2C/TWI, 2x IR Tx, 1x IR Rx|
|Analog|12-bit 7-channel GPADC (1 MHz), 6-bit 1-channel LRADC (2 kHz)|
|Security|Crypto engine, Security ID, SMC, SPC, TZMA|
|Package|15 x 15 mm, FCCSP 570 balls, 12 nm process|

---

## 3. Cubie A7S Board-Level Specs

### 3.1 RAM

- Up to 16 GB LPDDR5 @ 4800 MT/s
- Shipping configs: 4 GB, 6 GB, 8 GB (AliExpress)
- 16 GB may be 6 GB + 10 GB virtual (swap on UFS) — verify before relying.

### 3.2 Storage

- MicroSD card slot
- Up to 256 GB eMMC (optional, board-level)

### 3.3 Networking

|Interface|Details|
|---|---|
|Ethernet|Gigabit RJ45 (GMAC)|
|WiFi|Dual-band WiFi 6 (Quectel FCU760K module)|
|Bluetooth|5.4 (some sources say 5.2 — cnx article title says 5.2, body says 5.4)|
|Antenna|IPEX connector for external antenna|

### 3.4 USB Ports

|Port|Spec|RemotePFS Relevance|
|---|---|---|
|USB-C #1|USB 3.1 Gen2 OTG + DisplayPort Alt mode|**Primary gadget/OTG port** — 10 Gbps DRD|
|USB-C #2|USB 2.0 OTG, 5 V power input|Secondary OTG; lower bandwidth (480 Mbps)|
|USB-A|USB 2.0 host|General peripheral|

**USB OTG / Gadget Mode Notes:**
- SoC provides USB 3.1 Gen2 DRD (Dual-Role Device) = full OTG.
- SoC also provides USB 2.0 DRD — second OTG capable port.
- Linux USB gadget framework (configfs/functionfs) should work with
  the Allwinner musb/sunxi DRD controller driver in the BSP kernel.
- USB-C #1 with DP Alt mode is the high-bandwidth OTG port (10 Gbps
  SuperSpeed+) — ideal for RemotePFS USB mass-storage gadget.
- USB-C #2 is USB 2.0 OTG (480 Mbps) — usable but slower.

### 3.5 PCIe

- 16-pin PCIe Gen3 x1 FFC connector
- Compatible with Raspberry Pi 5 PCIe FFC connector spec
- SoC: 1-lane PCIe 3.0 DM (8 Gbps)
- Enables NVMe SSD, PCIe cards via FFC ribbon

### 3.6 GPIO

- 30-pin GPIO header
- 15-pin GPIO header
- SoC exposes 9x UART, 16x I2C, 5x SPI, 3x PWM — ample for control.

### 3.7 Display / Camera

- DisplayPort via USB-C (4Kp60) — no HDMI connector on board
- 4-lane MIPI CSI camera connector

### 3.8 Power

- 5 V / 3 A+ via USB-C connector
- 5 V via GPIO header
- Fan connector present
- USB BOOT button for flashing

### 3.9 Form Factor

- 51 x 51 mm
- Compact; between Pi Zero and Pi-sized boards.

---

## 4. OS and Linux Kernel Support

### 4.1 Vendor OS

|OS|Status|
|---|---|
|Android 13|Supported (Radxa)|
|Debian (Radxa OS)|Supported — Debian 13 server image tested with Linux 6.6|
|Armbian|"Coming soon" (per cnx-software, Feb 2026)|

- Radxa provides hardware access/control library for Linux/Android.
- U-Boot 2026 used in testing.
- OpenClaw personal AI assistant demoed on Debian 13 / Linux 6.6.

### 4.2 Linux Mainline Kernel Status

- **No mainline Linux kernel support as of Sep 2026.**
- Allwinner A733 is not in the sunxi mainline tree.
- Radxa ships a BSP (vendor) kernel based on Linux 6.6.
- Community frustration (cnx comments): Radxa has not upstreamed
  patches for prior Allwinner boards after multiple product launches.
- Allwinner reportedly wants to re-enter the "open source market"
  (per cnx), but A733 patches are not yet on LKML/sunxi mailing list.
- sunxi.org wiki has no Allwinner_A733 page (404 as of Sep 2026).

**Implication for RemotePFS:** Must use Radxa BSP kernel (6.6-based)
for the foreseeable future. No mainline kernel option. Pin to BSP
kernel version; do not assume mainline DT bindings.

### 4.3 USB Gadget on BSP Kernel

- Linux 6.6 BSP kernel includes sunxi USB DRD driver.
- configfs USB gadget framework available in 6.6.
- mass_storage function (g_mass_storage / configfs mass_storage.0)
  should work for presenting block device over USB-C OTG.
- f_fs (FunctionFS) available for custom gadget protocols.
- **Verify:** DRD controller driver name in BSP (likely sunxi-musb
  or dwc3-based for USB 3.1 Gen2 port).

---

## 5. RemotePFS Relevance Assessment

### 5.1 USB OTG Gadget Mode — Strong Fit

- Two OTG-capable USB-C ports (USB 3.1 Gen2 + USB 2.0).
- USB 3.1 Gen2 DRD at 10 Gbps is excellent for USB mass-storage
  gadget — higher than most SBCs in this price range.
- Linux 6.6 BSP has gadget framework; mass_storage function ready.
- Can present as USB block device to host (PS5 or PC).

### 5.2 Network Throughput — Good

- Gigabit Ethernet RJ45 (GMAC) — ~940 Mbps practical TCP.
- WiFi 6 dual-band — useful for wireless, but GbE preferred for
  RemotePFS file serving.
- USB-C #1 at 10 Gbps could also do USB Ethernet gadget (CDC NCM)
  as alternative to RJ45.

### 5.3 RAM for Caching — Excellent

- Up to 16 GB LPDDR5 @ 4800 MT/s.
- Ample for page cache, block-device write-back cache, NFS/FUSE
  caching layers.
- 8 GB config (commonly available) is more than sufficient for
  RemotePFS caching of USB-backed storage.
- LPDDR5 bandwidth: 4800 MT/s x 8 B = ~38.4 GB/s peak — fast.

### 5.4 Storage — Flexible

- eMMC up to 256 GB on-board.
- MicroSD for boot.
- PCIe Gen3 x1 FFC for NVMe SSD — full Gen3 bandwidth (~8 Gbps or
  ~985 MB/s) for high-speed backing store.
- UFS 3.0 at SoC level (not exposed on this board's connectors).

### 5.5 Concerns

|Concern|Severity|Mitigation|
|---|---|---|
|No mainline kernel|Medium|Use Radxa BSP 6.6; pin kernel version|
|Imagination GPU drivers|Low|GPU irrelevant to RemotePFS (headless)|
|BT version ambiguity|Negligible|Not relevant to RemotePFS|
|16 GB "virtual" RAM caveat|Low|Verify physical RAM with free -h|
|RISC-V core undocumented|Negligible|Not needed for RemotePFS|
|Community upstream frustration|Low|Accept BSP dependency|

---

## 6. Comparison with Siblings

|Feature|Cubie A7A|Cubie A7S|Cubie A7Z|
|---|---|---|---|
|Size|Credit-card|51 x 51 mm|Pi Zero|
|GbE|—|Yes|—|
|USB-C DP|—|Yes|—|
|PCIe FFC|—|Yes|—|
|RAM max|16 GB|16 GB|16 GB|

Cubie A7S is the connectivity-rich middle option — best for RemotePFS
among the three due to GbE + dual USB-C OTG + PCIe FFC.

---

## 7. Sources

1. cnx-software.com — "Cubie A7S – A compact Allwinner A733 SBC..."
   (Feb 5, 2026)
2. cnx-software.com — "Allwinner A733 octa-core Cortex-A76/A55 AI
   SoC..." (Dec 6, 2024)
3. docs.radxa.com/en/cubie/a7s — product landing (JS-rendered,
   limited extractable content)
4. docs.radxa.com/en/cubie/a7s/getting-started — quick start index
5. linux-sunxi.org/Allwinner_A733 — 404 (no wiki page as of Sep 2026)
6. Olimex blog (Dec 2024) — A733 variant info
7. Teclast P50Ai product page — A733 benchmark reference
