"""Unit tests for SBC-facing managers using fake roots and subprocesses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remotepfs.gadget_manager import GadgetManager
from remotepfs.mount_manager import MountError, MountManager
from remotepfs.nbdkit_manager import NbdkitManager
from remotepfs.preloader import build_nbdsh_command


def test_nbdkit_command_has_required_read_only_filter_order() -> None:
    manager = NbdkitManager()
    command = manager.command(image_size=1024, mapper_state="/run/remotepfs/mapper.state")
    assert "--readonly" in command
    assert command[0:2] == ["nbdkit", "-U"]
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
    assert calls == [["systemctl", "start", "remotepfs-nbdkit.service"]]


def test_gadget_manager_writes_configfs_values(tmp_path) -> None:
    configfs = tmp_path / "usb_gadget"
    udc = tmp_path / "udc"
    (udc / "test.udc").mkdir(parents=True)
    serial = tmp_path / "serial"
    manager = GadgetManager(configfs_root=str(configfs), udc_root=str(udc), serial_path=str(serial))
    bound = manager.bind(lun_file="/dev/nbd0")
    assert bound == "test.udc"
    assert (configfs / "remotepfs" / "idVendor").read_text() == "0x1d6b"
    assert (configfs / "remotepfs" / "functions/mass_storage.0/lun.0/ro").read_text() == "1"
    assert manager.status()["udc_bound"] is True


def test_preloader_command_contains_each_hot_range() -> None:
    command = build_nbdsh_command("/run/remotepfs/nbd.sock", [(0, 512), (4096, 1024)])
    assert command[:4] == ["nbdsh", "-u", "nbd+unix:///?socket=/run/remotepfs/nbd.sock", "-c"]
    assert "h.pread(bytearray(512), 0)" in command[-1]
    assert "h.pread(bytearray(1024), 4096)" in command[-1]


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
