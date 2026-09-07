"""Map virtual exFAT sectors to metadata or NFS-backed files."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .consts import PARTITION_START_LBA, SECTOR_SIZE, SECTORS_PER_CLUSTER
from .exfat_builder import ExfatLayout, FileMapping


@dataclass(frozen=True)
class SectorMapping:
    """Resolved source for one virtual sector."""

    kind: str
    source_fd: int | None = None
    offset: int | None = None
    metadata: bytes | None = None


class SectorMapper:
    """Read-only sector mapper backed by an immutable exFAT layout."""

    def __init__(self, layout: ExfatLayout) -> None:
        """Initialize mapper from an ``ExfatLayout``."""
        self.layout = layout
        self._fds: dict[str, int] = {}

    @property
    def total_bytes(self) -> int:
        """Total virtual image size in bytes."""
        return self.layout.total_bytes

    @property
    def total_sectors(self) -> int:
        """Total virtual image sectors."""
        return self.layout.total_sectors

    @classmethod
    def from_layout(cls, layout: ExfatLayout) -> SectorMapper:
        """Create mapper from a built layout."""
        return cls(layout)

    @classmethod
    def from_state(cls, state: dict[str, object]) -> SectorMapper:
        """Create mapper from pickle-safe layout state."""
        return cls(ExfatLayout.from_state(state))

    def to_state(self) -> dict[str, object]:
        """Return pickle-safe mapper state."""
        return self.layout.to_state()

    def map(self, sector: int) -> SectorMapping:
        """Resolve zero-based sector to metadata, file, or zero."""
        if sector < 0 or sector >= self.total_sectors:
            raise ValueError(f"sector outside image: {sector}")
        metadata = self.layout.metadata.get(sector)
        if metadata is not None:
            return SectorMapping(kind="metadata", metadata=metadata)
        for mapping in self.layout.file_mappings:
            start = self._file_start_sector(mapping)
            end = start + mapping.cluster_count * SECTORS_PER_CLUSTER
            if start <= sector < end:
                fd = self._open(mapping.source_path)
                source_offset = (sector - start) * SECTOR_SIZE
                return SectorMapping(kind="file", source_fd=fd, offset=source_offset)
        return SectorMapping(kind="zero")

    def read(self, offset: int, destination: bytearray | memoryview | None = None) -> bytes | None:
        """Read virtual bytes into destination or return immutable bytes.

        Args:
            offset: Byte offset in virtual image.
            destination: Optional writable buffer. Its length determines read size.

        Returns:
            Bytes when destination is omitted, otherwise ``None``.
        """
        if offset < 0:
            raise ValueError("offset must be non-negative")
        length = len(destination) if destination is not None else 0
        if destination is None:
            raise ValueError("destination buffer is required")
        if offset + length > self.total_bytes:
            raise ValueError("read exceeds image size")
        out = memoryview(destination)
        cursor = 0
        while cursor < length:
            absolute = offset + cursor
            sector = absolute // SECTOR_SIZE
            within = absolute % SECTOR_SIZE
            count = min(length - cursor, SECTOR_SIZE - within)
            mapping = self.map(sector)
            if mapping.kind == "metadata":
                out[cursor : cursor + count] = mapping.metadata[within : within + count]
            elif mapping.kind == "file":
                assert mapping.source_fd is not None and mapping.offset is not None
                data = os.pread(mapping.source_fd, count, mapping.offset + within)
                out[cursor : cursor + len(data)] = data
                if len(data) < count:
                    out[cursor + len(data) : cursor + count] = b"\0" * (count - len(data))
            else:
                out[cursor : cursor + count] = b"\0" * count
            cursor += count
        return None

    def read_bytes(self, offset: int, length: int) -> bytes:
        """Return virtual bytes for tests and preload helpers."""
        destination = bytearray(length)
        self.read(offset, destination)
        return bytes(destination)

    def extents(self, offset: int, count: int) -> list[tuple[int, int, bool]]:
        """Return contiguous data/zero extents for byte range."""
        if count <= 0:
            return []
        start = offset - offset % SECTOR_SIZE
        end = min(self.total_bytes, ((offset + count + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE)
        result: list[tuple[int, int, bool]] = []
        cursor = start
        while cursor < end:
            is_zero = self.map(cursor // SECTOR_SIZE).kind == "zero"
            run_start = cursor
            cursor += SECTOR_SIZE
            while cursor < end and (self.map(cursor // SECTOR_SIZE).kind == "zero") == is_zero:
                cursor += SECTOR_SIZE
            result.append((run_start, cursor - run_start, is_zero))
        return result

    def get_hot_ranges(self) -> list[tuple[int, int]]:
        """Return byte ranges precomputed for metadata warming."""
        return list(self.layout.hot_ranges)

    def is_metadata_region(self, offset: int, length: int) -> bool:
        """Return whether every covered sector is in metadata."""
        if length <= 0:
            return True
        first = offset // SECTOR_SIZE
        last = (offset + length - 1) // SECTOR_SIZE
        return all(self.layout.metadata.get(sector) is not None for sector in range(first, last + 1))

    def read_metadata(self, offset: int, destination: bytearray | memoryview) -> None:
        """Read metadata-only range into writable destination."""
        if not self.is_metadata_region(offset, len(destination)):
            raise ValueError("range contains non-metadata sectors")
        self.read(offset, destination)

    def close(self) -> None:
        """Close lazily opened source descriptors."""
        for fd in self._fds.values():
            os.close(fd)
        self._fds.clear()

    def _open(self, path: str) -> int:
        if path not in self._fds:
            self._fds[path] = os.open(path, os.O_RDONLY)
        return self._fds[path]

    def _file_start_sector(self, mapping: FileMapping) -> int:
        """Return first virtual sector for a file mapping."""
        return (
            PARTITION_START_LBA + self.layout.cluster_heap_offset + (mapping.start_cluster - 2) * SECTORS_PER_CLUSTER
        )


__all__ = ["SectorMapper", "SectorMapping"]
