"""Optional non-root host agent for fixed operator-owned inference units.

No TCP listener, shell, arbitrary argv, downloads or root commands.
Installation and existing-engine changes are separate operator actions.
"""
import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import subprocess
import threading
from urllib.parse import urlsplit
import urllib.request
from .adapters import MANAGED_UNITS
from .managed_models import ManagedModels, STATE_FILE
from .kmic_profiles import FLASH

UNIT = MANAGED_UNITS['kmic']
OPERATIONS = {'status', 'start', 'stop', 'enable_boot', 'disable_boot'}


def serving_profile(profile):
    if (not isinstance(profile, dict) or set(profile) != {'alias', 'quantization', 'context', 'max_output', 'reasoning', 'vision'}
            or not isinstance(profile['alias'], str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,160}', profile['alias'])
            or not isinstance(profile['quantization'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', profile['quantization'])
            or type(profile['context']) is not int or not 512 <= profile['context'] <= 1_000_000
            or type(profile['max_output']) is not int or not 0 < profile['max_output'] < profile['context']
            or type(profile['reasoning']) is not bool or type(profile['vision']) is not bool):
        raise ValueError('Invalid fixed serving profile')
    return profile


def secret(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027:
        raise ValueError('Secret must be a non-public regular file')
    with path.open() as stream:
        value = stream.read(514).strip()
    if not 32 <= len(value) <= 512 or '\n' in value or '\r' in value:
        raise ValueError('Invalid secret')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Controller:
    def __init__(self, config, *, engine='kmic'):
        self.engine = engine
        self.unit = MANAGED_UNITS[engine]
        self.peers = {}
        expected = {'unit_file', 'unit_sha256', 'endpoint', 'upstream_token_file', 'ipc_token_file', 'model_label'}
        optional = {'managed_models', *MANAGED_UNITS.keys() - {'kmic'}} if engine == 'kmic' else {'serving_profile'}
        if not isinstance(config, dict) or set(config) - optional != expected:
            raise ValueError('Invalid engine configuration')
        self.unit_file = Path(config['unit_file'])
        if not self.unit_file.is_absolute() or self.unit_file.name != self.unit or not re.fullmatch(r'[a-f0-9]{64}', config['unit_sha256']):
            raise ValueError('Invalid pinned unit')
        self.digest = config['unit_sha256']
        parsed = urlsplit(config['endpoint'])
        address = ipaddress.IPv4Address(parsed.hostname)
        if (parsed.scheme != 'http' or not (address.is_private or address.is_loopback) or
                parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment or
                parsed.port is None or not 1024 <= parsed.port <= 65535):
            raise ValueError('Endpoint must be a fixed local IPv4 HTTP address')
        self.endpoint = config['endpoint'].rstrip('/')
        if any(not Path(config[key]).is_absolute() for key in ('upstream_token_file', 'ipc_token_file')):
            raise ValueError('Secret paths must be absolute')
        self.key = secret(Path(config['upstream_token_file']))
        self.ipc_key = secret(Path(config['ipc_token_file']))
        if hmac.compare_digest(self.key, self.ipc_key):
            raise ValueError('Use distinct upstream and IPC secrets')
        self.model_label = config['model_label']
        if not isinstance(self.model_label, str) or not 1 <= len(self.model_label) <= 160 or not self.model_label.isprintable():
            raise ValueError('Invalid model label')
        self.models = ManagedModels(config['managed_models']) if 'managed_models' in config else None
        self.profile = serving_profile(config['serving_profile']) if 'serving_profile' in config else None
        self.lock = threading.Lock()
        if engine == 'kmic':
            controllers = {'kmic': self}
            for name in MANAGED_UNITS.keys() & config.keys() - {'kmic'}:
                entry = config[name]
                if not isinstance(entry, dict) or 'ipc_token_file' in entry:
                    raise ValueError('Invalid optional engine configuration')
                controllers[name] = Controller({**entry, 'ipc_token_file': config['ipc_token_file']}, engine=name)
            for name, controller in controllers.items():
                controller.peers = {peer_id: peer for peer_id, peer in controllers.items() if peer_id != name}
                controller.lock = self.lock

    def trusted_unit(self):
        try:
            info = self.unit_file.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            return False
        contents = self.unit_file.read_bytes()
        if (self.models is not None and any(entry.get('runtime') in FLASH for entry in self.models.entries.values())
                and b'-m lab.kmic_launcher ' not in contents):
            return False
        return (hashlib.sha256(contents).hexdigest() == self.digest and
                (self.models is None or f'EnvironmentFile={STATE_FILE}'.encode() in contents.splitlines()))

    def run(self, arguments, timeout=4):
        # Fixed executable, unit and allowlisted arguments only; never shell=True.
        result = subprocess.run(['/usr/bin/systemctl', '--user', *arguments, self.unit],
                                capture_output=True, text=True, timeout=timeout,
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
                                     'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
                                     'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{os.getuid()}/bus'})
        if result.returncode or len(result.stdout) > 16384:
            raise RuntimeError('Service manager refused request')
        return result.stdout

    def upstream(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(self.endpoint + path, headers={'Authorization': 'Bearer ' + self.key})
        with opener.open(request, timeout=2) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError('Oversized upstream response')
        return json.loads(body)

    def status(self):
        properties = self.properties('ActiveState', 'UnitFileState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Job')
        trusted = self.matches_unit(properties)
        state = properties.get('ActiveState', 'unavailable')
        if state not in {'active', 'inactive', 'activating', 'deactivating', 'failed'}:
            state = 'unavailable'
        model = None
        ready = False
        if state == 'active':
            try:
                ready = self.engine == 'kmic' and self.upstream('/health').get('status') == 'ok'
                if ready or self.engine != 'kmic':
                    items = self.upstream('/v1/models').get('data', [])
                    if self.profile is not None:
                        items = [item for item in items if item.get('id') == self.profile['alias']]
                    if len(items) == 1 and isinstance(items[0].get('id'), str):
                        status = items[0].get('status')
                        if not isinstance(status, dict) or status.get('value') != 'unloaded':
                            model = items[0]['id'].replace('\\', '/').rsplit('/', 1)[-1][:160]
                    if self.engine != 'kmic':
                        ready = bool(model)
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                pass
        result = {'id': self.engine, 'unit': self.unit, 'state': state, 'ready': ready,
                'model': model, 'configured_model': self.model_label,
                'controls_available': trusted,
                'note': 'Live host service and authenticated inference-server status.' if trusted else 'Unit configuration changed; controls refused.'}
        if self.engine == 'kmic':
            result['boot_enabled'] = properties.get('UnitFileState') == 'enabled'
            result['boot_state'] = properties.get('UnitFileState', 'unknown')
        if trusted and self.profile is not None:
            result['serving_profile'] = self.profile
        if self.models is not None:
            result['catalog'] = self.models.public()
            result['can_stage'] = trusted and self.can_stage(properties)
            try:
                result['configured_selection'] = self.models.read() if trusted else None
            except (OSError, ValueError, KeyError, TypeError):
                result['configured_selection'] = None
            selection = result['configured_selection']
            result['configured_model'] = self.models.entries[selection['model_id']]['name'] if selection else None
        return result

    def properties(self, *names):
        args = ['show']
        for name in names:
            args.extend(('-p', name))
        return dict(line.split('=', 1) for line in self.run(args).splitlines() if '=' in line)

    def matches_unit(self, properties):
        return (self.trusted_unit() and properties.get('FragmentPath') == str(self.unit_file)
                and properties.get('DropInPaths') == ''
                and ((self.engine == 'kmic' and self.models is None) or properties.get('NeedDaemonReload') == 'no'))

    def perform(self, operation, selection=None):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Agent busy; no queued control')
        try:
            return self._perform(operation, selection)
        finally:
            self.lock.release()

    def _perform(self, operation, selection):
        if operation not in OPERATIONS and operation != 'stage_selection':
            raise ValueError('Operation not permitted')
        if self.engine != 'kmic' and operation not in {'status', 'start', 'stop'}:
            raise ValueError('Operation not permitted for this engine')
        if operation == 'status':
            return self.status()
        # Recheck identity immediately before every mutation.
        names = ['FragmentPath', 'DropInPaths', 'ActiveState']
        if self.models is not None or self.engine != 'kmic':
            names.extend(('NeedDaemonReload', 'Job'))
        properties = self.properties(*names)
        if not self.matches_unit(properties):
            raise RuntimeError('Unit identity changed; controls refused')
        if operation == 'stage_selection':
            if (self.models is None or not self.can_stage(properties) or not isinstance(selection, dict)
                    or set(selection) != {'model_id', 'settings', 'confirmed'} or selection['confirmed'] is not True):
                raise ValueError('Selection requires a stopped managed unit and confirmation')
            configured = self.models.stage(selection['model_id'], selection['settings'])
            return {'accepted': True, 'operation': operation, 'unit': self.unit, 'configured_selection': configured}
        if operation == 'start' and properties.get('ActiveState') not in ('active', 'activating'):
            if any(not self.can_stage(peer.properties('ActiveState', 'NeedDaemonReload', 'Job'))
                   for peer in self.peers.values()):
                raise RuntimeError('Stop the other managed engine before starting this engine')
            if self.engine != 'kmic' and not self.can_stage(properties):
                raise RuntimeError('The engine is not ready for a start request')
            if self.models is not None:
                if not self.can_stage(properties):
                    raise RuntimeError('The managed unit is not ready for a start request')
                configured = self.models.read()
                self.models.selection(**configured, require_available=True)
            self.start_preflight()
        command = {'start': ['--no-block', 'start'], 'stop': ['--no-block', 'stop'],
                   'enable_boot': ['enable'], 'disable_boot': ['disable']}[operation]
        self.run(command)
        return {'accepted': True, 'operation': operation, 'unit': self.unit}

    @staticmethod
    def can_stage(properties):
        return (properties.get('ActiveState') in ('inactive', 'failed')
                and properties.get('NeedDaemonReload') == 'no' and properties.get('Job') in ('', '0'))

    def start_preflight(self):
        result = subprocess.run(['/usr/bin/nvidia-smi', '--query-gpu=memory.free,utilization.gpu,temperature.gpu',
                                 '--format=csv,noheader,nounits'], text=True, capture_output=True, timeout=3)
        if result.returncode or len(result.stdout) > 1024:
            raise RuntimeError('GPU preflight unavailable')
        rows = [[float(value.strip()) for value in row.split(',')] for row in result.stdout.splitlines()]
        if (len(rows) != 2 or any(len(row) != 3 or any(not math.isfinite(v) for v in row) for row in rows) or
                any(free < 14000 or load >= 10 or temp >= 75 for free, load, temp in rows)):
            raise RuntimeError('Both GPUs must be available before starting the engine')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw = self.rfile.readline(4097)
            if len(raw) > 4096 or not raw.endswith(b'\n'):
                raise ValueError('Invalid request size')
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError('Invalid request')
            fields = {'token', 'operation'}
            controller = self.server.controller
            if 'engine' in request:
                fields.add('engine')
                if not isinstance(request['engine'], str) or request['engine'] not in controller.peers:
                    raise ValueError('Engine is not configured')
                controller = controller.peers[request['engine']]
            if request.get('operation') == 'stage_selection':
                fields.add('selection')
            if set(request) != fields:
                raise ValueError('Invalid request')
            token = request['token']
            if not isinstance(token, str) or len(token) > 512 or not hmac.compare_digest(token.encode(), self.server.controller.ipc_key.encode()):
                raise ValueError('Invalid credential')
            if request['operation'] == 'stage_selection':
                result = controller.perform(request['operation'], request['selection'])
            else:
                result = controller.perform(request['operation'])
            reply = {'ok': True, 'result': result}
        except Exception:
            # Never return subprocess output, argv, upstream payloads or credentials.
            reply = {'ok': False, 'error': 'Engine request unavailable or refused'}
        try:
            self.wfile.write(json.dumps(reply, allow_nan=False, ensure_ascii=False).encode() + b'\n')
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, path, controller):
        self.controller = controller
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(str(path), Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


@contextmanager
def socket_path(path):
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise RuntimeError('Agent runtime directory must be private and owned by the agent')
    descriptor = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        if os.fstat(lock.fileno()).st_uid != os.getuid():
            raise RuntimeError('Unexpected agent lock owner')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('Unexpected existing socket path')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.5)
                try:
                    probe.connect(str(path))
                except OSError as error:
                    if error.errno != errno.ECONNREFUSED:
                        raise RuntimeError('Cannot establish that old socket is stale') from None
                else:
                    raise RuntimeError('Another agent is listening')
            path.unlink()
        yield
        if path.exists() and stat.S_ISSOCK(path.lstat().st_mode) and path.lstat().st_uid == os.getuid():
            path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--socket', type=Path, default=Path('/run/p100-lab-engine-agent/agent.sock'))
    args = parser.parse_args()
    if os.getuid() == 0:
        raise SystemExit('Run as the existing non-root engine account')
    info = args.config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit('Agent configuration must be root-owned and not writable by the engine account')
    controller = Controller(json.loads(args.config.read_text()))
    if not controller.trusted_unit():
        raise SystemExit('Pinned unit verification failed')
    os.umask(0o007)
    with socket_path(args.socket):
        with Server(args.socket, controller) as server:
            os.chmod(args.socket, 0o660)
            server.serve_forever(poll_interval=.5)


if __name__ == '__main__':
    main()
