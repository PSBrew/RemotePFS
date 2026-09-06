# 01 - ShadowMountPlus: Detection and Mounting Behavior

## Source

- Local copy: `../ShadowMountPlusDocsReview/` (source + README)
- GitHub: https://github.com/drakmor/ShadowMountPlus
- Discord: https://discord.gg/x2Ppvzwjhm

## Executive Summary

ShadowMountPlus (SMP) is an automated background auto-mounter payload for
jailbroken PS5 consoles. It scans configured paths for game folders and image
files (`.exfat`, `.ffpkg`, `.ffpfs`, `.ffpfsc`), mounts them via kernel attach
backends (LVD or MD), stages the title metadata, and registers it for launch.
For RemotePFS, the critical path is: SMP scans `/mnt/usb0` through `/mnt/usb7`
for `.exfat` image files, mounts them read-only by default, and presents the
contents to the PS5 as a mounted external drive.

## Architecture

SMP runs as a single payload process with these major subsystems:

1. **Scanner** (`sm_scanner.c`): kqueue-based filesystem watcher. Monitors scan
   roots for new/changed directories and image files. Debounces config reloads
   and manual list changes. Full rescan every `scan_interval_seconds` (default
   15s). Targeted rescan on filesystem events.
2. **Scan collector** (`sm_scan.c`): Walks scan roots, collects candidates.
   Classifies as directory candidates or image candidates. Checks
   `sce_sys/param.json` presence for folder-based games. Stability wait
   (`stability_wait_seconds`, default 10s) defers recently modified sources.
3. **Mount engine** (`sm_mount_device.c`): Resolves device nodes, attaches via
   LVD (`/dev/lvdctl`) or MD (`/dev/mdctl`) backend, waits for device node
   state, handles detach.
4. **Config** (`sm_config_mount.c`): INI-based runtime config with atomic
   slot swapping for hot reload. Per-image mode and sector size overrides.
   Autotune file (`/data/shadowmount/autotune.ini`) for persistent overrides.

## Image Format Support

| Extension | Mounted FS | Attach Backend | Status |
|-----------|-----------|----------------|--------|
| `.ffpkg` | UFS | LVD or MD | Recommended |
| `.exfat` | exFAT | LVD or MD | External-drive compatibility |
| `.ffpfs` | PFS (direct) | LVD | Experimental |
| `.ffpfsc` | PFS container | LVD | Experimental nested |

### Key exFAT Requirements (from README + config)

- **Cluster size**: `64 KB` recommended. Smaller clusters reduce performance.
- **Sector size**: Default `512` bytes for exFAT on LVD. Configurable via
  `lvd_exfat_sector_size` or per-image `image_sector=<filename>:<size>`.
- **Layout**: Game files must be at image root (no extra top-level folder).
  `sce_sys/param.json` must be present at image root.
- **Mount mode**: Read-only by default (`mount_read_only=1`).
- **Backend**: LVD by default (`exfat_backend=lvd`). MD alternative
  (`exfat_backend=md`).

## Scan Path Behavior

### Default Scan Locations

- `/data/homebrew`
- `/data/etaHEN/games`
- `/mnt/ext0/homebrew`, `/mnt/ext0/etaHEN/games`
- `/mnt/ext1/homebrew`, `/mnt/ext1/etaHEN/games`
- `/mnt/usb0/homebrew` through `/mnt/usb7/homebrew`
- `/mnt/usb0/etaHEN/games` through `/mnt/usb7/etaHEN/games`
- `/mnt/usb0` through `/mnt/usb7` (root level)
- `/mnt/ext0`, `/mnt/ext1`
- `/mnt/shadowmnt/pfsc` (PFSC container scan)
- `/mnt/shadowmnt` (mounted image content scan)

### Custom Scan Paths

- `scanpath=<absolute_path>` in `/data/shadowmount/config.ini` (repeatable).
- If any `scanpath` is set, only custom paths are used.
- `/mnt/shadowmnt/pfsc` and `/mnt/shadowmnt` are always added automatically.

### Scan Depth

- `scan_depth=1` (default): only first-level subdirectories.
- `scan_depth=2`: one additional nested level.
- `recursive_scan=1` (deprecated): forces `scan_depth=2`.

### Stability Wait

- `stability_wait_seconds` (default 10s): sources newer than this window are
  deferred until next scan cycle.
- Prevents mounting partially-written or still-copying files.
- **Critical for RemotePFS**: the emulated USB device must present stable,
  complete content. If files are being fetched from NAS during scan, SMP will
  defer mounting until the stability window passes.

## LVD Attach Pipeline (from `sm_mount_defs.h`)

1. Open `/dev/lvdctl`.
2. Build `lvd_ioctl_attach_v0_t` with:
   - `image_type`: 0 for single image (exFAT), 7 for UFS download data.
   - `entry_type`: 1 for file-backed, 2 for special device.
   - `flags`: bit0 = no bitmap (DONT_MAP_BM style).
   - `sector_size`: 512 for exFAT (configurable).
