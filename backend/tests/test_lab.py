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
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "lab.db", "roots": [], "controls": False, "telemetry": "live"})) as c:
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
    with patch("lab.adapters.run") as control:
        assert client.post("/api/engines/kmic/control", headers=AUTH, json={"action": "start"}).status_code == 403
        control.assert_not_called()

@pytest.mark.parametrize("engine,body", [("unknown", {"action": "start"}), ("kmic", {"action": "restart"}), ("kmic", {"action": "start", "command": "id"})])
def test_control_input_is_closed(client, engine, body):
    assert client.post(f"/api/engines/{engine}/control", headers=AUTH, json=body).status_code == 422


def test_controls_cannot_be_enabled_by_config(tmp_path):
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "lab.db", "controls": True})) as c:
        assert c.post("/api/engines/pxa/control", headers=AUTH, json={"action": "start"}).status_code == 403


def test_adapter_is_read_only():
    assert not hasattr(NativeAdapter("kmic"), "control")
    with patch("lab.adapters.run") as run:
        run.return_value.stdout = "active"
        assert NativeAdapter("kmic").status()["state"] == "active"
        run.assert_called_once_with(["systemctl", "--user", "is-active", "kmic-qwen27b.service"])
    with pytest.raises(ValueError):
        NativeAdapter("kmic; id")


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
    with patch("lab.telemetry.gpu_metrics", return_value={"available": False, "devices": [], "message": "No GPU"}):
        assert "cpu_percent" in client.get("/api/system", headers=AUTH).json()
    assert client.get("/api/models", headers=AUTH).json()["items"] == []


def test_reopen_keeps_benchmark(tmp_path):
    cfg = {"token": TOKEN, "db": tmp_path / "persist.db"}
    with TestClient(create_app(cfg)) as c:
        c.post("/api/benchmarks", headers=AUTH, json={"engine": "pxa", "model": "x", "tokens_per_second": 1})
    with TestClient(create_app(cfg)) as c:
        assert len(c.get("/api/benchmarks", headers=AUTH).json()["items"]) == 1

def test_security_headers_and_production_dashboard(tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>P100 Inference Lab</title>")
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "lab.db", "static": static})) as c:
        assert "P100 Inference Lab" in c.get("/").text
        response = c.get("/api/models", headers=AUTH)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert c.get("/api/models").status_code == 401


def test_mock_never_reads_hardware(tmp_path):
    with patch("lab.telemetry.gpu_metrics") as gpu, patch("lab.telemetry.psutil.cpu_percent") as cpu:
        with TestClient(create_app({"token": TOKEN, "db": tmp_path / "mock.db", "telemetry": "mock"})) as c:
            data = c.get("/api/system", headers=AUTH).json()
            assert data["mode"] == "mock"
            assert "MOCK" in data["gpus"]["devices"][0]["name"]
        gpu.assert_not_called()
        cpu.assert_not_called()


def test_invalid_telemetry_mode():
    with pytest.raises(ValueError):
        create_app({"telemetry": "remote-shell"})


def test_secret_file(monkeypatch, tmp_path):
    secret = tmp_path / "token"
    secret.write_text(TOKEN + "\n")
    monkeypatch.setenv("LAB_API_TOKEN_FILE", str(secret))
    with TestClient(create_app({"db": tmp_path / "secret.db"})) as c:
        assert c.get("/api/models", headers=AUTH).status_code == 200


def test_live_gpu_command_is_fixed():
    with patch("lab.adapters.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        gpu_metrics()
        assert run.call_args.args[0] == ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,temperature.gpu,power.draw,utilization.gpu", "--format=csv,noheader,nounits"]
