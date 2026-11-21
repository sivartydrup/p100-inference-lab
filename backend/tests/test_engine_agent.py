import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

if os.name != 'posix':
    pytest.skip('Host agent is Ubuntu-only', allow_module_level=True)

from lab.engine_agent import Controller, Server, UNIT, socket_path
from lab.engine_bridge import exchange, EngineUnavailable

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Host agent is Ubuntu-only')


@pytest.fixture
def controller(tmp_path):
    unit = tmp_path / UNIT
    unit.write_text('[Service]\nExecStart=/fixed/existing-engine\n')
    unit.chmod(0o644)
    for name, content in [('upstream', 'u' * 48), ('ipc', 'i' * 48)]:
        (tmp_path / name).write_text(content)
        (tmp_path / name).chmod(0o600)
    return Controller({'unit_file': str(unit), 'unit_sha256': hashlib.sha256(unit.read_bytes()).hexdigest(),
                       'endpoint': 'http://192.168.0.188:8080', 'upstream_token_file': str(tmp_path / 'upstream'),
                       'ipc_token_file': str(tmp_path / 'ipc'), 'model_label': 'Qwen3.8 27B Q6_K'})


def properties(controller, **overrides):
    values = {'FragmentPath': str(controller.unit_file), 'DropInPaths': '', 'ActiveState': 'inactive', 'UnitFileState': 'enabled'}
    values.update(overrides)
    return '\n'.join(k + '=' + v for k, v in values.items())


@pytest.mark.parametrize('operation,argv', [('start', ['--no-block', 'start']), ('stop', ['--no-block', 'stop']),
                                          ('enable_boot', ['enable']), ('disable_boot', ['disable'])])
def test_only_fixed_unit_commands(controller, operation, argv):
    with patch.object(controller, 'run', side_effect=[properties(controller), '']) as run, patch.object(controller, 'start_preflight'):
        assert controller.perform(operation)['accepted'] is True
        assert run.call_args.args == (argv,)


def test_subprocess_uses_no_shell_and_fixed_user_manager(controller):
    with patch('lab.engine_agent.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='')) as run:
        controller.run(['--no-block', 'stop'])
    call = run.call_args
    assert call.args[0] == ['/usr/bin/systemctl', '--user', '--no-block', 'stop', UNIT]
    assert call.kwargs.get('shell', False) is False
    assert set(call.kwargs['env']) == {'PATH', 'LANG', 'LC_ALL', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}


def test_unknown_operations_modified_unit_and_dropins_refused(controller):
    with pytest.raises(ValueError):
        controller.perform('restart')
    with patch.object(controller, 'run', return_value=properties(controller, DropInPaths='/changed.conf')) as run:
        with pytest.raises(RuntimeError):
            controller.perform('stop')
        assert run.call_count == 1
    controller.unit_file.write_text('changed')
    with patch.object(controller, 'run', return_value=properties(controller)) as run:
        with pytest.raises(RuntimeError):
            controller.perform('enable_boot')
        assert run.call_count == 1


def test_live_model_is_reported_only_when_ready(controller):
    with (patch.object(controller, 'run', return_value=properties(controller, ActiveState='active')),
          patch.object(controller, 'upstream', side_effect=[{'status': 'ok'}, {'data': [{'id': '/private/model.gguf'}]}])):
        status = controller.perform('status')
    assert status['ready'] and status['model'] == 'model.gguf' and status['boot_enabled']
    with (patch.object(controller, 'run', return_value=properties(controller, ActiveState='active')),
          patch.object(controller, 'upstream', side_effect=OSError)):
        status = controller.perform('status')
    assert not status['ready'] and status['model'] is None


@pytest.mark.parametrize('data,allowed', [('16000,0,40\n16000,0,41', True), ('100,0,40\n16000,0,40', False),
                                        ('16000,99,40\n16000,0,40', False), ('nan,0,40\n16000,0,40', False),
                                        ('16000,0,75\n16000,0,40', False), ('bad', False)])
def test_start_preflight(controller, data, allowed):
    with patch('lab.engine_agent.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=data)):
        if allowed:
            controller.start_preflight()
        else:
            with pytest.raises((ValueError, RuntimeError)):
                controller.start_preflight()


def test_busy_actions_refuse_instead_of_queue(controller):
    controller.lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            controller.perform('stop')
    finally:
        controller.lock.release()


def test_real_unix_transport_authentication_and_fixed_schema(controller):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='engine-agent-') as directory:
        path = Path(directory) / 'agent.sock'
        with patch.object(controller, 'perform', return_value={'state': 'active'}) as perform:
            with Server(path, controller) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    assert exchange(path, 'i' * 48, 'status')['state'] == 'active'
                    with pytest.raises(EngineUnavailable):
                        exchange(path, 'incorrect', 'stop')
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.connect(str(path))
                        connection.sendall(json.dumps({'token': 'i'*48, 'operation': 'stop', 'unit': 'sshd'}).encode()+b'\n')
                        assert b'"ok": false' in connection.recv(4096)
                    perform.assert_called_once_with('status')
                finally:
                    server.shutdown()
                    thread.join(3)


def test_socket_restart_handles_only_owned_stale_socket():
    import tempfile
    with tempfile.TemporaryDirectory(prefix='engine-restart-') as directory:
        path = Path(directory) / 'agent.sock'
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(path))
        stale.close()
        with socket_path(path):
            assert not path.exists()
            with pytest.raises(BlockingIOError):
                with socket_path(path):
                    pass
        path.write_text('not a socket')
        with pytest.raises(RuntimeError):
            with socket_path(path):
                pass
        assert path.read_text() == 'not a socket'
