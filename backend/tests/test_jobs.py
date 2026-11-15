import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from lab import jobs
from lab.main import create_app
from lab.worker import WorkerBusy, execute, fixture_sample, worker_lock

VIEWER = "viewer-" * 8
RUNNER = "runner-" * 8
AUTH = {"Authorization": "Bearer " + VIEWER}
CONTROL = {**AUTH, "X-Lab-Job-Token": RUNNER}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    jobs.initialize(path)
    return path


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app({"token": VIEWER, "fixture_jobs": True,
                               "job_token": RUNNER, "db": tmp_path / "lab.sqlite3"})) as c:
        yield c


def submit(client, payload=None, key=None, headers=None):
    return client.post("/api/benchmark-jobs", json=payload or {},
                       headers={**(CONTROL if headers is None else headers),
                                "Idempotency-Key": str(key or uuid4())})


def add(db, **settings):
    return jobs.enqueue(db, jobs.JobRequest(**settings), str(uuid4()))[0]


@pytest.mark.parametrize("method,path", [
    ("get", "/api/benchmark-presets"), ("get", "/api/benchmark-jobs"),
    ("post", "/api/benchmark-jobs"), ("get", "/api/benchmark-jobs/" + str(uuid4())),
    ("post", "/api/benchmark-jobs/" + str(uuid4()) + "/cancel"),
    ("get", "/api/benchmark-jobs/" + str(uuid4()) + "/export"),
])
def test_all_job_routes_require_viewer_auth(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_viewer_cannot_submit_or_cancel(client):
    assert submit(client, headers=AUTH).status_code == 403
    assert submit(client, headers={"X-Lab-Job-Token": RUNNER}).status_code == 401
    job = submit(client).json()["job"]
    assert client.post(f'/api/benchmark-jobs/{job["id"]}/cancel', headers=AUTH).status_code == 403


def test_disabled_by_default_and_strong_separate_secret(tmp_path):
    for secret in ("short", VIEWER):
        with pytest.raises(RuntimeError, match="distinct"):
            with TestClient(create_app({"token": VIEWER, "fixture_jobs": True,
                                       "job_token": secret, "db": tmp_path / "bad.db"})):
                pass
    with TestClient(create_app({"token": VIEWER, "db": tmp_path / "off.db"})) as c:
        assert not c.get("/api/benchmark-presets", headers=AUTH).json()["submission_enabled"]
        assert submit(c).status_code == 403


def test_job_token_file_and_wrong_credentials(monkeypatch, tmp_path):
    secret = tmp_path / "job-secret"
    secret.write_text(RUNNER + "\n")
    monkeypatch.setenv("LAB_JOB_TOKEN_FILE", str(secret))
    with TestClient(create_app({"token": VIEWER, "fixture_jobs": True,
                               "db": tmp_path / "file.sqlite3"})) as c:
        assert submit(c, headers={**AUTH, "X-Lab-Job-Token": "wrong"}).status_code == 403
        assert submit(c).status_code == 201


@pytest.mark.parametrize("payload", [
    {"preset": "kmic"}, {"command": "id"}, {"model": "/etc/passwd"},
    {"trials": 21}, {"trials": True}, {"trials": "2"},
    {"warmup": -1}, {"timeout_seconds": 0}, {"timeout_seconds": 61},
])
def test_untrusted_presets_paths_commands_and_unbounded_work_rejected(client, payload):
    assert submit(client, payload).status_code == 422


def test_idempotency_is_atomic_and_detects_conflicts(client):
    key = uuid4()
    first = submit(client, {"trials": 3}, key)
    assert first.status_code == 201
    second = submit(client, {"trials": 3}, key)
    assert second.status_code == 200 and second.json()["replayed"]
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert submit(client, {"trials": 4}, key).status_code == 409
    assert len(client.get("/api/benchmark-jobs", headers=AUTH).json()["items"]) == 1
    assert client.post("/api/benchmark-jobs", json={}, headers=CONTROL).status_code == 422


def test_concurrent_retries_create_one_job(db):
    key = str(uuid4())
    with ThreadPoolExecutor(4) as pool:
        replies = list(pool.map(lambda _: jobs.enqueue(db, jobs.JobRequest(), key), range(4)))
    assert len({r[0]["id"] for r in replies}) == 1
    assert sum(not r[1] for r in replies) == 1


def test_queue_capacity_and_idempotent_replay(db):
    key = str(uuid4())
    jobs.enqueue(db, jobs.JobRequest(), key)
    for _ in range(jobs.MAX_PENDING - 1):
        add(db)
    with pytest.raises(jobs.QueueFull):
        add(db)
    assert jobs.enqueue(db, jobs.JobRequest(), key)[1]


def test_one_active_job_even_with_concurrent_claims(db):
    add(db)
    add(db)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: jobs.claim_next(db), range(2)))
    assert sum(j is not None for j in claimed) == 1


