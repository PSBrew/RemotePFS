<div align="center">

# RemotePFS

**Expose read-only NAS game files to PS5 as virtual exFAT USB storage.**

[![Python](https://img.shields.io/badge/python-3.11%2B-2563EB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0-3B82F6?style=for-the-badge)](LICENSE)

[Architecture](specs/01-service-architecture.md) · [Configuration](docs/configuration.md) · [SBC install](docs/sbc-installation.md) · [Troubleshooting](docs/troubleshooting.md)

</div>

## Why

RemotePFS presents files from one or more NFS mounts through a virtual, read-only exFAT filesystem. An SBC serves that filesystem through a local nbdkit Python plugin, a Linux NBD block device, and a USB mass-storage gadget. No `.exfat` image, loop device, or game copy is required.

## Features

- Config-driven virtual root with files and recursively scanned directories.
- NFS v4.1 read-only sources with hardened mount options.
- Virtual exFAT metadata generated in memory.
- nbdkit Python API v2 over a Unix socket.
- Read-only enforcement at NFS, NBD, and USB gadget layers.
- FastAPI localhost control plane with compile/activate generations.
- Metadata preload support through nbdsh.
- macOS-compatible unit-test and software-development path; SBC hardware is required for USB/NBD integration.

## Install

RemotePFS targets a Linux SBC. macOS can run unit tests and software-only API checks, but cannot provide ConfigFS, `/dev/nbd0`, nbdkit, or a USB Device Controller.

```bash
uv sync
uv run --frozen pytest
uv run --frozen ruff format .
uv run --frozen ruff check .
```

See [SBC installation](docs/sbc-installation.md) for Debian/Ubuntu/Armbian deployment.

## Commands

```bash
cp config/remotepfs.conf.example /tmp/remotepfs.conf
# Edit /tmp/remotepfs.conf with real NFS paths, then:
remotepfs compile /tmp/remotepfs.conf
remotepfs serve --config /etc/remotepfs/remotepfs.conf
curl http://127.0.0.1:8080/api/health
```

API endpoints:

- `GET /api/health`, `/api/status`, `/api/config`, `/api/games`
- `POST /api/config/compile`, `/api/config/activate`, `/api/config/reload`, `/api/eject`
- `PUT /api/config`

## Documentation

- [Configuration reference](docs/configuration.md)
- [SBC installation and hardware checklist](docs/sbc-installation.md)
- [Troubleshooting and verification](docs/troubleshooting.md)
- [Canonical specifications](specs/)
- [Design decisions](plans/02-design-decisions.md)

## Sponsor

RemotePFS is a community project for reproducible, read-only PS5 storage workflows.

## Contributors

Contributions must preserve read-only behavior, update tests for observable contracts, and follow Ruff and Google-style docstrings.

## Related projects

- [MkPFS](https://github.com/PSBrew/MkPFS) — project conventions reference.
- [nbdkit](https://github.com/libguestfs/nbdkit) — NBD server and filter framework.

## Contributing

Use Conventional Commits. Run `uv run --frozen pytest` and `uv run --frozen ruff check .` before opening a change. Hardware-dependent validation must include SBC command output and PS5 observations; never claim those checks from macOS.
