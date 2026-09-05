# 03 — USB Gadget Configuration

> **RemotePFS context:** SBC exposes a virtual exFAT block device to PS5 via USB
> gadget's `g_mass_storage`. The backing store is `/dev/nbd0`, an NBD device served
> by a local nbdkit instance running a custom Python plugin. The virtual exFAT is
> built dynamically from NAS folder contents by the config system and sector mapper
> (see spec 08 and spec 06). No pre-created `.exfat` image exists on disk.

---

## 1. Architecture Overview

```
PS5 USB host
    |
    v
[g_mass_storage gadget]  file=/dev/nbd0, ro=1
    |
    v
[/dev/nbd0]  NBD client (kernel)
    |
    v  Unix socket
[nbdkit]  --filter=blocksize --filter=cache
    |
    v
[Python plugin]  pread() + extents()
    |
    v
[SectorMapper]  virtual exFAT ← config TOML
    |
    v
[NFS mounts]  NAS game files
```

The USB gadget presents `/dev/nbd0` as a read-only mass storage LUN. The PS5 sees
a standard USB block device with an exFAT filesystem. The exFAT metadata (boot
sector, FAT, directory entries) is generated on-the-fly by the nbdkit Python
plugin's sector mapper, backed by the config system's virtual path mappings to
NFS files on the NAS.

**No `.exfat` image file is ever created.** No `truncate`, no `mkfs.exfat`,
no loopback mounts. The virtual exFAT is purely in-memory metadata plus NFS file
data.

---

## 2. Kernel Requirements

Required kernel modules:

| Module              | Purpose                                      |
|---------------------|----------------------------------------------|
| `g_mass_storage`    | USB gadget mass storage function             |
| `libcomposite`      | ConfigFS-based USB gadget composition        |
| `nbd`               | Network Block Device client                  |
| `dwc2` or `dwc3`    | DesignWare USB controller (SBC-dependent)    |
| `usb_f_mass_storage`| Mass storage function driver (loaded by g_mass_storage) |

Verify at boot:
```bash
modprobe nbd
modprobe g_mass_storage
modprobe dwc2           # or dwc3, depending on SBC
```

The NBD module must be loaded before the gadget binds — the gadget's LUN file
is `/dev/nbd0`, which must exist as a valid block device at bind time.

---

## 3. NBD Device Setup

Before the USB gadget binds, the NBD device must be connected to the local
nbdkit instance:

```bash
# Start nbdkit with the Python plugin (see spec 06)
nbdkit -U /run/remotepfs/nbd.sock \
       --filter=blocksize \
       --filter=cache \
       python ./plugin.py \
       config=/etc/remotepfs/config.toml &

# Connect kernel NBD client to the local Unix socket
nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0

# Wait for device readiness (nbd0 must be sized before gadget bind)
while [ "$(cat /sys/block/nbd0/size)" -le 0 ] 2>/dev/null; do sleep 0.1; done
blockdev --getsize64 /dev/nbd0

The nbdkit instance runs as a daemon listening on a Unix domain socket.
The kernel NBD client connects to this socket, creating `/dev/nbd0`. Only after
`/dev/nbd0` is a valid block device can the USB gadget bind proceed.

**Important:** `/dev/nbd0` must be a real block device node with a connected
NBD server behind it. The gadget driver calls `blkdev_get()` on the LUN file
at bind time. If the NBD device is not connected or the nbdkit process has
exited, bind fails.

---

## 4. USB Gadget Configuration

### 4.1 ConfigFS Path

Standard ConfigFS gadget layout:
```
/sys/kernel/config/usb_gadget/remotepfs/
├── idVendor
├── idProduct
├── bcdDevice
├── bcdUSB
├── strings/0x409/
│   ├── serialnumber
│   ├── manufacturer
│   └── product
├── configs/c.1/
│   ├── MaxPower
│   └── bmAttributes
└── functions/mass_storage.0/
    ├── lun.0/
    │   ├── file
    │   ├── ro
    │   ├── nofua
    │   └── forced_eject
    └── stall
