"""RemotePFS service lifecycle and CLI."""

from __future__ import annotations

import argparse
import grp
import json
import logging
import os
import pickle
import platform
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .api import create_app
from .config import Config, ConfigError, load, parse, validate
from .consts import (
    CACHE_HIGH_THRESHOLD_PERCENT,
    CACHE_LOW_THRESHOLD_PERCENT,
    CACHE_MAX_SIZE_BYTES,
    CACHE_MIN_BLOCK_SIZE_BYTES,
)
from .gadget_manager import GadgetManager
from .mount_manager import MountManager
from .nbdkit_manager import NbdkitManager
from .preloader import PrefetchWorker

logger = logging.getLogger(__name__)


@dataclass
class CompiledGeneration:
    """Immutable-ish compiled generation held by service."""

    generation: int
    config: Config
    mapper: Any
    created_at: datetime


class RemotePfsService:
    """Coordinate config compilation, NBD, gadget, and HTTP control plane."""

    def __init__(
        self,
        config_path: str = "/etc/remotepfs/remotepfs.yaml",
        *,
        state_path: str = "/var/lib/remotepfs/mapper.state",
        mount_manager: MountManager | None = None,
        nbd_manager: NbdkitManager | None = None,
        gadget_manager: GadgetManager | None = None,
    ) -> None:
        """Initialize service and injectable platform managers."""
        self.config_path = config_path
        self.state_path = state_path
        self.mounts = mount_manager or MountManager()
        self.nbd = nbd_manager or NbdkitManager(state_path=state_path)
        self.gadget = gadget_manager or GadgetManager()
        self.active: CompiledGeneration | None = None
        self.compiled: dict[int, CompiledGeneration] = {}
        self.next_generation = 1
        self.prefetch_bytes_requested = 0
        self.prefetch_passes = 0
        self.state = "starting"
        self.state_detail = "not_started"
        self._sync_reload_lock = threading.Lock()

    async def startup(self) -> None:
        """Load config, compile generation, and activate SBC hardware."""
        self.started_at = time.monotonic()
        try:
            config = load(self.config_path)
            generation = self._compile(config)
            if platform.system() == "Linux":
                self._activate_hardware(generation, cold_start=True)
            self.active = generation
            self.state = "running"
            self.state_detail = "serving" if platform.system() == "Linux" else "software_only_macos"
        except (ConfigError, OSError, RuntimeError) as exc:
            logger.exception("RemotePFS startup failed")
            self.state = "degraded"
            self.state_detail = str(exc)

    def _activate_hardware(self, generation: CompiledGeneration, *, cold_start: bool) -> None:
        """Start nbdkit, warm metadata, connect NBD, and bind USB gadget."""
        if not cold_start:
            self.gadget.unbind()
            self.nbd.stop()
        state_path = self.state_path
        state_file = Path(state_path)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_state = state_file.with_name(f"{state_file.name}.tmp.{os.getpid()}")
        payload = pickle.dumps(generation.mapper.to_state(), protocol=pickle.HIGHEST_PROTOCOL)
        temporary_state.write_bytes(payload)
        os.chown(temporary_state, 0, grp.getgrnam("remotepfs").gr_gid)
        temporary_state.chmod(0o640)
        os.replace(temporary_state, state_file)
        self.nbd.start(image_size=generation.config.image_size_bytes, mapper_state=state_path)
        self.nbd.connect()
        self.gadget.bind(udc=generation.config.usb_port)
        self._start_prefetch_worker(generation)

    def _start_prefetch_worker(self, generation: CompiledGeneration) -> None:
        """Start asynchronous prefetch after NBD and USB activation."""
        self._stop_prefetch_worker()
        self.prefetch_worker = PrefetchWorker(
            generation.mapper,
            socket_path=self.nbd.socket_path,
            policy=generation.config.prefetch,
            on_pass=self._record_prefetch,
        )
        self.prefetch_worker.start()

    def _record_prefetch(self, requested: int) -> None:
        """Record one completed asynchronous prefetch pass."""
        with self._sync_reload_lock:
            self.prefetch_bytes_requested += requested
            self.prefetch_passes += 1

    def _stop_prefetch_worker(self) -> None:
        """Stop active prefetch worker."""
        worker = getattr(self, "prefetch_worker", None)
        if worker is not None:
            worker.stop()
            self.prefetch_worker = None

    async def shutdown(self) -> None:
        self._stop_prefetch_worker()
        try:
            self.gadget.unbind()
        except Exception:
            pass
        try:
            self.nbd.stop()
        except Exception:
            pass
        self.state = "stopped"
        self.state_detail = "shutdown"

    def _compile(self, config: Config) -> CompiledGeneration:
        """Mount sources, validate paths, and build SectorMapper generation."""
        if platform.system() == "Linux":
            for source in config.sources:
                if not self.mounts.is_mounted(source.mount_point):
                    self.mounts.mount(source)
        for entry in config.entries:
            source = Path(entry.source)
            if not source.exists():
                raise ConfigError(f"{entry.source}: source path does not exist", field="entries.source")
            if entry.type == "file" and not source.is_file():
                raise ConfigError(f"{entry.source}: expected file", field="entries.source")
            if entry.type == "directory" and not source.is_dir():
                raise ConfigError(f"{entry.source}: expected directory", field="entries.source")
        from .exfat_builder import ExfatBuilder
        from .sector_mapper import SectorMapper

        layout = ExfatBuilder(config).build()
        mapper = SectorMapper.from_layout(layout)
        generation = CompiledGeneration(self.next_generation, config, mapper, datetime.now(UTC))
        self.next_generation += 1
        self.compiled[generation.generation] = generation
        return generation

    def compile_config(self, text: str) -> dict[str, Any]:
        """Validate and compile config without activating it."""
        try:
            raw = json.loads(text) if text.lstrip().startswith("{") else None
            config = validate(raw) if raw is not None else parse(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"JSON parse error: {exc}") from exc
        generation = self._compile(config)
        size_bytes = getattr(generation.mapper, "total_bytes", config.image_size_bytes)
        return {"status": "compiled", "generation": generation.generation, "warnings": [], "size_bytes": size_bytes}

    def activate_generation(self, generation_id: int) -> dict[str, Any]:
        """Activate compiled generation and perform hardware cutover on Linux."""
        generation = self.compiled.get(generation_id)
        if generation is None:
            raise ConfigError(f"unknown generation {generation_id}", field="generation")
        if not self._sync_reload_lock.acquire(blocking=False):
            raise RuntimeError("another reload is already running")
        started = time.monotonic()
        try:
            if platform.system() == "Linux":
                self._activate_hardware(generation, cold_start=False)
            self.active = generation
            self.state = "running"
            self.state_detail = "serving" if platform.system() == "Linux" else "software_only_macos"
            return {
                "status": "activated",
                "generation": generation.generation,
                "reload_time_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            self._sync_reload_lock.release()

    def replace_config(self, text: str) -> dict[str, Any]:
        """Compile and immediately activate config."""
        result = self.compile_config(text)
        activated = self.activate_generation(result["generation"])
        activated["status"] = "applied"
        activated["warnings"] = result["warnings"]
        return activated

    def reload_config(self) -> dict[str, Any]:
        """Compile and activate on-disk configuration."""
        return self.replace_config(Path(self.config_path).read_text(encoding="utf-8"))

    async def get_health(self) -> dict[str, Any]:
        """Return liveness status; software-only macOS remains healthy."""
        return {"status": "healthy", "uptime_seconds": int(time.monotonic() - self.started_at)}

    async def get_config_json(self) -> dict[str, Any]:
        """Return active configuration as JSON-compatible data."""
        if self.active is None:
            return {"generation": 0, "sources": [], "entries": []}
        config = self.active.config
        return {
            "generation": self.active.generation,
            "loaded_at": self.active.created_at.isoformat(),
            "global": {
                "image_size_gib": config.image_size_gib,
                "cluster_size_kib": config.cluster_size_kib,
                "label": config.label,
                "oem_name": config.oem_name,
            },
            "sources": [source.__dict__ for source in config.sources],
            "entries": [entry.__dict__ for entry in config.entries],
        }

    async def get_games(self) -> dict[str, Any]:
        """Return configured root entries and local sizes where available."""
        if self.active is None:
            return {"games": []}
        games = []
        for entry in self.active.config.entries:
            item: dict[str, Any] = {"virtual_path": entry.virtual_path, "type": entry.type}
            try:
                size = Path(entry.source).stat().st_size if entry.type == "file" else None
            except OSError:
                size = None
            if entry.type == "file":
                item["size_bytes"] = size
            games.append(item)
        return {"games": games}

    async def get_status(self) -> dict[str, Any]:
        """Return structured status response."""
        active = self.active
        gadget = self.gadget.status()
        return {
            "service": {
                "version": __version__,
                "uptime_seconds": int(time.monotonic() - self.started_at),
                "state": self.state,
                "state_detail": self.state_detail,
            },
            "gadget": {
                **gadget,
                "lun_size_bytes": getattr(active.mapper, "total_bytes", None) if active else None,
            },
            "nbd": {
                "connected": self.nbd.is_ready(),
                "socket_path": self.nbd.socket_path,
                "connections": 1 if self.nbd.is_ready() else 0,
            },
            "cache": {
                "backend": "nbdkit-cache-filter",
                "cache_on_read": True,
                "max_size_bytes": CACHE_MAX_SIZE_BYTES,
                "min_block_size_bytes": CACHE_MIN_BLOCK_SIZE_BYTES,
                "high_threshold_percent": CACHE_HIGH_THRESHOLD_PERCENT,
                "low_threshold_percent": CACHE_LOW_THRESHOLD_PERCENT,
                "prefetch_bytes_requested": self.prefetch_bytes_requested,
                "prefetch_passes": self.prefetch_passes,
            },
            "mounts": [self.mounts.status(source).__dict__ for source in active.config.sources] if active else [],
            "config": {
                "path": self.config_path,
                "last_loaded": active.created_at if active else datetime.now(UTC),
                "generation": active.generation if active else 0,
                "game_count": len(active.config.entries) if active else 0,
                "valid": active is not None,
            },
            "system": {},
        }

    def eject(self) -> None:
        """Unbind gadget without clearing state."""
        self._stop_prefetch_worker()
        self.gadget.eject()
        self.gadget.unbind()


def run_server(config_path: str) -> None:
    """Run FastAPI service under uvicorn."""
    import uvicorn

    service = RemotePfsService(config_path)
    uvicorn.run(create_app(service), host="127.0.0.1", port=8080, log_level="info")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for serve, compile, status, and shutdown."""
    parser = argparse.ArgumentParser(prog="remotepfs")
    parser.add_argument("--config", default="/etc/remotepfs/remotepfs.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config", default=argparse.SUPPRESS)
    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("path", nargs="?")
    subparsers.add_parser("status")
    subparsers.add_parser("shutdown")
    args = parser.parse_args(argv)
    if args.command == "serve":
        run_server(args.config)
        return 0
    if args.command == "compile":
        config = load(args.path or args.config)
        print(f"valid: image_size={config.image_size_bytes} bytes, entries={len(config.entries)}")
        return 0
    print(f"{args.command}: use systemd or HTTP API for lifecycle operations")
    return 0
