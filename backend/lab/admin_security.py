"""Root controller trust checks; stdlib only, before optional worker imports."""
import os
from pathlib import Path
import stat
import sys
import json


class AdminTrustError(ValueError):
    pass


def trusted(path, *, missing=False, runtime=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise AdminTrustError('admin_trust_not_confirmed')
    for entry in reversed((path, *path.parents)):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            if entry == path and missing:
                continue
            raise AdminTrustError('admin_trust_not_confirmed') from None
        if info.st_uid != 0:
            raise AdminTrustError('admin_trust_not_confirmed')
        if stat.S_ISLNK(info.st_mode):
            if not runtime:
                raise AdminTrustError('admin_trust_not_confirmed')
            trusted(entry.resolve(strict=True))
        elif info.st_mode & 0o022 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise AdminTrustError('admin_trust_not_confirmed')
    return path


def database(path, *, missing=False):
    path = trusted(path, missing=missing)
    if path.parent.stat().st_mode & 0o077:
        raise AdminTrustError('admin_database_not_private')
    if path.exists() and (not path.is_file() or path.stat().st_mode & 0o077):
        raise AdminTrustError('admin_database_not_private')
    return path


def marker_path(db):
    return Path(db).with_name(Path(db).name + '.admin.json')


def initialize_marker(db):
    path = marker_path(db)
    with path.open('x', encoding='utf-8') as stream:
        json.dump({'format': 'p100-admin-worker-v1', 'payload_uid': None, 'payload_gid': None}, stream)
    os.chmod(path, 0o600)


def identity(db, user_id=None, group_id=None):
    path = trusted(marker_path(db))
    if path.stat().st_mode & 0o077:
        raise AdminTrustError('admin_identity_not_private')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) != {'format', 'payload_uid', 'payload_gid'} or value['format'] != 'p100-admin-worker-v1':
        raise AdminTrustError('admin_identity_invalid')
    uid, gid = value['payload_uid'], value['payload_gid']
    if (uid is None) != (gid is None) or any(number is not None and (type(number) is not int or number <= 0) for number in (uid, gid)):
        raise AdminTrustError('admin_identity_invalid')
    if user_id is not None:
        if uid is not None and (uid, gid) != (user_id, group_id):
            raise AdminTrustError('admin_payload_identity_changed')
        if uid is None:
            value.update(payload_uid=user_id, payload_gid=group_id)
            temporary = path.with_name(path.name + '.new')
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
    return value


def runtime():
    if os.name != 'posix' or not hasattr(os, 'geteuid') or os.geteuid() != 0 or not sys.flags.isolated:
        raise AdminTrustError('root_isolated_python_required')
    trusted(Path(sys.executable), runtime=True)
    trusted(Path(sys.prefix), runtime=True)
    package = trusted(Path(__file__).resolve().parent)
    for path in package.rglob('*'):
        trusted(path, runtime=True)
    for entry in sys.path:
        if not entry:
            raise AdminTrustError('admin_trust_not_confirmed')
        path = Path(entry)
        trusted(path, runtime=True, missing=not path.exists())
        # Dependencies must be sealed too, not just their top-level directory.
        if path.name in ('site-packages', 'dist-packages') and path.is_dir():
            for child in path.rglob('*'):
                trusted(child, runtime=True)


def preset_artifacts(preset):
    for artifact in (preset.binary, preset.model, *preset.libraries):
        path = trusted(artifact.path)
        if not path.is_file():
            raise AdminTrustError('admin_artifact_not_sealed')


def lock_path():
    root = Path('/run/p100-lab-native-admin')
    trusted(root, missing=True)
    root.mkdir(mode=0o700, exist_ok=True)
    trusted(root)
    if root.stat().st_mode & 0o077:
        raise AdminTrustError('admin_runtime_not_private')
    return root / 'global'
