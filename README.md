# RemotePFS

Linux SBC service that emulates an exFAT USB mass storage device via USB
OTG, presenting game files from a remote Synology NAS to a jailbroken
PlayStation 5 via ShadowMountPlus.

The SBC (Radxa Cubie A7S) builds a virtual exFAT filesystem from a NAS
folder containing multiple games, serves sectors on-the-fly via NBD (nbdkit
Python plugin), and exposes `/dev/nbd0` as a USB mass storage LUN to the
PS5. No `.exfat` image files, no loopback mounts.

> **This repository is PRIVATE.** Do not push, mirror, or expose publicly.
> The owner will make it public after a stable release.

## Status

Specification and planning complete. Implementation follows the 8-phase
roadmap in `plans/01-project-roadmap.md`: Prerequisites, Config System,
NBD Server, HTTP API, USB Gadget Bind, Metadata Preloading, E2E Testing,
Hardening. No implementation code yet.

## Structure

```
.
├── README.md               # This file
├── .gitignore
├── .claude/                # Assistant rules, skills, and project memory
│   ├── MEMORY.md
│   ├── rules/              # style, html-reporting, tmp-usage
│   └── skills/             # knowledge-base-add, knowledge-base-index, fix-tests, html-reporting
├── specs/                  # Implementation specs (canonical)
│   ├── 01-service-architecture.md
│   ├── 02-protocol-choice.md
│   ├── 03-usb-gadget-config.md
│   ├── 04-caching-layer.md
│   ├── 05-security-model.md
│   ├── 06-nbd-server.md
│   ├── 07-config-system.md
│   └── 08-http-api.md
├── plans/
│   ├── 01-project-roadmap.md
│   └── 02-design-decisions.md
├── knowledge-base/         # 14 research articles (specification phase)
│   ├── 00-index.md         # Topic index and cross-references
│   └── sources/            # Related-project source artifacts (on demand)
└── src/                    # Service code (created during implementation)
```

Start points:

- Implementation overview: `specs/01-service-architecture.md`
- Roadmap and phases: `plans/01-project-roadmap.md`
- Why decisions were made: `plans/02-design-decisions.md`
- Background research: `knowledge-base/00-index.md`

## Conventions

- Follow the PSBrew/MkPFS coding style: see `knowledge-base/12-mkpfs-conventions.md`.
- Use Conventional Commits for git messages.
- Python 3.11+, uv, Ruff (line-length=119), pytest, Google docstrings.
- No em dashes. `PFS` capitalization follows mkpfs rules.
