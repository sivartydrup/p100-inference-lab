import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

if os.name != 'posix':
    pytest.skip('The inference gateway runs on the Linux host', allow_module_level=True)

from lab.inference_gateway import Configuration, create_app


def test_two_agent_alias_has_separate_limits_and_never_routes_to_512k(tmp_path):
    from lab.scream_profiles import ALIAS
    keys={'gateway':'g'*48,'scream-512k':'s'*48,'scream-two-agent':'t'*48}
    for name,value in keys.items():
        p=tmp_path/name;p.write_text(value);p.chmod(0o600)
    old='scream-flash-next-iq3-s-512k'
    config=Configuration.model_validate(dict(key_file=tmp_path/'gateway',
        upstreams={name:dict(url=f'http://127.0.0.1:{port}',key_file=tmp_path/name)
                   for name,port in [('scream-512k',8087),('scream-two-agent',8088)]},
        models={old:dict(engine='scream-512k',name='512K',context=524288,max_output=8192),
                ALIAS:dict(engine='scream-two-agent',name='Two agents queued',context=262144,max_output=8192)}))
    active=ALIAS
    payload=dict(model=ALIAS,messages=[dict(role='user',content='Implement a queue')],max_tokens=8192,
                 chat_template_kwargs=dict(enable_thinking=False),stream=True)
    def upstream(request):
        engine='scream-512k' if request.url.port==8087 else 'scream-two-agent'
        alias=old if engine=='scream-512k' else ALIAS
        assert request.headers['authorization']=='Bearer '+keys[engine]
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':alias}] if alias==active else []})
        assert alias==ALIAS and json.loads(request.content)==payload
        return httpx.Response(200,content=b'data: [DONE]\n\n',headers={'Content-Type':'text/event-stream'})
    with TestClient(create_app(config,transport=httpx.MockTransport(upstream))) as client:
        auth={'Authorization':'Bearer '+keys['gateway']}
        assert client.get('/v1/models').status_code==401
        rows={r['id']:r for r in client.get('/v1/models',headers=auth).json()['data']}
        assert rows[ALIAS]['available'] and rows[ALIAS]['max_model_len']==262144
        assert rows[ALIAS]['limits']['max_output_tokens']==8192
        assert rows[old]['max_model_len']==524288 and not rows[old]['available']
        assert client.post('/v1/chat/completions',headers=auth,json={'model':old}).status_code==503
        assert client.post('/v1/chat/completions',headers=auth,json=payload).text=='data: [DONE]\n\n'
        active=old
        assert client.post('/v1/chat/completions',headers=auth,json=payload).status_code==503


def test_discovery_streaming_and_offline_routing(tmp_path):
    key = 'g' * 64
    upstream_key = 'u' * 64
    for name, value in [('gateway', key), ('backend', upstream_key)]:
        path = tmp_path / name
        path.write_text(value)
        path.chmod(0o600)
    config = Configuration.model_validate({
        'key_file': tmp_path / 'gateway',
        'upstreams': {engine: {'url': f'http://127.0.0.1:{port}', 'key_file': tmp_path / 'backend'}
                      for engine, port in [('strata', 8081), ('pxa', 8082), ('scream', 8085)]},
        'models': {'strata-model': {'engine': 'strata', 'name': 'Strata', 'context': 262144, 'max_output': 4096},
                   'pxa-model': {'engine': 'pxa', 'name': 'PXA', 'context': 32768, 'max_output': 1024},
                   'scream-model': {'engine': 'scream', 'name': 'Scream', 'context': 262144,
                                    'max_output': 4096, 'vision': True}},
    })
    payload = {'max_tokens': 8192, 'model': 'strata-model', 'messages': [{'role': 'user', 'content': 'Check this file'}],
               'tools': [{'type': 'function', 'function': {'name': 'read'}}], 'stream': True}
    event = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1"}]}}]}\n\n'

    def upstream(request):
        assert request.headers['authorization'] == 'Bearer ' + upstream_key
        if request.url.port == 8082:
            raise httpx.ConnectError('offline', request=request)
        if request.url.port == 8085:
            return httpx.Response(200, json={'data': [{'id': 'scream-model', 'status': {'value': 'unloaded'}}]})
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'strata-model'}]})
        assert request.url.path == '/v1/chat/completions'
        assert json.loads(request.content) == payload
        return httpx.Response(200, content=event + b'data: [DONE]\n\n',
                              headers={'Content-Type': 'text/event-stream'})

    with TestClient(create_app(config, transport=httpx.MockTransport(upstream))) as client:
        assert client.get('/v1/models').status_code == 401
        headers = {'Authorization': 'Bearer ' + key}
        models = client.get('/v1/models', headers=headers).json()['data']
        assert [(row['id'], row['available'], row['max_model_len']) for row in models] == [
            ('strata-model', True, 262144), ('pxa-model', False, 32768), ('scream-model', False, 262144)]
        assert models[0]['limits']['max_output_tokens'] == 4096
        assert models[-1]['architecture']['input_modalities'] == ['text', 'image']
        assert client.post('/v1/chat/completions', headers=headers, json={'model': 'pxa-model'}).status_code == 503
        assert client.post('/v1/chat/completions', headers=headers, json={'model': 'scream-model'}).status_code == 503
        assert client.post('/v1/chat/completions', headers=headers, json={'model': 'other'}).status_code == 404
        with client.stream('POST', '/v1/chat/completions', headers=headers, json=payload) as response:
            assert response.headers['Content-Type'] == 'text/event-stream'
            assert b''.join(response.iter_bytes()) == event + b'data: [DONE]\n\n'
