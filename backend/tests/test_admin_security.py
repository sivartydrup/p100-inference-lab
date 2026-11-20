import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from lab import admin_security as security
from lab.admin_cli import main


def marker_permissions(monkeypatch):
    if os.name != 'nt':
        return
    original = Path.stat
    def metadata(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path.name.endswith('.admin.json'):
            fields = list(value); fields[0] &= ~0o077
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(Path, 'stat', metadata)


def test_admin_cli_rejects_untrusted_runtime_before_worker_imports(tmp_path, monkeypatch):
    def refuse(): raise security.AdminTrustError('not sealed')
    monkeypatch.setattr(security, 'runtime', refuse)
    with pytest.raises(SystemExit, match='admin_trust_not_confirmed'):
        main(['--db',str(tmp_path/'untouched.sqlite3'),'init'])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('change', ['owner','writable','symlink','socket','missing_parent'])
def test_trusted_paths_refuse_unprivileged_replacement(monkeypatch, change):
    base = Path.cwd() / 'sealed-test-path'
    def metadata(path):
        if change == 'missing_parent' and path == base: raise FileNotFoundError()
        mode = stat.S_IFDIR | 0o755
        uid = 0
        if path == base / 'file':
            if change == 'owner': uid = 1000
            if change == 'writable': mode |= 0o020
            if change == 'symlink': mode = stat.S_IFLNK | 0o777
            if change == 'socket': mode = stat.S_IFSOCK | 0o600
        return SimpleNamespace(st_uid=uid, st_mode=mode)
    monkeypatch.setattr(Path,'lstat',metadata)
    with pytest.raises(security.AdminTrustError): security.trusted(base / 'file')


def test_database_marker_binds_payload_identity_and_refuses_rebinding(tmp_path, monkeypatch):
    marker_permissions(monkeypatch)
    monkeypatch.setattr(security,'trusted',lambda path,**kwargs: Path(path))
    db=tmp_path/'native.sqlite3'
    security.initialize_marker(db)
    assert security.identity(db)['payload_uid'] is None
    assert security.identity(db,1000,1000)['payload_uid'] == 1000
    assert security.identity(db,1000,1000)['payload_gid'] == 1000
    with pytest.raises(security.AdminTrustError,match='admin_payload_identity_changed'):
        security.identity(db,1001,1001)
    assert security.identity(db)['payload_uid'] == 1000


@pytest.mark.parametrize('value', [[], {'format':'wrong','payload_uid':None,'payload_gid':None},
    {'format':'p100-admin-worker-v1','payload_uid':0,'payload_gid':0},
    {'format':'p100-admin-worker-v1','payload_uid':1000,'payload_gid':None}])
def test_malformed_database_marker_fails_closed(tmp_path, monkeypatch, value):
    marker_permissions(monkeypatch)
    monkeypatch.setattr(security,'trusted',lambda path,**kwargs: Path(path))
    db=tmp_path/'native.sqlite3'; marker=security.marker_path(db)
    marker.write_text(json.dumps(value)); marker.chmod(0o600)
    with pytest.raises(security.AdminTrustError): security.identity(db)
