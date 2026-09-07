# SBC installation

Hardware validation requires Linux SBC with USB gadget mode. macOS cannot validate kernel NBD, ConfigFS, nbdkit, NFS mounts, or PS5 enumeration.

## Requirements

- Linux SBC with USB 3 OTG/device port and UDC support.
- Debian/Ubuntu/Armbian. RemotePFS requires Python 3.11+ for its service.
- At least 4 GiB RAM recommended. Systems with approximately 4 GiB RAM and adequate free storage are suitable for initial validation.
- `nbd`, `libcomposite`, and USB mass-storage ConfigFS kernel support.
- NAS exporting game files through NFS v4.1.
- USB-C data cable and stable external SBC power.

Debian 11 ships Python 3.9. Use `uv` to install and select Python 3.11; installing Debian's `python3` package alone is not sufficient.

Before installing, verify kernel and board support:

```bash
ls /sys/class/udc
mountpoint /sys/kernel/config
sudo modprobe nbd
sudo modprobe libcomposite
sudo modprobe usb_f_mass_storage
lsmod | grep -E '^(nbd|libcomposite|usb_f_mass_storage)\b'
```

If `/boot/config-$(uname -r)` does not exist, inspect `/proc/config.gz` when available:

```bash
grep -E '^CONFIG_(BLK_DEV_NBD|USB_LIBCOMPOSITE|USB_CONFIGFS|USB_CONFIGFS_F_MASS_STORAGE)=' \
  "/boot/config-$(uname -r)" 2>/dev/null \
  || zcat /proc/config.gz 2>/dev/null | \
     grep -E '^CONFIG_(BLK_DEV_NBD|USB_LIBCOMPOSITE|USB_CONFIGFS|USB_CONFIGFS_F_MASS_STORAGE)='
```

## Install packages

Debian Bullseye package name is `nbdkit-plugin-python`, not `nbdkit-plugin-python3`.

Important: Debian's nbdkit Python plugin runs with its system Python interpreter, not the RemotePFS `.venv`. The tested Radxa Cubie A7S image reports `python_version=3.9.2` from `nbdkit --dump-plugin python`; installing Python 3.11 with `uv` does not change the nbdkit plugin interpreter. The nbdkit import path must remain Python 3.9-compatible. RemotePFS keeps TOML parsing in the service process and does not import `tomllib` from the plugin path. Tests enforce Python 3.9 parsing for the plugin import chain.

```bash
sudo apt update
sudo apt install -y git nbdkit nbdkit-plugin-python nbd-client nfs-common \
  kmod curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
sudo install -m 0755 "$HOME/.local/bin/uv" /usr/local/bin/uv
sudo /usr/local/bin/uv python install 3.11
uv --version
nbdkit --dump-plugin python
nbdkit --version
```

Obtain source on the SBC before deployment. Set `REPOSITORY_URL` to the repository URL and configure Git authentication first when the repository is private. Alternatively, copy the source tree to `/tmp/remotepfs-src` using any secure transfer method.

```bash
REPOSITORY_URL="<repository-url>"
git clone --depth 1 "$REPOSITORY_URL" /tmp/remotepfs-src
```

Copy source into the deployment path:

```bash
sudo mkdir -p /opt/remotepfs /etc/remotepfs /var/lib/remotepfs /opt/remotepfs/source
sudo cp -a /tmp/remotepfs-src/. /opt/remotepfs/source/
cd /opt/remotepfs/source
sudo uv sync --frozen --python 3.11
sudo cp config/remotepfs.conf.example /etc/remotepfs/remotepfs.conf
sudo cp config/remotepfs.service config/remotepfs-nbdkit.service /etc/systemd/system/
sudo chown root:remotepfs /var/lib/remotepfs
sudo chmod 0750 /var/lib/remotepfs
```

Create service identity and permissions:

```bash
sudo groupadd --system remotepfs
sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
  --gid remotepfs remotepfs-nbd
sudo chown root:remotepfs /etc/remotepfs/remotepfs.conf
sudo chmod 0640 /etc/remotepfs/remotepfs.conf
```

The systemd unit runs orchestration as `root` because it must mount NFS,
connect `/dev/nbd0`, and write ConfigFS. It starts the separate
`remotepfs-nbdkit.service`, which runs nbdkit as
`remotepfs-nbd:remotepfs`. Do not run the nbdkit unit as root or change the
orchestration unit to the nbdkit service account.

Edit `/etc/remotepfs/remotepfs.conf`. Keep configured `mount_point` paths under `/mnt`; the hardened orchestration unit grants write access there for NFS mount directories. Mount options must include `ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec`.

Verify source mount before compiling:

```bash
sudo mkdir -p /mnt/nas1
sudo mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
  NAS_IP:/export/path /mnt/nas1
find /mnt/nas1 -maxdepth 2 -type f | head
```


## Start and verify

```bash
sudo modprobe nbd
sudo modprobe libcomposite
sudo modprobe usb_f_mass_storage
sudo systemctl daemon-reload
sudo systemctl enable --now remotepfs
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/status
```

Expected hardware checks:

```bash
stat -c '%a %U:%G' /run/remotepfs/nbd.sock
cat /sys/block/nbd0/ro
cat /sys/kernel/config/usb_gadget/remotepfs/UDC
blockdev --getsize64 /dev/nbd0
```

Expected: socket mode `600`, NBD read-only `1`, non-empty UDC, and device size matching configured image size.

## PS5 test

1. Connect SBC OTG/device port to PS5 with data-capable USB cable.
2. Confirm PS5 detects read-only extended storage.
3. Confirm ShadowMountPlus sees configured directories and `param.sfo` game folders.
4. Read one small file, then launch a test game.
5. Record `journalctl -u remotepfs`, `dmesg`, API status, and PS5 behavior.

Do not claim PS5 compatibility until this procedure passes on target hardware.
