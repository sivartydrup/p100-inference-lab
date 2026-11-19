import copy
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lab import native_store as store
from lab.main import create_app
from lab.native_cli import main
from lab.native_config import NativeError, NativePreset, Registry
from lab.native_process import Captured, SystemdSupervisor, duration_seconds, unit_name
from lab.native_protocol import parse_output
from lab.native_runner import execute, recover, NvidiaHardware

TOKEN = "viewer-test-" * 4
AUTH = {"Authorization": "Bearer " + TOKEN}
GPU = "GPU-11111111-1111-1111-1111-111111111111"


def preset_data():
    return {"id": "approved-test", "reviewed": True, "engine": "kmic", "engine_revision": "a" * 40,
            "binary": {"path": "/approved/llama-bench", "sha256": "b" * 64},
            "libraries": [{"path": "/approved/libggml-cuda.so", "sha256": "c" * 64}],
            "model_id": "test-model", "model": {"path": "/models/test.gguf", "sha256": "d" * 64},
            "model_license": "Apache-2.0 test data only", "declared_cuda_version": "12.8",
            "gpu_uuid": GPU, "compute_capability": "6.0", "driver_version": "580.178.04",
            "min_free_vram_mib": 1024, "min_free_ram_mib": 1024, "memory_max_mib": 4096,
            "threads": 1, "trials": 2, "warmup": 1}


@pytest.fixture
def preset():
    return NativePreset.model_validate(preset_data())


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "native.sqlite3"
    store.initialize(p)
    return p


def output(preset):
    rows = []
    for prompt, generated, ns in [(preset.prompt_tokens, 0, 1_000_000_000),
                                 (0, preset.output_tokens, 2_000_000_000)]:
        rate = (prompt + generated) * 1e9 / ns
        rows.append({"n_prompt": prompt, "n_gen": generated, "n_threads": preset.threads,
                     "n_batch": preset.batch, "n_ubatch": preset.microbatch, "n_gpu_layers": preset.gpu_layers,
                     "model_filename": preset.model.path, "build_commit": preset.engine_revision[:9],
                     "backends": "CUDA", "samples_ns": [ns], "samples_ts": [rate],
                     "avg_ns": ns, "avg_ts": rate})
    return json.dumps(rows).encode()


class Hardware:
    def preflight(self, preset, db, check):
        check()
        return self.snapshot(preset, check)

    def snapshot(self, preset, check):
        check()
        return {"uuid": preset.gpu_uuid, "driver_version": preset.driver_version,
                "compute_capability": preset.compute_capability, "name": "isolated test fixture"}


class Supervisor:
    def available(self):
        pass

    def __init__(self, behavior="success", action=None):
        self.behavior, self.action = behavior, action
        self.stopped, self.started = [], []
        self.units = set()

    def listed_units(self):
        return set(self.units)

    def stop(self, unit):
        self.stopped.append(unit)
        if self.behavior == "cleanup_error":
            raise NativeError("cleanup_unconfirmed")
        self.units.discard(unit)

    def run(self, preset, unit, deadline, check):
        self.started.append(unit)
        self.units.add(unit)
        if self.action:
            self.action()
        check()
        if self.behavior in {"timeout", "cleanup_error"}:
            raise NativeError("deadline_exceeded")
        return Captured(1 if self.behavior == "nonzero" else 0,
                        b"malformed" if self.behavior == "malformed" else output(preset), b"private diagnostic", 15.0)


def run_fixture(db, preset, monkeypatch, supervisor=None, stop=None):
    monkeypatch.setattr("lab.native_runner.verify_artifacts", lambda *_: {})
    monkeypatch.setattr("lab.native_runner.unchanged", lambda *_: None)
    queued = store.enqueue(db, preset, str(uuid4()))
    execute(db, store.claim(db), Registry(enabled=True, presets=[preset]), supervisor or Supervisor(),
            Hardware(), stop or threading.Event())
    return store.get_job(db, queued["id"])


