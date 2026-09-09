# 05 - exFAT Filesystem Specification

## Source

- Microsoft exFAT Specification v1.00: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
- elm-chan exFAT documentation: https://elm-chan.org/docs/exfat_e.html
- Wikipedia exFAT: https://en.wikipedia.org/wiki/ExFAT
- DeepWiki exfatprogs: https://deepwiki.com/exfatprogs/exfatprogs/6-exfat-on-disk-format
- Linux kernel exfat driver (since 5.7): drivers/fs/exfat/
- exfatprogs: https://github.com/exfatprogs/exfatprogs
- Researched: 2026-09-05

## Executive Summary

exFAT (Extensible File Allocation Table) is Microsoft's successor to FAT32. Designed for flash storage with 64-bit file sizes, large volumes (up to 128 PB theoretical), and cluster sizes up to 32 MB. The PS5 requires exFAT for external USB storage. RemotePFS must create and maintain an exFAT volume on the SBC that the USB gadget presents to the PS5.

## Volume Structure

exFAT volumes contain four regions:

```
+-------------------+  Sector 0
| Main Boot Region  |  (12 sectors)
+-------------------+  Sector 12
| Backup Boot Region|  (12 sectors)
+-------------------+  Sector 24
| FAT Region        |  (FatOffset to FatOffset + FatLength*NumberOfFats)
+-------------------+
| Data Region       |
|  + Cluster Heap   |  (ClusterHeapOffset onward)
|  + Excess Space   |
+-------------------+
```

### Boot Region (Main and Backup)

Each boot region: 12 sectors (Boot Sector + 8 Extended Boot + OEM Parameters + Reserved + Boot Checksum).

**Boot Sector Structure** (512 bytes):

| Field | Offset | Size | Description |
|-------|--------|------|-------------|
| JumpBoot | 0 | 3 | 0xEB 0x76 0x90 |
| FileSystemName | 3 | 8 | "EXFAT   " (8 chars, 3 trailing spaces) |
| MustBeZero | 11 | 53 | All zeros |
| PartitionOffset | 64 | 8 | Sector offset of partition |
| VolumeLength | 72 | 8 | Total sectors in volume |
| FatOffset | 80 | 4 | Sector offset of first FAT |
| FatLength | 84 | 4 | Sectors per FAT |
| ClusterHeapOffset | 88 | 4 | Sector offset of cluster heap |
| ClusterCount | 92 | 4 | Number of clusters |
| FirstClusterOfRootDirectory | 96 | 4 | Cluster index of root directory |
| VolumeSerialNumber | 100 | 4 | Unique volume serial |
| FileSystemRevision | 104 | 2 | Major.Minor revision (1.00) |
| VolumeFlags | 106 | 2 | ActiveFAT (bit 0), VolumeDirty (bit 1) |
| BytesPerSectorShift | 108 | 1 | Log2 of bytes per sector |
| SectorsPerClusterShift | 109 | 1 | Log2 of sectors per cluster |
| NumberOfFats | 110 | 1 | 1 or 2 |
| DriveSelect | 111 | 1 | Extended INT 13h drive number |
| PercentInUse | 112 | 1 | Percentage of allocated clusters |
| Reserved | 113 | 7 | Reserved |
| BootCode | 120 | 390 | Boot code (unused for data volumes) |
| BootSignature | 510 | 2 | 0xAA55 |

### FAT Region

- **First FAT**: starts at `FatOffset`.
- **Second FAT** (if `NumberOfFats = 2`): starts at `FatOffset + FatLength`.
- Each FAT entry: 32 bits (4 bytes).
- Special entries:
  - 0x00000000: Free cluster.
  - 0x00000001: Reserved.
  - 0x00000002 - 0xFFFFFFF6: Next cluster in chain.
  - 0xFFFFFFF7: Bad cluster.
  - 0xFFFFFFF8 - 0xFFFFFFFF: End of chain.

### Cluster Heap

Starts at `ClusterHeapOffset`. Contains:
- Allocation Bitmap (first cluster after any system metadata).
- Up-case Table (UPCASE table for case-insensitive filename matching).
- Root directory.
- File data.

### Directory Entries

32-byte entries. Types:

| Type | Code | Purpose |
|------|------|---------|
| Allocation Bitmap | 0x81 | Points to allocation bitmap |
| Up-case Table | 0x82 | Points to UPCASE table |
| Volume Label | 0x83 | Volume name |
| File | 0x85 | File directory entry |
| Volume GUID | 0xA0 | TexFAT volume GUID |
| File Name | 0xC1 | File name (Unicode) |

#### File Entry (0x85)

Contains: attributes (read-only, hidden, system, archive), timestamps (create, modify, access in 10ms resolution), FAT chain info, file size, stream extension.

## Geometry Parameters for RemotePFS

### Required Geometry (ShadowMountPlus Compatible)

