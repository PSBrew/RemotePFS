"""Focused API contract tests using an injected fake service."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from remotepfs.api import create_app


class FakeService:
    """Small service double exposing observable API behavior."""

    async def get_status(self):
        return {
            "service": {"version": "0.1.0", "uptime_seconds": 1, "state": "running", "state_detail": "serving"},
            "gadget": {"udc_bound": False, "udc_name": None, "lun_file": None, "lun_ro": True, "lun_size_bytes": None},
            "nbd": {"connected": False, "socket_path": "/run/remotepfs/nbd.sock"},
            "nfs_mounts": [],
            "config": {
                "path": "/tmp/remotepfs.conf",
                "last_loaded": datetime.now(UTC),
                "generation": 0,
                "game_count": 0,
                "valid": True,
            },
            "system": {},
        }

    async def get_config_json(self):
        return {"generation": 0, "sources": [], "entries": []}

    async def get_health(self):
        return {"status": "healthy", "uptime_seconds": 1}

    async def get_games(self):
        return {"games": []}

    def eject(self):
        return None


def test_health_and_games() -> None:
    client = TestClient(create_app(FakeService()))
    assert client.get("/api/health").json()["status"] == "healthy"
    assert client.get("/api/games").json() == {"games": []}


def test_status_shape() -> None:
    client = TestClient(create_app(FakeService()))
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["nbd"]["socket_path"] == "/run/remotepfs/nbd.sock"


def test_put_rejects_unsupported_media_type() -> None:
    client = TestClient(create_app(FakeService()))
    response = client.put("/api/config", content="{}", headers={"content-type": "text/plain"})
    assert response.status_code == 415


def test_health_does_not_require_asgihardware() -> None:
    client = TestClient(create_app(FakeService()))
    assert client.get("/api/health").status_code == 200