```

### 4.2 USB Descriptors

| Descriptor      | Value                  | Notes                                      |
|-----------------|------------------------|--------------------------------------------|
| `idVendor`      | `0x1d6b`               | Linux Foundation (default)                 |
| `idProduct`     | `0x0104`               | Mass storage gadget                        |
| `bcdDevice`     | `0x0100`               | Device release 1.00                        |
| `bcdUSB`        | `0x0300`               | USB 3.0 (SuperSpeed)                       |
| `manufacturer`  | `"RemotePFS"`          | Custom string                              |
| `product`       | `"Virtual exFAT Drive"`| Identifies as RemotePFS                    |
| `serialnumber`  | From `/etc/remotepfs/serial` | Persistent across reboots and config changes. Generated once at install via `uuidgen`. Consoles key on serial; changing it causes re-enumeration or prompts. |

Custom vendor/product IDs may be used but require no driver changes on PS5 —
the PS5 uses the standard USB mass storage class driver (08h). The `serialnumber`
is read from `/etc/remotepfs/serial` at gadget setup and must remain stable across
activations and reboots to prevent PS5 re-enumeration.

### 4.3 Configuration Descriptors

| Descriptor      | Value    | Notes                                      |
|-----------------|----------|--------------------------------------------|
| `MaxPower`      | `250`    | 250 mA (2 mA units); USB 3.0 max           |
| `bmAttributes`  | `0x80`   | Bus-powered, no remote wakeup              |

The gadget operates in bus-powered mode. The SBC must provide sufficient power
via its USB-C port (if powered from PS5 USB) or via external power (recommended
for stability, especially with Wi-Fi active).

### 4.4 LUN Parameters

```bash
# Backing store: the NBD device
echo /dev/nbd0 > functions/mass_storage.0/lun.0/file

# Read-only (PS5 should not write to the virtual exFAT)
echo 1 > functions/mass_storage.0/lun.0/ro

# Disable Force Unit Access (improves caching)
echo 1 > functions/mass_storage.0/lun.0/nofua

# Stall on unknown commands
echo 1 > functions/mass_storage.0/stall
```

| Parameter     | Value | Rationale                                                      |
|---------------|-------|----------------------------------------------------------------|
| `file`        | `/dev/nbd0` | NBD block device, backed by nbdkit + virtual exFAT     |
| `ro`          | `1`   | Read-only. PS5 must not write. Prevents filesystem corruption. |
| `nofua`       | `1`   | Disable Force Unit Access. Eliminates cache flush overhead for read-only device. Improves throughput by ~10–20%. |
| `stall`       | `1`   | Stall endpoint on unsupported SCSI commands. Required for standards compliance. |

### 4.5 Binding Sequence

Bind order matters. The NBD device must be ready before UDC bind. Read-only is
enforced at three layers: nbdkit `--readonly`, `nbd-client -r`, and
`g_mass_storage` `ro=1`.

```
1. modprobe nbd g_mass_storage
2. Start nbdkit daemon: nbdkit --readonly -U /run/remotepfs/nbd.sock \
      --pidfile /run/remotepfs/nbdkit.pid --unix-mode=0600 \
      --user remotepfs-nbd --group remotepfs --exit-with-parent \
      --filter=blocksize --filter=cache python remotepfs_nbd.py
3. nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
4. Wait for device readiness: poll for /sys/block/nbd0/dev (or udevadm settle)
5. Verify: blockdev --getsize64 /dev/nbd0 > 0
6. Create ConfigFS gadget directory structure
7. Set descriptors (vendor, product, serialnumber from /etc/remotepfs/serial)
8. Set LUN parameters (file=/dev/nbd0, ro=1, nofua=1, stall=1)
9. Symlink functions/mass_storage.0 → configs/c.1/
10. echo <UDC> > UDC   # Activate gadget
```

**Critical:** Steps 9 and 10 are the bind/activate sequence. If `/dev/nbd0` is
not a valid, connected block device at step 9, the symlink will fail with
`-ENODEV`. Always wait for `/sys/block/nbd0/dev` and verify the NBD device
exists before binding. On tear down, unbind UDC **before** disconnecting
nbd-client to prevent I/O errors.

---

## 5. UDC Selection

The UDC (USB Device Controller) name is SBC-specific. Determine at runtime:

```bash
# List available UDCs
ls /sys/class/udc/

