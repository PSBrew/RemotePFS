# 10 - Network-Level Compression

## Source

- zstd: https://facebook.github.io/zstd/
- lz4: https://lz4.github.io/lz4/
- zlib: https://zlib.net/
- brotli: https://github.com/google/brotli
- Linux crypto compression: https://www.kernel.org/doc/Documentation/crypto/
- Various compression benchmarks
- Researched: 2026-09-05

## Executive Summary

Game files on the NAS may contain uncompressed assets. Applying compression on the NAS side before transmission over the network can reduce bandwidth usage and improve effective throughput. However, many game assets are already compressed (Kraken, Oodle, LZ4) — re-compressing them wastes CPU for zero ratio gain. zstd (level 1) offers the best tradeoff: ~500 MB/s compression, ~1300 MB/s decompression, 2-3x ratio on text/uncompressed data. lz4 is faster (~800 MB/s) but lower ratio (~2x). zlib is too slow for real-time (~100 MB/s). NFS v4.1 has no built-in compression. Compression must be applied at the transport layer (SSH tunnel, IPsec) or application layer (custom proxy/gRPC), never by NFS itself.

## Compression Algorithm Comparison

| Algorithm | Compress Speed | Decompress Speed | Ratio (text) | Ratio (binary) | CPU Usage |
|-----------|---------------|------------------|-------------|----------------|-----------|
| **zstd 1** | ~500 MB/s | ~1300 MB/s | ~2.8x | ~1.1x (already compressed) | Moderate |
| **lz4** | ~800 MB/s | ~4000 MB/s | ~2.1x | ~1.05x | Low |
| **zlib 1** | ~100 MB/s | ~400 MB/s | ~2.7x | ~1.1x | High |
| **brotli 1** | ~300 MB/s | ~800 MB/s | ~3.1x | ~1.1x | Moderate |
| None | N/A | N/A | 1.0x | 1.0x | None |

Note: Speeds are approximate, vary by CPU. SBC has A76 (ARM, ~2.0 GHz) and NAS has J4125 (x86, ~2.7 GHz burst). J4125 is ~20-40% faster for zlib/lz4 due to x86 SIMD. zstd benefits less from SIMD.

## Game File Compression Reality

Most modern PS5 game assets are already compressed:
- **Textures**: BCn/DXT compressed (compressing further wastes CPU).
- **Audio**: Vorbis, OPUS, or custom codecs.
- **Geometry/Meshes**: Often custom compressed formats.
- **Kraken/Oodle**: Many PS5 games use RAD Game Tools compression, which is highly optimized for game asset compression.

**Net effective compression ratio on typical game files: 1.0-1.05x** (barely any savings).

Uncompressed assets that benefit from network compression:
- Script files (Lua, Python bytecode) — 3-4x compression.
- Configuration files (JSON, XML, INI) — 3-5x compression.
- Shader bytecode — 2-3x compression.
- Level data in custom uncompressed formats — variable.

## Architecture Options

### Option A: Pre-compressed NAS Storage

```bash
# Pre-compress game files on NAS with zstd
zstd -1 --rm game.pkg  # game.pkg.zst replaces game.pkg

# RemotePFS decompresses on-the-fly before serving to PS5
# Requires zstd-decompression proxy layer in SBC
```

**Pros**: Bandwidth saved on every read. Simple setup.
**Cons**: PS5 expects random access to exFAT files. zstd is streaming — can't seek to byte offset N in compressed stream without decompressing prefix. Must provide a virtual file layer that decompresses blocks on demand.

### Option B: Network-Level Compression (SOCKS/Proxy)

```bash
# Run a local proxy on NAS:
# socat with zlib/deflate compression (socat compress option; socat does NOT support zstd)
# OR use SSH tunnel with compression
ssh -C 192.168.1.100     # SSH built-in compression (zlib-based, slow)
ssh -o Compression=yes -o CompressionLevel=1
```

**Pros**: Transparent to NFS (compress the SSH tunnel, mount NFS over it).
**Cons**: SSH compression is zlib (slow). Can't match NFS raw throughput.

### Option C: gRPC/Custom RPC with Per-Request Compression

RemotePFS runs a small agent on the NAS that serves file blocks over gRPC with zstd:

