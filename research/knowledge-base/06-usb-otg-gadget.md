# 06 - USB OTG Mass Storage Gadget Emulation

## Source

- Linux kernel configfs gadget docs: https://www.kernel.org/doc/Documentation/usb/gadget_configfs.rst
- Mass Storage Gadget docs: https://docs.kernel.org/usb/mass-storage.html
- f_mass_storage.c source: https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_mass_storage.c
- configfs-usb-gadget ABI: https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget
- configfs-usb-gadget-mass_storage ABI: https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget-mass_storage
- sunxi musb driver: https://github.com/torvalds/linux/blob/master/drivers/usb/musb/sunxi.c
- A733 Datasheet v0.93: https://dl.radxa.com/cubie/a7a/docs/hw/datasheet/A733_Datasheet_V0.93.pdf
- linux-sunxi A733 page: https://linux-sunxi.org/A733
- Researched: 2026-09-05

## Executive Summary

Linux exposes USB gadget functionality through configfs at `/sys/kernel/config/usb_gadget/`. The `mass_storage` function (`f_mass_storage.ko`) emulates a USB mass storage device by presenting a backing file as a SCSI logical unit. For the Cubie A7S (Allwinner A733), USB OTG goes through musb (USB 2.0) or dwc3 (USB 3.1 Gen2) controllers. Neither has mainline support for sun60i/A733 as of Sep 2026 — the Radxa BSP kernel (Linux 6.6 based) is required.

## configfs Gadget Setup

### Prerequisites

```bash
# Load required modules
modprobe libcomposite
modprobe usb_f_mass_storage

# Mount configfs (if not auto-mounted)
mount -t configfs none /sys/kernel/config
```

### Step-by-Step Gadget Creation

```bash
CONFIGFS=/sys/kernel/config/usb_gadget
GADGET=$CONFIGFS/g1

# 1. Create gadget directory
mkdir -p $GADGET

# 2. Set USB VID/PID (use standard or custom)
echo 0x1d6b > $GADGET/idVendor   # Linux Foundation
echo 0x0104 > $GADGET/idProduct  # Multifunction Composite Gadget

# 3. Set device version (BCD)
echo 0x0100 > $GADGET/bcdDevice  # v1.0.0
echo 0x0200 > $GADGET/bcdUSB     # USB 2.0 (change to 0x0300 for USB 3.0)

# 4. Set device class (Misc / Interface-specific)
echo 0xEF > $GADGET/bDeviceClass
echo 0x02 > $GADGET/bDeviceSubClass
echo 0x01 > $GADGET/bDeviceProtocol

# 5. Create English strings
mkdir -p $GADGET/strings/0x409
echo "PSBrew"                > $GADGET/strings/0x409/manufacturer
echo "RemotePFS USB Storage" > $GADGET/strings/0x409/product
echo "000000000001"          > $GADGET/strings/0x409/serialnumber

# 6. Create mass storage function
mkdir -p $GADGET/functions/mass_storage.usb0

# 7. Configure LUN (backing file)
echo "/data/ps5-storage.img" > $GADGET/functions/mass_storage.usb0/lun.0/file
echo 0                       > $GADGET/functions/mass_storage.usb0/lun.0/removable
echo 0                       > $GADGET/functions/mass_storage.usb0/lun.0/ro
echo 1                       > $GADGET/functions/mass_storage.usb0/lun.0/nofua

# 8. Enable stall (kernel docs: "You should set it to true" for host compatibility)
echo 1 > $GADGET/functions/mass_storage.usb0/stall
# 9. Create configuration
mkdir -p $GADGET/configs/c.1/strings/0x409
echo "Mass Storage" > $GADGET/configs/c.1/strings/0x409/configuration
echo 900 > $GADGET/configs/c.1/MaxPower  # 900 mA (USB 3.0 max)

# 10. Link function to configuration
ln -s $GADGET/functions/mass_storage.usb0 $GADGET/configs/c.1/

# 11. Bind to UDC (USB Device Controller)
echo "musb-hdrc.0.auto" > $GADGET/UDC
```

### Finding the UDC

```bash
ls /sys/class/udc/
# Expected outputs on Cubie A7S (BSP kernel):
#   musb-hdrc.0.auto    (USB 2.0 OTG via musb)
#   or
#   dwc3.0.auto         (USB 3.1 Gen2 via dwc3)
```

## mass_storage Function Parameters

### LUN Attributes (per LUN in `lun.N/`)