# Example output on Radxa Rock 5:
# fc000000.usb   # Typically the USB-C port (OTG-capable)
```

The script should auto-detect the correct UDC. On multi-UDC SBCs, the USB-C
port with OTG support is the correct one for gadget mode.

```bash
UDC=$(ls /sys/class/udc/ | head -1)
echo "$UDC" > /sys/kernel/config/usb_gadget/remotepfs/UDC
```

---

## 6. Forced Eject

### 6.1 Behavior

Setting `forced_eject=1` on the LUN causes the gadget to simulate a media
eject event when the host (PS5) issues a START STOP UNIT command with the
eject bit set. This is useful for:

- **Graceful shutdown:** PS5 can "eject" the drive before the gadget is torn
  down, preventing I/O errors on the PS5 side.
- **Hot-reload:** After a config change (POST /activate), the gadget can
  eject the old virtual exFAT and the PS5 sees a "media removal" followed
  by "media insertion" when the new NBD device is re-bound.

### 6.2 Configuration

```bash
echo 1 > functions/mass_storage.0/lun.0/forced_eject
```

### 6.3 Integration with Config Reload

See spec 07 (HTTP API) for the config reload flow. The atomic generation swap
works with forced eject:

1. New config is compiled and validated (POST /compile)
2. Current generation is noted
3. POST /activate triggers: `forced_eject` → PS5 sees media removal
4. nbdkit is signaled to swap to new generation (SIGHUP or Unix socket command)
5. After swap completes, gadget is re-bound → PS5 sees media insertion
6. PS5 re-scans the exFAT filesystem

The PS5 should handle this gracefully — it's the same as physically unplugging
and re-plugging a USB drive. Games may need to be re-launched if they were
accessing files during the swap.

---

## 7. SCSI Command Support

The `g_mass_storage` gadget translates USB MSC (Mass Storage Class) commands
to SCSI commands and forwards them to the block device. The following table
lists SCSI commands and their handling:

| SCSI Command              | Opcode | Supported | Notes                                      |
|---------------------------|--------|-----------|--------------------------------------------|
| TEST UNIT READY           | 0x00   | Yes       | Always returns success for mounted device  |
| REQUEST SENSE             | 0x03   | Yes       | Returns standard sense data                |
| INQUIRY                   | 0x12   | Yes       | Returns vendor/product/version strings     |
| MODE SELECT (6)           | 0x15   | No        | Stalled (read-only device)                 |
| MODE SENSE (6)            | 0x1A   | Yes       | Returns read-only, FUA disabled            |
| START STOP UNIT           | 0x1B   | Yes       | Eject if forced_eject=1                    |
| PREVENT ALLOW MEDIUM REM  | 0x1E   | Yes       | Acknowledged, no state change              |
| READ CAPACITY (10)        | 0x25   | Yes       | Returns virtual exFAT size                 |
| READ (10)                 | 0x28   | Yes       | Primary read command (up to 64K blocks)    |
| READ (12)                 | 0xA8   | Yes       | Extended read (up to 4M blocks)            |
| READ (16)                 | 0x88   | Yes       | Extended read (up to 32M blocks)           |
| WRITE (10)                | 0x2A   | No        | Stalled (read-only, ro=1)                  |
| WRITE (12)                | 0xAA   | No        | Stalled (read-only)                        |
| WRITE (16)                | 0x8A   | No        | Stalled (read-only)                        |
| VERIFY (10)               | 0x2F   | Yes       | Acknowledged no-op (data assumed correct)  |
| SYNCHRONIZE CACHE (10)    | 0x35   | No        | Ignored (nofua=1, no write cache)          |
| READ FORMAT CAPACITIES    | 0x23   | Yes       | Returns current/exFAT capacity             |
| MODE SENSE (10)           | 0x5A   | Yes       | Returns read-only, FUA disabled            |
| REPORT LUNS               | 0xA0   | Yes       | Returns LUN 0 only                         |

### 7.1 INQUIRY Response

The gadget auto-generates the INQUIRY response from the backing device. With
`/dev/nbd0`, the response reflects the nbdkit export:

```
Peripheral Device Type: 0x00 (Direct Access Block Device)
RMB: 1 (Removable Medium)
Vendor Identification: "Linux   "
Product Identification: "File-Stor Gadget "
Product Revision Level: "0409"
```

The PS5 uses the RMB (Removable Media Bit) to determine whether the device
is removable. Setting it to 1 matches `forced_eject` behavior.

### 7.2 READ CAPACITY Response

```c
struct read_capacity_data {
    __be32 max_lba;       // Last logical block address
    __be32 block_size;    // Block length in bytes (typically 512)
};
```

The block size is 512 bytes (standard for USB mass storage). The max LBA is
determined by the virtual exFAT size from the config system. For a 500 GB
virtual exFAT with 512-byte blocks: `max_lba = (500 * 1024^3 / 512) - 1`.

### 7.3 Write Protection

With `ro=1`, all WRITE commands are rejected with sense key `ILLEGAL REQUEST`
(`0x05`) and additional sense code `WRITE PROTECTED` (`0x27/0x00`). The PS5
sees a write-protected device and will not attempt to mount read-write.

---

## 8. Monitoring

### 8.1 Gadget State

```bash
# Check if gadget is bound
cat /sys/kernel/config/usb_gadget/remotepfs/UDC

