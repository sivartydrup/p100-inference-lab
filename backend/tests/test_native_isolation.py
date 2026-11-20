"""Effective kernel checks cannot be replaced by configured systemd properties."""
import errno
from types import SimpleNamespace

import pytest

from lab import native_child as child


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(child.os, 'geteuid', lambda: 1000, raising=False)
    monkeypatch.setattr(child.os, 'readlink', lambda path: 'user:[11]' if path.endswith('/user') else 'mnt:[12]')
    monkeypatch.setattr(child.Path, 'read_text', lambda self: 'NoNewPrivs:\t1\nSeccomp:\t2\n')
    monkeypatch.setattr(child.os, 'ST_RDONLY', 1, raising=False)
    monkeypatch.setattr(child.os, 'statvfs', lambda path: SimpleNamespace(f_flag=1), raising=False)
    def denied(*_): raise OSError(errno.EAFNOSUPPORT, 'denied')
    monkeypatch.setattr(child.socket, 'socket', denied)
    return ['/private/llama-bench', '-m', '/private/model.gguf'], {'LD_LIBRARY_PATH': '/private/lib'}, {'user': 'user:[1]', 'mnt': 'mnt:[2]'}


def test_root_identity_is_refused_even_with_reported_isolation(isolated, monkeypatch):
    monkeypatch.setattr(child.os, 'geteuid', lambda: 0)
    with pytest.raises(SystemExit, match='Native isolation not confirmed'):
        child.confirm_isolation(*isolated)


def test_admin_requires_effective_permitted_and_bounding_capabilities_empty(isolated, monkeypatch):
    monkeypatch.setattr(child.Path, 'read_text', lambda self: 'NoNewPrivs: 1\nSeccomp: 2\nCapEff: 0\nCapPrm: 0\nCapBnd: 0\n')
    child.confirm_isolation(*isolated, require_empty_capabilities=True)
    for name in ('CapEff', 'CapPrm', 'CapBnd'):
        text='NoNewPrivs: 1\nSeccomp: 2\n'+''.join(key+': '+('1' if key==name else '0')+'\n' for key in ('CapEff','CapPrm','CapBnd'))
        monkeypatch.setattr(child.Path, 'read_text', lambda self,value=text: value)
        with pytest.raises(SystemExit, match='Native isolation not confirmed'):
            child.confirm_isolation(*isolated, require_empty_capabilities=True)


def test_effective_isolation_allows_only_verified_boundary(isolated):
    child.confirm_isolation(*isolated)


@pytest.mark.parametrize('change', ['shared_user', 'shared_mount', 'writable_model', 'writable_library', 'no_nnp', 'no_seccomp', 'inet_allowed', 'unexpected_socket_error', 'missing_reference'])
def test_missing_kernel_guarantee_refuses_before_exec(isolated, monkeypatch, change):
    command, environment, reference = isolated
    if change == 'shared_user': reference['user'] = 'user:[11]'
    if change == 'shared_mount': reference['mnt'] = 'mnt:[12]'
    if change == 'missing_reference': reference.clear()
    if change.startswith('writable_'):
        bad = '/private/model.gguf' if change == 'writable_model' else '/private/lib'
        monkeypatch.setattr(child.os, 'statvfs', lambda path: SimpleNamespace(f_flag=0 if path == bad else 1))
    if change == 'no_nnp': monkeypatch.setattr(child.Path, 'read_text', lambda self: 'NoNewPrivs:\t0\nSeccomp:\t2\n')
    if change == 'no_seccomp': monkeypatch.setattr(child.Path, 'read_text', lambda self: 'NoNewPrivs:\t1\nSeccomp:\t0\n')
    if change == 'inet_allowed': monkeypatch.setattr(child.socket, 'socket', lambda *_: SimpleNamespace(close=lambda: None))
    if change == 'unexpected_socket_error':
        def exhausted(*_): raise OSError(errno.EMFILE, 'not a policy denial')
        monkeypatch.setattr(child.socket, 'socket', exhausted)
    with pytest.raises(SystemExit, match='Native isolation not confirmed'):
        child.confirm_isolation(command, environment, reference)