```python
# NAS-side agent (Python, runs on Synology DSM)
import grpc  # or custom protobuf

def ReadBlock(filename: str, offset: int, length: int) -> bytes:
    data = read_file_block(filename, offset, length)
    return zstd.compress(data, 1)  # Compress block before sending
```

**Pros**: Fine-grained control. Only compress blocks, not whole files.
**Cons**: Not NFS-based. Custom protocol adds complexity. Need agent on NAS.

### Option D: No Compression (Recommended Initial Strategy)

Don't compress network traffic. Start with raw NFS, profile actual bandwidth usage.

**Rationale**:
- 1 GbE provides ~125 MB/s theoretical, ~112 MB/s practical. PS5 USB 3.2 Gen2 provides 10 Gbps but exFAT read speed is bottlenecked by the PS5 game loading thread, not raw USB bandwidth.
- If the PS5 reads at ~50 MB/s (typical game loading), 1 GbE is sufficient without compression.
- Compression adds CPU overhead on both NAS and SBC, plus seeks require decompressing from start of compressed block.
- For game files >90% already compressed: ratio benefit is negligible.

## When Compression Makes Sense

Add compression only if:

1. **Bandwidth is the bottleneck** — confirmed by profiling (e.g., iperf, NFS throughput monitoring).
2. **Game files have significant uncompressed content** — confirmed by scanning NAS game files with content-type detection.
3. **The PS5 read speed exceeds 1 GbE capacity** — profiling shows sustained read >100 MB/s from PS5.

At that point, implement **Option A (pre-compressed with block-level indexing)**:

```python
# zstd seekable format: compress in blocks of 64 KB
# Frames are independent — can seek to any block

import zstandard as zstd

def compress_file(input_path: str, output_path: str, block_size: int = 65536):
    cctx = zstd.ZstdCompressor(level=1)
    with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
        while True:
            chunk = fin.read(block_size)
            if not chunk:
                break
            compressed = cctx.compress(chunk)
            fout.write(compressed)

# Later, to read byte range [offset, offset+length]:
# 1. Calculate which 64 KB blocks cover the range
# 2. Decompress each needed block
# 3. Extract the requested byte range
```

zstd seekable format (frames) enables random access without decompressing the entire file.

## Kernel Module Availability

The Radxa Cubie A7S BSP kernel (6.6) includes all three compression algorithms as kernel modules:
- **zstd** (CONFIG_ZSTD, since kernel 4.14): Used by Btrfs, zram, f2fs, dm-compress.
- **lz4** (CONFIG_LZ4, since kernel 3.11): Used by Btrfs, zram, squashfs.
- **zlib** (CONFIG_ZLIB_DEFLATE, since kernel 2.6): Used by dm-crypt, Btrfs, ppp.

All three are available for kernel-level compression (zram, Btrfs compression, dm-compress). zram-backed NFS traffic compression is a valid architecture option: use zram as compressed swap to free physical RAM for page cache, or use zram as compressed tmpfs for caching.

Note: The code example above shows a simplified block-based approach with independent zstd frames, not the official zstd seekable format. The seekable format includes a seek table for O(1) offset lookup. For production use, consider `ZstdSeekableCompressor` from python-zstandard or `zstd --stream=ContentSize`.

## Relevance to RemotePFS

1. **No compression in initial implementation.** Add only if profiling proves bandwidth is the bottleneck.
2. **If added, use zstd level 1** with block-level (seekable) compression.
3. **Pre-compress game files on NAS** as a batch operation, not on-the-fly.
4. **zstd decompression on SBC** before serving blocks to PS5 via USB gadget.
5. **Don't compress already-compressed game assets** — content-type detection to skip Kraken/Oodle/BCn textures.
6. **Monitor the throughput** from NFS stats (/proc/self/mountstats) and compare to PS5 read demand.

## References

1. zstd: https://facebook.github.io/zstd/
2. lz4: https://lz4.github.io/lz4/
3. zlib: https://zlib.net/
4. zstd seekable format: https://github.com/facebook/zstd/blob/dev/contrib/seekable_format/zstd_seekable_compression_format.md
5. RAD Game Tools Oodle: https://www.radgametools.com/oodle.htm
