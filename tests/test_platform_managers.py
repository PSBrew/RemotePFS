"""Unit tests for SBC-facing managers using fake roots and subprocesses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remotepfs.gadget_manager import GadgetManager
from remotepfs.nbdkit_manager import NbdkitManager
from remotepfs.nfs_manager import NfsError, NfsManager
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


def test_nfs_mount_rejects_non_linux(monkeypatch) -> None:
    monkeypatch.setattr("remotepfs.nfs_manager.platform.system", lambda: "Darwin")
    source = SimpleNamespace(name="s", server="host", export="/e", mount_point="/mnt/s", nfs_options="")
    with pytest.raises(NfsError, match="require Linux"):
        NfsManager().mount(source)


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