# If empty, gadget is configured but not bound
# If contains UDC name, gadget is active
```

### 8.2 NBD Device Health

```bash
# Check NBD device size and existence
blockdev --getsize64 /dev/nbd0

# Check NBD connection state
cat /sys/block/nbd0/pid       # PID of nbd-client process

# Check I/O statistics
cat /sys/block/nbd0/stat      # Reads completed, sectors read, etc.
iostat -x nbd0 1              # Real-time I/O stats
```

### 8.3 nbdkit Health

```bash
# Check nbdkit process
pgrep -a nbdkit

# Check Unix socket
ls -l /run/remotepfs/nbd.sock

# nbdkit logs (if configured)
journalctl -u remotepfs-nbdkit -f
```

### 8.4 Health Check Script

```bash
#!/bin/bash
# RemotePFS health check

check_nbd() {
    if [ ! -b /dev/nbd0 ]; then
        echo "FAIL: /dev/nbd0 not a block device"
        return 1
    fi
    local size=$(blockdev --getsize64 /dev/nbd0 2>/dev/null)
    if [ -z "$size" ] || [ "$size" -eq 0 ]; then
        echo "FAIL: /dev/nbd0 has zero size"
        return 1
    fi
    echo "OK: /dev/nbd0 size=$size"
    return 0
}

check_nbdkit() {
    if ! pgrep -f nbdkit > /dev/null; then
        echo "FAIL: nbdkit not running"
        return 1
    fi
    echo "OK: nbdkit running"
    return 0
}

check_gadget() {
    local udc=$(cat /sys/kernel/config/usb_gadget/remotepfs/UDC 2>/dev/null)
    if [ -z "$udc" ]; then
        echo "WARN: gadget not bound to UDC"
        return 1
    fi
    echo "OK: gadget bound to $udc"
    return 0
}

echo "=== RemotePFS Health Check ==="
check_nbd
check_nbdkit
check_gadget
```

### 8.5 Prometheus Metrics (Future)

Planned metrics for monitoring (not in V1):
- `remotepfs_nbd_read_bytes_total`
- `remotepfs_nbd_read_errors_total`
- `remotepfs_gadget_bound` (gauge, 0 or 1)
- `remotepfs_nbdkit_uptime_seconds`
- `remotepfs_virtual_exfat_size_bytes`

---

## 9. Performance Tuning

### 9.1 Queue Depth

The kernel block layer queue depth for `/dev/nbd0` controls how many I/O
requests can be in-flight simultaneously:

```bash
# Check current queue depth
cat /sys/block/nbd0/queue/nr_requests

# Increase for better throughput (default 128 on most kernels)
echo 256 > /sys/block/nbd0/queue/nr_requests
```

Higher queue depth allows the kernel to keep the nbdkit pipeline full,
reducing idle time between requests. For sequential reads from a fast NAS,
256–512 is appropriate.

### 9.2 Read-Ahead

```bash
# Check current read-ahead
blockdev --getra /dev/nbd0