| Parameter | Value | Why |
|-----------|-------|-----|
| BytesPerSectorShift | 9 (512 B) | LVD backend expects 512 B sectors |
| SectorsPerClusterShift | 7 (128 sectors = 64 KB) | ShadowMountPlus default cluster size |
| NumberOfFats | 1 | Simplifies; second FAT optional |
| MBR partition type | 0x07 | exFAT partition type code |

### Size Calculations

For a volume of size `N` bytes:

```
SectorSize = 512
ClusterSize = 64 KB = 65536 bytes
SectorsPerCluster = 128 = ClusterSize / SectorSize
ClusterCount = (VolumeLength - ClusterHeapOffset) / SectorsPerCluster

FatLength = CEIL(ClusterCount * 4 / SectorSize)
FatOffset = 24 (minimum: 24 sectors for boot regions)
ClusterHeapOffset = FatOffset + FatLength * NumberOfFats
                (aligned to cluster boundary)
```

### Alignment Recommendations

- **FAT alignment**: Align FatOffset to erase block size of underlying storage (typically 4 KB or 8 sectors).
- **Cluster heap alignment**: Align ClusterHeapOffset to cluster boundary.
- **Partition alignment**: Align partition start to 1 MB (2048 sectors) for modern storage.

## Cluster Size Considerations

Recommended cluster sizes based on volume size:

| Volume Size | Cluster Size | SectorsPerClusterShift |
|-------------|--------------|------------------------|
| ≤ 256 MB | 4 KB | 3 |
| ≤ 32 GB | 32 KB | 6 |
| ≤ 256 GB | 128 KB | 8 |
| ≤ 32 TB | 1024 KB | 11 |
| ≤ 128 PB | 32 MB | 16 |

For RemotePFS (PS5 external storage 8 TB max, game files 10-100+ GB):

- **64 KB clusters**: optimal for ShadowMountPlus compatibility.
- **128 KB clusters**: better for very large sequential reads but wastes space for small files.
- **64 KB is the default** in ShadowMountPlus and is the recommended starting point.

## Linux exFAT Support

### Kernel Driver (since Linux 5.7)

- Mainlined from Samsung's sdfat driver.
- Read/write support.
- Supports standard exFAT features (read/write, timestamps, allocation bitmap, up-case table). TexFAT (transaction-safe exFAT) is not supported.
- Path: `drivers/fs/exfat/`
- Kernel config: `CONFIG_EXFAT_FS`

### Userspace Tools

- **exfatprogs** (https://github.com/exfatprogs/exfatprogs): Samsung-maintained, used for `mkfs.exfat`, `fsck.exfat`.
  - `mkfs.exfat -s 128 -c 64K /dev/sdX1` (128 sectors/cluster = 64 KB).
- **exfat-utils** (older): Original FUSE-based implementation, deprecated.

### Creating an exFAT Volume for RemotePFS

```bash
# Create partition
parted /dev/loop0 mklabel msdos
parted /dev/loop0 mkpart primary 2048s 100%

# Format
mkfs.exfat -s 128 -n "PS5-STORAGE" /dev/loop0p1
```

## Timestamps

exFAT timestamps have 10 ms resolution:

- **Create**: File creation time.
- **Modify**: Last modification time.
- **Access**: Last access time.

Format: `(Year - 1980) << 25 | Month << 21 | Day << 16 | Hour << 11 | Minute << 5 | Second/2`

## UPCASE Table

- Contains uncompressed uppercase mappings for all 65,536 UTF-16 code units.
- Required for case-insensitive filename matching.
- RemotePFS writes the full 131,072-byte table into one 128 KiB cluster.
- PS5 validation rejected earlier layouts when the first 128 bytes were not the
  expected uncompressed mapping (`UVFAT_copyupcasetable`).

## Relevance to RemotePFS

1. USB gadget must present a complete, valid exFAT volume.
2. Volume created/managed on SBC (backed by NAS files).
3. Geometry MUST match ShadowMountPlus expectations: 64 KB clusters, 512 B sectors.
4. MBR partition table with single exFAT partition.
5. Kernel exfat driver on BSP Linux 6.6 handles the filesystem on the SBC side.
6. exfatprogs used for formatting and maintenance.
7. Volume serial number should be stable (not regenerated on each boot).

## References

1. Microsoft exFAT Specification: https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification
2. exFAT Wikipedia: https://en.wikipedia.org/wiki/ExFAT
3. elm-chan exFAT docs: https://elm-chan.org/docs/exfat_e.html
4. exfatprogs GitHub: https://github.com/exfatprogs/exfatprogs
5. Linux kernel exfat: https://www.kernel.org/doc/html/latest/filesystems/exfat.html
6. DeepWiki exfatprogs: https://deepwiki.com/exfatprogs/exfatprogs/6-exfat-on-disk-format
