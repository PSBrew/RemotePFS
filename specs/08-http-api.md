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
### POST /api/config/compile

Validate config and build virtual exFAT metadata without activating.

Response:
```json
{
  "status": "compiled",
  "generation": 6,
  "warnings": [],
  "size_bytes": 2199023255552
}
```

### POST /api/config/activate

Activate previously compiled generation. Performs safe cutover:
- Unbind UDC
- Restart nbdkit with new plugin state
- Reconnect NBD client
- Rebind UDC

Response:
```json
{
  "status": "activated",
  "generation": 6,
  "reload_time_ms": 8200
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

    id: int  # Monotonic generation number
    config_hash: str  # SHA256 of config file
    created_at: datetime
    image_size_bytes: int
    cluster_size_bytes: int
    sector_mapper: SectorMapper  # Immutable sector → source mapping
    open_files: dict[int, int]  # fd cache (file_id → fd)
    metadata_buffer: bytes  # Boot sector + FAT + root dir (precomputed)
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

FastAPI with async request handlers and Pydantic v2 models for request/response
validation. Runs under uvicorn (ASGI). Async handlers avoid blocking the event
loop during I/O-bound config parsing; CPU-bound generation builds are dispatched
to a thread pool via `fastapi.concurrency.run_in_threadpool`.

```python
# api.py
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# --- Pydantic models ---


class StatusResponse(BaseModel):
    service: ServiceInfo
    gadget: GadgetInfo
    nbd: NbdInfo
    nfs_mounts: list[NfsMountInfo]
    config: ConfigInfo
    system: SystemInfo


class ServiceInfo(BaseModel):
    version: str
    uptime_seconds: int
    state: str
    state_detail: str


class GadgetInfo(BaseModel):
    udc_bound: bool
    udc_name: str | None
    lun_file: str | None
    lun_ro: bool
    lun_size_bytes: int | None


class NbdInfo(BaseModel):
    connected: bool
    socket_path: str
    connections: int
    requests_total: int
    bytes_served: int
    read_errors: int


class NfsMountInfo(BaseModel):
    name: str
    server: str
    export: str
    mount_point: str
    mounted: bool
    state: str


class ConfigInfo(BaseModel):
    path: str
    last_loaded: datetime
    generation: int
    game_count: int
    valid: bool


class SystemInfo(BaseModel):
    memory_used_mib: float
    page_cache_mib: float
    cpu_percent: float


class ConfigReplaceResponse(BaseModel):
    status: str
    generation: int
    warnings: list[str] = Field(default_factory=list)
    reload_time_ms: int


class ValidationErrorDetail(BaseModel):
    field: str
    message: str


class ConfigInvalidResponse(BaseModel):
    status: str
    errors: list[ValidationErrorDetail]


class ReloadResponse(BaseModel):
    status: str
    generation: int
    warnings: list[str] = Field(default_factory=list)
    reload_time_ms: int


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: int


class GameEntry(BaseModel):
    virtual_path: str
    type: str  # "file" | "directory"
    size_bytes: int | None = None
    entry_count: int | None = None
    total_size_bytes: int | None = None


class GamesResponse(BaseModel):
    games: list[GameEntry]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- App ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load config, start nbdkit, connect NBD, bind UDC."""
    await app.state.service.startup()
    yield
    await app.state.service.shutdown()


def create_app(service: "RemotePfsService") -> FastAPI:
    """Create FastAPI app with service context injected via app.state."""
    app = FastAPI(
        title="RemotePFS API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.service = service
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Return current service state."""
        return await app.state.service.get_status()

    @app.get("/api/config")
    async def get_config() -> dict:
        """Return active config as JSON."""
        return await app.state.service.get_config_json()

    @app.put("/api/config", response_model=ConfigReplaceResponse)
    async def put_config(request: Request) -> ConfigReplaceResponse:
        """Replace entire configuration. Body is TOML or JSON."""
        content_type = request.headers.get("content-type", "")
        body = await request.body()
        if len(body) > 1_048_576:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "PAYLOAD_TOO_LARGE", "message": "Config body exceeds 1 MiB limit"},
            )
        if "toml" in content_type:
            config_text = body.decode("utf-8")
        elif "json" in content_type:
            config_text = body.decode("utf-8")
        else:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={
                    "code": "UNSUPPORTED_MEDIA_TYPE",
                    "message": "Content-Type must be application/toml or application/json",
                },
            )
        try:
            result = await run_in_threadpool(app.state.service.replace_config, config_text)
        except ConfigValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ConfigInvalidResponse(status="rejected", errors=exc.errors).model_dump(),
            )
        return result

    @app.post("/api/config/reload", response_model=ReloadResponse)
    async def reload_config() -> ReloadResponse:
        """Reload config from on-disk path."""
        return await run_in_threadpool(app.state.service.reload_config)

    @app.get("/api/health", response_model=HealthResponse)
    async def get_health() -> HealthResponse:
        """Liveness check."""
        return await app.state.service.get_health()

    @app.get("/api/games", response_model=GamesResponse)
    async def get_games() -> GamesResponse:
        """List virtual filesystem entries."""
        return await app.state.service.get_games()

    @app.post("/api/eject")
    async def eject() -> dict:
        """Unbind UDC, disconnect NBD, stop nbdkit."""
        await run_in_threadpool(app.state.service.eject)
        return {"status": "ejected"}

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled errors — return structured 500."""
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error=ErrorBody(code="INTERNAL_ERROR", message=str(exc))).model_dump(),
        )
```

### Server Lifecycle

```python
# server.py
import uvicorn


def run_api(service: "RemotePfsService", host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run FastAPI under uvicorn — blocks until shutdown."""
    app = create_app(service)
    uvicorn.run(app, host=host, port=port, log_level="info")
```

### Concurrency Model

- FastAPI handlers are `async def` — run on the event loop, non-blocking.
- `run_in_threadpool()` bridges to sync code (TOML parsing, generation build,
  nbdkit process management) without blocking the event loop.
- Long-running reload sequence (UDC unbind + nbdkit restart + NBD reconnect +
  warm + UDC rebind) runs in thread pool; `POST /api/config/reload` awaits it.
- Concurrent reload attempts are guarded by `asyncio.Lock` — second attempt
  returns 423 `RELOAD_IN_PROGRESS`.

## Security

- **Binding:** `127.0.0.1` only — no network-exposed port
- **No auth:** Trusted local system. If SBC is multi-user, restrict via firewall rules: `iptables -A INPUT -p tcp --dport 8080 ! -s 127.0.0.1 -j DROP`
- **Payload size:** Max 1 MiB for PUT body (config files are ~1 KiB)
- **Rate limiting:** Optional — systemd can rate-limit via `IPAddressDeny=any` plus localhost-only binding makes it moot
- **Sensitive data:** Config may contain NAS server IPs (internal LAN, low sensitivity). No passwords (NFS uses host-based auth or Kerberos, not in config).

## References

- DD-07: HTTP API on localhost
- KB 05: exFAT filesystem details
- FastAPI: https://fastapi.tiangolo.com/
- Pydantic v2: https://docs.pydantic.dev/latest/
- uvicorn: https://www.uvicorn.org/
- TOML specification: https://toml.io/en/v1.0.0