@pytest.mark.parametrize("changes", [{"command": "id"}, {"args": ["--help"]}, {"engine": "pxa"},
                                   {"trials": 11}, {"warmup": 3}, {"gpu_layers": 0}, {"threads": True},
                                   {"microbatch": 200}, {"protocol": "shell"}])
def test_only_fixed_bounded_administrator_presets(changes):
    with pytest.raises(ValidationError):
        NativePreset.model_validate({**preset_data(), **changes})


@pytest.mark.parametrize("path", ["relative/llama-bench", "/tmp/$HOME/llama-bench", "/tmp/a\n/llama-bench", "/tmp/a:b/llama-bench"])
def test_artifact_paths_cannot_expand(path):
    with pytest.raises(ValidationError):
        NativePreset.model_validate({**preset_data(), "binary": {"path": path, "sha256": "b" * 64}})


def test_opt_in_and_approval_required(preset, db, monkeypatch):
    monkeypatch.delenv("LAB_ENABLE_NATIVE_RUNNER", raising=False)
    with pytest.raises(SystemExit, match="native_runner_disabled"):
        main(["--db", str(db), "work", "--registry", "does-not-exist"])
    with pytest.raises(NativeError, match="preset_not_approved"):
        Registry(presets=[preset]).approved(preset.id)
    unreviewed = preset.model_copy(update={"reviewed": False})
    with pytest.raises(NativeError, match="preset_not_approved"):
        Registry(enabled=True, presets=[unreviewed]).approved(preset.id)
    assert preset.public()["ttft_available"] is False


