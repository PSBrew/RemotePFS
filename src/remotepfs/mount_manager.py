"""NFS and CIFS mount lifecycle helpers for SBC deployment."""

from __future__ import annotations

import platform
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .consts import DEFAULT_CIFS_OPTIONS, DEFAULT_NFS_OPTIONS

SUPPORTED_PROTOCOLS = frozenset(("nfs", "cifs"))


class MountError(RuntimeError):
    """Network mount operation failed."""


@dataclass(frozen=True)
class MountStatus:
    """Status of one configured network mount."""

    name: str
    protocol: str
    endpoint: str
    mount_point: str
    mounted: bool
    state: str


class MountManager:
    """Mount and unmount configured NFS or CIFS sources."""

    def __init__(self, *, runner: Any = subprocess.run) -> None:
        """Initialize manager.

        Args:
            runner: Injectable subprocess runner for unit tests.
        """
        self._runner = runner

    def mount(self, source: Any) -> None:
        """Mount one read-only SourceConfig-compatible object.

        Raises:
            MountError: If platform, credentials, or mount operation fails.
        """
        if platform.system() != "Linux":
            raise MountError("Network mount operations require Linux SBC")
        if source.protocol not in SUPPORTED_PROTOCOLS:
            raise MountError(f"unsupported mount protocol: {source.protocol}")
        mount_point = Path(source.mount_point)
        mount_point.mkdir(parents=True, exist_ok=True)
        if source.protocol == "nfs":
            options = source.options or DEFAULT_NFS_OPTIONS
            command = ["mount", "-t", "nfs4", "-o", options, source.endpoint, str(mount_point)]
        elif source.protocol == "cifs":
            credentials = self._credentials_path(source)
            options = source.options or DEFAULT_CIFS_OPTIONS
            command = [
                "mount",
                "-t",
                "cifs",
                "-o",
                f"{options},credentials={credentials}",
                source.endpoint,
                str(mount_point),
            ]
        else:
            raise AssertionError(f"registered protocol was not dispatched: {source.protocol}")
        result = self._runner(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise MountError(result.stderr.strip() or f"mount failed for {source.name}")

    def _credentials_path(self, source: Any) -> str:
        """Validate and return CIFS credential path."""
        credentials_file = source.credentials_file
        if not credentials_file:
            raise MountError(f"CIFS source {source.name} requires credentials_file")
        path = Path(credentials_file)
        try:
            file_stat = path.stat()
        except OSError as exc:
            raise MountError(f"CIFS credentials file unavailable: {path}") from exc
        mode = stat.S_IMODE(file_stat.st_mode)
        if file_stat.st_uid != 0:
            raise MountError(f"CIFS credentials file must be owned by root: {path}")
        if mode != 0o600:
            raise MountError(f"CIFS credentials file must have mode 0600: {path}")
        return str(path)

    def unmount(self, source: Any) -> None:
        """Unmount one SourceConfig-compatible object."""
        if platform.system() != "Linux":
            raise MountError("Network unmount operations require Linux SBC")
        result = self._runner(["umount", source.mount_point], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise MountError(result.stderr.strip() or f"unmount failed for {source.name}")

    def is_mounted(self, mount_point: str) -> bool:
        """Return whether mount point appears in ``/proc/mounts``."""
        try:
            mounts = Path("/proc/mounts").read_text(encoding="utf-8")
        except OSError:
            return False
        return any(line.split()[1] == mount_point for line in mounts.splitlines() if len(line.split()) >= 2)

    def status(self, source: Any) -> MountStatus:
        """Return status for one SourceConfig-compatible object."""
        mounted = self.is_mounted(source.mount_point)
        return MountStatus(
            name=source.name,
            protocol=source.protocol,
            endpoint=source.endpoint,
            mount_point=source.mount_point,
            mounted=mounted,
            state="ok" if mounted else "not_mounted",
        )
