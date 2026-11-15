"""Durable fixture jobs. No native engine, network, plugin or shell execution."""
import hashlib
import json
import sqlite3
import statistics
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
MAX_PENDING = 20
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
FIXTURE_PROMPT = "Synthetic benchmark fixture version 1; no inference is performed."
PRESETS = (
    {"id": "fixture-v1", "label": "Successful fixture", "source": "fixture"},
    {"id": "fixture-failure-v1", "label": "Failure/recovery fixture", "source": "fixture"},
)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    preset: Literal["fixture-v1", "fixture-failure-v1"] = "fixture-v1"
    trials: int = Field(default=5, ge=1, le=20)
    warmup: int = Field(default=1, ge=0, le=5)
    timeout_seconds: float = Field(default=30.0, ge=0.1, le=60)


class QueueFull(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(db: Path):
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize(db: Path):
    """Additive, atomic migration; preserves Phase 1 history and refuses future DBs."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this application")
        if version == 0:
            c.execute("""CREATE TABLE benchmark_jobs (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                status TEXT NOT NULL CHECK(status IN
                  ('queued','running','succeeded','failed','cancelled','timed_out','interrupted')),
                source TEXT NOT NULL CHECK(source='fixture'),
                request_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, summary_json TEXT)""")
            c.execute("""CREATE TABLE benchmark_samples (
                job_id TEXT NOT NULL REFERENCES benchmark_jobs(id),
                phase TEXT NOT NULL CHECK(phase IN ('warmup','measured')),
                trial INTEGER NOT NULL, data_json TEXT NOT NULL,
                PRIMARY KEY(job_id,phase,trial))""")
            c.execute("""CREATE TABLE benchmark_job_events (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES benchmark_jobs(id),
                created_at TEXT NOT NULL, event TEXT NOT NULL)""")
            c.execute("CREATE INDEX benchmark_jobs_queue ON benchmark_jobs(status,created_at)")
            c.execute("CREATE INDEX benchmark_job_events_job ON benchmark_job_events(job_id,id)")
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def event(c, job_id, name):
    c.execute("INSERT INTO benchmark_job_events(job_id,created_at,event) VALUES(?,?,?)",
              (job_id, timestamp(), name))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def provenance(request: JobRequest):
    definition = {"protocol": "fixture-v1", "engine": "fixture", "model": "fixture-model-v1",
                  "prompt_tokens": 128, "output_tokens": 32, "seed": 0,
                  "prompt_sha256": hashlib.sha256(FIXTURE_PROMPT.encode()).hexdigest(),
                  "metrics": "synthetic formula v1; not observed performance"}
    return {"source": "fixture", "engine": "fixture", "model": "fixture-model-v1",
            "engine_revision": "fixture-v1", "model_license": "Apache-2.0 test fixture",
            "model_sha256": hashlib.sha256(b"fixture-model-v1:no-weights").hexdigest(),
            "definition": definition,
            "definition_sha256": hashlib.sha256(encode(definition).encode()).hexdigest(),
            "preset_sha256": hashlib.sha256(encode(request.model_dump()).encode()).hexdigest(),
            "hardware": None, "cuda": None, "driver": None,
            "notice": "SIMULATED: no engine, model weights, hardware measurement or inference"}


def public_job(row):
    job = dict(row)
    job.pop("request_key")
    for key in ("request", "provenance", "summary"):
        raw = job.pop(key + "_json")
        job[key] = json.loads(raw) if raw is not None else None
    job["cancel_requested"] = bool(job["cancel_requested"])
    return job


def enqueue(db, request: JobRequest, request_key: str):
    payload = encode(request.model_dump())
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT * FROM benchmark_jobs WHERE request_key=?", (request_key,)).fetchone()
        if previous:
            if previous["request_json"] != payload:
                raise IdempotencyConflict
            return public_job(previous), True
        pending = c.execute("SELECT COUNT(*) FROM benchmark_jobs WHERE status IN ('queued','running')").fetchone()[0]
        if pending >= MAX_PENDING:
            raise QueueFull
        job_id = str(uuid.uuid4())
        c.execute("""INSERT INTO benchmark_jobs
            (id,request_key,created_at,status,source,request_json,provenance_json)
            VALUES(?,?,?,'queued','fixture',?,?)""",
                  (job_id, request_key, timestamp(), payload, encode(provenance(request))))
        event(c, job_id, "queued")
        return public_job(c.execute("SELECT * FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone()), False


def list_jobs(db, limit=50):
    with connection(db) as c:
        return [public_job(r) for r in c.execute(
            "SELECT * FROM benchmark_jobs ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]


def get_job(db, job_id):
    with connection(db) as c:
        # Export metadata, samples and events from the same SQLite snapshot.
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        job = public_job(row)
        job["samples"] = [{"phase": r["phase"], "trial": r["trial"], **json.loads(r["data_json"])}
                          for r in c.execute("SELECT * FROM benchmark_samples WHERE job_id=? ORDER BY phase DESC,trial", (job_id,))]
        job["events"] = [dict(r) for r in c.execute(
            "SELECT created_at,event FROM benchmark_job_events WHERE job_id=? ORDER BY id", (job_id,))]
        return job


def cancel(db, job_id):
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "queued":
            c.execute("UPDATE benchmark_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?",
                      (timestamp(), job_id))
            event(c, job_id, "cancelled")
        elif row["status"] == "running" and not row["cancel_requested"]:
            c.execute("UPDATE benchmark_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            event(c, job_id, "cancel_requested")
        return public_job(c.execute("SELECT * FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone())


def recover_interrupted(db):
    """Only call after holding the exclusive OS worker lock."""
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT id FROM benchmark_jobs WHERE status='running'").fetchall()
        for row in rows:
            c.execute("UPDATE benchmark_jobs SET status='interrupted',finished_at=?,error_code='worker_restarted' WHERE id=?",
                      (timestamp(), row["id"]))
            event(c, row["id"], "interrupted")


def claim_next(db):
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM benchmark_jobs WHERE status='running'").fetchone():
            return None
        row = c.execute("SELECT * FROM benchmark_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if row is None:
            return None
        c.execute("UPDATE benchmark_jobs SET status='running',started_at=? WHERE id=?", (timestamp(), row["id"]))
        event(c, row["id"], "started")
        return {"id": row["id"], "request": json.loads(row["request_json"])}


def cancellation_requested(db, job_id):
    with connection(db) as c:
        return bool(c.execute("SELECT cancel_requested FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone()[0])


def add_sample(db, job_id, phase, trial, sample):
    with connection(db) as c:
        c.execute("INSERT INTO benchmark_samples VALUES(?,?,?,?)", (job_id, phase, trial, encode(sample)))
        event(c, job_id, phase + "_sample")


def summarize(samples):
    return {"source": "fixture", "sample_count": len(samples),
            "metrics": {key: {"median": statistics.median([s[key] for s in samples]),
                              "min": min(s[key] for s in samples), "max": max(s[key] for s in samples),
                              "stddev": statistics.pstdev(s[key] for s in samples)}
                        for key in ("ttft_ms", "prefill_tokens_per_second", "decode_tokens_per_second", "wall_ms")}}


def finish(db, job_id, status, summary=None, error_code=None):
    with connection(db) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT status,cancel_requested FROM benchmark_jobs WHERE id=?", (job_id,)).fetchone()
        if row["status"] != "running":
            return
        # Serialize completion against a concurrent cancellation request.
        if row["cancel_requested"]:
            status, summary, error_code = "cancelled", None, None
        c.execute("UPDATE benchmark_jobs SET status=?,finished_at=?,summary_json=?,error_code=? WHERE id=?",
                  (status, timestamp(), encode(summary) if summary is not None else None, error_code, job_id))
        event(c, job_id, status)
