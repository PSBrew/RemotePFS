"""NFS v4.1 mount lifecycle helpers for SBC deployment."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_NFS_OPTIONS = "ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec"


class NfsError(RuntimeError):
    """NFS operation failed."""


@dataclass(frozen=True)
class MountStatus:
    """Status of one configured NFS mount."""

    name: str
    server: str
    export: str
    mount_point: str
    mounted: bool
    state: str


class NfsManager:
    """Mount and unmount configured NFS sources through system utilities."""

    def __init__(self, *, runner=subprocess.run) -> None:
        """Initialize manager.

        Args:
            runner: Injectable subprocess runner for unit tests.
        """
        self._runner = runner

    def mount(self, source) -> None:
        """Mount one SourceConfig read-only.

        Args:
            source: ``SourceConfig``-compatible object.

        Raises:
            NfsError: If platform is not Linux or mount fails.
        """
        if platform.system() != "Linux":
            raise NfsError("NFS mount operations require Linux SBC")
        mount_point = Path(source.mount_point)
        mount_point.mkdir(parents=True, exist_ok=True)
        options = source.nfs_options or DEFAULT_NFS_OPTIONS
        result = self._runner(
            ["mount", "-t", "nfs4", "-o", options, f"{source.server}:{source.export}", str(mount_point)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise NfsError(result.stderr.strip() or f"mount failed for {source.name}")

    def unmount(self, source) -> None:
        """Unmount one source.

        Args:
            source: ``SourceConfig``-compatible object.
        """
        if platform.system() != "Linux":
            raise NfsError("NFS unmount operations require Linux SBC")
        result = self._runner(["umount", source.mount_point], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise NfsError(result.stderr.strip() or f"unmount failed for {source.name}")

    def is_mounted(self, mount_point: str) -> bool:
        """Return whether mount point appears in ``/proc/mounts``."""
        try:
            mounts = Path("/proc/mounts").read_text(encoding="utf-8")
        except OSError:
            return False
        return any(line.split()[1] == mount_point for line in mounts.splitlines() if len(line.split()) >= 2)

    def status(self, source) -> MountStatus:
        """Return status for one SourceConfig-compatible object."""
        mounted = self.is_mounted(source.mount_point)
        return MountStatus(
            name=source.name,
            server=source.server,
            export=source.export,
            mount_point=source.mount_point,
            mounted=mounted,
            state="ok" if mounted else "not_mounted",
        )
