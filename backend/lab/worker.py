"""Single, explicitly opted-in fixture worker. It cannot launch native engines."""
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import jobs


class WorkerBusy(RuntimeError):
    pass


@contextmanager
def worker_lock(db: Path):
    # Same local filesystem as SQLite. Kernel releases this lock on process death.
    lock_path = db.resolve().with_suffix(db.suffix + ".worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise WorkerBusy("Another fixture worker owns this database") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fixture_sample(trial):
    prefill_ms, decode_ms = 40 + trial * 2, 200 + trial * 5
    return {"source": "fixture", "prompt_tokens": 128, "output_tokens": 32,
            "ttft_ms": prefill_ms + 5, "prefill_tokens_per_second": 128_000 / prefill_ms,
            "decode_tokens_per_second": 32_000 / decode_ms, "wall_ms": prefill_ms + 5 + decode_ms}


def execute(db, job, stop, pause=0.05):
    request, job_id = job["request"], job["id"]
    started = time.monotonic()
    measured = []
    try:
        for phase, count in (("warmup", request["warmup"]), ("measured", request["trials"])):
            for trial in range(count):
                stop.wait(pause)
                if stop.is_set():
                    jobs.finish(db, job_id, "interrupted", error_code="worker_stopped")
                    return
                if jobs.cancellation_requested(db, job_id):
                    jobs.finish(db, job_id, "cancelled")
                    return
                if time.monotonic() - started >= request["timeout_seconds"]:
                    jobs.finish(db, job_id, "timed_out", error_code="deadline_exceeded")
                    return
                if request["preset"] == "fixture-failure-v1" and phase == "measured":
                    jobs.finish(db, job_id, "failed", error_code="fixture_failure")
                    return
                sample = fixture_sample(trial)
                jobs.add_sample(db, job_id, phase, trial, sample)
                if phase == "measured":
                    measured.append(sample)
        if time.monotonic() - started >= request["timeout_seconds"]:
            jobs.finish(db, job_id, "timed_out", error_code="deadline_exceeded")
        else:
            jobs.finish(db, job_id, "succeeded", summary=jobs.summarize(measured))
    except Exception:
        # Persist a bounded error code; do not leak paths, prompts or secrets.
        jobs.finish(db, job_id, "failed", error_code="worker_error")


def serve(db, stop):
    with worker_lock(db):
        jobs.initialize(db)
        jobs.recover_interrupted(db)
        while not stop.is_set():
            job = jobs.claim_next(db)
            if job:
                execute(db, job, stop)
            else:
                stop.wait(0.25)


def main():
    if os.getenv("LAB_ENABLE_FIXTURE_JOBS", "false").lower() != "true":
        raise SystemExit("Fixture worker is disabled; explicitly set LAB_ENABLE_FIXTURE_JOBS=true")
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        serve(Path(os.getenv("LAB_DB", "data/lab.sqlite3")), stop)
    except WorkerBusy as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
