import hmac
import asyncio
import json
import os
import sqlite3
import platform
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field

from .adapters import ENGINE_IDS, MANAGED_UNITS, NativeAdapter, inventory
from .telemetry import sample
from .telemetry_stream import TelemetryHub, TelemetryUnavailable
from . import jobs
from . import native_store
from .native_config import NativeError
from .engine_bridge import exchange as engine_exchange, EngineUnavailable
from . import model_profiles
from . import fan_control


EngineId = Literal["kmic", "kmic-moe", "pxa", "qr0n", "strata", "scream", "scream-512k", "inkling"]


class Benchmark(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    engine: EngineId
    model: str = Field(min_length=1, max_length=200)
    tokens_per_second: float = Field(gt=0, le=1000000)
    notes: str = Field(default="", max_length=2000)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop", "enable_boot", "disable_boot"]


def create_app(config=None):
    config = config or {}
    token_file = os.getenv("LAB_API_TOKEN_FILE")
    token = config.get("token", Path(token_file).read_text().strip() if token_file else os.getenv("LAB_API_TOKEN", ""))
    agent_socket = config.get('engine_socket', os.getenv('LAB_ENGINE_AGENT_SOCKET'))
    ipc_file = os.getenv('LAB_ENGINE_AGENT_TOKEN_FILE')
    ipc_token = config.get('engine_ipc_token', Path(ipc_file).read_text().strip() if ipc_file else '')
    control_file = os.getenv('LAB_ENGINE_CONTROL_TOKEN_FILE')
    engine_token = config.get('engine_control_token', Path(control_file).read_text().strip() if control_file else '')
    controls = config.get('engine_controls', os.getenv('LAB_ENABLE_ENGINE_CONTROL', 'false').lower() == 'true')
    agent_configured = bool(agent_socket and len(ipc_token) >= 32)
    fan_socket = config.get('fan_socket', os.getenv('LAB_FAN_AGENT_SOCKET'))
    fan_file = os.getenv('LAB_FAN_AGENT_TOKEN_FILE')
    fan_token = config.get('fan_ipc_token', Path(fan_file).read_text().strip() if fan_file else '')
    fan_controls = config.get('fan_controls', os.getenv('LAB_ENABLE_FAN_CONTROL', 'false').lower() == 'true')
    fixtures = config.get("fixture_jobs", os.getenv("LAB_ENABLE_FIXTURE_JOBS", "false").lower() == "true")
    job_token_file = os.getenv("LAB_JOB_TOKEN_FILE")
    job_token = config.get("job_token", (
        Path(job_token_file).read_text().strip() if job_token_file else os.getenv("LAB_JOB_TOKEN", "")
    )) if fixtures else ""
    mode = config.get("telemetry", os.getenv("LAB_TELEMETRY", "mock" if platform.system() == "Windows" else "live"))
    if mode not in {"mock", "live"}:
        raise ValueError("LAB_TELEMETRY must be mock or live")
    db = Path(config.get("db", os.getenv("LAB_DB", "data/lab.sqlite3")))
    roots = config.get("roots", json.loads(os.getenv("LAB_MODEL_ROOTS", "[]")))
    native_db = config.get("native_db", os.getenv("LAB_NATIVE_DB"))
    telemetry_hub = TelemetryHub(lambda: {**sample(mode, db.parent), "controls_enabled": False})
    if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
        raise ValueError("LAB_MODEL_ROOTS must be a JSON array of absolute paths")

    @asynccontextmanager
    async def lifespan(app):
        if len(token) < 32:
            raise RuntimeError("Set LAB_API_TOKEN to a random secret of at least 32 characters")
        if fan_controls and (not fan_socket or not 32 <= len(fan_token) <= 128 or
                             not 32 <= len(engine_token) <= 512 or len({token, fan_token, engine_token, ipc_token}) != 4):
            raise RuntimeError('Fan controls require a private broker, distinct IPC token and engine-control credential')
        if controls and (not agent_configured or not 32 <= len(engine_token) <= 512 or
                         engine_token in (token, ipc_token)):
            raise RuntimeError('Engine controls require an agent and a distinct control token')
        if agent_configured and (ipc_token == token or len(ipc_token) > 512):
            raise RuntimeError('Engine agent requires a distinct bounded IPC secret')
        if agent_socket and not agent_configured:
            raise RuntimeError('Configured engine agent requires an IPC secret')
        if fixtures and (len(job_token) < 32 or hmac.compare_digest(token.encode(), job_token.encode())):
            raise RuntimeError("Fixture jobs require a distinct LAB_JOB_TOKEN of at least 32 characters")
        db.parent.mkdir(parents=True, exist_ok=True)
        jobs.initialize(db)
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
            model_profiles.initialize(conn)
        try:
            yield
        finally:
            await asyncio.to_thread(telemetry_hub.close)

    app = FastAPI(title="P100 Inference Lab", version="0.4.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.telemetry_hub = telemetry_hub

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

    def authorize_jobs(x_lab_job_token: str | None = Header(default=None)):
        if not fixtures:
            raise HTTPException(403, "Fixture jobs are disabled by the administrator")
        if not x_lab_job_token or not hmac.compare_digest(x_lab_job_token.encode(), job_token.encode()):
            raise HTTPException(403, "A separate benchmark job credential is required")

    job_auth = [*auth, Depends(authorize_jobs)]

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/system", dependencies=auth)
    def system():
        try:
            return telemetry_hub.get()
        except TelemetryUnavailable:
            raise HTTPException(503, "Telemetry temporarily unavailable") from None

    @app.get("/api/system/stream", dependencies=auth)
    async def system_stream():
        try:
            key, queue = telemetry_hub.subscribe()
        except TelemetryUnavailable:
            raise HTTPException(429, "Telemetry stream capacity reached", headers={"Retry-After": "5"}) from None
        async def events():
            try:
                while not telemetry_hub.closed:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=5)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    try:
                        payload = telemetry_hub.frame(event)
                    except TelemetryUnavailable:
                        yield 'event: unavailable\ndata: {"message":"Telemetry temporarily unavailable"}\n\n'
                        continue
                    yield "event: telemetry\ndata: " + json.dumps(payload, allow_nan=False) + "\n\n"
            finally:
                telemetry_hub.unsubscribe(key)
        return StreamingResponse(events(), media_type="text/event-stream", background=BackgroundTask(telemetry_hub.unsubscribe, key), headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def ask_engine(engine, operation, *args):
        if engine == 'kmic':
            return engine_exchange(agent_socket, ipc_token, operation, *args)
        return engine_exchange(agent_socket, ipc_token, operation, *args, engine=engine)

    def engine_status(engine):
        unit = MANAGED_UNITS[engine]
        try:
            result = ask_engine(engine, 'status')
            if (result.get('id') != engine or result.get('unit') != unit or
                    result.get('state') not in {'active', 'inactive', 'activating', 'deactivating', 'failed', 'unavailable'}):
                raise EngineUnavailable('Invalid service status')
            result['controls_available'] = controls and result.get('controls_available') is True
            return result
        except EngineUnavailable:
            return {'id': engine, 'unit': unit, 'state': 'unavailable',
                    'controls_available': False, 'ready': False, 'model': None,
                    'note': 'Host engine agent has no current status for this engine.'}

    @app.get("/api/engines", dependencies=auth)
    def engines():
        items = [engine_status(engine) if agent_configured and engine in MANAGED_UNITS
                 else NativeAdapter(engine).status() for engine in ENGINE_IDS]
        return {"items": items, "controls_enabled": controls}

    def authorize_engine(engine, x_lab_engine_token):
        if engine not in MANAGED_UNITS or not controls or not agent_configured:
            raise HTTPException(403, 'Engine controls are disabled')
        if (not x_lab_engine_token or len(x_lab_engine_token) > 512 or
                not hmac.compare_digest(x_lab_engine_token.encode(), engine_token.encode())):
            raise HTTPException(403, 'A separate engine control token is required')

    def mutate_engine(engine, action, selection=None):
        outcome = 'accepted'
        try:
            args = (engine, action)
            result = ask_engine(*args) if selection is None else ask_engine(*args, selection)
            if (result.get('accepted') is not True or result.get('operation') != action or
                    result.get('unit') != MANAGED_UNITS[engine]):
                raise EngineUnavailable('Invalid action acknowledgement')
            if selection is not None and result.get('configured_selection') != {
                key: selection[key] for key in ('model_id', 'settings')
            }:
                raise EngineUnavailable('Invalid selection acknowledgement')
        except EngineUnavailable:
            outcome = 'unknown_or_refused'
            result = None
        with sqlite3.connect(db) as connection:
            connection.execute('INSERT INTO audit(created_at,engine,action,outcome) VALUES(?,?,?,?)',
                               (datetime.now(timezone.utc).isoformat(), engine, action, outcome))
        if result is None:
            raise HTTPException(503, 'Engine action refused or outcome unknown. Refresh status before trying again.')
        return result

    @app.post("/api/engines/{engine}/control", dependencies=auth)
    def control(engine: EngineId, request: Control,
                x_lab_engine_token: str | None = Header(default=None)):
        authorize_engine(engine, x_lab_engine_token)
        if engine != 'kmic' and request.action not in {'start', 'stop'}:
            raise HTTPException(403, 'This engine supports start and stop')
        return mutate_engine(engine, request.action)

    @app.post("/api/engines/{engine}/selection", dependencies=auth)
    def stage_selection(engine: EngineId, request: model_profiles.Selection,
                        x_lab_engine_token: str | None = Header(default=None)):
        authorize_engine(engine, x_lab_engine_token)
        if engine != 'kmic':
            raise HTTPException(403, 'Model selection is available for Kmic')
        return mutate_engine(engine, 'stage_selection', request.model_dump())

    @app.get('/api/fan-controls', dependencies=auth)
    def fan_status():
        if not fan_controls:
            return {'enabled': False, 'available': False, 'channels': {}}
        try:
            return {**fan_control.exchange(fan_socket, fan_token), 'enabled': True}
        except (fan_control.Unavailable, fan_control.Conflict):
            return {'enabled': True, 'available': False, 'channels': {}, 'message': 'Fan controller unavailable; controls disabled.'}

    @app.post('/api/fan-controls', dependencies=auth)
    def change_fan(request: fan_control.Change, x_lab_engine_token: str | None = Header(default=None)):
        if (not fan_controls or not x_lab_engine_token or len(x_lab_engine_token) > 512 or
                not hmac.compare_digest(x_lab_engine_token.encode(), engine_token.encode())):
            raise HTTPException(403, 'Fan controls require the separate engine control token')
        outcome, result, failure = 'unknown_or_refused', None, None
        try:
            result = fan_control.exchange(fan_socket, fan_token, request.model_dump(exclude_none=True))
            outcome = 'accepted'
        except fan_control.Conflict:
            failure = HTTPException(409, 'Fan state changed or safety fallback active. Refresh before changing it.')
        except fan_control.Unavailable:
            failure = HTTPException(503, 'Fan request refused or outcome unknown. Refresh before changing it.')
        with sqlite3.connect(db) as connection:
            connection.execute('INSERT INTO audit(created_at,engine,action,outcome) VALUES(?,?,?,?)',
                               (datetime.now(timezone.utc).isoformat(), 'fans',
                                request.channel + ':' + (request.mode or str(request.pwm)), outcome))
        if failure: raise failure
        return {**result, 'enabled': True}

    @app.get("/api/models", dependencies=auth)
    def models():
        return {**inventory(roots), "presets": model_profiles.list_presets(db)}

    @app.post("/api/model-presets", dependencies=auth, status_code=201)
    def save_model_preset(request: model_profiles.Preset):
        if not agent_configured:
            raise HTTPException(409, "The approved model catalog is not configured")
        try:
            status = engine_exchange(agent_socket, ipc_token, 'status')
        except EngineUnavailable:
            raise HTTPException(503, "The approved model catalog is unavailable") from None
        model = next((item for item in status.get('catalog', []) if item["id"] == request.model_id), None)
        if model is None:
            raise HTTPException(422, "Model is not approved for Kmic")
        return model_profiles.save(db, request, model)

    @app.put("/api/model-presets/{preset_id}/default", dependencies=auth, status_code=204)
    def default_model_preset(preset_id: int):
        model_profiles.set_default(db, preset_id)

    @app.delete("/api/model-presets/{preset_id}", dependencies=auth, status_code=204)
    def delete_model_preset(preset_id: int):
        model_profiles.delete(db, preset_id)

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

    @app.get("/api/benchmark-presets", dependencies=auth)
    def benchmark_presets():
        return {"items": jobs.PRESETS, "submission_enabled": fixtures,
                "real_engines_enabled": False, "max_pending": jobs.MAX_PENDING}

    @app.get("/api/benchmark-jobs", dependencies=auth)
    def benchmark_jobs(limit: int = Query(default=50, ge=1, le=100)):
        return {"items": jobs.list_jobs(db, limit)}

    @app.post("/api/benchmark-jobs", dependencies=job_auth)
    def submit_job(request: jobs.JobRequest, request_key: UUID = Header(alias="Idempotency-Key")):
        try:
            job, replayed = jobs.enqueue(db, request, str(request_key))
        except jobs.IdempotencyConflict:
            raise HTTPException(409, "Idempotency key was already used with different settings")
        except jobs.QueueFull:
            raise HTTPException(429, "Fixture queue is full; wait for jobs to finish",
                                headers={"Retry-After": "5"})
        return JSONResponse({"job": job, "replayed": replayed}, status_code=200 if replayed else 201)

    @app.get("/api/benchmark-jobs/{job_id}", dependencies=auth)
    def job_detail(job_id: UUID):
        job = jobs.get_job(db, str(job_id))
        if job is None:
            raise HTTPException(404, "Benchmark job not found")
        return job

    @app.get("/api/benchmark-jobs/{job_id}/export", dependencies=auth)
    def job_export(job_id: UUID):
        return JSONResponse(job_detail(job_id), headers={
            "Content-Disposition": f'attachment; filename="fixture-job-{job_id}.json"'})

    @app.post("/api/benchmark-jobs/{job_id}/cancel", dependencies=job_auth)
    def cancel_job(job_id: UUID):
        job = jobs.cancel(db, str(job_id))
        if job is None:
            raise HTTPException(404, "Benchmark job not found")
        return job

    @app.get("/api/native-benchmarks", dependencies=auth)
    def native_benchmarks(limit: int = Query(default=50, ge=1, le=100)):
        try:
            return {"configured": bool(native_db), "execution_available_via_api": False,
                    "items": native_store.list_jobs(native_db, limit) if native_db else []}
        except (OSError, sqlite3.Error, NativeError):
            raise HTTPException(503, "Native history is not available; no database was created or changed")

    @app.get("/api/native-benchmarks/{job_id}", dependencies=auth)
    def native_detail(job_id: UUID):
        if not native_db:
            raise HTTPException(404, "Native history is not configured")
        try:
            job = native_store.get_job(native_db, str(job_id))
        except (OSError, sqlite3.Error, NativeError):
            raise HTTPException(503, "Native history is unavailable")
        if job is None:
            raise HTTPException(404, "Native benchmark not found")
        return job

    @app.get("/api/native-benchmarks/{job_id}/export", dependencies=auth)
    def native_export(job_id: UUID):
        return JSONResponse(native_detail(job_id), headers={
            "Content-Disposition": f'attachment; filename="native-benchmark-{job_id}.json"'})

    @app.post("/api/native-benchmarks", dependencies=auth)
    @app.post("/api/native-benchmarks/{job_id}/cancel", dependencies=auth)
    def native_mutations_disabled():
        raise HTTPException(403, "Native job submission/cancellation are host-administrator CLI operations only")

    static = Path(config.get("static", os.getenv("LAB_STATIC_DIR", "../frontend/dist")))
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
    return app


app = create_app()
