import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lab.main import create_app
from lab.adapters import NativeAdapter, gpu_metrics, inventory

TOKEN = "test-secret-" * 4
AUTH = {"Authorization": f"Bearer {TOKEN}"}

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "lab.db", "roots": [], "controls": False})) as c:
        yield c

@pytest.mark.parametrize("method,url", [("get", "/api/system"), ("get", "/api/engines"), ("get", "/api/models"), ("get", "/api/benchmarks"), ("post", "/api/benchmarks"), ("post", "/api/engines/kmic/control")])
def test_every_api_requires_auth(client, method, url):
    assert getattr(client, method)(url).status_code == 401


def test_startup_rejects_missing_secret(tmp_path):
    with pytest.raises(RuntimeError):
        with TestClient(create_app({"token": "", "db": tmp_path / "bad.db"})):
            pass


def test_wrong_and_non_ascii_tokens(client):
    assert client.get("/api/models", headers={"Authorization": "Bearer incorrect"}).status_code == 401


def test_health_is_minimal(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_disabled_controls_cannot_execute(client):
    with patch("lab.main.NativeAdapter.control") as control:
        assert client.post("/api/engines/kmic/control", headers=AUTH, json={"action": "start"}).status_code == 403
        control.assert_not_called()

@pytest.mark.parametrize("engine,body", [("unknown", {"action": "start"}), ("kmic", {"action": "restart"}), ("kmic", {"action": "start", "command": "id"})])
def test_control_input_is_closed(client, engine, body):
    assert client.post(f"/api/engines/{engine}/control", headers=AUTH, json=body).status_code == 422


def test_control_audit_and_failure(tmp_path):
    import sqlite3
    db = tmp_path / "lab.db"
    with TestClient(create_app({"token": TOKEN, "db": db, "controls": True})) as c:
        with patch("lab.main.NativeAdapter.control"), patch("lab.main.NativeAdapter.status", return_value={"state": "active"}):
            assert c.post("/api/engines/pxa/control", headers=AUTH, json={"action": "start"}).status_code == 200
        with patch("lab.main.NativeAdapter.control", side_effect=subprocess.TimeoutExpired("systemctl", 20)):
            assert c.post("/api/engines/pxa/control", headers=AUTH, json={"action": "stop"}).status_code == 502
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT outcome FROM audit ORDER BY id").fetchall() == [("succeeded",), ("failed_or_timed_out",)]


def test_fixed_argv_no_shell():
    with patch("lab.adapters.run") as run:
        run.return_value.returncode = 0
        NativeAdapter("qr0n").control("start")
        run.assert_called_once_with(["systemctl", "--user", "--no-ask-password", "start", "p100-lab-qr0n.service"], timeout=20)
    with pytest.raises(ValueError):
        NativeAdapter("kmic; id")
    with pytest.raises(ValueError):
        NativeAdapter("kmic").control("sh")


def test_benchmark_persistence_and_validation(client):
    payload = {"engine": "kmic", "model": "model.gguf", "tokens_per_second": 12.5, "notes": "manual test"}
    assert client.post("/api/benchmarks", headers=AUTH, json=payload).status_code == 201
    rows = client.get("/api/benchmarks", headers=AUTH).json()["items"]
    assert rows[0]["tokens_per_second"] == 12.5 and rows[0]["source"] == "manual"
    payload["tokens_per_second"] = -1
    assert client.post("/api/benchmarks", headers=AUTH, json=payload).status_code == 422
    assert client.get("/api/benchmarks?limit=10000", headers=AUTH).status_code == 422


def test_inventory_bounded_and_relative(tmp_path):
    (tmp_path / "a.gguf").write_bytes(b"model")
    (tmp_path / "ignore.txt").write_text("ignore")
    result = inventory([str(tmp_path)], limit=1)
    assert result["items"] == [{"root": 1, "name": "a.gguf", "bytes": 5}]
    assert result["truncated"]
    assert inventory([str(tmp_path / "missing")])["warnings"]


def test_inventory_skips_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.gguf").write_bytes(b"hidden")
    models = tmp_path / "models"
    models.mkdir()
    try:
        (models / "link").symlink_to(outside, target_is_directory=True)
        (models / "file.gguf").symlink_to(outside / "secret.gguf")
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    assert inventory([str(models)])["items"] == []


def test_no_gpu_and_service_manager_are_reported():
    with patch("lab.adapters.run", side_effect=FileNotFoundError):
        assert not gpu_metrics()["available"]
        assert NativeAdapter("kmic").status()["state"] == "unavailable"


def test_gpu_unavailable_values():
    with patch("lab.adapters.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "0, Tesla P100, 16384, 42, 35, [N/A], 0\n"
        gpu = gpu_metrics()["devices"][0]
        assert gpu["power_w"] is None and gpu["memory_total_mib"] == 16384


def test_system_and_empty_inventory(client):
    with patch("lab.main.gpu_metrics", return_value={"available": False, "devices": [], "message": "No GPU"}):
        assert "cpu_percent" in client.get("/api/system", headers=AUTH).json()
    assert client.get("/api/models", headers=AUTH).json()["items"] == []


def test_reopen_keeps_benchmark(tmp_path):
    cfg = {"token": TOKEN, "db": tmp_path / "persist.db"}
    with TestClient(create_app(cfg)) as c:
        c.post("/api/benchmarks", headers=AUTH, json={"engine": "pxa", "model": "x", "tokens_per_second": 1})
    with TestClient(create_app(cfg)) as c:
        assert len(c.get("/api/benchmarks", headers=AUTH).json()["items"]) == 1
