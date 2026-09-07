"""Lifecycle control for dedicated remotepfs-nbdkit systemd unit."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

DEFAULT_PLUGIN_PATH = "/opt/remotepfs/source/src/remotepfs/remotepfs_nbd.py"
DEFAULT_UNIT = "remotepfs-nbdkit.service"
DEFAULT_STATE_PATH = "/var/lib/remotepfs/mapper.state"


class NbdkitError(RuntimeError):
    """nbdkit lifecycle operation failed."""


class NbdkitManager:
    """Control nbdkit running as unprivileged systemd service."""

    DEFAULT_PLUGIN_PATH = DEFAULT_PLUGIN_PATH
    DEFAULT_UNIT = DEFAULT_UNIT
    DEFAULT_STATE_PATH = DEFAULT_STATE_PATH

    def __init__(
        self,
        *,
        socket_path: str = "/run/remotepfs/nbd.sock",
        pidfile: str = "/run/remotepfs/nbdkit.pid",
        plugin_path: str = DEFAULT_PLUGIN_PATH,
        unit_name: str = DEFAULT_UNIT,
        state_path: str = DEFAULT_STATE_PATH,
        runner=subprocess.run,
    ) -> None:
        """Initialize manager with injectable systemd runner."""
        self.socket_path = socket_path
        self.pidfile = pidfile
        self.plugin_path = plugin_path
        self.unit_name = unit_name
        self.state_path = state_path
        self._runner = runner

    def command(self, *, image_size: int, mapper_state: str) -> list[str]:
        """Build canonical nbdkit command used by the dedicated unit.

        ``image_size`` remains accepted for API compatibility; plugin state
        supplies authoritative size when loaded.
        """
        return [
            "nbdkit",
            "-U",
            self.socket_path,
            "--pidfile",
            self.pidfile,
            "--unix-mode=0600",
            "--exit-with-parent",
            "--threads",
            "1",
            "--max-request",
            "98304",
            "--readonly",
            "--filter=blocksize",
            "--filter=cache",
            "python",
            self.plugin_path,
            f"image_size={image_size}",
            f"mapper_state={mapper_state}",
            "minblock=512",
            "maxdata=65536",
            "maxlen=134217728",
            "cache-min-block-size=262144",
            "cache-max-size=1073741824",
            "cache-on-read=true",
        ]

    def start(self, *, image_size: int, mapper_state: str, foreground: bool = False) -> None:
        """Start dedicated nbdkit unit and wait for Unix socket."""
        if Path(mapper_state) != Path(self.state_path) or not Path(mapper_state).is_file():
            raise NbdkitError(f"mapper state must exist at {self.state_path} before systemd start")
        if foreground:
            raise NbdkitError("foreground nbdkit requires direct deployment, not systemd unit")
        result = self._runner(["systemctl", "start", self.unit_name], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise NbdkitError(result.stderr.strip() or "failed to start nbdkit systemd unit")
        deadline = time.monotonic() + 10
        while not Path(self.socket_path).exists():
            if time.monotonic() >= deadline:
                raise NbdkitError(f"nbdkit socket did not appear: {self.socket_path}")
            time.sleep(0.05)

    def stop(self) -> None:
        """Stop dedicated nbdkit unit."""
        self.disconnect()
        self._runner(["systemctl", "stop", self.unit_name], check=False, capture_output=True, text=True)

    def connect(self, device: str = "/dev/nbd0") -> None:
        """Connect kernel NBD client read-only through Unix socket."""
        result = self._runner(
            ["nbd-client", "-U", self.socket_path, "-r", device],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise NbdkitError(result.stderr.strip() or "nbd-client connect failed")

    def disconnect(self, device: str = "/dev/nbd0") -> None:
        """Disconnect kernel NBD client if available."""
        self._runner(["nbd-client", "-d", device], check=False, capture_output=True, text=True)

    def is_ready(self) -> bool:
        """Return whether nbdkit Unix socket exists."""
        return Path(self.socket_path).exists()
