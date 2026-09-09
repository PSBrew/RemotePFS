"""Unit tests for SBC-facing managers using fake roots and subprocesses."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from remotepfs.gadget_manager import GadgetError, GadgetManager
from remotepfs.mount_manager import MountError, MountManager
from remotepfs.nbdkit_manager import NbdkitManager
from remotepfs.preloader import build_nbdsh_command, preload


def test_nbdkit_command_has_required_read_only_filter_order() -> None:
    manager = NbdkitManager()
    command = manager.command(image_size=1024, mapper_state="/run/remotepfs/mapper.state")
    assert "--readonly" in command
    assert command[0:2] == ["nbdkit", "-U"]
    assert command[command.index("--pidfile") : command.index("--pidfile") + 2] == [
        "--pidfile",
        "/run/remotepfs/nbdkit.pid",
    ]
    assert "--exit-with-parent" in command
    threads_index = command.index("--threads")
    assert command[threads_index : threads_index + 2] == ["--threads", "1"]
    assert "--unix-mode=0600" not in command
    assert command[command.index("python") + 1] == "/opt/remotepfs/source/src/remotepfs/remotepfs_nbd.py"
    assert command[command.index("python") + 2 :] == [
        "image_size=1024",
        "mapper_state=/run/remotepfs/mapper.state",
        "minblock=512",
        "maxdata=65536",
        "maxlen=134217728",
        "cache-min-block-size=262144",
        "cache-max-size=1073741824",
        "cache-on-read=true",
    ]
    assert "--user" not in command
    assert "--group" not in command


def test_nbdkit_start_hands_off_existing_state_to_systemd(tmp_path) -> None:
    state = tmp_path / "mapper.state"
    state.write_bytes(b"state")
    socket = tmp_path / "run" / "nbd.sock"
    socket.parent.mkdir()
    socket.touch()
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    manager = NbdkitManager(socket_path=str(socket), state_path=str(state), runner=runner)
    manager.start(image_size=1, mapper_state=str(state))
    assert calls == [["systemctl", "start", "--no-block", "remotepfs-nbdkit.service"]]


def test_nbdkit_disconnect_uses_legacy_kernel_control(tmp_path) -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    manager = NbdkitManager(socket_path=str(tmp_path / "nbd.sock"), runner=runner)
    manager.disconnect()

    assert calls == [["nbd-client", "-L", "-d", "/dev/nbd0"]]


def test_gadget_manager_writes_configfs_values(tmp_path, monkeypatch) -> None:
    configfs = tmp_path / "usb_gadget"
    udc = tmp_path / "udc"
    (udc / "test.udc").mkdir(parents=True)
    (udc / "test.udc" / "state").write_text("not attached")
    serial = tmp_path / "serial"
    manager = GadgetManager(configfs_root=str(configfs), udc_root=str(udc), serial_path=str(serial))
    writes = []
    original_write = manager._write

    def record_write(path, value):
        writes.append(path)
        original_write(path, value)

    monkeypatch.setattr(manager, "_write", record_write)
    bound = manager.bind(lun_file="/dev/nbd0")
    assert bound == "test.udc"
    assert (configfs / "remotepfs" / "idVendor").read_text() == "0x0781"
    assert (configfs / "remotepfs" / "functions/mass_storage.0/lun.0/ro").read_text() == "1"
    lun_file = configfs / "remotepfs/functions/mass_storage.0/lun.0/file"
    assert writes.index(configfs / "remotepfs/functions/mass_storage.0/lun.0/ro") < writes.index(lun_file)
    assert not (configfs / "remotepfs/functions/mass_storage.0/lun.0/forced_eject").exists()
    assert manager.status()["udc_bound"] is True

    writes.clear()
    manager.unbind()
    assert writes[0].name == "UDC"
    assert writes[-1].name == "file"
    assert not (configfs / "remotepfs").exists()

    manager.bind(lun_file="/dev/nbd0")
    assert manager.status()["udc_bound"] is True


def test_preloader_command_contains_each_hot_range() -> None:
    command = build_nbdsh_command("/run/remotepfs/nbd.sock", [(0, 512), (4096, 1024)])
    assert command[:4] == ["nbdsh", "-u", "nbd+unix:///?socket=/run/remotepfs/nbd.sock", "-c"]
    assert "h.pread(512, 0)" in command[-1]
    assert "h.pread(1024, 4096)" in command[-1]


def test_preload_reads_all_bounded_hot_ranges() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="", stderr="")

    mapper = SimpleNamespace(get_hot_ranges=lambda: [(0, 512), (65536, 1024)])
    assert preload(mapper, socket_path="/tmp/nbd.sock", runner=runner) == 1536
    assert len(calls) == 1
    assert "h.pread(512, 0)" in calls[0][0][-1]
    assert calls[0][1]["timeout"] == 60.0


def test_gadget_manager_auto_selects_fastest_device_udc(tmp_path) -> None:
    udc_root = tmp_path / "udc"
    slow = udc_root / "slow"
    fast = udc_root / "fast"
    for entry, speed in ((slow, "high-speed"), (fast, "super-speed")):
        entry.mkdir(parents=True)
        (entry / "state").write_text("not attached")
        (entry / "maximum_speed").write_text(speed)

    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(udc_root))
    assert manager._select_udc("auto") == "fast"


def test_gadget_manager_auto_selection_breaks_speed_ties_by_name(tmp_path) -> None:
    udc_root = tmp_path / "udc"
    for name in ("zeta", "alpha"):
        entry = udc_root / name
        entry.mkdir(parents=True)
        (entry / "state").write_text("not attached")
        (entry / "current_speed").write_text("super speed")

    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(udc_root))
    assert manager._select_udc("auto") == "alpha"


def test_gadget_manager_auto_selection_uses_current_speed_only(tmp_path) -> None:
    udc_root = tmp_path / "udc"
    entry = udc_root / "current-only"
    entry.mkdir(parents=True)
    (entry / "state").write_text("not attached")
    (entry / "current_speed").write_text("super speed plus")

    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(udc_root))
    assert manager._select_udc("auto") == "current-only"


def test_gadget_manager_auto_selection_rejects_empty_udc_root(tmp_path) -> None:
    udc_root = tmp_path / "udc"
    udc_root.mkdir()
    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(udc_root))
    with pytest.raises(GadgetError, match="no device-capable"):
        manager._select_udc("auto")


def test_gadget_manager_auto_selection_reports_udc_root_error(tmp_path, monkeypatch) -> None:
    udc_root = tmp_path / "udc"
    udc_root.mkdir()
    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(udc_root))
    original_iterdir = Path.iterdir

    def fail_for_udc_root(path):
        if path == udc_root:
            raise OSError("sysfs unavailable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_for_udc_root)
    with pytest.raises(GadgetError, match="cannot inspect"):
        manager._select_udc("auto")


def test_gadget_manager_rejects_unknown_explicit_udc(tmp_path) -> None:
    manager = GadgetManager(configfs_root=str(tmp_path / "configfs"), udc_root=str(tmp_path / "udc"))
    with pytest.raises(GadgetError, match="not device-capable"):
        manager._select_udc("missing")


def test_network_mount_rejects_non_linux(monkeypatch) -> None:
    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Darwin")
    source = SimpleNamespace(
        name="s",
        protocol="nfs",
        endpoint="host:/e",
        mount_point="/mnt/s",
        options="ro",
    )
    with pytest.raises(MountError, match="require Linux"):
        MountManager().mount(source)


def test_cifs_mount_command_uses_read_only_credentials(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("username=user\npassword=secret\n", encoding="utf-8")
    credentials.chmod(0o600)
    monkeypatch.setattr(
        "remotepfs.mount_manager.Path.stat",
        lambda _path: SimpleNamespace(st_mode=0o100600, st_uid=0),
    )
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Linux")
    source = SimpleNamespace(
        name="s",
        protocol="cifs",
        endpoint="//nas/games",
        mount_point=str(tmp_path / "mount"),
        options="ro,vers=3.1.1",
        credentials_file=str(credentials),
    )
    MountManager(runner=runner).mount(source)
    assert calls == [
        [
            "mount",
            "-t",
            "cifs",
            "-o",
            f"ro,vers=3.1.1,credentials={credentials}",
            "//nas/games",
            str(tmp_path / "mount"),
        ]
    ]


def test_cifs_mount_rejects_world_readable_credentials(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("username=user\npassword=secret\n", encoding="utf-8")
    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "remotepfs.mount_manager.Path.stat",
        lambda _path: SimpleNamespace(st_mode=0o100644, st_uid=0),
    )
    source = SimpleNamespace(
        name="s",
        protocol="cifs",
        endpoint="//nas/games",
        mount_point=str(tmp_path / "mount"),
        options="ro",
        credentials_file=str(credentials),
    )
    with pytest.raises(MountError, match="mode 0600"):
        MountManager().mount(source)


def test_cifs_mount_rejects_non_root_credentials_owner(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("username=user\npassword=secret\n", encoding="utf-8")
    credentials.chmod(0o600)
    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "remotepfs.mount_manager.Path.stat",
        lambda _path: SimpleNamespace(st_mode=0o100600, st_uid=501),
    )
    source = SimpleNamespace(
        name="s",
        protocol="cifs",
        endpoint="//nas/games",
        mount_point=str(tmp_path / "mount"),
        options="ro",
        credentials_file=str(credentials),
    )
    with pytest.raises(MountError, match="owned by root"):
        MountManager().mount(source)


def test_cifs_mount_rejects_owner_only_non_0600_mode(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text("username=user\npassword=secret\n", encoding="utf-8")
    credentials.chmod(0o400)
    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "remotepfs.mount_manager.Path.stat",
        lambda _path: SimpleNamespace(st_mode=0o100400, st_uid=0),
    )
    source = SimpleNamespace(
        name="s",
        protocol="cifs",
        endpoint="//nas/games",
        mount_point=str(tmp_path / "mount"),
        options="ro",
        credentials_file=str(credentials),
    )
    with pytest.raises(MountError, match="mode 0600"):
        MountManager().mount(source)


def test_mount_rejects_unsupported_protocol_before_directory_creation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("remotepfs.mount_manager.platform.system", lambda: "Linux")
    mount_point = tmp_path / "unsupported"
    source = SimpleNamespace(
        name="s",
        protocol="https",
        endpoint="https://example.invalid/games",
        mount_point=str(mount_point),
        options="ro",
        credentials_file=None,
    )
    with pytest.raises(MountError, match="unsupported mount protocol"):
        MountManager().mount(source)
    assert not mount_point.exists()


def test_systemd_units_preserve_privilege_split() -> None:
    orchestrator = Path("config/remotepfs.service").read_text(encoding="utf-8")
    nbdkit = Path("config/remotepfs-nbdkit.service").read_text(encoding="utf-8")
    assert "User=root" in orchestrator
    assert "User=remotepfs-nbd" in nbdkit
    assert "Group=remotepfs" in nbdkit
    assert "RuntimeDirectoryMode=0700" in nbdkit
    assert "RestrictAddressFamilies=AF_UNIX" in nbdkit
    assert "--user" not in nbdkit
    assert "--group" not in nbdkit
