import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lab.main import create_app
from lab.engine_bridge import EngineUnavailable

READ = 'r' * 48
IPC = 'i' * 48
CONTROL = 'c' * 48
AUTH = {'Authorization': 'Bearer ' + READ}
ADMIN = {**AUTH, 'X-Lab-Engine-Token': CONTROL}


def settings(tmp_path, **overrides):
    return {'token': READ, 'db': tmp_path / 'lab.db', 'telemetry': 'mock',
            'engine_socket': '/engine-agent/agent.sock', 'engine_ipc_token': IPC,
            'engine_control_token': CONTROL, 'engine_controls': True, **overrides}


@pytest.mark.parametrize('action', ['start', 'stop', 'enable_boot', 'disable_boot'])
def test_control_requires_both_credentials_and_audits(tmp_path, action):
    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange', return_value={
            'accepted': True, 'operation': action, 'unit': 'kmic-qwen27b.service'}) as agent:
        assert client.post('/api/engines/kmic/control', json={'action': action}).status_code == 401
        assert client.post('/api/engines/kmic/control', headers=AUTH, json={'action': action}).status_code == 403
        agent.assert_not_called()
        assert client.post('/api/engines/kmic/control', headers=ADMIN, json={'action': action}).status_code == 200
        agent.assert_called_once_with('/engine-agent/agent.sock', IPC, action)
    with sqlite3.connect(tmp_path / 'lab.db') as connection:
        assert connection.execute('SELECT engine,action,outcome FROM audit').fetchall() == [('kmic', action, 'accepted')]


@pytest.mark.parametrize('body', [{'action': 'restart'}, {'action': 'start', 'unit': 'sshd.service'},
                                  {'action': 'start', 'command': 'id'}, {'action': 'download'}])
def test_arbitrary_control_is_rejected(tmp_path, body):
    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange') as agent:
        assert client.post('/api/engines/kmic/control', headers=ADMIN, json=body).status_code == 422
        agent.assert_not_called()


def test_control_is_disabled_by_default_and_other_engines_stay_denied(tmp_path):
    with TestClient(create_app(settings(tmp_path, engine_controls=False))) as client, patch('lab.main.engine_exchange') as agent:
        assert client.post('/api/engines/kmic/control', headers=ADMIN, json={'action': 'stop'}).status_code == 403
        agent.assert_not_called()
    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange', side_effect=EngineUnavailable) as agent:
        assert client.post('/api/engines/pxa/control', headers=ADMIN, json={'action': 'start'}).status_code == 503
        agent.assert_called_once_with('/engine-agent/agent.sock', IPC, 'start', engine='pxa')


def test_engine_agent_status_and_unavailable_state(tmp_path):
    status = {'id': 'kmic', 'unit': 'kmic-qwen27b.service', 'state': 'active', 'ready': True,
              'model': 'qwen3.8-27b-p100', 'boot_enabled': True, 'controls_available': True}
    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange', return_value=status):
        result = client.get('/api/engines', headers=AUTH).json()
        assert result['items'][0]['model'] == 'qwen3.8-27b-p100'
        assert result['items'][0]['boot_enabled'] is True
    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange', side_effect=EngineUnavailable):
        result = client.get('/api/engines', headers=AUTH).json()['items'][0]
        assert result['state'] == 'unavailable' and result['model'] is None
        assert result['controls_available'] is False
        response = client.post('/api/engines/kmic/control', headers=ADMIN, json={'action': 'stop'})
        assert response.status_code == 503 and 'unknown' in response.json()['detail']


@pytest.mark.parametrize('target', ['strata', 'pxa', 'qr0n', 'scream', 'scream-512k', 'scream-two-agent', 'inkling', 'kmic-moe'])
def test_optional_engine_dispatches_only_authenticated_start_and_stop(tmp_path, target):
    status = {'id': target, 'unit': f'p100-lab-{target}.service', 'state': 'active',
              'ready': True, 'controls_available': True}

    def exchange(path, token, operation, *, engine='kmic'):
        assert token == IPC
        if engine != target:
            raise EngineUnavailable()
        return status.copy() if operation == 'status' else {
            'accepted': True, 'operation': operation, 'unit': status['unit']}

    with TestClient(create_app(settings(tmp_path))) as client, patch('lab.main.engine_exchange', side_effect=exchange) as agent:
        route = f'/api/engines/{target}/control'
        assert client.post(route, headers=AUTH, json={'action': 'stop'}).status_code == 403
        assert client.post(route, headers=ADMIN, json={'action': 'enable_boot'}).status_code == 403
        agent.assert_not_called()
        response = client.get('/api/engines', headers=AUTH)
        assert next(item for item in response.json()['items'] if item['id'] == target) == status
        for action in ('start', 'stop'):
            assert client.post(route, headers=ADMIN, json={'action': action}).status_code == 200
            agent.assert_called_with('/engine-agent/agent.sock', IPC, action, engine=target)
    with sqlite3.connect(tmp_path / 'lab.db') as connection:
        assert connection.execute('SELECT engine,action,outcome FROM audit').fetchall() == [
            (target, 'start', 'accepted'), (target, 'stop', 'accepted')]


@pytest.mark.parametrize('overrides', [{'engine_control_token': READ}, {'engine_control_token': IPC},
                                       {'engine_ipc_token': READ}, {'engine_ipc_token': ''}, {'engine_control_token': ''}])
def test_invalid_control_boundaries_prevent_startup(tmp_path, overrides):
    with pytest.raises(RuntimeError):
        with TestClient(create_app(settings(tmp_path, **overrides))):
            pass
