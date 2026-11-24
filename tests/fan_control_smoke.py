"""Disposable HTTP-to-root-broker acceptance using synthetic hardware only."""
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
from lab.fan_agent import Controller,Server
root=Path('/broker');os.chown(root,0,10001);os.chmod(root,0o750)
for name,variable in [('ipc','FAN_IPC'),('control','CONTROL')]:
 p=root/name;p.write_text(os.environ[variable]);os.chown(p,0,10001);os.chmod(p,0o640)
class Hardware:
 def __init__(self):self.values={c:{'mode':2,'pwm':70,'rpm':4000} for c in ('fan1','fan2')}
 def read(self,c,a):return self.values[c][{'enable':'mode','duty':'pwm','rpm':'rpm'}[a]]
 def write(self,c,a,v):
  assert c in ('fan1','fan2') and a in ('enable','duty')
  self.values[c][{'enable':'mode','duty':'pwm'}[a]]=v
 def snapshot(self):return {k:dict(v) for k,v in self.values.items()}
 def full(self):
  for c in self.values:self.write(c,'enable',0)
def temperatures():
 if (root/'fault').exists():raise ValueError('synthetic monitor failure')
 return [40,42]
control=Controller(Hardware(),temperatures);control.guard()
with Server(root/'agent.sock',control,os.environ['FAN_IPC']) as server:
 os.chown(root/'agent.sock',0,10001);os.chmod(root/'agent.sock',0o660)
 server.serve_forever()
'''


def main():
    name = 'p100-fan-smoke-' + uuid.uuid4().hex[:10]
    broker, volume = name + '-broker', name + '-ipc'
    image = os.getenv('LAB_TEST_IMAGE', 'p100-lab:phase1')
    read, control, ipc = [secrets.token_urlsafe(48) for _ in range(3)]
    env = {**os.environ, 'LAB_API_TOKEN': read, 'CONTROL': control, 'FAN_IPC': ipc}
    def docker(*args):
        return subprocess.check_output(['docker', *args], text=True, env=env).strip()
    try:
        docker('run', '-d', '--name', broker, '--user', '0:10001', '--read-only', '--network', 'none',
               '--cap-drop=ALL', '--cap-add=CHOWN', '--security-opt=no-new-privileges:true',
               '--no-healthcheck', '--tmpfs', '/tmp', '-v', volume + ':/broker', '-e', 'CONTROL',
               '-e', 'FAN_IPC', '--entrypoint', 'python', image, '-c', FIXTURE)
        for _ in range(60):
            if subprocess.run(['docker', 'exec', broker, 'test', '-S', '/broker/agent.sock'], capture_output=True).returncode == 0: break
            time.sleep(.2)
        else: raise RuntimeError('Synthetic broker socket not ready')
        docker('run', '-d', '--name', name, '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
               '--tmpfs', '/tmp', '--tmpfs', '/data:uid=10001,gid=10001,mode=0700',
               '-v', volume + ':/fan-agent:ro', '-p', '127.0.0.1::8000',
               '-e', 'LAB_API_TOKEN', '-e', 'LAB_TELEMETRY=mock', '-e', 'LAB_ENABLE_FAN_CONTROL=true',
               '-e', 'LAB_FAN_AGENT_SOCKET=/fan-agent/agent.sock',
               '-e', 'LAB_FAN_AGENT_TOKEN_FILE=/fan-agent/ipc',
               '-e', 'LAB_ENGINE_CONTROL_TOKEN_FILE=/fan-agent/control', image)
        port = docker('port', name, '8000/tcp').rsplit(':', 1)[1]
        base = 'http://127.0.0.1:' + port
        for _ in range(60):
            try:
                urllib.request.urlopen(base + '/healthz', timeout=2).close();break
            except OSError: time.sleep(.2)
        def request(body=None, admin=False, authenticated=True):
            headers = {'Authorization': 'Bearer ' + read} if authenticated else {}
            if admin: headers['X-Lab-Engine-Token'] = control
            if body is not None: headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base + '/api/fan-controls', headers=headers,
                data=json.dumps(body).encode() if body is not None else None)
            try: response = urllib.request.urlopen(req, timeout=10)
            except urllib.error.HTTPError as error: response = error
            with response: return response.status, json.load(response)
        assert request(authenticated=False)[0] == 401
        status, current = request()
        assert status == 200 and current['enabled'] and current['available']
        body = {'channel': 'fan1', 'mode': 'manual', 'version': current['version'], 'confirmed': True}
        assert request(body)[0] == 403
        for invalid in ({**body, 'channel': 'fan4'}, {**body, 'path': '/sys/evil'}, {**body, 'pwm': 70}):
            assert request(invalid, admin=True)[0] == 422
        status, manual = request(body, admin=True)
        assert status == 200 and manual['channels']['fan1']['mode'] == 1 and manual['channels']['fan1']['pwm'] == 70
        duty = {'channel': 'fan1', 'pwm': 128, 'version': manual['version'], 'confirmed': True}
        status, changed = request(duty, admin=True)
        assert status == 200 and changed['channels']['fan1']['pwm'] == 128
        assert request(duty, admin=True)[0] == 409
        status, automatic = request({**body, 'mode': 'auto', 'version': changed['version']}, admin=True)
        assert status == 200 and automatic['channels']['fan1']['mode'] == 2
        assert request({**duty, 'version': automatic['version']}, admin=True)[0] == 409
        assert request()[1]['channels']['fan1']['mode'] == 2
        status, full = request({**body, 'mode': 'full', 'version': automatic['version']}, admin=True)
        assert status == 200 and full['channels']['fan1']['mode'] == 0
        assert full['channels']['fan2']['mode'] == 2
        docker('exec', broker, 'python', '-c', "open('/broker/fault','w').close()")
        assert request({**body, 'version': full['version']}, admin=True)[0] == 409
        fallback = request()[1]
        assert not fallback['available'] and fallback['safety_override']
        assert all(item['mode'] == 0 for item in fallback['channels'].values())
        info = json.loads(docker('inspect', name))[0]
        assert info['Config']['User'] == '10001:10001'
        assert next(m for m in info['Mounts'] if m['Destination'] == '/fan-agent')['RW'] is False
        print('Fan acceptance passed: real HTTP/Unix transport, separate authorization, scoped modes/duty, stale conflict, manual-only writes and synthetic fail-full. No hardware accessed.')
    finally:
        for container in (name, broker): subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)


if __name__ == '__main__': main()
