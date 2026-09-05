# Spec 08 - HTTP API

## Overview

`remotepfsd` exposes an HTTP API on `localhost` for configuration management and status monitoring. Serves as the control plane for RemotePFS — config changes, health checks, and operational status.

## Binding

- **Default:** `127.0.0.1:8080` (localhost-only, no external network exposure)
- **Option:** Unix socket at `/run/remotepfs/api.sock` via systemd socket activation (V2)
- No TLS, no authentication (trusted local system)

## Endpoints

### GET /api/status

Returns current service state.

```json
{
  "service": {
    "version": "0.1.0",
    "uptime_seconds": 86400,
    "state": "running",
    "state_detail": "serving"
  },
  "gadget": {
    "udc_bound": true,
    "udc_name": "fc000000.usb",
    "lun_file": "/dev/nbd0",
    "lun_ro": true,
    "lun_size_bytes": 2199023255552
  },
  "nbd": {
    "connected": true,
    "socket_path": "/run/remotepfs/nbd.sock",
    "connections": 1,
    "requests_total": 12345678,
    "bytes_served": 827364829172,
    "read_errors": 0
  },
  "nfs_mounts": [
    {
      "name": "nas1",
      "server": "192.168.1.100",
      "export": "/volume1/games",
      "mount_point": "/mnt/nas1",
      "mounted": true,
      "state": "ok"
    }
  ],
  "config": {
    "path": "/etc/remotepfs/remotepfs.conf",
    "last_loaded": "2026-09-05T10:30:00Z",
    "generation": 3,
    "game_count": 15,
    "valid": true
  },
  "system": {
    "memory_used_mib": 180,
    "page_cache_mib": 2200,
    "cpu_percent": 2.5
  }
}
```

### GET /api/config

Returns current active config as JSON.

```json
{
  "generation": 3,
  "loaded_at": "2026-09-05T10:30:00Z",
  "global": {
    "image_size_gib": 2048,
    "cluster_size_kib": 64,
    "label": "REMOTEPFS",
    "oem_name": "REMOTEPFS"
  },
  "sources": [...],
  "entries": [...]
}
```

### PUT /api/config

Replace the entire configuration. Body is TOML (Content-Type: `application/toml`) or JSON (Content-Type: `application/json`).

**Request:**
```
PUT /api/config
Content-Type: application/toml

[global]
image_size_gib = 2048
...

[[sources]]
...
```

**Response 200:**
```json
{
  "status": "applied",
  "generation": 4,
  "warnings": [],
  "reload_time_ms": 8500
}
```

**Response 422 (validation failure):**
```json
{
  "status": "rejected",
  "errors": [
    {
      "field": "entries[2].source",
      "message": "Source path '/mnt/nas1/missing/' does not exist"
    }
  ]
}
```

**Behavior:**
1. Parse + validate new config
2. On failure: return 422 with field-level errors, keep current config running
3. On success: execute hot-reload sequence (unbind UDC, rebuild, rebind UDC)
4. PS5 sees ~5-15s disconnect during rebind

### POST /api/config/reload

Reload config from file on disk. Equivalent to `PUT /api/config` with current file contents.

```json
{
  "status": "applied",
  "generation": 5,
  "warnings": [],
  "reload_time_ms": 8200
}
```

### GET /api/health

Liveness check. No side effects. Returns 200 if service is alive.

```json
{
  "status": "healthy",
  "uptime_seconds": 86400
}
```

Returns 503 if service is starting up or in degraded state.

### GET /api/games

List virtual filesystem entries visible to PS5.

```json
{
  "games": [
    {
      "virtual_path": "FPS Games/",
      "type": "directory",
      "entry_count": 8,
      "total_size_bytes": 245000000000
    },
    {
      "virtual_path": "game.iso",
      "type": "file",
      "size_bytes": 58000000000
    }
  ]
}
```

## Error Responses

All errors follow a consistent format:

```json
{
  "error": {
    "code": "CONFIG_INVALID",
    "message": "entries[0]: source path does not exist",
    "details": null
  }
}
```

| HTTP Status | Code | Meaning |
|-------------|------|---------|
| 400 | `BAD_REQUEST` | Malformed request body |
| 413 | `PAYLOAD_TOO_LARGE` | Config >1 MiB |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Not TOML or JSON |
| 422 | `CONFIG_INVALID` | Config validation failed |
| 423 | `RELOAD_IN_PROGRESS` | Another reload already running |
| 500 | `INTERNAL_ERROR` | Unexpected server error |
| 503 | `SERVICE_UNAVAILABLE` | Service starting up |

## Hot-Reload as Atomic Generation Swap

Per advisory: config reload uses atomic generation swap, not in-place mutation.

### Generation Lifecycle

