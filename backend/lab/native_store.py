"""Separate native-run database; the API opens it read-only, never initializes it."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .jobs import encode, timestamp
from .native_config import NativeError

APPLICATION_ID = 0x5031304E
ACTIVE = ("running", "cleanup_failed")


@contextmanager
def connect(path, readonly=False, initialize=False):
    path = Path(path).resolve()
    uri = path.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    c = sqlite3.connect(str(path) if initialize else uri, uri=not initialize, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        with c:
            if not initialize and (c.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                                   or c.execute("PRAGMA user_version").fetchone()[0] != 1):
                raise NativeError("native_database_identity")
            yield c
    finally:
        c.close()


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect(path, initialize=True) as c:
        c.execute("BEGIN IMMEDIATE")
        app_id = c.execute("PRAGMA application_id").fetchone()[0]
        version = c.execute("PRAGMA user_version").fetchone()[0]
        tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if app_id == APPLICATION_ID and version == 1:
            return
        if app_id or version or tables:
            raise NativeError("refuse_existing_database")
        c.execute("""CREATE TABLE native_jobs (
            id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, preset_id TEXT NOT NULL,
            preset_hash TEXT NOT NULL, provenance_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','cleanup_failed','succeeded',
              'failed','cancelled','timed_out','interrupted')),
            created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT, summary_json TEXT)""")
        c.execute("""CREATE TABLE native_attempts (
            job_id TEXT NOT NULL REFERENCES native_jobs(id), phase TEXT NOT NULL,
            trial INTEGER NOT NULL, unit TEXT NOT NULL UNIQUE, sample_json TEXT,
            PRIMARY KEY(job_id,phase,trial))""")
        c.execute("""CREATE TABLE native_events (
            id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES native_jobs(id),
            created_at TEXT NOT NULL,event TEXT NOT NULL)""")
        c.execute(f"PRAGMA application_id={APPLICATION_ID}")
        c.execute("PRAGMA user_version=1")
    os.chmod(path, 0o600)


def event(c, job_id, name):
    c.execute("INSERT INTO native_events(job_id,created_at,event) VALUES(?,?,?)", (job_id, timestamp(), name))


def public(row):
    result = dict(row)
    result.pop("request_key", None)
    result["source"] = "native"
    for key in ("provenance", "summary"):
        data = result.pop(key + "_json")
        result[key] = json.loads(data) if data else None
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


def enqueue(path, preset, request_key):
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM native_jobs WHERE request_key=?", (request_key,)).fetchone()
        if prior:
            if prior["preset_hash"] != preset.fingerprint():
                raise NativeError("idempotency_conflict")
            return public(prior)
        if c.execute("SELECT COUNT(*) FROM native_jobs WHERE status IN ('queued','running','cleanup_failed')").fetchone()[0] >= 10:
            raise NativeError("native_queue_full")
        job_id = str(uuid4())
        c.execute("INSERT INTO native_jobs(id,request_key,preset_id,preset_hash,provenance_json,status,created_at) VALUES(?,?,?,?,?,'queued',?)",
                  (job_id, request_key, preset.id, preset.fingerprint(), encode(preset.public()), timestamp()))
        event(c, job_id, "queued_by_local_administrator")
        return public(c.execute("SELECT * FROM native_jobs WHERE id=?", (job_id,)).fetchone())


def list_jobs(path, limit=50):
    with connect(path, readonly=True) as c:
        return [public(r) for r in c.execute("SELECT * FROM native_jobs ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]


def get_job(path, job_id):
    with connect(path, readonly=True) as c:
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM native_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = public(row)
        result["attempts"] = [{"phase": r["phase"], "trial": r["trial"], "unit": r["unit"],
                               "sample": json.loads(r["sample_json"]) if r["sample_json"] else None}
                              for r in c.execute("SELECT * FROM native_attempts WHERE job_id=? ORDER BY phase DESC,trial", (job_id,))]
        result["events"] = [dict(r) for r in c.execute("SELECT created_at,event FROM native_events WHERE job_id=? ORDER BY id", (job_id,))]
        return result


def cancel(path, job_id):
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT status,cancel_requested FROM native_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NativeError("unknown_job")
        if row["status"] == "queued":
            c.execute("UPDATE native_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=?", (timestamp(), job_id))
            event(c, job_id, "cancelled")
        elif row["status"] in ACTIVE and not row["cancel_requested"]:
            c.execute("UPDATE native_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            event(c, job_id, "cancel_requested_by_local_administrator")


def claim(path):
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM native_jobs WHERE status IN ('running','cleanup_failed')").fetchone():
            raise NativeError("cleanup_required")
        row = c.execute("SELECT * FROM native_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if row is None:
            return None
        c.execute("UPDATE native_jobs SET status='running',started_at=? WHERE id=?", (timestamp(), row["id"]))
        event(c, row["id"], "started")
        return public(row)


def plan(path, job_id, phase, trial, unit):
    with connect(path) as c:
        c.execute("INSERT INTO native_attempts VALUES(?,?,?,?,NULL)", (job_id, phase, trial, unit))
        event(c, job_id, "unit_intent_persisted")


def sample(path, job_id, phase, trial, value):
    with connect(path) as c:
        c.execute("UPDATE native_attempts SET sample_json=? WHERE job_id=? AND phase=? AND trial=?",
                  (encode(value), job_id, phase, trial))
        event(c, job_id, phase + "_sample")


def finish(path, job_id, status, error=None, summary=None):
    with connect(path) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT status,cancel_requested FROM native_jobs WHERE id=?", (job_id,)).fetchone()
        if row["status"] not in ACTIVE:
            return
        if row["cancel_requested"] and status != "cleanup_failed":
            status, error, summary = "cancelled", None, None
        c.execute("UPDATE native_jobs SET status=?,error_code=?,summary_json=?,finished_at=? WHERE id=?",
                  (status, error, encode(summary) if summary else None, None if status == "cleanup_failed" else timestamp(), job_id))
        event(c, job_id, status)


def active_jobs(path):
    with connect(path, readonly=True) as c:
        return [public(r) for r in c.execute("SELECT * FROM native_jobs WHERE status IN ('running','cleanup_failed')")]


def cancellation_requested(path, job_id):
    with connect(path, readonly=True) as c:
        return bool(c.execute("SELECT cancel_requested FROM native_jobs WHERE id=?", (job_id,)).fetchone()[0])
