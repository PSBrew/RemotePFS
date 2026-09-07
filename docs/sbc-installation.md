# SBC installation

Hardware validation requires Linux SBC with USB gadget mode. macOS cannot validate kernel NBD, ConfigFS, nbdkit, network mounts, or PS5 enumeration.

## Requirements

- Linux SBC with USB 3 OTG/device port and UDC support.
- Debian/Ubuntu/Armbian. RemotePFS requires Python 3.11+ for its service.
- At least 4 GiB RAM recommended. Systems with approximately 4 GiB RAM and adequate free storage are suitable for initial validation.
- Linux CIFS client support for the default SMB backend, plus `nbd`, `libcomposite`, and USB mass-storage ConfigFS kernel support.
- NAS SMB share with read-only credentials.
- USB-C data cable and stable external SBC power.

RemotePFS also retains NFS support. NFS requires matching kernel client support and `nfs-common`; vendor kernels can omit NFS even when `nfs-common` is installed.

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

Important: Debian's nbdkit Python plugin runs with its system Python interpreter, not the RemotePFS `.venv`. The tested Radxa Cubie A7S image reports `python_version=3.9.2` from `nbdkit --dump-plugin python`; installing Python 3.11 with `uv` does not change the nbdkit plugin interpreter. The nbdkit import path must remain Python 3.9-compatible. RemotePFS parses YAML in the service process; the plugin does not import the YAML parser. Tests enforce Python 3.9 parsing for the plugin import chain.

```bash
sudo apt install -y nbdkit nbdkit-plugin-python nbd-client libnbd-bin python3-libnbd \
  cifs-utils nfs-common kmod curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
sudo install -m 0755 "$HOME/.local/bin/uv" /usr/local/bin/uv
sudo mkdir -p /opt/remotepfs/python
sudo env UV_PYTHON_INSTALL_DIR=/opt/remotepfs/python \
  /usr/local/bin/uv python install 3.11
uv --version
nbdkit --dump-plugin python
nbdkit --version
```

Obtain source on the SBC before deployment. Set `REPOSITORY_URL` to the repository URL and configure Git authentication first when the repository is private. Alternatively, copy the source tree to `/tmp/remotepfs-src` using any secure transfer method.

For a Git checkout, install Git and clone the repository:

```bash
sudo apt install -y git
REPOSITORY_URL="<repository-url>"
git clone --depth 1 "$REPOSITORY_URL" /tmp/remotepfs-src
```

If Git is unavailable, download a source archive instead:

```bash
SOURCE_ARCHIVE_URL="<source-archive-url>"
curl -fL "$SOURCE_ARCHIVE_URL" -o /tmp/remotepfs.tar.gz
rm -rf /tmp/remotepfs-src
mkdir -p /tmp/remotepfs-src
tar -xzf /tmp/remotepfs.tar.gz -C /tmp/remotepfs-src --strip-components=1
```

Copy source into the deployment path:

```bash
sudo mkdir -p /opt/remotepfs /etc/remotepfs /var/lib/remotepfs /opt/remotepfs/source
sudo cp -a /tmp/remotepfs-src/. /opt/remotepfs/source/
cd /opt/remotepfs/source
sudo env UV_PYTHON_INSTALL_DIR=/opt/remotepfs/python \
  /usr/local/bin/uv sync --frozen --python 3.11
sudo cp config/remotepfs.yaml.example /etc/remotepfs/remotepfs.yaml
sudo cp config/remotepfs.service config/remotepfs-nbdkit.service /etc/systemd/system/
```

Create service identity and permissions. Commands are safe to rerun:

```bash
if ! getent group remotepfs >/dev/null; then
  sudo groupadd --system remotepfs
fi
if ! id remotepfs-nbd >/dev/null 2>&1; then
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
    --gid remotepfs remotepfs-nbd
fi
sudo chown root:remotepfs /etc/remotepfs/remotepfs.yaml /var/lib/remotepfs
sudo chmod 0640 /etc/remotepfs/remotepfs.yaml
sudo chmod 0750 /var/lib/remotepfs
```

Validate installed unit files before editing or starting services:

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify \
  /etc/systemd/system/remotepfs.service \
  /etc/systemd/system/remotepfs-nbdkit.service
```

The systemd unit runs orchestration as `root` because it must mount network
filesystems, connect `/dev/nbd0`, and write ConfigFS. It starts the separate
`remotepfs-nbdkit.service`, which runs nbdkit as
`remotepfs-nbd:remotepfs`. Do not run the nbdkit unit as root or change the
orchestration unit to the nbdkit service account.

Edit `/etc/remotepfs/remotepfs.yaml`. Keep configured `mount_point` paths under
`/mnt`. CIFS options must include `ro`; recommended SMB dialect is `vers=3.1.1`.

Create credentials file outside repository:

```bash
sudo install -m 0600 /dev/null /etc/remotepfs/nas1.credentials
sudoedit /etc/remotepfs/nas1.credentials
```

Add `username=...` and `password=...` lines. Create a host-visible systemd
mount unit for each configured source. This is required because
`remotepfs.service` and `remotepfs-nbdkit.service` use separate hardened mount
namespaces.

For `/mnt/nas1`, create `/etc/systemd/system/mnt-nas1.mount`:

```ini
[Unit]
Description=RemotePFS NAS source
After=network-online.target
Wants=network-online.target

[Mount]
What=//NAS_IP/games
Where=/mnt/nas1
Type=cifs
Options=ro,vers=3.1.1,cache=strict,actimeo=30,rsize=1048576,credentials=/etc/remotepfs/nas1.credentials

[Install]
WantedBy=multi-user.target
```

Mount units must use escaped paths (`mnt-nas1.mount` for `/mnt/nas1`).
Repeat unit creation for every configured source, then enable them before
RemotePFS:

```bash
sudo mkdir -p /mnt/nas1
sudo systemctl daemon-reload
sudo systemctl enable --now mnt-nas1.mount
findmnt -T /mnt/nas1
find /mnt/nas1 -maxdepth 2 -type f | head
```

Install the matching RemotePFS drop-in so service startup requires every
host-visible source mount:

```bash
sudo mkdir -p /etc/systemd/system/remotepfs.service.d
sudo cp config/remotepfs.service.d/mounts.conf \
  /etc/systemd/system/remotepfs.service.d/mounts.conf
```

Do not run `mount` from the shell after enabling the unit. To unmount:

```bash
sudo systemctl disable --now mnt-nas1.mount
```

For NFS, set `protocol: nfs` and use an NFS endpoint with `options`.


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
