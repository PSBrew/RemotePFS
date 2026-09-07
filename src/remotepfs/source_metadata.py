"""Portable source filesystem metadata helpers."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable


class _StatxTimestamp(ctypes.Structure):
    """Linux statx timestamp layout."""

    _fields_ = [
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    """Linux statx structure through kernel-defined 256-byte size."""

    _fields_ = [
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("stx_mnt_id", ctypes.c_uint64),
        ("stx_dio_mem_align", ctypes.c_uint32),
        ("stx_dio_offset_align", ctypes.c_uint32),
        ("stx_subvol", ctypes.c_uint64),
        ("stx_atomic_write_unit_min", ctypes.c_uint32),
        ("stx_atomic_write_unit_max", ctypes.c_uint32),
        ("stx_atomic_write_segments_max", ctypes.c_uint32),
        ("stx_dio_read_offset_align", ctypes.c_uint32),
        ("spare3", ctypes.c_uint64 * 9),
    ]


_AT_FDCWD = -100
_AT_SYMLINK_NOFOLLOW = 0x100
_STATX_BASIC_STATS = 0x07FF
_STATX_BTIME = 0x0800


def _load_statx() -> Callable[..., int] | None:
    """Load libc statx on Linux, or return None on unsupported platforms."""
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statx = libc.statx
        statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
        statx.restype = ctypes.c_int
        return statx
    except (AttributeError, OSError):
        return None


_statx = _load_statx()


def source_birthtime_ns(path: str) -> int | None:
    """Return source birth time in nanoseconds when Linux statx provides it."""
    if _statx is None:
        return None
    result = _Statx()
    try:
        status = _statx(_AT_FDCWD, os.fsencode(path), _AT_SYMLINK_NOFOLLOW, _STATX_BASIC_STATS | _STATX_BTIME, result)
    except OSError:
        return None
    if status != 0 or not result.stx_mask & _STATX_BTIME:
        return None
    return result.stx_btime.tv_sec * 1_000_000_000 + result.stx_btime.tv_nsec


__all__ = ["source_birthtime_ns"]
