import copy
import json
import os
from pathlib import Path
import socket
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lab import fan_control
from lab.fan_agent import Controller, Conflict, Hardware, Server
from lab.main import create_app
from test_engine_control import ADMIN, AUTH, settings


class Fixture:
    def __init__(self):
        self.values = {channel: {'mode': 2, 'pwm': 70, 'rpm': 4000} for channel in ('fan1', 'fan2')}
        self.writes = []

    def read(self, channel, attribute):
        return self.values[channel][{'enable': 'mode', 'duty': 'pwm', 'rpm': 'rpm'}[attribute]]

    def write(self, channel, attribute, value):
        assert channel in ('fan1', 'fan2') and attribute in ('enable', 'duty')
        self.writes.append((channel, attribute, value))
        self.values[channel][{'enable': 'mode', 'duty': 'pwm'}[attribute]] = value

    def snapshot(self): return copy.deepcopy(self.values)

    def full(self):
        for channel in self.values: self.write(channel, 'enable', 0)


def controller():
    hardware = Fixture()
    control = Controller(hardware, lambda: [40, 42])
    control.guard()
    return control, hardware


def change(control, **body):
    return control.perform('set', {'channel': 'fan1', 'version': control.version, 'confirmed': True, **body})


def test_manual_preserves_current_level_and_edits_require_manual():
    control, hw = controller()
    with pytest.raises(Conflict): change(control, pwm=128)
    assert not hw.writes
    old = control.version
    result = change(control, mode='manual')
    assert hw.writes == [('fan1', 'enable', 1), ('fan1', 'duty', 70)]
    assert result['channels']['fan1']['pwm'] == 70 and control.version != old
    assert change(control, pwm=128)['channels']['fan1']['pwm'] == 128
    assert change(control, pwm=0)['channels']['fan1']['pwm'] == 0
    assert change(control, pwm=255)['channels']['fan1']['pwm'] == 255
    assert change(control, mode='auto')['channels']['fan1']['mode'] == 2
    assert change(control, mode='full')['channels']['fan1']['mode'] == 0
    hw.values['fan1']['pwm'] = 40  # Full bypasses this stored value.
    assert change(control, mode='manual')['channels']['fan1']['pwm'] == 255
    assert hw.values['fan2']['mode'] == 2


@pytest.mark.parametrize('invalid', [
    {'channel': 'fan4', 'mode': 'full'}, {'channel': '../pwm4', 'mode': 'manual'},
    {'mode': 'manual', 'pwm': 70}, {'mode': 'unknown'}, {'pwm': True}, {'pwm': -1},
    {'pwm': 256}, {'pwm': 2.5}, {'mode': 'auto', 'path': '/sys/evil'},
    {'mode': 'auto', 'confirmed': 1},
])
def test_unapproved_writes_never_touch_hardware(invalid):
    control, hw = controller()
    with pytest.raises((ValueError, Conflict)): change(control, **invalid)
    assert not hw.writes


def test_stale_request_and_busy_controller_refuse_without_writes():
    control, hw = controller()
    with pytest.raises(Conflict): change(control, mode='manual', version='old-session:0')
    control.lock.acquire()
    try:
        with pytest.raises(Conflict): change(control, mode='auto')
    finally: control.lock.release()
    assert not hw.writes


def test_temperature_loss_stall_and_write_failure_force_both_full():
    control, hw = controller()
    change(control, mode='manual')
    control.temperatures = lambda: (_ for _ in ()).throw(ValueError('unavailable'))
    control.guard()
    assert not control.status()['available'] and control.status()['safety_override']
    assert all(value['mode'] == 0 for value in hw.values.values())
    with pytest.raises(Conflict): change(control, mode='auto')
    control.temperatures = lambda: [40, 42]
    change(control, mode='manual')
    hw.values['fan1']['rpm'] = 0
    for _ in range(3): control.guard()
    assert all(value['mode'] == 0 for value in hw.values.values())
    control.guard()
    change(control, mode='manual')
    original = hw.write
    def fail(channel, attribute, value):
        if attribute == 'duty': raise OSError('fixture write failed')
        original(channel, attribute, value)
    hw.write = fail
    with pytest.raises(OSError): change(control, pwm=150)
    assert all(value['mode'] == 0 for value in hw.values.values())


