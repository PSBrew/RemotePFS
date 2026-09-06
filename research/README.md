# RemotePFS Research Archive

Research, knowledge base, and project state for the RemotePFS project.
Merged into the implementation repo; this directory is the frozen research
archive. The implementation repo also contains the canonical specs and plans
under `specs/` and `plans/`.

## What This Is

All research from the RemotePFS specification phase:

- 14 knowledge-base articles (00-index through 13-future-features): PS5 USB
  mass storage handling, exFAT filesystem, USB OTG gadget, NBD protocol,
  nbdkit, NFS, caching strategies, load balancing, compression, latency,
  hardware (Radxa Cubie A7S, Synology DS224+), ShadowMountPlus, conventions,
  and future features.
- Project state tracker (PROGRESS.md).
- Claude assistant rules and memory.

The SBC (Radxa Cubie A7S) builds a virtual exFAT filesystem from a NAS
folder containing multiple games, serves sectors on-the-fly via NBD (nbdkit
Python plugin), and exposes `/dev/nbd0` as a USB mass storage LUN to the
PS5 via ShadowMountPlus. No `.exfat` image files, no loopback mounts.

## Structure

```
research/
├── README.md               # This file
├── .claude/                # Research-phase assistant rules
├── knowledge-base/         # 14 structured markdown research articles
│   ├── 00-index.md         # Topic index and cross-references
│   ├── 01-shadowmountplus.md
│   ├── 02-ps5-usb-exfat.md
│   ├── 03-radxa-cubie-a7s.md
│   ├── 04-synology-nas.md
│   ├── 05-exfat-filesystem.md
│   ├── 06-usb-otg-gadget.md
│   ├── 07-network-protocols.md
│   ├── 08-caching-strategies.md
│   ├── 09-load-balancing.md
│   ├── 10-network-compression.md
│   ├── 11-latency-optimization.md
│   ├── 12-mkpfs-conventions.md
│   └── 13-future-features.md
└── state/
    └── PROGRESS.md         # Resumable state tracker (research phase)
```

Notes:

- HTML reports were removed; the equivalent `.md` files in
  `knowledge-base/` are authoritative and contain the same information.
- PoC directory was empty and dropped.
- Project memory lives in `.claude/MEMORY.md` at the implementation repo
  root (merged research + implementation memory).

## Where Things Live Now

| Artifact | Location |
|----------|----------|
| Research KB articles | `research/knowledge-base/` (this archive) |
| Implementation specs (01-08) | `specs/` |
| Project roadmap | `plans/01-project-roadmap.md` |
| Design decisions | `plans/02-design-decisions.md` |
| Service code | `src/` (created during implementation) |

## Conventions

- Follow the PSBrew/MkPFS coding style: see `research/knowledge-base/12-mkpfs-conventions.md`.
- Use Conventional Commits for git messages.
- Python 3.11+, uv, Ruff (line-length=119), pytest, Google docstrings.
- No em dashes. `PFS` capitalization follows mkpfs rules.
