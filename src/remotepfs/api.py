"""FastAPI control plane for RemotePFS."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import MAX_CONFIG_BYTES, ConfigError


class ServiceInfo(BaseModel):
    """Service runtime information."""

    version: str
    uptime_seconds: int
    state: str
    state_detail: str


class GadgetInfo(BaseModel):
    """USB gadget status."""

    udc_bound: bool
    udc_name: str | None = None
    lun_file: str | None = None
    lun_ro: bool = True
    lun_size_bytes: int | None = None


class NbdInfo(BaseModel):
    """NBD status and counters."""

    connected: bool
    socket_path: str
    connections: int = 0
    requests_total: int = 0
    bytes_served: int = 0
    read_errors: int = 0


class MountInfo(BaseModel):
    """Network mount status."""

    name: str
    protocol: str
    endpoint: str
    mount_point: str
    mounted: bool
    state: str


class ConfigInfo(BaseModel):
    """Active config status."""

    path: str
    last_loaded: datetime
    generation: int
    game_count: int
    valid: bool


class SystemInfo(BaseModel):
    """Host resource summary."""

    memory_used_mib: float = 0
    page_cache_mib: float = 0
    cpu_percent: float = 0


class StatusResponse(BaseModel):
    """Full service status response."""

    service: ServiceInfo
    gadget: GadgetInfo
    nbd: NbdInfo
    mounts: list[MountInfo]
    config: ConfigInfo
    system: SystemInfo


class ValidationErrorDetail(BaseModel):
    """Field-level config error."""

    field: str
    message: str


class ConfigInvalidResponse(BaseModel):
    """Config rejection response."""

    status: str
    errors: list[ValidationErrorDetail]


class ConfigReplaceResponse(BaseModel):
    """Config activation response."""

    status: str
    generation: int
    warnings: list[str] = Field(default_factory=list)
    reload_time_ms: int = 0


class CompileResponse(BaseModel):
    """Config compilation response."""

    status: str
    generation: int
    warnings: list[str] = Field(default_factory=list)
    size_bytes: int


class ActivateRequest(BaseModel):
    """Generation activation request."""

    generation: int


class ActivateResponse(BaseModel):
    """Generation activation response."""

    status: str
    generation: int
    reload_time_ms: int


class HealthResponse(BaseModel):
    """Liveness response."""

    status: str
    uptime_seconds: int


class GameEntry(BaseModel):
    """Virtual filesystem root entry."""

    virtual_path: str
    type: str
    size_bytes: int | None = None
    entry_count: int | None = None
    total_size_bytes: int | None = None


class GamesResponse(BaseModel):
    """Virtual filesystem listing."""

    games: list[GameEntry]


def _config_error_response(exc: ConfigError) -> JSONResponse:
    """Convert ConfigError to spec 08 field-level 422 response."""
    error = ValidationErrorDetail(field=exc.field or "<root>", message=exc.message)
    return JSONResponse(status_code=422, content=ConfigInvalidResponse(status="rejected", errors=[error]).model_dump())


def create_app(service: Any) -> FastAPI:
    """Create FastAPI app with injected service context.

    Args:
        service: RemotePfsService-compatible object.

    Returns:
        Configured FastAPI application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start and stop service when ASGI lifespan runs."""
        if hasattr(app.state.service, "startup"):
            await app.state.service.startup()
        yield
        if hasattr(app.state.service, "shutdown"):
            await app.state.service.shutdown()

    app = FastAPI(title="RemotePFS API", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status() -> StatusResponse:
        """Return current service state."""
        return await service.get_status()

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """Return active config as JSON."""
        return await service.get_config_json()

    @app.post("/api/config/compile", response_model=CompileResponse)
    async def compile_config(request: Request) -> CompileResponse:
        """Validate and compile config without activation."""
        body = await request.body()
        if len(body) > MAX_CONFIG_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "PAYLOAD_TOO_LARGE", "message": "Config body exceeds 1 MiB limit"},
            )
        try:
            result = await run_in_threadpool(service.compile_config, body.decode("utf-8"))
        except ConfigError as exc:
            return _config_error_response(exc)  # type: ignore[return-value]
        return result

    @app.post("/api/config/activate", response_model=ActivateResponse)
    async def activate_config(payload: ActivateRequest) -> ActivateResponse:
        """Activate a previously compiled generation."""
        try:
            return await run_in_threadpool(service.activate_generation, payload.generation)
        except RuntimeError as exc:
            raise HTTPException(status_code=423, detail={"code": "RELOAD_IN_PROGRESS", "message": str(exc)}) from exc

    @app.put("/api/config", response_model=ConfigReplaceResponse)
    async def put_config(request: Request) -> ConfigReplaceResponse:
        """Replace configuration from YAML or JSON body."""
        body = await request.body()
        if len(body) > MAX_CONFIG_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "PAYLOAD_TOO_LARGE", "message": "Config body exceeds 1 MiB limit"},
            )
        content_type = request.headers.get("content-type", "")
        if not any(kind in content_type for kind in ("yaml", "yml", "json")):
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "UNSUPPORTED_MEDIA_TYPE",
                    "message": "Content-Type must be application/yaml or application/json",
                },
            )
        try:
            return await run_in_threadpool(service.replace_config, body.decode("utf-8"))
        except ConfigError as exc:
            return _config_error_response(exc)  # type: ignore[return-value]

    @app.post("/api/config/reload", response_model=ConfigReplaceResponse)
    async def reload_config() -> ConfigReplaceResponse:
        """Reload on-disk config."""
        try:
            return await run_in_threadpool(service.reload_config)
        except ConfigError as exc:
            return _config_error_response(exc)  # type: ignore[return-value]

    @app.get("/api/health", response_model=HealthResponse)
    async def get_health() -> HealthResponse:
        """Return liveness status."""
        return await service.get_health()

    @app.get("/api/games", response_model=GamesResponse)
    async def get_games() -> GamesResponse:
        """List virtual filesystem entries."""
        return await service.get_games()

    @app.post("/api/eject")
    async def eject() -> dict[str, str]:
        """Gracefully unbind the USB gadget."""
        await run_in_threadpool(service.eject)
        return {"status": "ejected"}

    @app.exception_handler(ConfigError)
    async def config_exception_handler(request: Request, exc: ConfigError) -> JSONResponse:
        """Return field-level validation errors."""
        return _config_error_response(exc)

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """Return structured internal error without stack trace."""
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": str(exc), "details": None}},
        )

    return app