| Attribute | Read/Write | Description |
|-----------|------------|-------------|
| `file` | RW | Path to backing file or block device |
| `ro` | RW | Read-only flag (1 = read-only) |
| `removable` | RW | Media removable flag (1 = ejectable) |
| `cdrom` | RW | CD-ROM emulation (1 = CD-ROM) |
| `nofua` | RW | Ignore FUA bit in SCSI writes |

### Global Attributes (in function directory)

| Attribute | Description |
|-----------|-------------|
| `stall` | Allow halting bulk endpoints (1 = enabled, per kernel docs) |

### Module Parameters (g_mass_storage.ko)

```
file=<path>[,<path>...]    Backing files for each LUN
removable=<b>[,<b>...]     Removable flag per LUN
cdrom=<b>[,<b>...]         CD-ROM flag per LUN
ro=<b>[,<b>...]            Read-only flag per LUN
nofua=<b>[,<b>...]         No FUA flag per LUN
luns=<N>                   Number of LUNs (max 8)
stall=<b>                  Halt bulk endpoints
```

### Legacy Module Parameters

```
idVendor=<16-bit>          USB Vendor ID
idProduct=<16-bit>         USB Product ID
bcdDevice=<16-bit>         Device version (BCD)
iManufacturer=<string>     Manufacturer string
iProduct=<string>          Product string
iSerialNumber=<string>     Serial number string
```

## SCSI Command Handling

The `f_mass_storage` driver emulates a SCSI block device. Key SCSI commands handled:

| Command | Opcode | Purpose |
|---------|--------|---------|
| TEST_UNIT_READY | 0x00 | Check device ready |
| REQUEST_SENSE | 0x03 | Return sense data |
| INQUIRY | 0x12 | Return device info (vendor, product, etc.) |
| MODE_SENSE(6) | 0x1A | Return mode pages (write protect, caching) |
| MODE_SENSE(10) | 0x5A | Extended mode sense |
| START_STOP_UNIT | 0x1B | Eject/load media (removable LUNs only) |
| PREVENT_ALLOW_MEDIUM_REMOVAL | 0x1E | Lock/unlock media removal |
| READ_CAPACITY(10) | 0x25 | Return max LBA and block size (≤ 2 TB) |
| READ_CAPACITY(16) | 0x9E | Extended read capacity (up to ~8 ZB) |
| READ(10) | 0x28 | Read blocks (≤ 2 TB range) |
| READ(16) | 0x88 | Extended read (up to ~8 ZB range) |
| WRITE(10) | 0x2A | Write blocks (≤ 2 TB range) |
| WRITE(16) | 0x8A | Extended write (up to ~8 ZB range) |
| SYNCHRONIZE_CACHE(10) | 0x35 | Flush write cache |
| VERIFY(10) | 0x2F | Verify blocks without transferring |
| READ(6) | 0x08 | Read blocks (6-byte CDB, legacy, <= 2 GB) |
| READ(12) | 0xA8 | Read blocks (12-byte CDB, <= 2 TB) |
| WRITE(6) | 0x0A | Write blocks (6-byte CDB, legacy, <= 2 GB) |
| WRITE(12) | 0xAA | Write blocks (12-byte CDB, <= 2 TB) |
| MODE_SELECT(6) | 0x15 | Set mode pages (6-byte CDB) |
| MODE_SELECT(10) | 0x55 | Extended mode select |

### f_mass_storage Architecture

- **Buffers**: Double-buffered with two 16 KiB buffers (`FSG_BUFLEN`=16384 in storage_common.h). Buffer count may be tunable when `CONFIG_USB_GADGET_DEBUG_FILES` exposes `num_buffers`. Not configurable via configfs.
- **Transfer**: Bulk-only transport (BOT) protocol.
- **Chunk size**: I/O processed in 16 KiB chunks (`FSG_BUFLEN=16384` in storage_common.h). SCSI commands loop with double buffering until transfer length satisfied. Not configurable via configfs or module parameters.
- **Queue depth**: Single command at a time (synchronous, no NCQ).

### do_scsi_command() Flow

```
1. Receive CBW (Command Block Wrapper) with SCSI CDB
2. Dispatch to handler (do_read, do_write, do_inquiry, etc.)
3. Process via backing file (VFS read/write)
4. Send CSW (Command Status Wrapper) with completion status
```

## Allwinner A733 (sun60i) USB OTG Support

### USB Controllers

