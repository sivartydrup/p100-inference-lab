import json
import sqlite3
import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lab import native_store as store
from lab.main import create_app
from lab.measured_history import MeasuredRun, import_run, load_run, publish_snapshot
from lab.native_cli import main
from lab.native_config import NativeError
from lab.native_runner import recover
from lab.resident_bundle import prepare_bundle
from resident_fixture import measurement


def parsed(data=None):
    return MeasuredRun.model_validate_json(json.dumps(data or measurement()))


def test_import_recomputes_stats_and_excludes_warmup(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ENABLE_NATIVE_RUNNER", "false")
    db = tmp_path / "native.db"
    store.initialize(db)
    run = parsed()
    result = import_run(db, run)
    assert result["status"] == "succeeded" and result["source"] == "native"
    assert result["provenance"]["record_origin"] == "operator_import"
    assert result["summary"]["sample_count"] == 2
    assert result["summary"]["metrics"]["decode_tokens_per_second"]["median"] == 191.25
    assert result["summary"]["metrics"]["first_text_ms"]["median"] == 1100
    assert result["summary"]["ttft_ms"] is None
    detail = store.get_job(db, result["id"])
    assert len(detail["attempts"]) == 5  # 2 requests, excluded warmup, 2 telemetry samples
    assert store.claim(db) is None and store.active_jobs(db) == []
    assert import_run(db, run)["id"] == result["id"]
    assert len(store.list_jobs(db)) == 1
    changed = measurement(); changed["requests"][0]["first_text_ms"] = 1200
    with pytest.raises(NativeError, match="idempotency_conflict"):
        import_run(db, parsed(changed))
    class NoUnits:
        def available(self): pass
        def listed_units(self): return set()
        def stop(self, *_): pytest.fail("Imported history must not cause process operations")
    recover(db, NoUnits())


@pytest.mark.parametrize("change", [
    lambda d: d.update(command="reboot"),
    lambda d: d.update(observed_seconds=9.0),
    lambda d: d.update(finished_at="2026-01-01T00:00:05Z"),
    lambda d: d["requests"][0].update(first_text_ms=float("nan")),
    lambda d: d["requests"][1].update(completed_at="2026-01-01T00:00:01Z"),
    lambda d: d["workload"].update(cache_prompt=True),
    lambda d: d["telemetry"][0].update(enforced_power_limit_w=250.0),
    lambda d: d["telemetry"][0].update(temperature_c=75.0),
    lambda d: d["telemetry"][0].update(elapsed_measured_seconds=8.0),
    lambda d: d.update(gpu_name="/private/path"),
    lambda d: d.update(source_sha256={"../../secret": "a" * 64}),
    lambda d: d.update(outcome="temperature_cutoff"),
    lambda d: d.update(requests=[]),
    lambda d: d.update(warmup=None),
])
def test_invalid_or_misleading_measurements_rejected(change):
    data = measurement(); change(data)
    with pytest.raises((ValidationError, ValueError)):
        parsed(data)


def test_cutoff_is_failed_partial_history_not_a_success(tmp_path):
    data = measurement()
    data.update(outcome="temperature_cutoff", observed_seconds=9.5)
    data["telemetry"][-1]["temperature_c"] = 75.0
    run = parsed(data)
    db = tmp_path / "native.db"; store.initialize(db)
    imported = import_run(db, run)
    assert imported["status"] == "failed" and imported["error_code"] == "temperature_cutoff"
    assert imported["summary"]["partial"] and imported["summary"]["sample_count"] == 2


def test_comparison_key_groups_workload_but_not_different_prompts():
    first = parsed().provenance()["comparison_key"]
    data = measurement(); data["engine"] = "qr0n"; data["engine_revision"] = "f" * 40
    assert parsed(data).provenance()["comparison_key"] == first
    data["workload"]["prompt_token_ids_sha256"] = "a" * 64
    assert parsed(data).provenance()["comparison_key"] != first


def test_declared_and_unknown_caps_are_distinct():
    data = measurement(); data["power_limit_evidence"] = "operator_declared"
    for r in data["telemetry"]:
        r["requested_power_limit_w"] = r["enforced_power_limit_w"] = None
    assert parsed(data).provenance()["power_limit_evidence"] == "operator_declared"
    data.update(power_limit_evidence="unknown", declared_power_limit_w=None)
    assert parsed(data).provenance()["declared_power_limit_w"] is None


def test_cutoff_cannot_claim_it_continued_after_reaching_limit():
    data = measurement(); data.update(outcome="temperature_cutoff", observed_seconds=9.5)
    for reading in data["telemetry"]: reading["temperature_c"] = 75.0
    with pytest.raises(ValidationError): parsed(data)


def test_import_collision_preserves_existing_queue_record(tmp_path):
    db = tmp_path / "native.db"; store.initialize(db)
    with store.connect(db) as c:
        c.execute("INSERT INTO native_jobs(id,request_key,preset_id,preset_hash,provenance_json,status,created_at) VALUES(?,?,?,?,?,'queued',?)",
                  (measurement()["run_id"], "existing-managed-key", "existing-preset", "a" * 64, "{}", "2026-01-01T00:00:00Z"))
    before = db.read_bytes()
    with pytest.raises(sqlite3.IntegrityError): import_run(db, parsed())
    assert db.read_bytes() == before
    assert store.list_jobs(db)[0]["status"] == "queued"
    assert store.get_job(db, measurement()["run_id"])["attempts"] == []


def test_loader_duplicate_keys_nonfinite_and_oversize(tmp_path):
    path = tmp_path / "run.json"
    path.write_text('{"format":"x","format":"y"}')
    with pytest.raises(NativeError, match="duplicate_json_key"): load_run(path)
    path.write_text('{"x":NaN}')
    with pytest.raises(ValueError): load_run(path)
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    with pytest.raises(NativeError, match="invalid_measurement_file"): load_run(path)


def test_publish_is_read_only_consistent_and_preserves_source(tmp_path):
    source = tmp_path / "private" / "native.db"; store.initialize(source)
    imported = import_run(source, parsed())
    original = source.read_bytes()
    published = publish_snapshot(source, tmp_path / "published")
    assert source.read_bytes() == original
    assert store.get_job(published, imported["id"])["summary"] == imported["summary"]
    second = measurement(); second["run_id"] = "22222222-2222-4222-8222-222222222222"
    import_run(source, parsed(second))
    publish_snapshot(source, published.parent)
    assert len(store.list_jobs(published)) == 2
    assert not list(published.parent.glob(".native-*"))
    with pytest.raises(NativeError, match="unsafe_snapshot_destination"):
        publish_snapshot(published, published.parent)


def test_publish_refuses_to_overwrite_unrelated_data(tmp_path):
    source = tmp_path / "source.db"; store.initialize(source)
    directory = tmp_path / "published"; directory.mkdir()
    target = directory / "native.sqlite3"
    with sqlite3.connect(target) as c: c.execute("CREATE TABLE unrelated(value)")
    before = target.read_bytes()
    with pytest.raises(NativeError, match="native_database_identity"):
        publish_snapshot(source, directory)
    assert target.read_bytes() == before


def test_import_cli_and_authenticated_api_stay_execution_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ENABLE_NATIVE_RUNNER", "false")
    db = tmp_path / "native.db"; store.initialize(db)
    payload = tmp_path / "measurement.json"; payload.write_text(json.dumps(measurement()))
    main(["--db", str(db), "import", "--file", str(payload)])
    main(["--db", str(db), "publish", "--directory", str(tmp_path / "public")])
    token = "viewer-only-" * 4; auth = {"Authorization": "Bearer " + token}
    snapshot = tmp_path / "public" / "native.sqlite3"; before = snapshot.read_bytes()
    with TestClient(create_app({"token": token, "db": tmp_path / "web.db", "native_db": snapshot})) as client:
        assert client.get("/api/native-benchmarks").status_code == 401
        result = client.get("/api/native-benchmarks", headers=auth).json()
        job_id = result["items"][0]["id"]
        exported = client.get(f"/api/native-benchmarks/{job_id}/export", headers=auth).json()
        assert exported["provenance"]["record_origin"] == "operator_import"
        assert exported["summary"]["telemetry"]["peak_temperature_c"] == 51
        assert client.post("/api/native-benchmarks", headers=auth, json=measurement()).status_code == 403
        assert client.get("/api/benchmarks", headers=auth).json() == {"items": []}
    assert snapshot.read_bytes() == before


def write_bundle(directory):
    data = measurement()
    data.update(target_seconds=600.0, observed_seconds=600.0, finished_at="2026-01-01T00:10:00.2Z")
    data["telemetry"][-1].update(timestamp="2026-01-01T00:09:59.5Z", elapsed_measured_seconds=599.5)
    run = parsed(data)
    def write(name, value):
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    write("metadata.json", {"argv": ["/synthetic/private/llama-server", "-m", "/synthetic/private/model.gguf",
        "-ngl", "99", "-c", "2048", "-np", "1", "-t", "4", "-tb", "4", "-b", "512", "-ub", "128",
        "-fa", "off", "--fit", "off", "--host", "/synthetic/private/engine.sock", "--api-key-file", "/synthetic/private/key",
        "--no-webui", "--no-slots", "--timeout", "15"], "target_measured_seconds": 600, "cutoff_c": 75,
        "engine_revision": data["engine_revision"], "binary_sha256": data["binary_sha256"],
        "model_artifacts": [{"filename": "fake.gguf", **data["model_artifacts"][0]}]})
    write("measurement-start.json", {"timestamp_utc": data["started_at"], "duration_seconds": 600})
    write("request.json", {"prompt": [1] * 512, "n_predict": 256, "stream": True, "temperature": 0,
                           "seed": 42, "ignore_eos": True, "cache_prompt": False, "timings_per_token": False})
    def records(sample):
        value = sample.metrics(run.workload)
        row = {"completed_at_utc": sample.completed_at, "ttft_ms": sample.first_text_ms,
               "request_wall_ms": sample.request_wall_ms, "prefill_tokens_per_second": value["prefill_tokens_per_second"],
               "decode_tokens_per_second": value["decode_tokens_per_second"], "prompt_tokens": 512,
               "generated_tokens": 256, "cached_tokens": 0, "output_sha256": sample.output_sha256}
        evidence = {"stop_type": "limit", "timings": {"prompt_n": 512, "predicted_n": 256, "cache_n": 0,
                    "prompt_ms": sample.prompt_ms, "predicted_ms": sample.decode_ms,
                    "prompt_per_second": value["prefill_tokens_per_second"], "predicted_per_second": value["decode_tokens_per_second"]}}
        return row, evidence
    warmup, evidence = records(run.warmup)
    write("warmup.json", {"record": warmup, "evidence": evidence})
    rows, raw = [], []
    for number, sample in enumerate(run.requests, 1):
        row, evidence = records(sample)
        rows.append({"request": number, **row}); raw.append({"request": number, **evidence})
    with (directory / "requests.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    (directory / "timings.jsonl").write_text("\n".join(json.dumps(r) for r in raw))
    rows = [{"timestamp_utc": r.timestamp, "phase": r.phase, "elapsed_total_seconds": r.elapsed_measured_seconds,
             "elapsed_measured_seconds": r.elapsed_measured_seconds, "gpu_uuid": run.gpu_uuid, "gpu_name": run.gpu_name,
             "driver": run.driver_version, **r.model_dump(exclude={"timestamp", "phase", "elapsed_measured_seconds"})} for r in run.telemetry]
    with (directory / "telemetry.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    computed = run.summary(); metrics = computed["metrics"]; metrics["ttft_ms"] = metrics.pop("first_text_ms")
    write("summary.json", {"reason": "completed_600_seconds", "finished_at_utc": run.finished_at,
          "completed_requests": 2, "measured_telemetry_samples": 2, "metrics": metrics,
          "peak_temperature_c_all_phases": 51, "peak_power_w_measured": 151, "mean_power_w_measured": 150,
          "peak_vram_mib_sampled": 4000})


def test_prepare_bundle_crosschecks_raw_samples_and_redacts_paths(tmp_path):
    write_bundle(tmp_path)
    run = prepare_bundle(tmp_path, "kmic", "synthetic-model", "Synthetic fixture only")
    assert run.summary()["metrics"]["decode_tokens_per_second"]["median"] == 191.25
    assert run.power_limit_evidence == "sampled"
    assert "/synthetic/private" not in run.model_dump_json()
    assert run.run_id == prepare_bundle(tmp_path, "kmic", "synthetic-model", "Synthetic fixture only").run_id
    p = tmp_path / "summary.json"; value = json.loads(p.read_text()); value["completed_requests"] = 99; p.write_text(json.dumps(value))
    with pytest.raises(NativeError, match="summary_or_sample_mismatch"):
        prepare_bundle(tmp_path, "kmic", "synthetic-model", "Synthetic fixture only")


@pytest.mark.parametrize("name,change", [
    ("metadata.json", lambda d: d["argv"].extend(["--extra-command", "reboot"])),
    ("request.json", lambda d: d.update(cache_prompt=True)),
    ("request.json", lambda d: d.update(prompt=[True] * 512)),
    ("summary.json", lambda d: d.update(reason="some_unknown_failure")),
    ("summary.json", lambda d: d["metrics"]["decode_tokens_per_second"].update(median=999)),
])
def test_prepare_bundle_rejects_changed_protocol_or_claims(tmp_path, name, change):
    write_bundle(tmp_path)
    path = tmp_path / name; data = json.loads(path.read_text()); change(data); path.write_text(json.dumps(data))
    with pytest.raises((NativeError, ValidationError)):
        prepare_bundle(tmp_path, "kmic", "synthetic-model", "Synthetic fixture only")


def test_prepare_bundle_rejects_ambiguous_csv(tmp_path):
    write_bundle(tmp_path)
    p = tmp_path / "requests.csv"; p.write_text("request,request\n1,2\n")
    with pytest.raises(NativeError, match="invalid_resident_csv"):
        prepare_bundle(tmp_path, "kmic", "synthetic-model", "Synthetic fixture only")