def test_native_database_is_separate_and_never_overwrites_fixture_history(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE benchmarks(id INTEGER PRIMARY KEY,notes TEXT)")
        c.execute("INSERT INTO benchmarks(notes) VALUES('keep')")
    before = path.read_bytes()
    with pytest.raises(NativeError, match="refuse_existing_database"):
        store.initialize(path)
    assert path.read_bytes() == before
    with pytest.raises(NativeError, match="native_database_identity"):
        store.list_jobs(path)


def test_native_queue_idempotency_capacity_and_one_claim(db, preset):
    key = str(uuid4())
    first = store.enqueue(db, preset, key)
    assert store.enqueue(db, preset, key)["id"] == first["id"]
    with pytest.raises(NativeError, match="idempotency_conflict"):
        store.enqueue(db, preset.model_copy(update={"trials": 3}), key)
    for _ in range(9):
        store.enqueue(db, preset, str(uuid4()))
    with pytest.raises(NativeError, match="native_queue_full"):
        store.enqueue(db, preset, str(uuid4()))
    running = store.claim(db)
    assert running
    with pytest.raises(NativeError, match="cleanup_required"):
        store.claim(db)
    store.finish(db, running["id"], "cleanup_failed", "cleanup_unconfirmed")
    with pytest.raises(NativeError, match="cleanup_required"):
        store.claim(db)


def test_measured_samples_are_separate_from_warmup_and_paths_are_redacted(db, preset, monkeypatch):
    result = run_fixture(db, preset, monkeypatch)
    assert result["status"] == "succeeded" and result["source"] == "native"
    assert len(result["attempts"]) == 3
    assert result["summary"]["sample_count"] == 2
    assert result["summary"]["metrics"]["prefill_tokens_per_second"]["median"] == 128
    assert result["summary"]["metrics"]["decode_tokens_per_second"]["median"] == 16
    assert result["summary"]["ttft_ms"] is None
    rendered = json.dumps(result)
    assert preset.binary.path not in rendered and preset.model.path not in rendered
    assert "private diagnostic" not in rendered
    assert result["attempts"][0]["sample"]["stderr_sha256"]


@pytest.mark.parametrize("behavior,expected,error", [
    ("nonzero", "failed", "engine_exit_nonzero"), ("malformed", "failed", "invalid_benchmark_output"),
    ("timeout", "timed_out", "deadline_exceeded")])
def test_failures_do_not_publish_success(db, preset, monkeypatch, behavior, expected, error):
    supervisor = Supervisor(behavior)
    result = run_fixture(db, preset, monkeypatch, supervisor)
    assert result["status"] == expected and result["error_code"] == error
    assert result["summary"] is None and supervisor.stopped


def test_cancellation_and_shutdown_confirm_cleanup(db, preset, monkeypatch):
    monkeypatch.setattr("lab.native_runner.verify_artifacts", lambda *_: {})
    monkeypatch.setattr("lab.native_runner.unchanged", lambda *_: None)
    queued = store.enqueue(db, preset, str(uuid4()))
    supervisor = Supervisor(action=lambda: store.cancel(db, queued["id"]))
    execute(db, store.claim(db), Registry(enabled=True, presets=[preset]), supervisor, Hardware(), threading.Event())
    assert store.get_job(db, queued["id"])["status"] == "cancelled" and supervisor.stopped
    stop = threading.Event()
    supervisor = Supervisor(action=stop.set)
    result = run_fixture(db, preset, monkeypatch, supervisor, stop)
    assert result["status"] == "interrupted" and supervisor.stopped


def test_unconfirmed_cleanup_blocks_queue_until_recovery(db, preset, monkeypatch):
    monkeypatch.setattr("lab.native_runner.verify_artifacts", lambda *_: {})
    monkeypatch.setattr("lab.native_runner.unchanged", lambda *_: None)
    queued = store.enqueue(db, preset, str(uuid4()))
    supervisor = Supervisor("cleanup_error")
    with pytest.raises(NativeError, match="cleanup_unconfirmed"):
        execute(db, store.claim(db), Registry(enabled=True, presets=[preset]), supervisor, Hardware(), threading.Event())
    assert store.get_job(db, queued["id"])["status"] == "cleanup_failed"
    with pytest.raises(NativeError, match="cleanup_required"):
        store.claim(db)
    supervisor.behavior = "success"
    recover(db, supervisor)
    assert store.get_job(db, queued["id"])["status"] == "interrupted"
    supervisor.units.add(unit_name(str(uuid4()), "measured", 0))
    with pytest.raises(NativeError, match="unknown_native_unit_active"):
        recover(db, supervisor)


def test_changed_preset_never_launches(db, preset):
    queued = store.enqueue(db, preset, str(uuid4()))
    supervisor = Supervisor()
    changed = preset.model_copy(update={"trials": 3})
    execute(db, store.claim(db), Registry(enabled=True, presets=[changed]), supervisor, Hardware(), threading.Event())
    assert store.get_job(db, queued["id"])["error_code"] == "preset_changed"
    assert not supervisor.started


@pytest.mark.parametrize("change", ["nan", "duplicate", "wrong_model", "cpu", "extra_row", "bad_duration", "wrong_workload", "build"])
def test_protocol_rejects_invalid_or_mismatched_evidence(preset, change):
    rows = json.loads(output(preset))
    if change == "nan": rows[0]["avg_ts"] = float("nan")
    elif change == "duplicate": rows[1] = rows[0]
    elif change == "wrong_model": rows[0]["model_filename"] = "/different.gguf"
    elif change == "cpu": rows[0]["backends"] = "CPU"
    elif change == "extra_row": rows.append(rows[0])
    elif change == "bad_duration": rows[0]["samples_ns"] = [0]
    elif change == "wrong_workload": rows[0]["n_batch"] = 1
    elif change == "build": rows[0]["build_commit"] = "b" * 9
    with pytest.raises(NativeError):
        parse_output(json.dumps(rows).encode(), preset)


def test_preflight_rejects_busy_wrong_hot_or_exhausted_gpu(db, preset, monkeypatch):
    hardware = NvidiaHardware()
    snapshot = {"uuid": GPU, "driver_version": preset.driver_version, "compute_capability": "6.0",
                "free_vram_mib": 16000, "temperature_c": 35}
    monkeypatch.setattr(hardware, "snapshot", lambda *_: dict(snapshot))
    monkeypatch.setattr(hardware, "query", lambda *_: [[GPU, "123"]])
    with pytest.raises(NativeError, match="gpu_busy"):
        hardware.preflight(preset, db, lambda: None)
    monkeypatch.setattr(hardware, "query", lambda *_: [])
    for updates, code in [({"driver_version": "0"}, "gpu_or_driver_mismatch"),
                          ({"free_vram_mib": 1}, "gpu_resource_preflight_failed"),
                          ({"temperature_c": 89}, "gpu_resource_preflight_failed")]:
        original = dict(snapshot); snapshot.update(updates)
        with pytest.raises(NativeError, match=code): hardware.preflight(preset, db, lambda: None)
        snapshot = original


def test_systemd_command_and_resource_attestation_are_fixed(preset):
    supervisor = object.__new__(SystemdSupervisor)
    supervisor.namespace_reference = lambda: {"user": "user:[1]", "mnt": "mnt:[2]"}
    unit = unit_name(str(uuid4()), "measured", 0)
    argv = supervisor.command(preset, unit, 5, Path("/run/user/1000/private/approved"))
    assert "--expand-environment=no" in argv and "--property=KillMode=control-group" in argv
    assert "--property=RuntimeMaxSec=5.000" in argv and "--property=RestrictAddressFamilies=AF_UNIX" in argv
    assert "--environment" in argv and "-i" in argv
    assert "--property=PrivateUsers=yes" in argv
    assert argv[-len(preset.argv()):] == preset.argv()
    with pytest.raises(NativeError): supervisor.command(preset, "ssh.service", 5, Path("/tmp/gate"))
    values = {"LoadState": "loaded", "ActiveState": "active", "KillMode": "control-group",
              "SendSIGKILL": "yes", "NoNewPrivileges": "yes", "PrivateUsers": "yes", "PrivateTmp": "yes",
              "ProtectSystem": "strict", "ProtectHome": "read-only", "RestrictAddressFamilies": "AF_UNIX",
              "MemoryMax": str(preset.memory_max_mib * 2**20), "TasksMax": "64", "RuntimeMaxUSec": "5s",
              "CPUQuotaPerSecUSec": "1s"}
    supervisor.control = lambda *_: Captured(0, "\n".join(k+"="+v for k,v in values.items()).encode(), b"", 1)
    assert supervisor.attest(unit, preset, 5)
    values["PrivateUsers"] = "no"
    with pytest.raises(NativeError, match="unit_policy_not_confirmed"): supervisor.attest(unit, preset, 5)
    values["PrivateUsers"] = "yes"
    values["KillMode"] = "process"
    with pytest.raises(NativeError, match="unit_policy_not_confirmed"): supervisor.attest(unit, preset, 5)
    assert duration_seconds("1min 500ms") == 60.5


def test_native_api_is_read_only_authenticated_and_does_not_create_files(tmp_path, db, preset):
    missing = tmp_path / "missing.sqlite3"
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "web.sqlite3", "native_db": missing})) as client:
        assert client.get("/api/native-benchmarks").status_code == 401
        assert client.get("/api/native-benchmarks", headers=AUTH).status_code == 503
        assert not missing.exists()
        assert client.post("/api/native-benchmarks", headers=AUTH, json={"command":"id"}).status_code == 403
    job = store.enqueue(db, preset, str(uuid4()))
    before = db.read_bytes()
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "web.sqlite3", "native_db": db})) as client:
        listed = client.get("/api/native-benchmarks", headers=AUTH).json()
        assert listed["configured"] and not listed["execution_available_via_api"]
        assert listed["items"][0]["id"] == job["id"]
        assert client.get(f'/api/native-benchmarks/{job["id"]}/export', headers=AUTH).json()["source"] == "native"
        assert client.post(f'/api/native-benchmarks/{job["id"]}/cancel', headers=AUTH).status_code == 403
        assert client.get(f'/api/native-benchmarks/{job["id"]}').status_code == 401
    assert db.read_bytes() == before