| Controller | Type | Speed | Linux Driver | Mainline |
|-----------|------|-------|-------------|----------|
| USB 2.0 OTG | musb | 480 Mbps | sunxi.c (musb_hdrc) | No* |
| USB 3.1 Gen2 DRD | dwc3 | 10 Gbps | dwc3-of-simple | No* |

*As of Sep 2026, no `sun60i` compatible string in mainline kernel musb or dwc3 drivers.

### BSP Kernel Status

- Radxa provides BSP kernel based on Allwinner AIoT Linux 6.6.
- BSP kernel: `https://github.com/radxa/kernel` branch `allwinner-aiot-linux-6.6`.
- The BSP `sunxi.c` musb driver does **not** contain `sun60i` compatible either.
- USB OTG likely enabled through a separate platform driver or DT overlay in the Radxa BSP.
- The Cubie A7S product page confirms USB OTG support.

### USB Controller Discovery

```bash
# On Cubie A7S (expected):
cat /sys/class/udc/
# Should list available USB Device Controllers

# Check which UDC to bind:
# For USB 2.0 OTG: musb-hdrc.<N>.auto
# For USB 3.1 Gen2: dwc3.<N>.auto
```

## USB 3.1 Gen2 SuperSpeed Mode

For USB 3.1 Gen2 (10 Gbps) via dwc3:

```bash
# Set USB version to 3.1 (BCD 0x0310 = USB 3.1 Gen2)
echo 0x0310 > $GADGET/bcdUSB

# Note: bcdUSB alone does not enable SuperSpeed.
# The UDC driver (dwc3) must support SuperSpeed and the
# USB controller must be wired to a USB 3.1-capable port.
# The Cubie A7S USB-C port supports USB 3.1 Gen2 (10 Gbps).

# Use SuperSpeed-capable UDC
echo "dwc3.0.auto" > $GADGET/UDC
```

### USB 3.0 SuperSpeed Mode (Fallback)

For USB 3.0 (5 Gbps) via dwc3:

```bash
# Set USB version to 3.0 (BCD 0x0300 = USB 3.0)
echo 0x0300 > $GADGET/bcdUSB

# Use SuperSpeed-capable UDC
echo "dwc3.0.auto" > $GADGET/UDC
```

## Backing Store: Image File vs Block Device

### File-Based Backing

```bash
# Create sparse image file
dd if=/dev/zero of=/data/ps5-storage.img bs=1M count=0 seek=8192

# Format as exFAT
mkfs.exfat -s 128 -n "PS5-STORAGE" /data/ps5-storage.img

# Use as backing
echo "/data/ps5-storage.img" > functions/mass_storage.usb0/lun.0/file
```

### Block Device Backing

```bash
# Create loop device (preferred for performance)
losetup /dev/loop0 /data/ps5-storage.img

# Or use dm-linear for sparse + writable overlay
# Use as backing
echo "/dev/loop0" > functions/mass_storage.usb0/lun.0/file
```

### Performance Considerations

| Parameter | Default | Notes |
|-----------|---------|-------|
| Chunk size | 16 KiB (`FSG_BUFLEN`=16384) | I/O processed in 16 KiB chunks with double buffering; no fixed per-command cap. Not configurable. |
| Buffer size | 16 KiB each | Double-buffered, 2 buffers, not configurable via configfs |
| `nofua` | 0 | Set to 1 to avoid sync writes (FUA ignored) |
| `stall` | 1 | Set to 1 per kernel docs ("You should set it to true") |

## Relevance to RemotePFS

1. **The USB gadget is the SBC-to-PS5 data path.** The `f_mass_storage` function presents the exFAT volume to PS5 as a USB flash drive.
2. **Backing store is the exFAT image** which RemotePFS manages (reads from NAS, caches locally).
3. **SCSI READ/WRITE commands** from PS5 are translated to file I/O on the backing image.
4. **READ_CAPACITY(16)** must report the correct exFAT volume size matching the image.
5. **WRITE commands with FUA** (Force Unit Access) are critical — PS5 may use synchronous writes; `nofua=1` loses data on power loss but improves performance.
6. **USB 3.1 Gen2 (10 Gbps)** via dwc3 is the performance target — matches USB Type-C front port on PS5.
7. **UDC binding** is the final step — until bound, the gadget is invisible to the PS5.
8. **BSP kernel dependency**: The Cubie A7S requires Radxa's BSP kernel for USB OTG; mainline Linux does not yet support A733 USB controllers.

## Sample Setup Script for RemotePFS

