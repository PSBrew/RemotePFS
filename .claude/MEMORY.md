# Project Memory

Curated, durable knowledge base for the RemotePFS project (research and
implementation). Keep entries concise, source-backed, and oriented toward
repeatable workflows. This file is injected as context when Claude works in
this repository.

## How To Use This Memory

- Read this file first for high-signal project context before deep investigation.
- Treat this as a navigation layer; use linked source files as ground truth.
- Prefer compact bullets over long prose.
- When behavior or findings change, update the related section and keep references current.

## Core Project Identity

- **Project:** RemotePFS - Linux SBC service emulating exFAT USB mass storage for PS5.
- **Repo:** PSBrew/RemotePFS (single repo, private; research merged in 2026-09-05).
- **Goal phase:** Implementation (research, specification, and planning complete).
- **Methodology:** spec-driven development, resumable state tracking.
- **Local path:** ~/development/ps5/remotepfs (case-insensitive APFS; also resolves as RemotePFS).

## Repository Layout

- `specs/` - canonical implementation specs 01-08.
- `plans/` - project roadmap (8 phases) and design decisions record.
- `research/` - frozen research archive from the specification phase.
  - `research/knowledge-base/` - 14 markdown research articles (00-index through 13).
  - `research/state/PROGRESS.md` - research-phase state tracker and decision log.
  - `research/README.md` - archive index.
  - HTML reports were removed; the `.md` files are authoritative.
- `src/` - service code (created during implementation).
- `.claude/` - assistant rules and this memory.

## Key Technical Decisions

| # | Decision | Rationale | Source |
|---|-----------|-----------|--------|
| 1 | Python 3.11+ with `uv` | Matches MkPFS conventions; PSBrew org standard | research/knowledge-base/12-mkpfs-conventions.md |
| 2 | Radxa Cubie A7S as target SBC | USB 3.1 Gen2 OTG (10 Gbps), GbE, up to 16 GB LPDDR5 | research/knowledge-base/03-radxa-cubie-a7s.md |
| 3 | Synology DS224+ as NAS backend | 2x 1GbE bonded, J4125 with AES-NI, SMB 3.1.1 + NFS v4.1 | research/knowledge-base/04-synology-nas.md |
| 4 | exFAT USB gadget via Linux configfs | mass_storage function presents as USB flash drive to PS5 | research/knowledge-base/06-usb-otg-gadget.md |
| 5 | 64 KB exFAT cluster size, 512 B sectors | Matches ShadowMountPlus LVD defaults | research/knowledge-base/01-shadowmountplus.md |
| 6 | BSP kernel (Linux 6.6) for A733 | No mainline support as of Sep 2026 | research/knowledge-base/03-radxa-cubie-a7s.md |
| 7 | NBD over FUSE | NBD userspace server over Unix socket to /dev/nbd0 then g_mass_storage; loop-on-FUSE unreliable | specs/02-protocol-choice.md |
| 8 | nbdkit with Python plugin | Only pread() and extents(); filters blocksize + cache | specs/06-nbd-server.md |
| 9 | Triple read-only enforcement | nbdkit --readonly + nbd-client -r + g_mass_storage ro=1 | specs/05-security-model.md |
| 10 | FastAPI + Pydantic v2 + uvicorn | Async HTTP API bound to 127.0.0.1 only | specs/08-http-api.md |
| 11 | Atomic generation swap for config reload | Immutable gen dir tree + precomputed extent table; stable file IDs via SHA256 truncated to 32 bits | specs/07-config-system.md |
| 12 | Metadata preloading in V1 scope | pread()-based warming (~9-10 MiB) via NBD before UDC bind | specs/04-caching-layer.md |
| 13 | DS224+ has NO M.2 NVMe slots | Use 2.5" SATA SSDs; DS423+ for SSD caching | ValidateKB11 finding |

## Architecture Summary

The SBC (Radxa Cubie A7S) builds a virtual exFAT filesystem from a NAS
folder containing multiple games, serves sectors on-the-fly via NBD (nbdkit
Python plugin), and exposes `/dev/nbd0` as a USB mass storage LUN to the
PS5 via ShadowMountPlus. No `.exfat` image files, no loopback mounts.

V1 scope: single active game mount (one LUN). Switching requires atomic
generation swap + UDC unbind/rebind cycle. All games visible simultaneously
on the exFAT device.

## Research Topics Index

- 01 ShadowMountPlus: auto-mounter for jailbroken PS5; scans /mnt/usb0-7; LVD/MD backends. Defines the USB presentation contract.
- 02 PS5 USB/exFAT handling: rear 2x USB-A 3.1 Gen2, front USB-C 3.1 Gen2, front USB-A 2.0; UAS and BOT supported.
- 03 Radxa Cubie A7S: Allwinner A733 (2x A76 + 6x A55), up to 16 GB LPDDR5, USB 3.1 Gen2 OTG, GbE, PCIe Gen3.
- 04 Synology DS224+: J4125, 2x 1GbE, 2-bay, SMB 3.1.1 + NFS v4.1.
- 05 exFAT filesystem: cluster/sector layout; Linux exFAT does NOT support TexFAT.
- 06 USB OTG gadget: configfs mass_storage function; g_mass_storage.
- 07 Network protocols: NFS v4.1 chosen (nconnect, kernel-native caching).
- 08 Caching strategies: nbdkit cache L1 + page cache L2.
- 09 Load balancing: ethernet + wifi.
- 10 Network compression.
- 11 Latency optimization: pread() warming, kernel tunables.
- 12 MkPFS conventions: uv, ruff (119), pytest, Google docstrings, Conventional Commits.
- 13 Future features: alt transports, FUSE game-folder mounting, multi-network load balancing.

Full articles: `research/knowledge-base/<NN>-<slug>.md`. Index:
`research/knowledge-base/00-index.md`.

## Update Standard

- Keep each entry factual, short, and tied to concrete source links.
- Prefer stable paths over temporary artifacts.
- Update when findings change or new decisions are made.
- Implementation progress tracked in `research/state/PROGRESS.md` (historical) and commit history.

## Skills

- knowledge-base-add
  - When: add or update a research article under `research/knowledge-base/`
  - Quickstart:
    - Topic-focused: “Run knowledge-base-add for ‘iSCSI vs NBD for LAN’ with sources: <urls>”
    - Output: `research/knowledge-base/{NN}-{slug}.md`, index entry in `00-index.md`, memory bullet
- knowledge-base-index
  - When: rebuild/verify the KB index and memory entries from disk
  - Quickstart:
    - “Run knowledge-base-index to resync 00-index.md and MEMORY”
    - Verifies missing/extra entries, numbering, links, and the cross-ref matrix
