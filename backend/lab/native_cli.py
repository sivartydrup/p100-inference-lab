"""Host-administrator commands only. No remote execution API or default launch."""
import argparse
import json
import os
import signal
import sys
import sqlite3
import threading
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from . import native_store as store
from .native_config import NativeError, load_registry
from .native_process import SystemdSupervisor
from .native_runner import NvidiaHardware, serve
from .worker import WorkerBusy


def parser():
    result = argparse.ArgumentParser(description="Disabled-by-default host-only native microbenchmarks")
    result.add_argument("--db", required=True, type=Path, help="Separate native SQLite database; never the dashboard database")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize only an empty dedicated native database")
    submit = commands.add_parser("submit")
    submit.add_argument("--registry", required=True, type=Path)
    submit.add_argument("--preset", required=True)
    submit.add_argument("--request-id", required=True, type=UUID)
    work = commands.add_parser("work")
    work.add_argument("--registry", required=True, type=Path)
    work.add_argument("--once", action="store_true")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id", type=UUID)
    commands.add_parser("list")
    export = commands.add_parser("export")
    export.add_argument("job_id", type=UUID)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            store.initialize(args.db)
            print("Dedicated native database initialized; no job submitted or executed.")
        elif args.command == "list":
            print(json.dumps({"items": store.list_jobs(args.db)}, indent=2))
        elif args.command == "export":
            result = store.get_job(args.db, str(args.job_id))
            if result is None:
                raise NativeError("unknown_job")
            print(json.dumps(result, indent=2))
        elif args.command == "cancel":
            store.cancel(args.db, str(args.job_id))
            print("Cancellation recorded; the worker must confirm process-tree cleanup.")
        else:
            if os.getenv("LAB_ENABLE_NATIVE_RUNNER", "false").lower() != "true":
                raise NativeError("native_runner_disabled")
            if sys.platform != "linux" or os.getuid() == 0:
                raise NativeError("non_root_linux_user_required")
            registry = load_registry(args.registry)
            if not registry.enabled:
                raise NativeError("registry_disabled")
            if args.command == "submit":
                preset = registry.approved(args.preset)
                print(json.dumps(store.enqueue(args.db, preset, str(args.request_id)), indent=2))
            else:
                stop = threading.Event()
                for signum in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(signum, lambda *_: stop.set())
                serve(args.db, registry, SystemdSupervisor(), NvidiaHardware(), stop, once=args.once)
    except (NativeError, OSError, sqlite3.Error, WorkerBusy, ValidationError, ValueError) as error:
        # No secrets, filesystem paths or process output in command errors.
        code = error.code if isinstance(error, NativeError) else "native_configuration_or_io_error"
        raise SystemExit(code) from None


if __name__ == "__main__":
    main()
