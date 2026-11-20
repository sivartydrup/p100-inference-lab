"""Manually administrator-started worker. No daemon, privilege grant or HTTP route."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
from uuid import UUID

from . import admin_security as security


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--db', required=True, type=Path)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('list')
    for command in ('export', 'cancel'):
        commands.add_parser(command).add_argument('job_id', type=UUID)
    submit = commands.add_parser('submit')
    submit.add_argument('--registry', required=True, type=Path)
    submit.add_argument('--preset', required=True)
    submit.add_argument('--request-id', required=True, type=UUID)
    work = commands.add_parser('work')
    work.add_argument('--registry', required=True, type=Path)
    work.add_argument('--user', required=True, help='Existing non-root payload account; no account is created')
    work.add_argument('--once', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        security.runtime()
        os.umask(0o077)
        db = security.database(args.db, missing=args.command == 'init')
    except (security.AdminTrustError, OSError):
        raise SystemExit('admin_trust_not_confirmed; use an isolated root-owned runtime and private root-owned database directory') from None
    # Import non-stdlib worker code only after checking the runtime/dependency tree.
    from pydantic import ValidationError
    from . import native_store as store
    from .native_config import NativeError, load_registry
    from .native_runner import NvidiaHardware, serve
    from .native_system import AdminSystemdSupervisor
    from .worker import WorkerBusy
    import sqlite3
    try:
        if args.command == 'init':
            if db.exists() or security.marker_path(db).exists():
                raise NativeError('admin_init_requires_new_database')
            store.initialize(db)
            security.initialize_marker(db)
            security.database(db)
            print('Private administrator database initialized; no execution enabled.')
            return
        security.identity(db)
        if args.command == 'list':
            print(json.dumps({'items': store.list_jobs(db)}, indent=2))
        elif args.command == 'export':
            job = store.get_job(db, str(args.job_id))
            if job is None:
                raise NativeError('unknown_job')
            print(json.dumps(job, indent=2))
        elif args.command == 'cancel':
            store.cancel(db, str(args.job_id))
            print('Cancellation recorded; worker cleanup must still be confirmed.')
        else:
            if os.getenv('LAB_ENABLE_ADMIN_RUNNER', 'false').lower() != 'true':
                raise NativeError('admin_runner_disabled')
            registry = load_registry(security.trusted(args.registry))
            if not registry.enabled:
                raise NativeError('registry_disabled')
            for preset in registry.presets:
                if preset.reviewed:
                    security.preset_artifacts(preset)
                    if preset.max_temperature_c > 75:
                        raise NativeError('admin_temperature_policy_required')
            if args.command == 'submit':
                preset = registry.approved(args.preset)
                print(json.dumps(store.enqueue(db, preset, str(args.request_id), transport='admin-systemd-v1'), indent=2))
            else:
                hardware = NvidiaHardware()
                supervisor = AdminSystemdSupervisor(args.user, hardware)
                stop = threading.Event()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(sig, lambda *_: stop.set())
                serve(db, registry, supervisor, hardware, stop, once=args.once, lock_path=security.lock_path(),
                      startup_check=lambda: security.identity(db, supervisor.user_id, supervisor.group_id))
    except NativeError as error:
        raise SystemExit(error.code) from None
    except security.AdminTrustError:
        raise SystemExit('admin_trust_not_confirmed') from None
    except (OSError, sqlite3.Error, WorkerBusy, ValidationError, ValueError, KeyError, TypeError):
        # Do not print private paths, presets, engine output or credentials.
        raise SystemExit('admin_operation_failed; inspect private job state with list/export') from None


if __name__ == '__main__':
    main()