# Set aggressive read-ahead for sequential game reads
blockdev --setra 8192 /dev/nbd0   # 4096 KB
```

The kernel's read-ahead prefetches data before the PS5 requests it. For
sequential game file reads, large read-ahead (4–8 MB) improves throughput
by hiding NFS round-trip latency. The nbdkit cache filter (1 GiB) also
benefits from large read-ahead — prefetched data lands in the cache before
the PS5 needs it.

### 9.3 No Loop Device

The virtual exFAT has no backing loop device. There is no `losetup`, no
`.exfat` file, no double-buffering through the loop layer. All I/O goes:

```
PS5 → g_mass_storage → /dev/nbd0 → nbd-client → nbdkit → Python plugin → NFS
```

This is one fewer kernel I/O layer compared to a file-backed loop device
approach. The elimination of the loop layer removes:
- `loop.ko` thread overhead
- Double page cache (once for loop file, once for NBD)
- `losetup` management complexity

### 9.4 NBD Multi-Connection (Future)

Linux 5.10+ supports multiple NBD connections to the same export. This can
parallelize I/O across connections. For RemotePFS V1 (single NFS server,
bonded 2×1 GbE), a single NBD connection is sufficient to saturate the
NAS bandwidth. Multi-connection may be explored if a single connection
becomes a bottleneck on 10 GbE or with multiple NFS servers.

### 9.5 NBD Timeout

```bash
# Set shorter timeout for faster failure detection
echo 5 > /sys/block/nbd0/timeout   # 5 seconds (default 30)
```

A shorter timeout allows faster recovery if the nbdkit process hangs or
crashes. However, too short a timeout may cause spurious I/O errors if
the NAS or NFS server is temporarily slow. 5–10 seconds is reasonable
for a LAN deployment.

---

## 10. Failure Modes

### 10.1 NBD Device Not Ready

**Symptom:** Gadget bind fails with `-ENODEV`. ConfigFS symlink to
`functions/mass_storage.0` returns "No such device."

**Cause:** `/dev/nbd0` does not exist or nbd-client has not connected.
The gadget driver calls `blkdev_get()` on the LUN file at bind time
and requires a valid block device.

**Resolution:**
1. Verify nbdkit is running: `pgrep nbdkit`
2. Verify NBD connection: `cat /sys/block/nbd0/pid`
3. Reconnect: `nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0`
4. Verify size: `blockdev --getsize64 /dev/nbd0`
5. Retry gadget bind

### 10.2 nbdkit Process Crash

**Symptom:** PS5 sees I/O errors. `/dev/nbd0` still exists but all reads
return `-EIO`. `dmesg` shows `nbd0: Receive control failed`.

**Cause:** nbdkit process exited (crash, OOM, unhandled exception in Python
plugin).

**Resolution:**
1. Restart nbdkit with the same config
2. nbd-client reconnects automatically (TCP keepalive) or restart nbd-client
3. PS5 may need to re-read the affected files (game restart)
4. Check nbdkit logs for the crash cause
5. Consider systemd `Restart=always` for nbdkit

### 10.3 NAS / NFS Unreachable

**Symptom:** PS5 reads hang or return I/O errors after timeout. `dmesg`
shows `nfs: server <nas_ip> not responding, still trying`.

**Cause:** NAS is down, network link is down, or NFS server is unresponsive.
The NFS mount hangs by default (hard mount), causing nbdkit reads to block
indefinitely.

**Resolution:**
1. Restore NAS connectivity
2. Consider `soft` NFS mount option (`soft,timeo=50,retrans=2`) for faster
   failure detection at the cost of potential data corruption
3. With `hard` mount (default), NFS operations block until the NAS recovers;
   the PS5 sees a hung I/O, which is safer than I/O errors for game integrity
4. Monitor network link: `ip link show bond0`

### 10.4 ConfigFS Symlink Failure

**Symptom:** `ln -s functions/mass_storage.0 configs/c.1/` fails with
"Operation not permitted" or "Invalid argument."

**Cause:** Usually a dangling reference or incomplete ConfigFS setup. Common
causes:
- LUN file not set before symlink
- Directory structure created out of order
- Previous gadget instance not fully torn down

**Resolution:**
1. Remove and recreate the gadget directory: `rmdir /sys/kernel/config/usb_gadget/remotepfs`
2. Follow the binding sequence in section 4.5 exactly
3. Ensure functions directory exists: `mkdir -p functions/mass_storage.0`

### 10.5 SCSI Command Stalls

**Symptom:** `dmesg` shows `g_mass_storage: unknown command` or PS5 reports
"device not responding."

**Cause:** The PS5 issues a SCSI command not handled by `g_mass_storage` or
not forwarded to `/dev/nbd0`. With `stall=1`, the gadget stalls the endpoint
for unknown commands, which the PS5 should retry or ignore.

**Resolution:**
1. Check `stall` parameter is set to 1 (not 0)
2. Unknown commands with `stall=1` are standard-compliant behavior; PS5 should
   handle stalls gracefully
3. If a specific command causes persistent issues, it may need to be forwarded
   by the gadget or implemented by nbdkit (current NBD protocol does not
   support arbitrary SCSI command passthrough)

### 10.6 Virtual exFAT Size Mismatch

**Symptom:** PS5 reports wrong filesystem size, or filesystem appears
truncated.

**Cause:** The virtual exFAT metadata (boot sector, total sectors field)
does not match the NBD device size from `blockdev --getsize64`.

**Resolution:**
1. Verify the config system's computed exFAT size matches the NBD device
   size reported to nbdkit
2. The Python plugin's `get_size()` must return exactly the size of the
   virtual exFAT as described in the metadata
3. Recompile config (POST /compile) and re-activate (POST /activate)

---

## 11. Teardown Sequence

Graceful teardown order:

```bash
# 1. Unbind UDC (PS5 sees device disconnect)
echo "" > /sys/kernel/config/usb_gadget/remotepfs/UDC

