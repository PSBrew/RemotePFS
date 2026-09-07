# Radxa Cubie A7S USB OTG and Gadget Field Notes

> Research and field validation: 2026-09-07
> Board: Radxa Cubie A7S, Allwinner A733, Radxa BSP Linux 6.6
> Scope: USB-C port roles, UDC selection, ConfigFS mass-storage behavior, and RemotePFS recovery

## Executive summary

RemotePFS must bind its ConfigFS gadget to the UDC connected to the USB-C OTG/device connector. The first UDC listed by sysfs is not necessarily the correct physical port.

- `4100000.udc-controller` registers as `sunxi_usb_udc` and is the UDC currently used by RemotePFS.
- `6a00000.xhci2-controller` resolves below `12.usbc2` and registers as `dwc3-gadget`; this does not prove that USB-C 2 currently operates in device role.
- The board documentation identifies USB-C 2 as USB 3.2, DisplayPort Alt Mode, and OTG. USB-C 1 is USB 2.0 and power input, with OTG support.
- The tested session successfully used USB-C 1. USB-C 2 remains unverified, not proven host-only and not proven to have gadget mode disabled.
- UDC-to-port mapping remains BSP role-dependent. Do not infer it from the UDC name alone or bind a different UDC ad hoc.
- ConfigFS teardown must clear `UDC`, remove LUN backing, unlink functions, and remove the gadget tree before rebuilding it. Stale LUN state can make a restart fail with `EBUSY`.

## Physical port contract

Use board documentation and runtime role state together. Product labels describe capability; the running device tree and BSP kernel decide which controller is available as a gadget.

| Connector | Documented capability | RemotePFS use |
|---|---|---|
| USB-C 1 | USB 2.0, 5 V power input, OTG | Possible gadget path, lower bandwidth; do not rely on it for board power during experiments |
| USB-C 2 | USB 3.2, DisplayPort Alt Mode, OTG | Preferred PS5 gadget/device connector |
| USB-A | USB host | Not a gadget connector |

A cable connected to a connector operating in host role cannot make a ConfigFS gadget visible. For PS5 or Windows validation, connect to a USB-C OTG/device connector and verify the selected UDC reaches `configured`; do not classify USB-C 2 as host-only without runtime evidence.

## Observed RemotePFS connection

On 2026-09-07, the working connection used the first USB-C connector, farther from the Ethernet port and also used for board power. This is the documented USB-C 1 path: USB 2.0 with OTG support and 5 V power input.

The SBC reported:

```text
/sys/class/udc/4100000.udc-controller/current_speed=high-speed
/sys/class/udc/4100000.udc-controller/maximum_speed=high-speed
```

`high-speed` means USB 2.0 High-Speed, with a 480 Mb/s signaling rate. It is not USB 3.x SuperSpeed. Find negotiated speed from the SBC:

```bash
for file in /sys/class/udc/*/current_speed /sys/class/udc/*/maximum_speed; do
    printf '%s=' "$file"
    cat "$file"
done
```

Find selected connector and gadget state from the SBC:

```bash
for udc in /sys/class/udc/*; do
    printf '%s state=' "$(basename "$udc")"
    cat "$udc/state"
done
cat /sys/kernel/config/usb_gadget/remotepfs/UDC
```

`configured` confirms host enumeration completed. `high-speed` confirms USB 2.0 negotiation on this connection.

## Runtime UDC discovery

Run on the SBC:

```bash
ls -l /sys/class/udc
for udc in /sys/class/udc/*; do
    printf '%s: ' "$(basename "$udc")"
    cat "$udc/state"
done
```

Inspect controller relationships before binding:

```bash
readlink -f /sys/class/udc/<udc-name>
cat /sys/class/udc/<udc-name>/uevent
find /sys/devices/platform -maxdepth 5 \\
    \( -name role -o -name data_role -o -name otg_role -o -name usb_role \) \\
    -print -exec sh -c 'printf "  "; cat "$1"' sh {} \\
    \\
```

Also inspect the relevant USB role-switch nodes and kernel log after changing the cable:

```bash
cat /sys/class/usb_role/*/role 2>/dev/null
journalctl -k -b --no-pager | grep -Ei 'dwc3|musb|udc|role|gadget|typec|usb'
```

Expected gadget validation sequence:

1. Confirm cable is in USB-C 2/device-capable connector.
2. Confirm selected UDC exists.
3. Build ConfigFS tree.
4. Write selected UDC name to `gadget/UDC`.
5. Read `gadget/UDC` and UDC `state`.
6. Confirm host sees a new USB mass-storage device.

`configured` means host enumeration completed. `not attached` means no device-capable host connection reached selected UDC. A host-only controller can remain `not attached` regardless of correct ConfigFS syntax.

## RemotePFS ConfigFS sequence

RemotePFS uses `libcomposite` and `mass_storage.0`. Required read-only settings:

```bash
modprobe libcomposite
modprobe usb_f_mass_storage
mount -t configfs none /sys/kernel/config 2>/dev/null || true

GADGET=/sys/kernel/config/usb_gadget/remotepfs
LUN=$GADGET/functions/mass_storage.0/lun.0

mkdir -p "$GADGET"
echo 0x1d6b > "$GADGET/idVendor"
echo 0x0104 > "$GADGET/idProduct"
mkdir -p "$GADGET/strings/0x409"
printf '%s' 'PSBrew' > "$GADGET/strings/0x409/manufacturer"
printf '%s' 'RemotePFS USB Storage' > "$GADGET/strings/0x409/product"
printf '%s' '000000000001' > "$GADGET/strings/0x409/serialnumber"

mkdir -p "$GADGET/functions/mass_storage.0"
printf '%s' '/path/to/backing-device' > "$LUN/file"
printf '%s' '1' > "$LUN/ro"
printf '%s' '1' > "$LUN/nofua"
printf '%s' '1' > "$GADGET/functions/mass_storage.0/stall"

mkdir -p "$GADGET/configs/c.1/strings/0x409"
printf '%s' 'Mass Storage' > "$GADGET/configs/c.1/strings/0x409/configuration"
ln -s "$GADGET/functions/mass_storage.0" "$GADGET/configs/c.1/"
printf '%s' '<selected-udc>' > "$GADGET/UDC"
```

RemotePFS also enforces read-only at NBD and block-device layers. USB `ro=1` is necessary but not sufficient.

## Safe teardown and restart

Never overwrite a bound gadget tree during restart. Teardown first:

```bash
GADGET=/sys/kernel/config/usb_gadget/remotepfs

if [ -e "$GADGET/UDC" ]; then
    printf '' > "$GADGET/UDC"
fi

if [ -e "$GADGET/functions/mass_storage.0/lun.0/file" ]; then
    printf '' > "$GADGET/functions/mass_storage.0/lun.0/file"
fi

[ -L "$GADGET/configs/c.1/mass_storage.0" ] && \
    unlink "$GADGET/configs/c.1/mass_storage.0"
rm -rf "$GADGET/functions/mass_storage.0" "$GADGET/configs/c.1" \
    "$GADGET/strings/0x409" "$GADGET"
```

ConfigFS virtual attribute files cannot be unlinked. Teardown must unlink symlinks and remove directories. `os_desc` is a ConfigFS-managed directory on some kernels; removal may fail or be unnecessary after the gadget root is removed. Do not treat that failure alone as proof that mass-storage teardown failed.

Observed failure pattern:

- old LUN remains attached to `/dev/nbd0`;
- service restart writes new LUN attributes;
- kernel returns `EBUSY` because backing media is still in use.

Correct recovery order:

1. Stop RemotePFS service.
2. Disconnect NBD client and clear `/dev/nbd0`.
3. Unbind and tear down ConfigFS gadget.
4. Confirm no stale LUN file remains.
5. Start service and bind fresh gadget.

A physical reboot clears kernel state when stale NBD or ConfigFS ownership cannot be removed safely. Use external stable power while testing OTG roles. Do not assume software changed board power rails, but avoid using an OTG/power connector as the only supply during controller experiments.

## RemotePFS implementation requirements

- Select UDC from explicit configuration or validated runtime discovery.
- Prefer UDC whose resolved sysfs path matches the USB-C 2 device-capable controller.
- Reject ambiguous auto-detection instead of binding first result.
- Check UDC state before and after bind.
- Teardown old gadget before writing new LUN attributes.
- Keep systemd service shutdown ordered: disconnect NBD, unbind gadget, remove ConfigFS tree.
- Log selected UDC, resolved controller path, bind result, and UDC state.
- Keep host enumeration and PS5 validation as hardware acceptance tests, not macOS unit tests.

## Evidence and limitations

Local field evidence confirmed ConfigFS creation, UDC binding, NBD read-only attachment, and service health. Host enumeration was not confirmed during this session because the selected connection reported no device attachment. The board's USB-C port mapping came from Radxa documentation plus runtime controller and role inspection.

Do not generalize UDC names across BSP releases. Names such as `4100000.udc-controller` and `6a00000.xhci2-controller` are device-tree and kernel-instance details, not stable API identifiers.

## Sources

1. Radxa Cubie A7S hardware documentation: https://docs.radxa.com/en/cubie/a7s
2. Linux USB gadget ConfigFS documentation: https://docs.kernel.org/usb/gadget_configfs.html
3. Linux Mass Storage Gadget documentation: https://docs.kernel.org/usb/mass-storage.html
4. Linux ConfigFS USB gadget ABI: https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget
5. RemotePFS implementation: `src/remotepfs/gadget_manager.py`, `src/remotepfs/service.py`
6. RemotePFS USB design: `specs/03-usb-gadget-config.md`, `plans/01-project-roadmap.md`, `plans/02-design-decisions.md`
