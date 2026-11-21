import sqlite3
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.main import create_app
from test_engine_control import ADMIN, AUTH, IPC, settings

MODEL_ID = "fixture-q6-v1"
VALUES = {"context": 8192, "batch": 512, "ubatch": 128}
CATALOG_ENTRY = {
    "id": MODEL_ID, "engine_version": "fixture-build", "gpu": "Tesla P100 PCIe 16GB", "gpu_count": 2,
    "quantization": "Q6_K", "bytes": 1000, "sha256": "b" * 64, "limits": VALUES,
}


def test_presets_are_persistent_and_default_is_transactional(tmp_path):
    body = {"model_id": MODEL_ID, "name": "Decode tune", "settings": VALUES, "make_default": True}
    evidence = {"report_sha256": "a" * 64, "metric": "decode_tokens_per_second", "value": 18.5}
    with TestClient(create_app(settings(tmp_path))) as client, patch(
        "lab.main.engine_exchange", return_value={"catalog": [CATALOG_ENTRY]},
    ):
        assert client.post("/api/model-presets", json=body).status_code == 401
        assert client.post("/api/model-presets", headers=AUTH, json={**body, "evidence": evidence}).status_code == 422
        first = client.post("/api/model-presets", headers=AUTH, json={
            **body, "evidence": evidence, "workload": "Single stream, 2048 prompt and 256 decode tokens",
        })
        assert first.status_code == 201
        saved = first.json()
        assert saved["evidence"] == evidence and saved["environment"]["sha256"] == "b" * 64
        assert client.post("/api/model-presets", headers=AUTH, json={**body, "name": "decode tune"}).status_code == 409
        assert client.get("/api/models", headers=AUTH).json()["presets"][0]["is_default"]
        for values in ({**VALUES, "context": 16384}, {**VALUES, "ubatch": 1024}, {**VALUES, "batch": True}):
            assert client.post("/api/model-presets", headers=AUTH, json={**body, "settings": values}).status_code == 422
        assert client.post("/api/model-presets", headers=AUTH, json={**body, "model_id": "unknown"}).status_code == 422
        second = client.post("/api/model-presets", headers=AUTH, json={**body, "name": "Chat"})
        assert second.status_code == 201
        assert second.json()["evidence"] is None
    with TestClient(create_app(settings(tmp_path))) as client:
        presets = client.get("/api/models", headers=AUTH).json()["presets"]
        assert [(item["name"], item["is_default"]) for item in presets] == [("Chat", True), ("Decode tune", False)]
        assert client.put(f"/api/model-presets/{saved['id']}/default", headers=AUTH).status_code == 204
        assert client.delete(f"/api/model-presets/{second.json()['id']}", headers=AUTH).status_code == 204
        assert client.get("/api/models", headers=AUTH).json()["presets"] == [saved]


def test_staging_requires_confirmation_and_both_credentials(tmp_path):
    live_settings = {"context": 262144, "batch": 2048, "ubatch": 2048}
    body = {"model_id": MODEL_ID, "settings": live_settings, "confirmed": True}
    path = "/api/engines/kmic/selection"
    acknowledgment = {"accepted": True, "operation": "stage_selection", "unit": "kmic-qwen27b.service",
                      "configured_selection": {"model_id": MODEL_ID, "settings": live_settings}}
    with TestClient(create_app(settings(tmp_path))) as client, patch(
        "lab.main.engine_exchange", return_value=acknowledgment,
    ) as agent:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=AUTH).status_code == 403
        assert client.post(path, json={**body, "confirmed": False}, headers=ADMIN).status_code == 422
        assert client.post(path, json={**body, "settings": {**live_settings, "context": 262145}},
                           headers=ADMIN).status_code == 422
        assert client.post(path, json={**body, "path": "/etc/passwd"}, headers=ADMIN).status_code == 422
        assert client.post(path.replace("kmic", "pxa"), json=body, headers=ADMIN).status_code == 403
        agent.assert_not_called()
        assert client.post(path, json=body, headers=ADMIN).json() == acknowledgment
        agent.assert_called_once_with("/engine-agent/agent.sock", IPC, "stage_selection", body)
    with sqlite3.connect(tmp_path / "lab.db") as connection:
        assert connection.execute("SELECT action,outcome FROM audit").fetchall() == [("stage_selection", "accepted")]
