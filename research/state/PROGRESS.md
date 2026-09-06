# RemotePFS Research Progress Tracker

> **Resumable state file.** Update after every research phase completion.
> Read this file to resume work from where it was paused.

## Metadata

- **Project:** RemotePFS
- **Goal:** Research, specification, and planning phase
- **Private repo:** PSBrew/RemotePFS-Research (https://github.com/PSBrew/RemotePFS-Research)
- **Public repo:** PSBrew/RemotePFS (later)
- **Local path:** ~/development/ps5/RemotePFS-Research
- **Last updated:** 2026-09-05

## Phase Status

| Phase | Status | Started | Completed |
|-------|--------|---------|-----------|
| Setup | completed | 2026-09-05 | 2026-09-05 |
| Research | completed | 2026-09-05 | 2026-09-05 |
| KnowledgeBase | completed | 2026-09-05 | 2026-09-05 |
| Specification | completed | 2026-09-05 | 2026-09-05 |
| Planning | completed | 2026-09-05 | 2026-09-05 |

## Setup Checklist

- [x] Create private GitHub repo (PSBrew/RemotePFS-Research)
- [x] Clone repo locally to ~/development/ps5/RemotePFS-Research
- [x] Create directory structure (knowledge-base/, reports/, specs/, plans/, poc/, state/)
- [x] Create .gitignore
- [x] Create state tracking file (this file)
- [x] Study mkpfs conventions (CLAUDE.md, README.md, .claude/ settings, rules/, skills/)
- [x] Study mkpfs related-projects-index skill for indexing patterns
- [x] Initial commit + push

## Research Checklist

### Hardware
- [x] Radxa Cubie A7S (Allwinner A733, 8-core, LPDDR5, GbE, WiFi 6)
- [x] Synology DS224+ (Intel J4125, 2x 1GbE, 2GB DDR4, 2-bay)
- [x] PS5 USB and exFAT handling

### Software
- [x] ShadowMountPlus detection and mounting behavior
- [x] exFAT filesystem specification
- [x] USB OTG mass storage gadget emulation (Linux configfs/gadgetfs)

### Network
- [x] Network protocols for low-latency file access (SMB/NFS/custom)
- [x] Multi-tier caching strategies (RAM -> local HDD -> remote NAS)
- [x] Load balancing (ethernet + wifi)
- [x] Network-level compression
- [x] Latency optimization techniques

### Conventions
- [x] MkPFS coding style and project conventions

## KnowledgeBase Checklist

- [x] 00-index.md — topic index
- [x] 01-shadowmountplus.md
- [x] 02-ps5-usb-exfat.md (all fixes applied: USB port speeds, UAS/BOT clarification)
- [x] 03-radxa-cubie-a7s.md
- [x] 04-synology-nas.md (all fixes applied: SHA-NI, MPX, dedup, concurrent connections, 1.8 Physical section in HTML)
- [x] 05-exfat-filesystem.md (TexFAT claim corrected)
- [x] 06-usb-otg-gadget.md (all 16 ValidateKB06 fixes applied)
- [x] 07-network-protocols.md (all 14 ValidateKB07 fixes applied)
- [x] 08-caching-strategies.md (all 15 ValidateKB08 fixes applied)
- [x] 09-load-balancing.md (all 8+ ValidateKB09 fixes applied)
- [x] 10-network-compression.md (all 8+ ValidateKB10 fixes applied)
- [x] 11-latency-optimization.md (all 10+ ValidateKB11 fixes applied: M.2 NVMe removed, max_sectors, zero-copy, zcat, nfsd async, tcp_fastopen, dynamic_debug, BBR jitter)
- [x] 12-mkpfs-conventions.md
- [x] 13-future-features.md
- [x] HTML reports for all 13 KB articles — all synced with markdown

### Validation Status

| KB | Validator | Status | Fixes Applied |
|----|-----------|--------|---------------|
| 02 | ValidateKB02 + manual review | Completed | USB port speeds (5->10 Gbps rear), UAS/BOT clarification, constraints table |
| 03 | - | Not needed (factual spec sheet) | - |
| 04 | ValidateKB04 | Completed (8 findings) | SHA-NI, MPX, dedup caveat, concurrent connections, 1.8 Physical in HTML, TOC fixes |
| 05 | ValidateKB05 + manual review | Completed | TexFAT claim corrected (Linux exFAT does NOT support TexFAT) |
| 06 | ValidateKB06 | Completed (16 issues) | All fixed in MD + HTML |
| 07 | ValidateKB07 | Completed (14 issues) | All fixed in MD + HTML |
| 08 | ValidateKB08 | Completed (15 issues) | All fixed in MD + HTML |
| 09 | ValidateKB09 | Completed (8+ issues) | All fixed in MD + HTML |
| 10 | ValidateKB10 | Completed (8+ issues) | All fixed in MD + HTML |
| 11 | ValidateKB11 | Completed (10+ issues) | All fixed in MD + HTML |

## Specification Checklist

- [x] 01-service-architecture.md — Overall architecture, component breakdown, data flow, failure modes
- [x] 02-protocol-choice.md — NFS v4.1 rationale, mount config, kernel tunables, transport abstraction
- [x] 03-usb-gadget-config.md — Configfs gadget setup, backing store, exFAT image, SCSI emulation
- [x] 04-caching-layer.md — Page cache, read-ahead, metadata preloading (future), local SSD cache tier
- [x] 05-security-model.md — Trust zones, threat analysis, hardening, NFS/API security, VLAN

## Planning Checklist

- [x] 01-project-roadmap.md — Implementation roadmap with 6 phases, milestones, risk assessment
- [x] Decision log
- [x] Final review

## Decision Log

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| 1 | 2026-09-05 | Repo at PSBrew/RemotePFS-Research (private) | User instruction; keep research private |
| 2 | 2026-09-05 | Repo name case: RemotePFS and RemotePFS-Research | User instruction; case-sensitive names |
| 3 | 2026-09-05 | NAS model is DS224+ not DS244+ | DS244+ does not exist; DS224+ matches 2x1GbE spec |
| 4 | 2026-09-05 | Follow mkpfs conventions (uv, ruff, pytest, Google docstrings) | Project consistency across PSBrew org |
| 5 | 2026-09-05 | Research repo at ~/development/ps5/RemotePFS-Research | User instruction; not /tmp/ |
| 6 | 2026-09-05 | Use mkpfs related-projects-index pattern for KB structure | User instruction; consistent indexing |
| 7 | 2026-09-05 | NFS v4.1 over SMB for SBC-to-NAS transport | Lower overhead, nconnect parallelism, kernel-native caching |
| 8 | 2026-09-05 | f_mass_storage (BOT) for USB gadget; f_tcm (UAS) as fallback | BOT simpler; UAS if PS5 requires better performance |
| 9 | 2026-09-05 | Deferred metadata preloading, alt transports, FUSE mounting to v2 | User instruction; catalog in KB 13, spec as extension points |
| 10 | 2026-09-05 | DS224+ has NO M.2 NVMe slots; use 2.5" SATA SSDs or DS423+ for SSD caching | ValidateKB11 critical finding |

## Notes

- Radxa Cubie A7S: Allwinner A733 octa-core (2x A76 + 6x A55), 3 TOPS NPU, up to 16GB LPDDR5, GbE, WiFi 6, USB-C 3.1 Gen2 OTG, BSP kernel 6.6 only (no mainline)
- Synology DS224+: Intel Celeron J4125 quad-core 2.0GHz, 2GB DDR4, 2x 1GbE LAN, 2-bay, NO M.2 NVMe slots
- ShadowMountPlus: scans /mnt/usb0-7 for .exfat images, 64KB cluster, 512-byte sector (LVD), scan_interval=15s, stability_wait=10s
- ShadowMountPlus LVD attach: /dev/lvdctl, SCE_LVD_IOC_ATTACH_V0 (0xC0286D00), image_type=0 for exFAT
- PS5 original (2020): rear 2x USB-A 3.1 Gen 2 (10 Gbps), front 1x USB-C 3.1 Gen 2 (10 Gbps), front 1x USB-A 2.0 (480 Mbps)
- PS5 supports both UAS (0x62) and BOT (0x50); f_mass_storage only does BOT
- MkPFS conventions: Python 3.11+, uv, ruff (line-length=119), pytest, Google docstrings, Conventional Commits, no em dashes
- All HTML reports synced with markdown for all 13 KB articles
- All 5 specs written and saved
- Project plan (01-project-roadmap.md) written with 6 phases, milestones, risk assessment

## Migration Record

| Date | Event |
|------|-------|
| 2026-09-05 | Specs and plans moved to PSBrew/RemotePFS (implementation repo). |
| 2026-09-05 | Research repo merged into implementation repo as `research/`. Single repo now: PSBrew/RemotePFS. HTML reports removed; markdown KB articles are authoritative. `.claude/` merged at repo root with combined research + implementation memory. |
