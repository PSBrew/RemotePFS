"""Warm nbdkit cache with SectorMapper metadata ranges."""

from __future__ import annotations

import subprocess
from collections.abc import Callable


class PreloadError(RuntimeError):
    """Metadata preloading failed."""


def build_nbdsh_command(socket_path: str, ranges: list[tuple[int, int]]) -> list[str]:
    """Build nbdsh command that reads each hot range through nbdkit."""
    commands = [f"h.pread({length}, {offset})" for offset, length in ranges]
    script = "; ".join(commands) or "pass"
    return [
        "nbdsh",
        "-u",
        f"nbd+unix:///?socket={socket_path}",
        "-c",
        script,
    ]


def preload(
    mapper,
    *,
    socket_path: str = "/run/remotepfs/nbd.sock",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_seconds: float = 60.0,
) -> int:
    """Read mapper hot ranges through nbdsh and return bytes requested.

    Args:
        mapper: SectorMapper-compatible object exposing ``get_hot_ranges``.
        socket_path: nbdkit Unix socket path.
        runner: Injectable subprocess runner.
        timeout_seconds: Maximum duration for one nbdsh pass.

    Raises:
        PreloadError: If nbdsh returns a non-zero status or times out.
    """
    ranges = mapper.get_hot_ranges()
    try:
        result = runner(
            build_nbdsh_command(socket_path, ranges),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise PreloadError("nbdsh metadata preload timed out") from error
    if result.returncode != 0:
        raise PreloadError(result.stderr.strip() or "nbdsh metadata preload failed")
    return sum(length for _, length in ranges)
