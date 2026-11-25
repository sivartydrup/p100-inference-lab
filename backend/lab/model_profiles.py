"""SQLite-backed profiles for administrator-approved models."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .profile_settings import LIMITS, validate_settings

MAX_PROFILES = 128


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Settings(StrictInput):
    context: int = Field(ge=LIMITS["context"][0], le=LIMITS["context"][1])
    batch: int = Field(ge=LIMITS["batch"][0], le=LIMITS["batch"][1])
    ubatch: int = Field(ge=LIMITS["ubatch"][0], le=LIMITS["ubatch"][1])

    @model_validator(mode="after")
    def ordered(self):
        validate_settings(self.model_dump())
        return self


class Selection(StrictInput):
    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    settings: Settings
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError("Confirmation required")
        return value


class BenchmarkEvidence(StrictInput):
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric: Literal["decode_tokens_per_second", "prefill_tokens_per_second"]
    value: float = Field(gt=0, le=1000000)


class Preset(StrictInput):
    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    settings: Settings
    name: str = Field(min_length=1, max_length=80)
    workload: str = Field(default="", max_length=240)
    evidence: BenchmarkEvidence | None = None
    make_default: bool = False

    @field_validator("name", "workload")
    @classmethod
    def clean(cls, value):
        if value and (not value.isprintable() or value != value.strip()):
            raise ValueError("Use printable text without surrounding spaces")
        return value

    @model_validator(mode="after")
    def documented(self):
        if self.evidence is not None and not self.workload:
            raise ValueError("Benchmark evidence requires a workload description")
        return self


def initialize(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS model_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL,
            data TEXT NOT NULL, is_default INTEGER NOT NULL,
            UNIQUE(model_id, name));
        CREATE UNIQUE INDEX IF NOT EXISTS model_profiles_default
            ON model_profiles(model_id) WHERE is_default = 1;
    """)


def public(row):
    data = json.loads(row["data"])
    return {**data, "id": row["id"], "model_id": row["model_id"], "name": row["name"],
            "created_at": row["created_at"], "is_default": bool(row["is_default"])}


def list_presets(db):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [public(row) for row in conn.execute("SELECT * FROM model_profiles ORDER BY is_default DESC, name, id")]


def save(db, request, model):
    settings = request.settings.model_dump()
    limits = model["limits"]
    try:
        validate_settings(settings, limits["context"], limits["batch"], limits["ubatch"])
        if model.get("recipe") and settings != model["initial"]:
            raise ValueError("This profile requires its exact launch parameters")
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    data = {
        "settings": settings, "workload": request.workload,
        "evidence": request.evidence.model_dump() if request.evidence else None,
        "environment": {key: model[key] for key in ("engine_version", "gpu", "gpu_count", "quantization", "bytes", "sha256")},
        "engine": "kmic",
    }
    if model.get("recipe"):
        data["environment"]["recipe_id"] = model["recipe"]["id"]
        data["environment"]["recipe_sha256"] = model["recipe"]["sha256"]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT count(*) FROM model_profiles").fetchone()[0] >= MAX_PROFILES:
            raise HTTPException(409, "Preset library is full")
        if request.make_default:
            conn.execute("UPDATE model_profiles SET is_default = 0 WHERE model_id = ? AND is_default = 1",
                         (request.model_id,))
        try:
            cursor = conn.execute(
                "INSERT INTO model_profiles(model_id,name,created_at,data,is_default) VALUES(?,?,?,?,?)",
                (request.model_id, request.name, datetime.now(timezone.utc).isoformat(),
                 json.dumps(data), request.make_default),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A preset with this name already exists for this model") from None
        return public(conn.execute("SELECT * FROM model_profiles WHERE id = ?", (cursor.lastrowid,)).fetchone())


def delete(db, preset_id):
    with closing(sqlite3.connect(db)) as conn, conn:
        if not conn.execute("DELETE FROM model_profiles WHERE id = ?", (preset_id,)).rowcount:
            raise HTTPException(404, "Preset not found")


def set_default(db, preset_id):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT model_id FROM model_profiles WHERE id = ?", (preset_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Preset not found")
        conn.execute("UPDATE model_profiles SET is_default = 0 WHERE model_id = ? AND is_default = 1", row)
        conn.execute("UPDATE model_profiles SET is_default = 1 WHERE id = ?", (preset_id,))
