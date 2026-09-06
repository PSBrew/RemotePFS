# 02 - PS5 USB and exFAT Storage Requirements

## Source

- PlayStation official extended storage page: https://www.playstation.com/en-us/support/hardware/ps5-extended-storage/
- PS5 Wikipedia: https://en.wikipedia.org/wiki/PlayStation_5
- ShadowMountPlus source: `sm_scanner.c`, `sm_mount_device.c`, `sm_types.h`
- PS5 USB ports: sony.com specifications page
- Researched: 2026-09-05

## Executive Summary

PS5 supports external USB storage for PS4 games and media. Format requirement is exFAT (not FAT32 or NTFS). ShadowMountPlus (jailbreak) scans `/mnt/usb0-7` for exFAT volumes with 64 KB clusters and 512-byte sectors, presenting them through LVD/MD backends. For RemotePFS, the USB gadget must present as a standard USB mass storage device with an exFAT partition matching these parameters.

## PS5 USB Hardware

### USB Ports

| Port | Location | Speed | Notes |
|------|----------|-------|-------|
| USB Type-A (2x) | Rear | USB 3.1 Gen 2 (10 Gbps) | Primary data ports |
| USB Type-C (1x) | Front | USB 3.1 Gen 2 (10 Gbps) | High-speed front port |
| USB Type-A (1x) | Front | USB 2.0 (480 Mbps) | Low-speed front port |

> **Source:** Wikipedia (PlayStation 5): rear 2x USB-A USB 3.1 Gen 2, front 1x USB-C USB 3.1 Gen 2, front 1x USB-A USB 2.0. Note: original PS5 (2020). Slim/Pro revisions differ.

### Maximum External Storage

- Up to 8 TB per external USB drive (official limit).
- Multiple drives supported simultaneously.
- Only one extended storage drive can be active at a time for game storage.
- Media playback: multiple USB drives simultaneously.

### USB Protocol

- PS5 supports both UAS (USB Attached SCSI, protocol 0x62) and BOT (Bulk-Only Transport, protocol 0x50).
- Standard USB Mass Storage class (0x08), subclass SCSI transparent command set (0x06).
- **RemotePFS constraint**: Linux `f_mass_storage` gadget only implements BOT (0x50). UAS requires `target_usb`/`target_core_mod` (TCM) framework. If UAS is required for PS5 performance, use f_tcm gadget instead.
- BOT performance overhead vs UAS: ~20-30% slower for small random I/O due to command queuing and USB round trips.

## exFAT Format Requirements

### Official Requirements

- The PS5 expects external USB drives to be formatted as **exFAT**.
- FAT32 is not supported for extended game storage (file size limit).
- NTFS is not supported natively (exFAT only).
- Single partition, MBR partition table.

### ShadowMountPlus Expectations (Jailbreak Context)

ShadowMountPlus monitors `/mnt/usb0-7` for mount events and scans for specific markers:

```
Default configuration (config.ini.example):
- Filesystem: exFAT
- Cluster size: 64 KB (65536 bytes)
- Sector size: 512 bytes (LVD backend)
- Timeout: 10 seconds stability wait
- Scan interval: 15 seconds
```

#### Mount Detection

1. `sm_scanner.c` monitors `/proc/mounts` for new USB mounts.
2. When `/mnt/usb<N>` appears, waits 10 seconds for filesystem stability.
3. `sm_scan.c` scans the mount root for:
   - `.exfat` files → LVD (Logical Volume Device) mount via `/dev/lvdctl`, ioctl `0xC0286D00`.
   - `.ffpkg` files → Legacy FFPKG mount.
   - `.ffpfs` files → Fuse-based FFPFS mount.
4. `sm_config_mount.c` reads mount configuration: auto-mount, title, icon path, mount background.

#### Sector Access (LVD)

- `sm_types.h` defines `SECTOR_SIZE_LVD = 512`.
- LVD attachment: `/dev/lvdctl` ioctl command `0xC0286D00`.
- 64 KB cluster alignment from the raw device.

## Implications for RemotePFS

1. **USB gadget must present as standard USB Mass Storage** (BOT protocol 0x50 via f_mass_storage; UAS via f_tcm if PS5 requires it for performance).
2. **exFAT partition geometry** must match: 64 KB clusters, 512-byte sectors.
3. **MBR partition table** with single primary partition, type `0x07` (HPFS/NTFS/exFAT).
4. **Volume must mount at `/mnt/usb<N>`** on the PS5 (kernel automount).
5. **LVD backend** requires the USB device be accessible as a block device on the PS5 (`/dev/sdX`).
6. **300 ms stability** may be sufficient for ShadowMountPlus detection if mount events are reliable.
7. **No `.exfat` marker file needed** — ShadowMountPlus scans for exFAT filesystem directly.

## Key Constraints

| Parameter | Value | Source |
|-----------|-------|--------|
| Max USB capacity | 8 TB (official) | playstation.com |
| Filesystem | exFAT | playstation.com |
| Partition table | MBR (single partition) | playstation.com |
| Cluster size | 64 KB (65536 B) | ShadowMountPlus config.ini.example |
| Sector size (LVD) | 512 B | ShadowMountPlus sm_types.h |
| USB speed (Type-C front) | 10 Gbps | Wikipedia (PlayStation 5) |
| USB speed (Type-A rear) | 10 Gbps | Wikipedia (PlayStation 5) |
| ShadowMountPlus scan | 15s interval, 10s stability | sm_scanner.c |

## Open Questions

- PS5 jailbreak community knowledge: any undocumented USB quirks.
- USB 3.2 Gen 2 Type-C: full OTG compatibility with Cubie A7S USB-C.
- exFAT timestamps: PS5 timezone handling for file modification times.
- Max file size: exFAT 16 EB theoretical, but PS5/Orbis kernel may impose limits.
- ShadowMountPlus compatibility with BSP Linux (kernel 6.6) device node paths.

## References

1. PlayStation 5 Extended Storage: https://www.playstation.com/en-us/support/hardware/ps5-extended-storage/
2. PS5 Wikipedia: https://en.wikipedia.org/wiki/PlayStation_5
3. ShadowMountPlus GitHub: https://github.com/drakmor/ShadowMountPlus
4. exFAT Specification: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
5. USB Mass Storage Class Specification: https://www.usb.org/document-library/mass-storage-class-specification-10