3. Issue `SCE_LVD_IOC_ATTACH_V0` (`0xC0286D00`).
4. Wait for `/dev/lvdN` device node to appear (100us poll, 100 retries).
5. Mount the device at `/mnt/shadowmnt/<image_name>_<hash>`.
6. On detach: issue `SCE_LVD_IOC_DETACH` (`0xC0286D01`).

### Sector Size Constants

```
LVD_SECTOR_SIZE_EXFAT = 512
LVD_SECTOR_SIZE_UFS   = 4096
LVD_SECTOR_SIZE_PFS   = 4096
MD_SECTOR_SIZE_EXFAT  = 512
MD_SECTOR_SIZE_UFS    = 512
```

### Image Type Constants

```
LVD_ATTACH_IMAGE_TYPE_SINGLE           = 0  (exFAT)
LVD_ATTACH_IMAGE_TYPE_PFS_SAVE_DATA    = 5
LVD_ATTACH_IMAGE_TYPE_UFS_DOWNLOAD_DATA = 7
PFS_NESTED_OUTER_IMG_TYPE             = 0x02
PFS_NESTED_INNER_IMG_TYPE             = 0x82
```

## Mount Point Naming

- Image mount: `/mnt/shadowmnt/<image_name>_<hash>`
- PFSC container mount: `/mnt/shadowmnt/pfsc/<image_name>_<hash>`

## Per-Image Configuration

From `config.ini`:

```ini
# Force read-only for specific images
image_ro=MyGame.exfat

# Force read-write for specific images
image_rw=MyGame.exfat

# Override sector size for specific images
image_sector=MyGame.exfat:65536
```

From `autotune.ini` (highest priority, auto-managed):

```ini
image_sector=MyGame.exfat:65536
kstuff_delay=PPSA12345:30
```

## Manual Install List

`/data/shadowmount/manual.lst` - one source per line:
- Path to game folder with `sce_sys/param.json`.
- Path to image file (`.ffpkg`, `.exfat`, `.ffpfs`, `.ffpfsc`).
- Lines starting with `#` are comments.
- Empty lines ignored.
- Watched for changes; new lines trigger rescan + mount + install.

## Kstuff Game Lifecycle

- `kstuff_game_auto_toggle=1` (default): pauses kstuff after game launch,
  resumes on stop.
- `kstuff_pause_delay_image_seconds` (default 25): delay before pausing for
  image-backed launches.
- `kstuff_pause_delay_direct_seconds` (default 15): delay for direct launches.
- `kstuff_no_pause=<TITLE_ID>`: skip auto-pause for specific titles.
- Crash detection: if app crashes within 2 minutes of kstuff pause, delay is
  doubled and upserted into `autotune.ini` (up to 3600s).

## Relevance to RemotePFS

### What RemotePFS Must Present

1. **USB mass storage device**: PS5 sees a standard USB flash drive via the
   SBC's USB OTG gadget. The device must have an exFAT partition with
   `64 KB` cluster size and `512` byte sector size.
2. **Image file on USB**: The exFAT partition must contain `.exfat` image
   files (or `.ffpkg` for UFS) at the filesystem root, with game files at
   the image root (no extra nesting).
3. **Stable source**: SMP's stability wait (`stability_wait_seconds=10`)
   means the emulated USB device must present stable file content. If files
   are still being fetched from NAS, SMP will defer mounting.
4. **Scan path**: The USB device must be mounted at `/mnt/usbN` by the PS5
   kernel. SMP scans `/mnt/usb0` through `/mnt/usb7`.

### Compatibility Requirements

- exFAT partition with 64KB cluster size.
- 512-byte sector size (LVD default for exFAT).
- Game files at image root: `sce_sys/param.json`, `eboot.bin`.
- No extra top-level folder inside the image.
- Read-only mount is fine (SMP default).
- Image must be a valid exFAT filesystem that the PS5 kernel can mount.

### Potential Challenges

- **Latency**: If the SBC fetches blocks from NAS on-demand, the PS5 may
  experience high latency. The stability wait + scan interval (15s) gives
  some buffer, but active gameplay reads must be fast enough to avoid
  timeouts or stuttering.
- **File size**: Game images can be 10-100+ GB. The exFAT partition must
  report a size large enough for the image. The USB gadget's backing store
  must handle this (virtual size vs actual storage).
- **USB enumeration**: The PS5 must enumerate the USB device correctly.
  USB descriptor timing and device class must match expected mass storage
  behavior.
- **Write operations**: If `mount_read_only=0` is configured, the PS5 may
  attempt writes. RemotePFS must either handle writes (write-back to NAS) or
  force read-only at the USB gadget level.