# 2. Remove symlink
rm /sys/kernel/config/usb_gadget/remotepfs/configs/c.1/mass_storage.0

# 3. Remove ConfigFS directories
rmdir /sys/kernel/config/usb_gadget/remotepfs/configs/c.1/strings/0x409
rmdir /sys/kernel/config/usb_gadget/remotepfs/configs/c.1
rmdir /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0/lun.0
rmdir /sys/kernel/config/usb_gadget/remotepfs/functions/mass_storage.0
rmdir /sys/kernel/config/usb_gadget/remotepfs/strings/0x409
rmdir /sys/kernel/config/usb_gadget/remotepfs

# 4. Disconnect NBD
nbd-client -d /dev/nbd0

# 5. Stop nbdkit
pkill nbdkit
```

If `forced_eject=1` is set, the PS5 may have already ejected the media before
step 1. In that case, the PS5 sees a clean device removal rather than a
surprise disconnect.

---

## 12. udev Rules (Optional)

For automatic gadget setup on boot, udev rules can trigger when `/dev/nbd0`
appears:

```udev
# /etc/udev/rules.d/99-remotepfs.rules
ACTION=="add", KERNEL=="nbd0", RUN+="/usr/local/bin/remotepfs-bind-gadget.sh"
```

The script must be idempotent — check if the gadget is already bound before
creating ConfigFS entries.

---

## Appendix A: Full Setup Script

```bash
#!/bin/bash
set -euo pipefail

GADGET_DIR="/sys/kernel/config/usb_gadget/remotepfs"
NBDKIT_SOCK="/run/remotepfs/nbd.sock"
CONFIG_TOML="/etc/remotepfs/config.toml"

echo "=== RemotePFS USB Gadget Setup ==="

# 1. Load modules
modprobe nbd
modprobe g_mass_storage

# 2. Start nbdkit (if not already running)
if ! pgrep -f nbdkit > /dev/null; then
    echo "Starting nbdkit..."
    nbdkit -U "$NBDKIT_SOCK" \
           --filter=blocksize \
           --filter=cache \
           python /usr/local/lib/remotepfs/plugin.py \
           config="$CONFIG_TOML" &
    sleep 2