```
gen=3 (serving) → validate new config
    ├─ validated → build gen=4 (inert, not serving)
    │   ├─ compile immutable dir tree
    │   ├─ precompute extent table
    │   ├─ assign stable file IDs (SHA256 of virtual_path → 8-byte hex)
    │   └─ generation pointer swap: gen=4 → active
    │       ├─ brief window: in-flight reads use gen=3 (wait/leak)
    │       ├─ new reads use gen=4
    │       └─ cleanup: close gen=3 fds, free gen=3 memory
    └─ invalidated → return errors, gen=3 continues serving
```

### Stable File IDs

File IDs (inode numbers in exFAT directory entries) are derived from `SHA256(virtual_path.encode())` truncated to 32 bits. This ensures:

- Same virtual_path across reloads → same file ID
- File IDs stable across config changes (add/remove entries don't shift IDs)
- Collision probability: negligible for <1000 entries (32-bit birthday bound)

```python
import hashlib

def stable_file_id(virtual_path: str) -> int:
    """Derive stable 32-bit file ID from virtual path."""
    h = hashlib.sha256(virtual_path.encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little") & 0x7FFFFFFF  # positive 31-bit
```

### Immutable Generation

A "generation" is an immutable snapshot:

```python
@dataclass(frozen=True)
class Generation:
    """Immutable, validated virtual exFAT layout."""
    id: int                        # Monotonic generation number
    config_hash: str               # SHA256 of config file
    created_at: datetime
    image_size_bytes: int
    cluster_size_bytes: int
    sector_mapper: SectorMapper    # Immutable sector → source mapping
    open_files: dict[int, int]     # fd cache (file_id → fd)
    metadata_buffer: bytes         # Boot sector + FAT + root dir (precomputed)
```

The generation pointer is guarded by `threading.Lock`:

```python
_active_gen: Generation | None = None
_gen_lock = threading.Lock()

def get_active_gen() -> Generation:
    with _gen_lock:
        return _active_gen

def swap_gen(new: Generation) -> Generation:
    with _gen_lock:
        old = _active_gen
        _active_gen = new
        return old  # caller cleans up old
```

### Reload Sequence (Updated)

```
1. Parse + validate new config → Config object
2. Build Generation(gen=N+1, config=config) — fully inert
   a. Mount new NFS sources (retain old mounts during build)
   b. Scan directories, compute entries
   c. Assign stable file IDs
   d. Build SectorMapper
   e. Precompute metadata buffer
3. Unbind UDC → PS5 sees disconnect
4. Unmount old NFS sources not in new config
5. Pickle new generation state to /run/remotepfs/mapper.state
6. Restart nbdkit with new state
7. Reconnect nbd-client to new nbdkit socket
8. Atomically swap _active_gen = new_gen
9. Rebind UDC → PS5 sees new device
10. Cleanup: close old gen's orphaned fds, free old gen memory
```

## Implementation

### Framework

Standard library `http.server` with routes mapped via a simple dispatch table. No external web framework dependency (aiohttp, Flask, FastAPI) to minimize dependency footprint on the SBC.

```python
# api.py
import json
import http.server
from functools import partial

class RemotePfsHandler(http.server.BaseHTTPRequestHandler):
    """HTTP API handler for remotepfsd."""

    routes: dict = {
        ("GET", "/api/status"): "handle_status",
        ("GET", "/api/config"): "handle_get_config",
        ("PUT", "/api/config"): "handle_put_config",
        ("POST", "/api/config/reload"): "handle_reload",
        ("GET", "/api/health"): "handle_health",
        ("GET", "/api/games"): "handle_games",
    }

    # Inject service context during construction
    def __init__(self, service, *args, **kwargs):
        self.service = service
        super().__init__(*args, **kwargs)

    def do_GET(self): ...
    def do_PUT(self): ...
    def do_POST(self): ...

    # Response helpers
    def _json(self, data, status=200): ...
    def _error(self, code, message, status): ...
```

### Server Lifecycle

```python
class ApiServer:
    """HTTP API server bound to localhost."""

    def __init__(self, service, host="127.0.0.1", port=8080):
        self.host = host
        self.port = port
        handler = partial(RemotePfsHandler, service)
        self._server = http.server.HTTPServer((host, port), handler)

    def start(self):
        """Start serving in a daemon thread."""
        import threading
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """Graceful shutdown."""
        self._server.shutdown()
        self._thread.join(timeout=5)
```

## Security

- **Binding:** `127.0.0.1` only — no network-exposed port
- **No auth:** Trusted local system. If SBC is multi-user, restrict via firewall rules: `iptables -A INPUT -p tcp --dport 8080 ! -s 127.0.0.1 -j DROP`
- **Payload size:** Max 1 MiB for PUT body (config files are ~1 KiB)
- **Rate limiting:** Optional — systemd can rate-limit via `IPAddressDeny=any` plus localhost-only binding makes it moot
- **Sensitive data:** Config may contain NAS server IPs (internal LAN, low sensitivity). No passwords (NFS uses host-based auth or Kerberos, not in config).

## References

- DD-07: HTTP API on localhost
- KB 05: exFAT filesystem details
- Python `http.server` module: https://docs.python.org/3/library/http.server.html
- TOML specification: https://toml.io/en/v1.0.0
