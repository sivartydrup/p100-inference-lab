import hmac
import json
import os
import sqlite3
import platform
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .adapters import ENGINE_IDS, NativeAdapter, inventory
from .telemetry import sample


class Benchmark(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    engine: Literal["kmic", "pxa", "qr0n"]
    model: str = Field(min_length=1, max_length=200)
    tokens_per_second: float = Field(gt=0, le=1000000)
    notes: str = Field(default="", max_length=2000)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop"]


def create_app(config=None):
    config = config or {}
    token_file = os.getenv("LAB_API_TOKEN_FILE")
    token = config.get("token", Path(token_file).read_text().strip() if token_file else os.getenv("LAB_API_TOKEN", ""))
    controls = False  # Phase 1 cannot enable host management.
    mode = config.get("telemetry", os.getenv("LAB_TELEMETRY", "mock" if platform.system() == "Windows" else "live"))
    if mode not in {"mock", "live"}:
        raise ValueError("LAB_TELEMETRY must be mock or live")
    db = Path(config.get("db", os.getenv("LAB_DB", "data/lab.sqlite3")))
    roots = config.get("roots", json.loads(os.getenv("LAB_MODEL_ROOTS", "[]")))
    if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
        raise ValueError("LAB_MODEL_ROOTS must be a JSON array of absolute paths")

    @asynccontextmanager
    async def lifespan(app):
        if len(token) < 32:
            raise RuntimeError("Set LAB_API_TOKEN to a random secret of at least 32 characters")
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                    engine TEXT NOT NULL, model TEXT NOT NULL,
                    tokens_per_second REAL NOT NULL, notes TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual');
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                    engine TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL);
            """)
        yield

    app = FastAPI(title="P100 Inference Lab", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authenticate(authorization: str | None = Header(default=None)):
        expected = f"Bearer {token}"
        if len(token) < 32 or not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "Valid bearer token required", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(authenticate)]

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/system", dependencies=auth)
    def system():
        return {**sample(mode, db.parent), "controls_enabled": controls}

    @app.get("/api/engines", dependencies=auth)
    def engines():
        return {"items": [NativeAdapter(e).status() for e in ENGINE_IDS], "controls_enabled": controls}

    @app.post("/api/engines/{engine}/control", dependencies=auth)
    def control(engine: Literal["kmic", "pxa", "qr0n"], request: Control):
        raise HTTPException(403, "Engine controls are not implemented in Phase 1")

    @app.get("/api/models", dependencies=auth)
    def models():
        return inventory(roots)

    @app.get("/api/benchmarks", dependencies=auth)
    def benchmarks(limit: int = Query(default=50, ge=1, le=200)):
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return {"items": [dict(r) for r in conn.execute("SELECT * FROM benchmarks ORDER BY id DESC LIMIT ?", (limit,))]}

    @app.post("/api/benchmarks", dependencies=auth, status_code=201)
    def record_benchmark(request: Benchmark):
        with sqlite3.connect(db) as conn:
            cursor = conn.execute("INSERT INTO benchmarks(created_at,engine,model,tokens_per_second,notes,source) VALUES(?,?,?,?,?,?)",
                                  (datetime.now(timezone.utc).isoformat(), request.engine, request.model,
                                   request.tokens_per_second, request.notes, "manual"))
            return {"id": cursor.lastrowid, "source": "manual"}

    static = Path(config.get("static", os.getenv("LAB_STATIC_DIR", "../frontend/dist")))
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
    return app


app = create_app()