fi

# 3. Connect NBD
if [ ! -b /dev/nbd0 ]; then
    echo "Connecting NBD device..."
    nbd-client -U "$NBDKIT_SOCK" -r /dev/nbd0
fi

# 4. Verify NBD device
NBD_SIZE=$(blockdev --getsize64 /dev/nbd0)
if [ "$NBD_SIZE" -eq 0 ]; then
    echo "ERROR: /dev/nbd0 has zero size"
    exit 1
fi
echo "NBD device ready: $NBD_SIZE bytes"

# 5. Create gadget
if [ -d "$GADGET_DIR" ]; then
    echo "Gadget directory exists, tearing down..."
    echo "" > "$GADGET_DIR/UDC" 2>/dev/null || true
    rm -rf "$GADGET_DIR"
fi

mkdir -p "$GADGET_DIR"

# Descriptors
echo 0x1d6b > "$GADGET_DIR/idVendor"
echo 0x0104 > "$GADGET_DIR/idProduct"
echo 0x0100 > "$GADGET_DIR/bcdDevice"
echo 0x0300 > "$GADGET_DIR/bcdUSB"

# Strings
mkdir -p "$GADGET_DIR/strings/0x409"
echo "RemotePFS" > "$GADGET_DIR/strings/0x409/manufacturer"
echo "Virtual exFAT Drive" > "$GADGET_DIR/strings/0x409/product"
echo "REMOTEPFS000001" > "$GADGET_DIR/strings/0x409/serialnumber"

# Configuration
mkdir -p "$GADGET_DIR/configs/c.1/strings/0x409"
echo "RemotePFS Config" > "$GADGET_DIR/configs/c.1/strings/0x409/configuration"
echo 250 > "$GADGET_DIR/configs/c.1/MaxPower"
echo 0x80 > "$GADGET_DIR/configs/c.1/bmAttributes"

# Mass storage function
mkdir -p "$GADGET_DIR/functions/mass_storage.0/lun.0"
echo /dev/nbd0 > "$GADGET_DIR/functions/mass_storage.0/lun.0/file"
echo 1 > "$GADGET_DIR/functions/mass_storage.0/lun.0/ro"
echo 1 > "$GADGET_DIR/functions/mass_storage.0/lun.0/nofua"
echo 1 > "$GADGET_DIR/functions/mass_storage.0/stall"
echo 1 > "$GADGET_DIR/functions/mass_storage.0/lun.0/forced_eject"

# Bind
ln -s "$GADGET_DIR/functions/mass_storage.0" "$GADGET_DIR/configs/c.1/"

# Activate
UDC=$(ls /sys/class/udc/ | head -1)
echo "Binding to UDC: $UDC"
echo "$UDC" > "$GADGET_DIR/UDC"

echo "=== Setup complete ==="
```

---

## Appendix B: SCSI Sense Data Reference

Common sense data the PS5 may receive:

| Sense Key | ASC  | ASCQ | Meaning                          | Context                              |
|-----------|------|------|----------------------------------|--------------------------------------|
| 0x02      | 0x04 | 0x01 | NOT READY — Becoming Ready       | During gadget bind, before NBD ready |
| 0x02      | 0x04 | 0x02 | NOT READY — Initializing         | During nbdkit startup                |
| 0x05      | 0x24 | 0x00 | ILLEGAL REQUEST — Invalid Field  | Malformed SCSI CDB                   |
| 0x05      | 0x25 | 0x00 | ILLEGAL REQUEST — LUN Not Supported | Wrong LUN number                  |
| 0x05      | 0x27 | 0x00 | ILLEGAL REQUEST — Write Protected | Write command with ro=1             |
| 0x06      | 0x28 | 0x00 | UNIT ATTENTION — Media Changed   | After forced_eject + re-bind        |
| 0x06      | 0x29 | 0x00 | UNIT ATTENTION — Power On/Reset  | After gadget bind/rebind            |

With `ro=1`, the PS5 should never attempt writes. If it does (e.g., mount
attempt with write flag), it receives `WRITE PROTECTED` sense and should
fall back to read-only mount or report the device as read-only to the user.
