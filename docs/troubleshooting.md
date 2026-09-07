# Troubleshooting

## First checks

Collect service state and recent logs before changing configuration:

```bash
curl -i http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/status
sudo systemctl status remotepfs remotepfs-nbdkit --no-pager
sudo journalctl -u remotepfs -u remotepfs-nbdkit -n 100 --no-pager
```

`503` means service startup or degraded state. Check configuration syntax, NFS mounts, and permissions before restarting.

## macOS development

macOS cannot load Linux `nbd`, ConfigFS, `g_mass_storage`, or nbdkit. Run unit tests and API health checks on macOS. Run NBD, USB gadget, and PS5 checks on the target SBC.

## APT package download returns 404

Debian mirror metadata can reference a rotated security package. Refresh indexes:

```bash
sudo apt-get update --allow-releaseinfo-change
sudo apt-cache policy git
```

If the package still returns `404`, do not keep retrying Git installation. Download a source archive instead:

```bash
SOURCE_ARCHIVE_URL="<source-archive-url>"
curl -fL "$SOURCE_ARCHIVE_URL" -o /tmp/remotepfs.tar.gz
rm -rf /tmp/remotepfs-src
mkdir -p /tmp/remotepfs-src
tar -xzf /tmp/remotepfs.tar.gz -C /tmp/remotepfs-src --strip-components=1
```

For kernel build dependencies, inspect each candidate and pin only broken packages to versions available from Bullseye main:

```bash
apt-cache policy gnupg libcpanel-json-xs-perl libc6-dev libssl-dev
sudo apt-get install -y gnupg=<bullseye-main-version> \
  libcpanel-json-xs-perl=<bullseye-main-version>
sudo apt-get build-dep -y /tmp/linux-a733
```

Do not force every dependency to the old Bullseye target; security-updated `libc6-dev` and `libssl-dev` may be required by the running image.

## Source deployment errors

Confirm source and virtual environment paths:

```bash
test -f /opt/remotepfs/source/pyproject.toml
test -x /opt/remotepfs/source/.venv/bin/remotepfs
sudo /opt/remotepfs/source/.venv/bin/python --version
```

Copy source contents with `/.`:

```bash
sudo cp -a /tmp/remotepfs-src/. /opt/remotepfs/source/
```

This avoids creating `/opt/remotepfs/source/source` during redeployment.

## uv Python path warning

When uv runs through `sudo`, its managed Python belongs to root. Keep interpreter storage explicit:

```bash
sudo mkdir -p /opt/remotepfs/python
sudo env UV_PYTHON_INSTALL_DIR=/opt/remotepfs/python \
  /usr/local/bin/uv python install 3.11
sudo env UV_PYTHON_INSTALL_DIR=/opt/remotepfs/python \
  /usr/local/bin/uv sync --frozen --python 3.11
```

Warning about `/root/.local/bin` is harmless when commands use `/usr/local/bin/uv` explicitly.

## nbdkit Python version mismatch

The Debian nbdkit Python plugin uses system Python, not RemotePFS `.venv`:

```bash
nbdkit --dump-plugin python | grep python_version
python3 --version
sudo python3 -c 'import sys; sys.path.insert(0, "/opt/remotepfs/source/src"); import remotepfs.remotepfs_nbd; print("plugin import: OK")'
```

Bullseye commonly reports Python 3.9. The nbdkit import chain must remain Python 3.9-compatible. The service itself uses the uv-managed Python 3.11 environment.

## Service configuration failure

Validate YAML before starting systemd:

```bash
sudo /opt/remotepfs/source/.venv/bin/remotepfs compile /etc/remotepfs/remotepfs.yaml
sudo systemd-analyze verify \
  /etc/systemd/system/remotepfs.service \
  /etc/systemd/system/remotepfs-nbdkit.service
```

Required configuration rules:

- `mount_point` paths stay under `/mnt`.
- NFS options include `ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec`.
- `virtual_path` contains no `/`.
- Entry source paths exist below configured mount points.
- `oem_name` is exactly eight uppercase ASCII characters.

## NAS unavailable

Verify network, mount state, and NFS parameters:

```bash
sudo mkdir -p /mnt/nas1
sudo mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
  NAS_IP:/export/path /mnt/nas1
mountpoint /mnt/nas1
mount | grep /mnt/nas
nfsstat -m
ping NAS_IP
```

NFS uses `hard`, so reads wait while NAS recovers instead of returning early I/O errors. Do not switch to `soft` unless accepting corruption risk.