def test_fixture_summary_excludes_warmup_and_records_provenance(db):
    job = add(db, trials=3, warmup=2)
    with patch("subprocess.run", side_effect=AssertionError("Fixture must not execute programs")):
        execute(db, jobs.claim_next(db), threading.Event(), pause=0)
    result = jobs.get_job(db, job["id"])
    assert result["status"] == "succeeded"
    assert len(result["samples"]) == 5
    assert result["summary"]["sample_count"] == 3
    assert result["summary"]["metrics"]["ttft_ms"]["median"] == 47
    assert result["summary"]["metrics"]["ttft_ms"]["stddev"] == pytest.approx((8 / 3) ** 0.5)
    assert result["provenance"]["hardware"] is None
    assert len(result["provenance"]["preset_sha256"]) == 64
    assert result["source"] == "fixture"
    assert all(s["source"] == "fixture" for s in result["samples"])
    assert result["events"][0]["event"] == "queued"
    assert result["events"][-1]["event"] == "succeeded"


def test_results_are_deterministic(db):
    results = []
    for _ in range(2):
        job = add(db)
        execute(db, jobs.claim_next(db), threading.Event(), pause=0)
        result = jobs.get_job(db, job["id"])
        results.append((result["samples"], result["summary"], result["provenance"]))
    assert results[0] == results[1]


def test_queued_and_running_cancellation_and_completion_race(db):
    queued = add(db)
    assert jobs.cancel(db, queued["id"])["status"] == "cancelled"
    assert jobs.claim_next(db) is None
    running = add(db)
    claimed = jobs.claim_next(db)
    jobs.cancel(db, running["id"])
    jobs.cancel(db, running["id"])
    execute(db, claimed, threading.Event(), pause=0)
    result = jobs.get_job(db, running["id"])
    assert result["status"] == "cancelled" and not result["samples"]
    assert [e["event"] for e in result["events"]].count("cancel_requested") == 1
    raced = add(db)
    jobs.claim_next(db)
    jobs.cancel(db, raced["id"])
    jobs.finish(db, raced["id"], "succeeded", {"must_not_commit": True})
    assert jobs.get_job(db, raced["id"])["summary"] is None


def test_failure_timeout_and_shutdown_are_terminal(db):
    fail = add(db, preset="fixture-failure-v1")
    execute(db, jobs.claim_next(db), threading.Event(), pause=0)
    assert jobs.get_job(db, fail["id"])["error_code"] == "fixture_failure"
    timeout = add(db, timeout_seconds=0.1)
    with patch("lab.worker.time.monotonic", side_effect=[0.0, 0.2]):
        execute(db, jobs.claim_next(db), threading.Event(), pause=0)
    assert jobs.get_job(db, timeout["id"])["status"] == "timed_out"
    interrupted = add(db)
    stop = threading.Event()
    stop.set()
    execute(db, jobs.claim_next(db), stop, pause=0)
    assert jobs.get_job(db, interrupted["id"])["status"] == "interrupted"
    healthy = add(db)
    execute(db, jobs.claim_next(db), threading.Event(), pause=0)
    assert jobs.get_job(db, healthy["id"])["status"] == "succeeded"


