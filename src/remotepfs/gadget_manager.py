"""Linux ConfigFS manager for the read-only USB mass-storage gadget."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

GADGET_NAME = "remotepfs"
VENDOR_ID = "0x1d6b"
PRODUCT_ID = "0x0104"


class GadgetError(RuntimeError):
    """USB gadget operation failed."""


class GadgetManager:
    """Create, bind, inspect, and tear down a ConfigFS mass-storage gadget."""

    def __init__(
        self,
        *,
        configfs_root: str = "/sys/kernel/config/usb_gadget",
        udc_root: str = "/sys/class/udc",
        serial_path: str = "/etc/remotepfs/serial",
    ) -> None:
        """Initialize manager with injectable filesystem roots."""
        self.root = Path(configfs_root) / GADGET_NAME
        self.udc_root = Path(udc_root)
        self.serial_path = Path(serial_path)

    def _write(self, path: Path, value: str) -> None:
        """Write one ConfigFS attribute."""
        try:
            path.write_text(value, encoding="utf-8")
        except OSError as exc:
            raise GadgetError(f"cannot write {path}: {exc}") from exc

    def _serial(self) -> str:
        """Load persistent serial or create one."""
        try:
            value = self.serial_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
        value = f"REMOTEPFS-{uuid.uuid4().hex[:12].upper()}"
        try:
            self.serial_path.parent.mkdir(parents=True, exist_ok=True)
            self.serial_path.write_text(value + "\n", encoding="utf-8")
        except OSError:
            # Real ConfigFS setup still works when serial persistence is read-only.
            pass
        return value

    def bind(self, lun_file: str = "/dev/nbd0", udc: str | None = None) -> str:
        """Configure and bind read-only mass storage gadget.

        Args:
            lun_file: Backing block device.
            udc: UDC name; first available UDC when omitted.

        Returns:
            Bound UDC name.
        """
        chosen_udc = udc or self._first_udc()
        if not chosen_udc:
            raise GadgetError("no USB Device Controller found")
        strings = self.root / "strings" / "0x409"
        config = self.root / "configs" / "c.1"
        config_strings = config / "strings" / "0x409"
        lun = self.root / "functions" / "mass_storage.0" / "lun.0"
        for directory in (strings, config_strings, config, lun):
            directory.mkdir(parents=True, exist_ok=True)
        self._write(self.root / "idVendor", VENDOR_ID)
        self._write(self.root / "idProduct", PRODUCT_ID)
        self._write(self.root / "bcdDevice", "0x0100")
        self._write(self.root / "bcdUSB", "0x0300")
        self._write(strings / "manufacturer", "RemotePFS")
        self._write(strings / "product", "Virtual exFAT Drive")
        self._write(strings / "serialnumber", self._serial())
        self._write(config / "MaxPower", "250")
        self._write(config / "bmAttributes", "0x80")
        self._write(config_strings / "configuration", "RemotePFS Config")
        self._write(lun / "file", lun_file)
        self._write(lun / "ro", "1")
        self._write(lun / "nofua", "1")
        self._write(lun / "forced_eject", "1")
        self._write(self.root / "functions" / "mass_storage.0" / "stall", "1")
        link = config / "mass_storage.0"
        if not link.exists():
            os.symlink(self.root / "functions" / "mass_storage.0", link)
        self._write(self.root / "UDC", chosen_udc)
        return chosen_udc

    def unbind(self) -> None:
        """Unbind gadget and remove ConfigFS tree when possible."""
        if self.root.exists():
            udc = self.root / "UDC"
            if udc.exists():
                self._write(udc, "")
            link = self.root / "configs" / "c.1" / "mass_storage.0"
            if link.is_symlink():
                link.unlink()

    def eject(self) -> None:
        """Request media eject through the LUN attribute."""
        path = self.root / "functions" / "mass_storage.0" / "lun.0" / "forced_eject"
        if path.exists():
            self._write(path, "1")

    def status(self) -> dict[str, object]:
        """Return gadget binding status."""
        udc_path = self.root / "UDC"
        try:
            udc = udc_path.read_text(encoding="utf-8").strip()
        except OSError:
            udc = ""
        lun = self.root / "functions" / "mass_storage.0" / "lun.0" / "file"
        try:
            lun_file = lun.read_text(encoding="utf-8").strip() or None
        except OSError:
            lun_file = None
        return {"udc_bound": bool(udc), "udc_name": udc or None, "lun_file": lun_file, "lun_ro": True}

    def _first_udc(self) -> str | None:
        """Return first available UDC name."""
        try:
            return next((entry.name for entry in sorted(self.udc_root.iterdir()) if entry.name), None)
        except OSError:
            return None
