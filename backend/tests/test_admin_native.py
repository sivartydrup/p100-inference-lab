"""Fixed administrator transport; all systemd/hardware interactions here are simulated."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from lab import native_store as store
from lab.native_config import NativeError, NativePreset, Registry
from lab.native_process import Captured, SystemdSupervisor, unit_name
from lab.native_system import AdminSystemdSupervisor
from lab.native_runner import execute, recover, serve
from test_native import preset_data, Hardware, Supervisor, output


def admin():
    supervisor = object.__new__(AdminSystemdSupervisor)
    supervisor.user_id = 1000; supervisor.group_id = 1000
    supervisor.env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    supervisor.namespace_reference = lambda: {'user': 'user:[1]', 'mnt': 'mnt:[2]'}
    supervisor.worker_starttime = lambda: '1'
    return supervisor


def config():
    return NativePreset.model_validate({**preset_data(), 'max_temperature_c': 75})


def test_root_transport_has_fixed_unprivileged_payload_and_no_shell():
    supervisor = admin(); preset = config()
    argv = supervisor.command(preset, unit_name(str(uuid4()), 'measured', 0), 5, Path('/run/private/approved'))
    assert '--user' not in argv
    for setting in ('User=1000', 'Group=1000', 'CapabilityBoundingSet=', 'PrivateUsers=yes',
                    'KillMode=control-group', 'ProtectSystem=strict', 'NoNewPrivileges=yes', 'RuntimeMaxSec=5.000'):
        assert '--property=' + setting in argv
    assert argv[-len(preset.argv()):] == preset.argv()
    assert '--namespace-reference' in argv and '--environment' in argv
    assert '-I' in argv
    assert '--require-empty-capabilities' in argv
    with pytest.raises(NativeError): supervisor.command(preset, 'ssh.service', 5, Path('/run/gate'))


def test_root_control_never_uses_user_bus(monkeypatch):
    calls = []
    monkeypatch.setattr('lab.native_system.capture', lambda argv, *args, **kwargs: calls.append(argv))
    admin().control('show', '--property=Version')
    assert calls == [['/usr/bin/systemctl', '--no-ask-password', 'show', '--property=Version']]


@pytest.mark.parametrize('values', [b'User=0\nGroup=0\nCapabilityBoundingSet=\n',
                                  b'User=1000\nGroup=1000\nCapabilityBoundingSet=cap_sys_admin\n', b'User=1000\n'])
def test_root_gate_refuses_root_identity_or_capabilities(monkeypatch, values):
    monkeypatch.setattr(SystemdSupervisor, 'attest', lambda *args: True)
    supervisor = admin(); supervisor.control = lambda *args: Captured(0, values, b'', 1)
    with pytest.raises(NativeError, match='non_root_payload_not_confirmed'):
        supervisor.attest(unit_name(str(uuid4()), 'measured', 0), config(), 5)


def test_root_gate_accepts_only_exact_non_root_identity(monkeypatch):
    monkeypatch.setattr(SystemdSupervisor, 'attest', lambda *args: True)
    supervisor = admin()
    supervisor.control = lambda *args: Captured(0, b'User=1000\nGroup=1000\nCapabilityBoundingSet=\n', b'', 1)
    assert supervisor.attest(unit_name(str(uuid4()), 'measured', 0), config(), 5)


@pytest.mark.parametrize('reading,code', [
    (['75','150','150'], 'temperature_cutoff'),
    (['30','250','150'], 'admin_150w_policy_required'),
    (['30','150','250'], 'admin_150w_policy_required'),
    (['nan','150','150'], 'admin_gpu_policy_unavailable'),
    (['30'], 'admin_gpu_policy_unavailable'),
])
def test_continuous_policy_refuses_before_native_launch(monkeypatch, reading, code):
    supervisor = admin(); preset = config()
    monkeypatch.setattr('lab.native_system.preset_artifacts', lambda *_: None)
    supervisor.hardware = SimpleNamespace(query=lambda *args: [[preset.gpu_uuid, *reading]])
    def transport(self, preset, unit, deadline, check): check(); pytest.fail('Unsafe policy passed')
    monkeypatch.setattr(SystemdSupervisor, 'run', transport)
    with pytest.raises(NativeError, match=code): supervisor.run(preset, 'unused', time.monotonic()+5, lambda: None)


def test_policy_is_rechecked_during_execution(monkeypatch):
    supervisor = admin(); preset = config()
    monkeypatch.setattr('lab.native_system.preset_artifacts', lambda *_: None)
    rows = iter([[preset.gpu_uuid, '30', '150', '150'], [preset.gpu_uuid, '76', '150', '150']])
    supervisor.hardware = SimpleNamespace(query=lambda *args: [next(rows)])
    clock = [0.0]
    monkeypatch.setattr('lab.native_system.time.monotonic', lambda: clock[0])
    def transport(self, preset, unit, deadline, check):
        check(); clock[0] = .6; check()
    monkeypatch.setattr(SystemdSupervisor, 'run', transport)
    with pytest.raises(NativeError, match='temperature_cutoff'): supervisor.run(preset, 'unused', 5, lambda: None)


def test_admin_intent_is_durable_and_not_replayed_after_restart(tmp_path, monkeypatch):
    db = tmp_path/'native.sqlite3'; store.initialize(db); preset = config()
    job = store.enqueue(db, preset, str(uuid4()), transport='admin-systemd-v1')
    assert job['provenance']['execution_transport'] == 'admin-systemd-v1'
    assert job['provenance']['execution_policy'] == {'power_limit_w':150,'temperature_cutoff_c':75}
    store.claim(db)
    unit = unit_name(job['id'], 'measured', 0); store.plan(db, job['id'], 'measured', 0, unit)
    supervisor = Supervisor(); supervisor.units.add(unit)
    recover(db, supervisor)
    assert store.get_job(db, job['id'])['status'] == 'interrupted'
    assert unit in supervisor.stopped and supervisor.started == [] and store.claim(db) is None


def test_durable_recording_retains_wall_time_and_excludes_warmup(tmp_path, monkeypatch):
    db = tmp_path/'native.sqlite3'; store.initialize(db); preset = config()
    monkeypatch.setattr('lab.native_runner.verify_artifacts', lambda *_: {})
    monkeypatch.setattr('lab.native_runner.unchanged', lambda *_: None)
    job = store.enqueue(db, preset, str(uuid4()), transport='admin-systemd-v1')
    execute(db, store.claim(db), Registry(enabled=True, presets=[preset]), Supervisor(), Hardware(), threading.Event())
    exported = store.get_job(db, job['id'])
    assert exported['summary']['sample_count'] == 2
    assert exported['summary']['metrics']['process_wall_ms']['median'] == 15.0
    assert len(exported['attempts']) == 3
    assert all(attempt['sample']['process_wall_ms'] == 15.0 for attempt in exported['attempts'])


def test_startup_identity_check_happens_under_lock_before_recovery(tmp_path):
    db = tmp_path/'native.sqlite3'; store.initialize(db)
    def refuse(): raise NativeError('admin_payload_identity_changed')
    class NoRecovery:
        def available(self): pytest.fail('Recovery started before identity validation')
    with pytest.raises(NativeError, match='admin_payload_identity_changed'):
        serve(db, Registry(), NoRecovery(), Hardware(), threading.Event(), once=True,
              lock_path=tmp_path/'global', startup_check=refuse)