@pytest.mark.parametrize("reverse_ids", [False, True])
def test_crash_recovery_preserves_partial_samples_and_queue(db, reverse_ids):
    ids = [UUID("11111111-1111-4111-8111-111111111111"),
           UUID("22222222-2222-4222-8222-222222222222")]
    if reverse_ids:
        ids.reverse()
    # Force a timestamp tie and both UUID orderings; never assume insertion order.
    with patch("lab.jobs.timestamp", return_value="2026-11-16T00:00:00+00:00"), \
            patch("lab.jobs.uuid.uuid4", side_effect=ids):
        first, second = add(db), add(db)
    running = jobs.claim_next(db)
    queued = second if running["id"] == first["id"] else first
    jobs.add_sample(db, running["id"], "measured", 0, fixture_sample(0))
    # Represents a dead worker; recovery is only entered under the OS lock.
    with worker_lock(db):
        jobs.recover_interrupted(db)
    result = jobs.get_job(db, running["id"])
    assert result["status"] == "interrupted" and len(result["samples"]) == 1
    assert jobs.claim_next(db)["id"] == queued["id"]


def test_worker_lock_excludes_different_suffix_alias_to_same_database(db):
    alias = db.with_name("alias.db")
    try:
        alias.symlink_to(db)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    job = add(db)
    with worker_lock(db):
        jobs.claim_next(db)
        with pytest.raises(WorkerBusy):
            with worker_lock(alias):
                jobs.recover_interrupted(alias)
        assert jobs.get_job(db, job["id"])["status"] == "running"
    # Reverse the alias/canonical order as well.
    with worker_lock(alias):
        with pytest.raises(WorkerBusy):
            with worker_lock(db):
                pass
        jobs.recover_interrupted(alias)
    assert jobs.get_job(db, job["id"])["status"] == "interrupted"


def test_os_lock_prevents_second_worker_process(db):
    script = "from pathlib import Path; import sys; from lab.worker import worker_lock;\nwith worker_lock(Path(sys.argv[1])): print('locked')"
    with worker_lock(db):
        result = subprocess.run([sys.executable, "-c", script, str(db)], capture_output=True, text=True)
        assert result.returncode != 0 and "Another fixture worker" in result.stderr
    result = subprocess.run([sys.executable, "-c", script, str(db)], capture_output=True, text=True)
    assert result.returncode == 0


def test_killed_worker_releases_lock_and_recovery_marks_job_interrupted(db):
    queued = add(db)
    script = (
        "from pathlib import Path; import sys,time; from lab import jobs; from lab.worker import worker_lock;\n"
        "with worker_lock(Path(sys.argv[1])):\n"
        " jobs.claim_next(Path(sys.argv[1])); print('claimed',flush=True); time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script, str(db)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "claimed"
        with pytest.raises(WorkerBusy):
            with worker_lock(db):
                pass
        process.kill()
        process.wait(timeout=5)
        with worker_lock(db):
            jobs.recover_interrupted(db)
        assert jobs.get_job(db, queued["id"])["status"] == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def test_queue_api_returns_capacity_status_and_retry_header(client):
    for _ in range(jobs.MAX_PENDING):
        assert submit(client).status_code == 201
    full = submit(client)
    assert full.status_code == 429 and full.headers["Retry-After"] == "5"


def test_worker_cli_is_opt_in(monkeypatch):
    from lab.worker import main
    monkeypatch.delenv("LAB_ENABLE_FIXTURE_JOBS", raising=False)
    with pytest.raises(SystemExit, match="disabled"):
        main()


def test_additive_migration_preserves_phase1_and_refuses_future_schema(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE benchmarks(id INTEGER PRIMARY KEY,notes TEXT)")
        c.execute("INSERT INTO benchmarks(notes) VALUES('preserve this')")
    jobs.initialize(db)
    jobs.initialize(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT notes FROM benchmarks").fetchone()[0] == "preserve this"
        c.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="newer"):
        jobs.initialize(db)


def test_export_is_authenticated_and_fixture_results_never_enter_manual_history(client):
    job = submit(client).json()["job"]
    response = client.get(f'/api/benchmark-jobs/{job["id"]}/export', headers=AUTH)
    assert response.status_code == 200 and "attachment" in response.headers["Content-Disposition"]
    assert response.json()["source"] == "fixture"
    assert VIEWER not in response.text and RUNNER not in response.text
    assert client.get("/api/benchmarks", headers=AUTH).json()["items"] == []
    assert client.get("/api/benchmark-jobs?limit=101", headers=AUTH).status_code == 422
    assert client.get(f'/api/benchmark-jobs/{uuid4()}', headers=AUTH).status_code == 404
    assert client.get("/api/benchmark-jobs/not-a-uuid", headers=AUTH).status_code == 422