## NFS client reports `No such device`

`mount.nfs4: No such device` means Linux NFS client support is unavailable to the running kernel. NFSv4 support may be built into `nfs.ko`; a separate `nfsv4` module is not universally present.

```bash
sudo modprobe nfs || true
grep -w nfs /proc/filesystems || true
lsmod | grep -E '^nfs\b' || true
sudo modinfo nfs || true
```

Retry the mount after loading `nfs`:

```bash
sudo mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
  NAS_IP:/export/path /mnt/nas1
```

If `nfs` is absent from `/proc/filesystems` and `modprobe nfs` reports a missing module, install matching kernel modules or select an SBC kernel with NFSv4.1 client support. Do not debug NAS credentials until the kernel exposes NFS support.

For vendor SBCs, inspect the matching kernel package before reinstalling. Do not replace a board kernel with a generic Debian kernel unless its device tree, UDC, storage, and boot path are confirmed.

An image that contains `fs/nfsd` but no `fs/nfs` or `nfs.ko` has NFS server support without NFS client support. Reinstalling the same image does not add the missing client; use a matching vendor kernel build with NFS enabled.

## Vendor kernel build safety

Do not build or install a kernel on a production SBC until the board has a recovery path. Prefer a vendor image that already includes NFSv4.1 client support, or build the exact vendor kernel on a separate Linux build host. Inspect the generated package before installation:

```bash
dpkg-deb -f linux-image-*.deb Version Architecture
dpkg-deb -c linux-image-*.deb | grep '/fs/nfs/nfs.ko'
```



## NFS credentials and read-only access

NFS mounts normally do not use a username and password. Configure the NAS export with the SBC IP allowlisted and read-only privilege. Keep root squashing or anonymous mapping enabled when possible, and ensure the mapped identity can read files and traverse directories.

If the NAS denies access, inspect the server export permissions and SBC address before changing Linux file ownership:

```bash
ip -4 addr
showmount -e NAS_IP
sudo mount -t nfs4 -o ro,hard,nconnect=2,rsize=1048576,noatime,nosuid,nodev,noexec \
  NAS_IP:/export/path /mnt/nas1
```

## NBD device or socket is not ready

```bash
sudo systemctl status remotepfs remotepfs-nbdkit --no-pager
sudo journalctl -u remotepfs -u remotepfs-nbdkit -n 100 --no-pager
pgrep -a nbdkit
stat /run/remotepfs/nbd.sock
sudo nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
blockdev --getsize64 /dev/nbd0
```

The gadget must bind only after nbdkit has a socket and `/dev/nbd0` has non-zero size. `nbd-client` uses uppercase `-U` for the Unix socket.

## Gadget bind fails

```bash
sudo modprobe nbd
sudo modprobe libcomposite
sudo modprobe usb_f_mass_storage
mountpoint /sys/kernel/config
ls /sys/class/udc
cat /sys/kernel/config/usb_gadget/remotepfs/UDC
sudo dmesg | tail -n 100
```

An empty `/sys/class/udc` means kernel or board lacks an available USB device controller. Use the SBC OTG/device port, not a host-only USB port.

## Permission failures

Expected deployment model:

- `/run/remotepfs`: owner `remotepfs-nbd:remotepfs`, mode `0700`.
- `nbd.sock`: owner `remotepfs-nbd`, mode `0600`.
- `/etc/remotepfs/remotepfs.yaml`: owner `root:remotepfs`, mode `0640`.
- `/var/lib/remotepfs`: owner `root:remotepfs`, mode `0750`.

Check:

```bash
stat -c '%a %U:%G %n' \
  /run/remotepfs /run/remotepfs/nbd.sock \
  /etc/remotepfs/remotepfs.yaml /var/lib/remotepfs
```

`RuntimeDirectory=` ownership follows systemd `User=` and `Group=`. Group membership does not bypass socket mode `0600`.

## PS5 does not detect storage

Run software checks first:

```bash
cat /sys/block/nbd0/ro
cat /sys/kernel/config/usb_gadget/remotepfs/UDC
blockdev --getsize64 /dev/nbd0
curl http://127.0.0.1:8080/api/status
sudo journalctl -u remotepfs -u remotepfs-nbdkit -n 100 --no-pager
```

Expected: NBD read-only value `1`, non-empty UDC, non-zero device size, and a running API. Then verify data-capable cable, correct OTG/device port, stable SBC power, and PS5-side detection.
