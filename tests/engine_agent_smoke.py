"""Disposable HTTP-to-Unix-agent acceptance. Engine actions are synthetic."""
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid

FIXTURE = r'''
import os
from pathlib import Path
from lab.engine_agent import Server,UNIT
p=Path('/data')
for name,variable in [('ipc','IPC'),('control','CONTROL')]:
 (p/name).write_text(os.environ[variable]); (p/name).chmod(0o600)
class Fixture:
 ipc_key=os.environ['IPC']
 state='active'
 boot=True
 def perform(self,operation):
  if operation=='status':
   return {'id':'kmic','unit':UNIT,'state':self.state,'ready':self.state=='active',
    'model':'synthetic-qwen27b' if self.state=='active' else None,
    'configured_model':'Synthetic acceptance fixture','boot_enabled':self.boot,
    'controls_available':True,'note':'Synthetic host-agent fixture'}
  if operation not in ('start','stop','enable_boot','disable_boot'): raise ValueError()
  if operation in ('start','stop'): self.state='active' if operation=='start' else 'inactive'
  else: self.boot=operation=='enable_boot'
  return {'accepted':True,'operation':operation,'unit':UNIT}
with Server(p/'agent.sock',Fixture()) as server: server.serve_forever()
'''


def main():
    name = 'p100-engine-smoke-' + uuid.uuid4().hex[:10]
    agent, volume = name + '-agent', name + '-ipc'
    image = os.getenv('LAB_TEST_IMAGE', 'p100-lab:phase1')
    read, control, ipc = [secrets.token_urlsafe(48) for _ in range(3)]
    env = {**os.environ, 'LAB_API_TOKEN': read, 'CONTROL': control, 'IPC': ipc}
    def docker(*args):
        return subprocess.check_output(['docker', *args], text=True, env=env).strip()
    try:
        docker('run', '-d', '--name', agent, '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
               '--network', 'none', '--no-healthcheck', '--tmpfs', '/tmp', '-v', volume+':/data',
               '-e', 'IPC', '-e', 'CONTROL', '--entrypoint', 'python', image, '-c', FIXTURE)
        for _ in range(60):
            if subprocess.run(['docker', 'exec', agent, 'test', '-S', '/data/agent.sock'], capture_output=True).returncode == 0:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Fixture socket not ready')
        docker('run', '-d', '--name', name, '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
               '--tmpfs', '/tmp', '--tmpfs', '/data:uid=10001,gid=10001,mode=0700', '-v', volume+':/engine-agent:ro',
               '-p', '127.0.0.1::8000', '-e', 'LAB_API_TOKEN', '-e', 'LAB_TELEMETRY=mock',
               '-e', 'LAB_ENGINE_AGENT_SOCKET=/engine-agent/agent.sock',
               '-e', 'LAB_ENGINE_AGENT_TOKEN_FILE=/engine-agent/ipc',
               '-e', 'LAB_ENGINE_CONTROL_TOKEN_FILE=/engine-agent/control', '-e', 'LAB_ENABLE_ENGINE_CONTROL=true', image)
        port = docker('port', name, '8000/tcp').rsplit(':', 1)[1]
        base = 'http://127.0.0.1:'+port
        for _ in range(60):
            try:
                urllib.request.urlopen(base+'/healthz', timeout=2).close()
                break
            except OSError:
                time.sleep(.2)
        def request(path, body=None, admin=False, authenticated=True):
            headers = {'Authorization': 'Bearer '+read} if authenticated else {}
            if admin:
                headers['X-Lab-Engine-Token'] = control
            if body is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base+path, headers=headers,
                                         data=json.dumps(body).encode() if body is not None else None)
            try:
                response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)
        assert request('/api/engines', authenticated=False)[0] == 401
        status, data = request('/api/engines')
        assert status == 200 and data['items'][0]['model'] == 'synthetic-qwen27b'
        assert request('/api/engines/kmic/control', {'action': 'stop'})[0] == 403
        assert request('/api/engines/kmic/control', {'action': 'start', 'unit': 'sshd'}, admin=True)[0] == 422
        for action, state in [('disable_boot', False), ('enable_boot', True)]:
            assert request('/api/engines/kmic/control', {'action': action}, admin=True)[0] == 200
            assert request('/api/engines')[1]['items'][0]['boot_enabled'] is state
        assert request('/api/engines/kmic/control', {'action': 'stop'}, admin=True)[0] == 200
        assert request('/api/engines')[1]['items'][0]['model'] is None
        assert request('/api/engines/kmic/control', {'action': 'start'}, admin=True)[0] == 200
        assert request('/api/engines')[1]['items'][0]['ready'] is True
        assert request('/api/engines/qr0n/control', {'action': 'start'}, admin=True)[0] == 403
        print('Engine agent acceptance passed: HTTP/Unix transport, dual authorization, fixed schema, synthetic status/start/stop/boot changes. No host bus, GPU or native engine accessed.')
    finally:
        for container in (name, agent):
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)


if __name__ == '__main__':
    main()