@pytest.mark.parametrize('raw', ['Tesla P100-PCIE-16GB,74\nTesla P100-PCIE-16GB,40',
                               'Tesla P100-PCIE-16GB,75\nTesla P100-PCIE-16GB,40',
                               'Tesla P100-PCIE-16GB,nan\nTesla P100-PCIE-16GB,40',
                               'Other GPU,40\nOther GPU,40'])
def test_gpu_guard_is_read_only_and_cutoff_is_75(raw):
    from types import SimpleNamespace
    from lab.fan_agent import gpu_temperatures
    with patch('lab.fan_agent.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=raw)) as run:
        if ',74' in raw: assert gpu_temperatures() == [74, 40]
        else:
            with pytest.raises(ValueError): gpu_temperatures()
        assert run.call_args.args[0] == ['/usr/bin/nvidia-smi', '--query-gpu=name,temperature.gpu', '--format=csv,noheader,nounits']
        assert 'shell' not in run.call_args.kwargs


@pytest.mark.skipif(os.name != 'posix', reason='Linux sysfs operations')
def test_real_attribute_allowlist_never_opens_pwm3_or_pwm4(tmp_path):
    dmi = tmp_path / 'dmi'; dmi.mkdir()
    (dmi / 'board_name').write_text('X99-SLI-CF')
    (dmi / 'board_vendor').write_text('Gigabyte Technology Co., Ltd.')
    device = tmp_path / 'it87.2624'; hwmon = device / 'hwmon/hwmon2'; hwmon.mkdir(parents=True)
    (hwmon / 'name').write_text('it8620')
    for channel in (1, 2):
        for suffix, value in [('_enable', '2\n'), ('', '70\n')]: (hwmon / f'pwm{channel}{suffix}').write_text(value)
        (hwmon / f'fan{channel}_input').write_text('4000\n')
    for name in ('pwm3', 'pwm3_enable', 'pwm4', 'pwm4_enable'): (hwmon / name).write_text('untouched')
    hw = Hardware(device, dmi)
    assert hw.snapshot()['fan1']['pwm'] == 70
    hw.write('fan1', 'duty', 80)
    assert (hwmon / 'pwm1').read_text() == '80\n'
    for channel in ('fan3', 'fan4', '../pwm4'):
        with pytest.raises(ValueError): hw.write(channel, 'enable', 0)
    for name in ('pwm3', 'pwm3_enable', 'pwm4', 'pwm4_enable'): assert (hwmon / name).read_text() == 'untouched'
    (dmi / 'board_name').write_text('different board')
    with pytest.raises(ValueError): hw.write('fan1', 'enable', 0)


def test_http_requires_two_credentials_strict_fields_and_audits(tmp_path):
    config = settings(tmp_path, fan_controls=True, fan_socket='/fans/agent.sock', fan_ipc_token='f' * 48)
    control, _ = controller()
    body = {'channel': 'fan1', 'mode': 'manual', 'version': control.version, 'confirmed': True}
    def exchange(path, key, request=None):
        assert path == '/fans/agent.sock' and key == 'f' * 48
        return control.perform('status' if request is None else 'set', request)
    with TestClient(create_app(config)) as client, patch('lab.main.fan_control.exchange', side_effect=exchange) as ipc:
        assert client.get('/api/fan-controls').status_code == 401
        assert client.post('/api/fan-controls', headers=AUTH, json=body).status_code == 403
        ipc.assert_not_called()
        for invalid in ({**body, 'channel': 'fan4'}, {**body, 'path': '/sys/pwm4'},
                        {**body, 'pwm': 70}, {**body, 'confirmed': False},
                        {**body, 'mode': None, 'pwm': True}):
            assert client.post('/api/fan-controls', headers=ADMIN, json=invalid).status_code == 422
        ipc.assert_not_called()
        assert client.get('/api/fan-controls', headers=AUTH).json()['available']
        assert client.post('/api/fan-controls', headers=ADMIN, json=body).json()['channels']['fan1']['mode'] == 1
    import sqlite3
    with sqlite3.connect(tmp_path / 'lab.db') as db:
        assert db.execute("select engine,action,outcome from audit").fetchall() == [('fans', 'fan1:manual', 'accepted')]


def test_disabled_and_unavailable_control_paths(tmp_path):
    body = {'channel': 'fan1', 'mode': 'full', 'version': 'a' * 32 + ':0', 'confirmed': True}
    with TestClient(create_app(settings(tmp_path))) as client:
        assert not client.get('/api/fan-controls', headers=AUTH).json()['enabled']
        assert client.post('/api/fan-controls', headers=ADMIN, json=body).status_code == 403
    config = settings(tmp_path, fan_controls=True, fan_socket='/fans/agent.sock', fan_ipc_token='f' * 48)
    with TestClient(create_app(config)) as client:
        with patch('lab.main.fan_control.exchange', side_effect=fan_control.Conflict):
            assert client.post('/api/fan-controls', headers=ADMIN, json=body).status_code == 409
        with patch('lab.main.fan_control.exchange', side_effect=fan_control.Unavailable):
            assert client.post('/api/fan-controls', headers=ADMIN, json=body).status_code == 503
            assert not client.get('/api/fan-controls', headers=AUTH).json()['available']


@pytest.mark.skipif(os.name != 'posix', reason='Real Unix transport')
def test_unix_transport_authentication_and_stale_version():
    import tempfile
    control, hw = controller()
    with tempfile.TemporaryDirectory(prefix='fans-') as folder:
        path = Path(folder) / 'agent.sock'
        with Server(path, control, 'f' * 48) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                assert fan_control.exchange(path, 'f' * 48)['available']
                with pytest.raises(fan_control.Unavailable): fan_control.exchange(path, 'wrong')
                with pytest.raises(fan_control.Conflict):
                    fan_control.exchange(path, 'f' * 48, {'channel': 'fan1', 'mode': 'full', 'version': 'old', 'confirmed': True})
                assert not hw.writes
            finally: server.shutdown(); thread.join(3)


@pytest.mark.skipif(os.name != 'posix', reason='Linux service entrypoints')
def test_operator_helpers_only_write_two_allowlisted_channels():
    from lab.fan_agent import main
    hw = Fixture()
    with patch('lab.fan_agent.Hardware', return_value=hw), patch('lab.fan_agent.os.geteuid', return_value=0):
        with patch('sys.argv', ['fan-agent', '--failsafe']): main()
        assert hw.writes == [('fan1', 'enable', 0), ('fan2', 'enable', 0)]
        hw.writes.clear()
        with patch('sys.argv', ['fan-agent', '--restore-auto']), patch('lab.fan_agent.gpu_temperatures', return_value=[40, 40]): main()
        assert hw.writes == [('fan1', 'enable', 2), ('fan2', 'enable', 2)]
        hw.writes.clear()
        with patch('sys.argv', ['fan-agent', '--restore-auto']), patch('lab.fan_agent.gpu_temperatures', return_value=[60, 40]):
            with pytest.raises(ValueError): main()
        assert not hw.writes


@pytest.mark.skipif(os.name != 'posix', reason='Unix runtime protection')
def test_socket_lock_and_unexpected_path_are_not_overwritten():
    from lab.fan_agent import socket_path
    import tempfile
    with tempfile.TemporaryDirectory(prefix='fan-lock-') as folder:
        path = Path(folder) / 'agent.sock'
        with socket_path(path):
            with pytest.raises(BlockingIOError):
                with socket_path(path): pass
        path.write_text('preserved')
        with pytest.raises(ValueError):
            with socket_path(path): pass
        assert path.read_text() == 'preserved'
