"""Software-only service lifecycle tests for macOS."""

from __future__ import annotations

from fastapi.testclient import TestClient

import remotepfs.service as service_module
from remotepfs.api import create_app
from remotepfs.service import RemotePfsService


def test_service_starts_and_health_works_without_sbc_hardware(tmp_path) -> None:
    source = tmp_path / "game.bin"
    source.write_bytes(b"game")
    config_path = tmp_path / "remotepfs.yaml"
    config_path.write_text(
        f"""global:
  image_size_gib: 1
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: local
    protocol: nfs
    endpoint: localhost:/exports
    mount_point: {tmp_path}
    options: ro
entries:
  - virtual_path: game.bin
    source: {source}
    type: file
""",
        encoding="utf-8",
    )
    service = RemotePfsService(str(config_path))
    with TestClient(create_app(service)) as client:
        assert client.get("/api/health").json()["status"] == "healthy"
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/games").json()["games"][0]["virtual_path"] == "game.bin"
    assert service.state == "stopped"


def test_linux_compile_mounts_unmounted_sources(tmp_path, monkeypatch) -> None:
    source = tmp_path / "game.bin"
    source.write_bytes(b"game")
    config_path = tmp_path / "remotepfs.yaml"
    config_path.write_text(
        f"""global:
  image_size_gib: 1
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: nas
    protocol: nfs
    endpoint: nas:/games
    mount_point: {tmp_path}
    options: ro
entries:
  - virtual_path: game.bin
    source: {source}
    type: file
""",
        encoding="utf-8",
    )

    class FakeMounts:
        def __init__(self) -> None:
            self.mounted: list[str] = []

        def is_mounted(self, mount_point: str) -> bool:
            return False

        def mount(self, source) -> None:
            self.mounted.append(source.mount_point)

    mounts = FakeMounts()
    monkeypatch.setattr("remotepfs.service.platform.system", lambda: "Linux")
    service = RemotePfsService(str(config_path), mount_manager=mounts)
    result = service.compile_config(config_path.read_text(encoding="utf-8"))
    assert result["status"] == "compiled"
    assert mounts.mounted == [str(tmp_path)]


def test_linux_startup_mounts_and_activates_stack(tmp_path, monkeypatch) -> None:
    import asyncio

    source = tmp_path / "game.bin"
    source.write_bytes(b"game")
    config_path = tmp_path / "remotepfs.yaml"
    config_path.write_text(
        f"""global:
  image_size_gib: 1
  label: REMOTEPFS
  oem_name: REMOTEPF
sources:
  - name: nas
    protocol: nfs
    endpoint: nas:/games
    mount_point: {tmp_path}
    options: ro
entries:
  - virtual_path: game.bin
    source: {source}
    type: file
""",
        encoding="utf-8",
    )

    class FakeMounts:
        def is_mounted(self, mount_point: str) -> bool:
            return False

        def mount(self, source) -> None:
            return None

        def status(self, source):
            return {"name": source.name, "mounted": True}

    class FakeNbd:
        socket_path = str(tmp_path / "nbd.sock")

        def __init__(self) -> None:
            self.started = False
            self.connected = False

        def start(self, **kwargs) -> None:
            self.started = True

        def connect(self) -> None:
            self.connected = True

        def is_ready(self) -> bool:
            return self.connected

        def stop(self) -> None:
            return None

    class FakeGadget:
        def __init__(self) -> None:
            self.bound = False

        def bind(self, udc: str = "auto", **kwargs) -> None:
            self.bound = True

        def unbind(self) -> None:
            return None

        def status(self):
            return {"udc_bound": self.bound, "udc_name": None, "lun_file": None, "lun_ro": True}

    nbd = FakeNbd()
    gadget = FakeGadget()
    monkeypatch.setattr("remotepfs.service.grp.getgrnam", lambda name: type("Group", (), {"gr_gid": 0})())
    monkeypatch.setattr("remotepfs.service.os.chown", lambda path, uid, gid: None)
    monkeypatch.setattr("remotepfs.service.platform.system", lambda: "Linux")
    monkeypatch.setattr("remotepfs.preloader.preload", lambda mapper, socket_path: mapper.total_bytes)
    service = RemotePfsService(
        str(config_path),
        state_path=str(tmp_path / "mapper.state"),
        mount_manager=FakeMounts(),
        nbd_manager=nbd,
        gadget_manager=gadget,
    )
    asyncio.run(service.startup())
    assert service.state == "running"
    assert nbd.started is True
    assert nbd.connected is True
    assert gadget.bound is True
    assert (tmp_path / "mapper.state").stat().st_mode & 0o777 == 0o640


def test_cli_accepts_config_before_or_after_serve(monkeypatch, tmp_path) -> None:
    seen: list[str] = []
    monkeypatch.setattr(service_module, "run_server", lambda path: seen.append(path))
    assert service_module.main(["--config", str(tmp_path / "before"), "serve"]) == 0
    assert service_module.main(["serve", "--config", str(tmp_path / "after")]) == 0
    assert seen == [str(tmp_path / "before"), str(tmp_path / "after")]