def test_start_gate_is_released_only_after_policy_confirmation(tmp_path, preset, monkeypatch):
    supervisor = object.__new__(SystemdSupervisor)
    supervisor.env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    supervisor.namespace_reference = lambda: {"user": "user:[1]", "mnt": "mnt:[2]"}
    attestations = iter([False, True])
    supervisor.attest = lambda *_: next(attestations)
    def transport(argv, env, deadline, check):
        gate = Path(argv[argv.index("--gate") + 1])
        check()
        assert not gate.exists()
        check()
        assert gate.read_bytes() == b"approved\n"
        return Captured(0, b"[]", b"", 1)
    monkeypatch.setattr("lab.native_process.capture", transport)
    supervisor.run(preset, unit_name(str(uuid4()), "measured", 0), time.monotonic()+10, lambda: None)
    assert not list(tmp_path.iterdir())


def test_cleanup_requires_a_confirmed_inactive_unit():
    supervisor = object.__new__(SystemdSupervisor)
    unit = unit_name(str(uuid4()), "measured", 0)
    calls = []
    def active(*args):
        calls.append(args)
        return Captured(0, b"LoadState=loaded\nActiveState=active\n", b"", 1)
    supervisor.control = active
    with pytest.raises(NativeError, match="cleanup_unconfirmed"):
        supervisor.stop(unit)
    before = len(calls)
    with pytest.raises(NativeError, match="invalid_unit_identity"):
        supervisor.stop("netdata.service")
    assert len(calls) == before
    supervisor.control = lambda *_: Captured(4,b"LoadState=not-found\nActiveState=inactive\n",b"",1)
    supervisor.stop(unit)


