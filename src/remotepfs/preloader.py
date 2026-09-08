"""Warm nbdkit cache with SectorMapper metadata ranges."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable

from .config import PrefetchConfig

logger = logging.getLogger(__name__)


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
    policy: PrefetchConfig | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_seconds: float = 60.0,
    stop_event: threading.Event | None = None,
) -> int:
    """Read mapper hot ranges through nbdsh and return bytes requested."""
    policy = policy or PrefetchConfig()
    ranges = mapper.get_hot_ranges(
        include_directory_metadata=policy.directory_metadata.enabled,
        include_full_fat=policy.fat.enabled,
    )
    command = build_nbdsh_command(socket_path, ranges)
    try:
        if stop_event is not None:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if stop_event.wait(0.1):
                    process.kill()
                    process.wait()
                    return 0
                if time.monotonic() >= deadline:
                    process.kill()
                    process.wait()
                    raise PreloadError("nbdsh metadata preload timed out")
            result = subprocess.CompletedProcess(command, process.returncode, "", "")
        else:
            result = runner(
                command,
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


class PrefetchWorker:
    """Run cancellable asynchronous prefetch passes for one generation."""

    def __init__(self, mapper, *, socket_path: str, policy: PrefetchConfig, on_pass: Callable[[int], None]) -> None:
        """Initialize worker with immutable mapper and prefetch policy."""
        self.mapper = mapper
        self.socket_path = socket_path
        self.policy = policy
        self.on_pass = on_pass
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="remotepfs-prefetch", daemon=True)

    def start(self) -> None:
        """Start background prefetch."""
        self.thread.start()

    def stop(self) -> None:
        """Request stop and wait for worker exit."""
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        """Prefetch immediately, then refresh at configured intervals."""
        intervals = [
            category.refresh_interval_seconds
            for category in (self.policy.directory_metadata, self.policy.fat)
            if category.enabled
        ]
        if not intervals:
            return
        interval = min(intervals)
        while not self.stop_event.is_set():
            try:
                requested = preload(
                    self.mapper,
                    socket_path=self.socket_path,
                    policy=self.policy,
                    stop_event=self.stop_event,
                )
                if self.stop_event.is_set():
                    return
                self.on_pass(requested)
            except (OSError, PreloadError) as error:
                logger.warning("metadata prefetch failed: %s", error)
            if self.stop_event.wait(interval):
                return
