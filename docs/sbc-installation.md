# SBC installation

Hardware validation requires Linux SBC with USB gadget mode. macOS cannot validate kernel NBD, ConfigFS, nbdkit, NFS mounts, or PS5 enumeration.

## Requirements

- Linux SBC with USB 3 OTG/device port and UDC support.
- Debian/Ubuntu/Armbian, Python 3.11+, and 4 GiB RAM recommended.
- `nbd`, `nfs`, `nfsv4`, `libcomposite`, and `usb_f_mass_storage` kernel modules.
- NAS exporting game files through NFS v4.1.
- USB-C data cable and stable external SBC power.

## Install packages

```bash
sudo apt install nbdkit nbdkit-plugin-python3 nbd-client nfs-common \
  python3 python3-venv curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo install -m 0755 "$HOME/.local/bin/uv" /usr/local/bin/uv
uv --version
nbdkit --dump-plugin python
nbdkit --version
```

Install RemotePFS with `uv` in its deployment environment:

```bash
sudo mkdir -p /opt/remotepfs /etc/remotepfs /var/lib/remotepfs
sudo cp -a . /opt/remotepfs/source
cd /opt/remotepfs/source
sudo uv sync --frozen
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