```bash
#!/bin/bash
# Setup USB mass storage gadget for RemotePFS on Cubie A7S

GADGET=/sys/kernel/config/usb_gadget/remotepfs
IMAGE=/data/ps5-storage.img

# Ensure modules loaded
modprobe libcomposite
modprobe usb_f_mass_storage

# Create image if missing
if [ ! -f "$IMAGE" ]; then
    truncate -s 8G "$IMAGE"
    mkfs.exfat -s 128 -n "PS5-STORAGE" "$IMAGE"
fi

# Setup gadget
mkdir -p "$GADGET"

echo 0x1d6b > "$GADGET/idVendor"
echo 0x0104 > "$GADGET/idProduct"
echo 0x0310 > "$GADGET/bcdUSB"
echo 0x0100 > "$GADGET/bcdDevice"

echo 0xEF > "$GADGET/bDeviceClass"
echo 0x02 > "$GADGET/bDeviceSubClass"
echo 0x01 > "$GADGET/bDeviceProtocol"

mkdir -p "$GADGET/strings/0x409"
echo "PSBrew" > "$GADGET/strings/0x409/manufacturer"
echo "RemotePFS" > "$GADGET/strings/0x409/product"
echo "000000000001" > "$GADGET/strings/0x409/serialnumber"

mkdir -p "$GADGET/functions/mass_storage.usb0"
echo "$IMAGE" > "$GADGET/functions/mass_storage.usb0/lun.0/file"
echo 1 > "$GADGET/functions/mass_storage.usb0/lun.0/nofua"
echo 1 > "$GADGET/functions/mass_storage.usb0/stall"

mkdir -p "$GADGET/configs/c.1/strings/0x409"
echo "Mass Storage" > "$GADGET/configs/c.1/strings/0x409/configuration"
echo 900 > "$GADGET/configs/c.1/MaxPower"
ln -sf "$GADGET/functions/mass_storage.usb0" "$GADGET/configs/c.1/"

# Find and bind UDC
UDC=$(ls /sys/class/udc/ | head -1)
if [ -n "$UDC" ]; then
    echo "$UDC" > "$GADGET/UDC"
    echo "Gadget bound to $UDC"
else
    echo "ERROR: No UDC found!"
fi
```

## Gadget Teardown

To cleanly detach the gadget from the PS5 without data corruption:

```bash
GADGET=/sys/kernel/config/usb_gadget/remotepfs

# 1. Unbind UDC (gadget disappears from host)
echo "" > "$GADGET/UDC"

# 2. Wait for host to notice disconnect
sleep 2

# 3. Remove function symlink from config
rm -f "$GADGET/configs/c.1/mass_storage.usb0"

# 4. Remove config strings
rmdir "$GADGET/configs/c.1/strings/0x409" 2>/dev/null
rmdir "$GADGET/configs/c.1" 2>/dev/null

# 5. Remove function (closes backing file)
rmdir "$GADGET/functions/mass_storage.usb0/lun.0"
rmdir "$GADGET/functions/mass_storage.usb0"

# 6. Remove device strings
rmdir "$GADGET/strings/0x409"

# 7. Remove gadget entirely
rmdir "$GADGET"
```

### Forced Eject

The `forced_eject` LUN attribute (if present in the kernel version) allows
triggering a media-eject event to the host without unbinding the UDC:

```bash
echo 1 > "$GADGET/functions/mass_storage.usb0/lun.0/forced_eject"
```

This sends a SCSI UNIT_ATTENTION (MEDIA CHANGED) sense to the host, forcing
the PS5 to re-read the medium. Not all kernel versions expose this attribute.

## References

1. Linux kernel configfs gadget docs: https://www.kernel.org/doc/Documentation/usb/gadget_configfs.rst
2. Mass Storage Gadget docs: https://docs.kernel.org/usb/mass-storage.html
3. configfs-usb-gadget ABI: https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget
4. mass_storage ABI: https://www.kernel.org/doc/Documentation/ABI/testing/configfs-usb-gadget-mass_storage
5. f_mass_storage.c source: https://github.com/torvalds/linux/blob/master/drivers/usb/gadget/function/f_mass_storage.c
6. sunxi musb driver: https://github.com/torvalds/linux/blob/master/drivers/usb/musb/sunxi.c
7. A733 Datasheet: https://dl.radxa.com/cubie/a7a/docs/hw/datasheet/A733_Datasheet_V0.93.pdf
8. Radxa BSP kernel: https://github.com/radxa/kernel (branch allwinner-aiot-linux-6.6; historical 5.15 branch also exists)
