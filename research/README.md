# RemotePFS Research Archive

Research and project state from the RemotePFS specification phase.
The implementation repo also contains specifications under `specs/`,
plans under `plans/`, and the knowledge base at the repo root under
`knowledge-base/`.

## What This Is

All research from the specification phase:

- 14 knowledge-base articles (00-index through 13-future-features): PS5 USB
  mass storage handling, exFAT filesystem, USB OTG gadget, NBD protocol,
  nbdkit, NFS, caching strategies, load balancing, compression, latency,
  hardware (Radxa Cubie A7S, Synology DS224+), ShadowMountPlus, conventions,
  and future features.
- Project state tracker (`state/PROGRESS.md`).

The SBC (Radxa Cubie A7S) builds a virtual exFAT filesystem from a NAS
folder containing multiple games, serves sectors on-the-fly via NBD (nbdkit
Python plugin), and exposes `/dev/nbd0` as a USB mass storage LUN to the
PS5 via ShadowMountPlus. No `.exfat` image files, no loopback mounts.

## Structure

```
research/
├── README.md               # This file
└── state/
    └── PROGRESS.md         # Resumable state tracker (research phase)
```

The knowledge base articles moved to the repo root at `knowledge-base/`
in September 2026. See `../knowledge-base/00-index.md` for topic index.

Notes:

- HTML reports were removed; the `.md` files in `knowledge-base/` are
  authoritative.
- PoC directory was empty and dropped.
- Project memory lives in `.claude/MEMORY.md` at the implementation repo
  root (merged research + implementation memory).

## Where Things Live Now

| Artifact | Location |
|----------|----------|
| Research KB articles | `../knowledge-base/` |
| Implementation specs (01-08) | `../specs/` |
| Project roadmap | `../plans/01-project-roadmap.md` |
| Design decisions | `../plans/02-design-decisions.md` |
| Service code | `../src/` (created during implementation) |

## Conventions

- Follow the PSBrew/MkPFS coding style: see `../knowledge-base/12-mkpfs-conventions.md`.
- Use Conventional Commits for git messages.
- Python 3.11+, uv, Ruff (line-length=119), pytest, Google docstrings.
- No em dashes. `PFS` capitalization follows mkpfs rules.
