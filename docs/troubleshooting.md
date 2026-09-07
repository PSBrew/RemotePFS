# Troubleshooting

## macOS development

macOS cannot load Linux `nbd`, ConfigFS, `g_mass_storage`, or nbdkit. Run unit tests and API health checks on macOS. Run NBD and PS5 checks on target SBC.

## Service health

```bash
curl -i http://127.0.0.1:8080/api/health
journalctl -u remotepfs -n 100 --no-pager
```

`503` means service startup or degraded state. Check config syntax, source mounts, and permissions.

## NBD device is not ready

```bash
pgrep -a nbdkit
stat /run/remotepfs/nbd.sock
nbd-client -U /run/remotepfs/nbd.sock -r /dev/nbd0
blockdev --getsize64 /dev/nbd0
```

The gadget must bind only after `/dev/nbd0` is connected and has non-zero size.

## Gadget bind fails

```bash
mountpoint /sys/kernel/config
ls /sys/class/udc
cat /sys/kernel/config/usb_gadget/remotepfs/UDC
```

Load `libcomposite` and `usb_f_mass_storage`, verify an OTG-capable UDC exists, then inspect `dmesg`.

## Permission failures

Expected deployment model:

- `/run/remotepfs`: owner `remotepfs-nbd:remotepfs`, mode `0700`.
- `nbd.sock`: owner `remotepfs-nbd`, mode `0600`.
- `/etc/remotepfs/remotepfs.conf`: `root:remotepfs`, mode `0640`.

`RuntimeDirectory=` ownership follows systemd `User=` and `Group=`. Group membership does not bypass socket mode `0600`.

## NAS unavailable

NFS uses `hard`, so reads block while NAS recovers instead of returning corrupted data. Check:

```bash
mount | grep /mnt/nas
nfsstat -m
ping NAS_IP
```

Do not switch to `soft` unless accepting possible I/O errors and filesystem corruption risk.