@pytest.mark.parametrize("behavior", ["policy_error", "never_active"])
def test_unconfirmed_policy_never_opens_start_gate(tmp_path, preset, monkeypatch, behavior):
    supervisor = object.__new__(SystemdSupervisor)
    supervisor.namespace_reference = lambda: {"user": "user:[1]", "mnt": "mnt:[2]"}
    supervisor.env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    def attest(*_):
        if behavior == "policy_error":
            raise NativeError("unit_policy_not_confirmed")
        return False
    supervisor.attest = attest
    def transport(argv, env, deadline, check):
        gate = Path(argv[argv.index("--gate") + 1])
        try:
            check()
            return Captured(0, b"[]", b"", 1)
        finally:
            assert not gate.exists()
    monkeypatch.setattr("lab.native_process.capture", transport)
    with pytest.raises(NativeError, match="unit_policy_not_confirmed|native_start_not_approved"):
        supervisor.run(preset, unit_name(str(uuid4()), "measured", 0), time.monotonic()+10, lambda: None)
    assert not list(tmp_path.iterdir())


def test_systemd_specifier_paths_are_rejected():
    data = preset_data()
    data["model"]["path"] = "/models/%h/model.gguf"
    with pytest.raises(ValidationError):
        NativePreset.model_validate(data)


def test_no_native_remote_mutation_even_with_host_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ENABLE_NATIVE_RUNNER", "true")
    with TestClient(create_app({"token": TOKEN, "db": tmp_path / "web.sqlite3"})) as client:
        assert client.get("/api/native-benchmarks", headers=AUTH).json() == {
            "configured": False, "execution_available_via_api": False, "items": []}
        assert client.post("/api/native-benchmarks", headers=AUTH, json={"preset":"approved-test"}).status_code == 403
        for path in ("/api/native-benchmarks", f"/api/native-benchmarks/{uuid4()}", f"/api/native-benchmarks/{uuid4()}/export"):
            assert client.get(path).status_code == 401
