"""Optional root broker for exactly IT8620 PWM1/PWM2 on the reviewed X99 board.

No engine actions, shell, driver loading or writable paths from requests.
"""
import argparse
from contextlib import contextmanager
import errno
import hmac
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import stat
import subprocess
import threading
import uuid

if os.name == 'posix':
    import fcntl

DEVICE = Path('/sys/devices/platform/it87.2624')
MODES = {'full': 0, 'manual': 1, 'auto': 2}


class Conflict(ValueError):
    pass


class Hardware:
    def __init__(self, device=DEVICE, dmi=Path('/sys/class/dmi/id')):
        self.device, self.dmi = device, dmi

    def directory(self):
        if ((self.dmi / 'board_name').read_text().strip() != 'X99-SLI-CF' or
                (self.dmi / 'board_vendor').read_text().strip() != 'Gigabyte Technology Co., Ltd.'):
            raise ValueError('Unapproved motherboard')
        entries = list((self.device / 'hwmon').glob('hwmon[0-9]*'))
        if len(entries) != 1:
            raise ValueError('Expected one IT8620 monitor')
        directory = entries[0].resolve(strict=True)
        if directory.parent != self.device.resolve(strict=True) / 'hwmon' or (directory / 'name').read_text().strip() != 'it8620':
            raise ValueError('Controller identity changed')
        return directory

    def path(self, channel, attribute):
        if channel not in ('fan1', 'fan2') or attribute not in ('enable', 'duty', 'rpm'):
            raise ValueError('Unapproved fan attribute')
        number = channel[-1]
        name = {'enable': f'pwm{number}_enable', 'duty': f'pwm{number}', 'rpm': f'fan{number}_input'}[attribute]
        return self.directory() / name

    def read(self, channel, attribute):
        path = self.path(channel, attribute)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r', encoding='ascii') as stream:
            value = stream.read(16).strip()
        if not re.fullmatch(r'[0-9]+', value):
            raise ValueError('Invalid fan reading')
        result = int(value)
        maximum = {'enable': 2, 'duty': 255, 'rpm': 1_000_000}[attribute]
        if not 0 <= result <= maximum:
            raise ValueError('Fan reading out of range')
        return result

    def write(self, channel, attribute, value):
        if attribute not in ('enable', 'duty') or type(value) is not int or not 0 <= value <= (2 if attribute == 'enable' else 255):
            raise ValueError('Unapproved fan write')
        fd = os.open(self.path(channel, attribute), os.O_WRONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(str(value) + '\n')

    def snapshot(self):
        return {channel: {'mode': self.read(channel, 'enable'), 'pwm': self.read(channel, 'duty'),
                          'rpm': self.read(channel, 'rpm')} for channel in ('fan1', 'fan2')}

    def full(self):
        errors = []
        for channel in ('fan1', 'fan2'):
            try:
                if self.read(channel, 'enable') != 0:
                    self.write(channel, 'enable', 0)
                if self.read(channel, 'enable') != 0:
                    raise ValueError('Full-speed verification failed')
            except (OSError, ValueError): errors.append(channel)
        if errors:
            raise RuntimeError('Cannot confirm full-speed fallback')


def gpu_temperatures():
    result = subprocess.run(['/usr/bin/nvidia-smi', '--query-gpu=name,temperature.gpu',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > 1024:
        raise ValueError('GPU monitoring unavailable')
    rows = [line.split(',') for line in result.stdout.splitlines()]
    if len(rows) != 2 or any(len(row) != 2 or row[0].strip() != 'Tesla P100-PCIE-16GB' for row in rows):
        raise ValueError('Expected both P100 GPUs')
    values = [float(row[1]) for row in rows]
    if any(not math.isfinite(value) or not 0 <= value < 75 for value in values):
        raise ValueError('75C cutoff or invalid GPU temperature')
    return values


class Controller:
    def __init__(self, hardware, temperatures=gpu_temperatures):
        self.hardware, self.temperatures = hardware, temperatures
        self.lock = threading.Lock()
        self.epoch, self.revision = uuid.uuid4().hex, 0
        self.healthy, self.fault = False, None
        self.stalls = {'fan1': 0, 'fan2': 0}

    @property
    def version(self):
        return f'{self.epoch}:{self.revision}'

    def guard(self):
        try:
            if self.fault and not self.healthy:
                self.hardware.full()
            self.temperatures()
            readings = self.hardware.snapshot()
            for channel, reading in readings.items():
                self.stalls[channel] = self.stalls[channel] + 1 if reading['mode'] == 1 and reading['pwm'] > 0 and reading['rpm'] == 0 else 0
                if self.stalls[channel] >= 3:
                    raise ValueError('Manual fan rotation lost')
            self.healthy = True
        except Exception:
            self.healthy = False
            self.fault = 'Full-speed fallback: GPU monitoring, temperature or fan feedback check failed.'
            self.hardware.full()
            self.revision += 1

    def status(self):
        return {'available': self.healthy, 'version': self.version, 'safety_override': self.fault,
                'cutoff_c': 75, 'controller': 'it8620', 'device': 'it87.2624',
                'channels': self.hardware.snapshot()}

    def perform(self, operation, change=None):
        if not self.lock.acquire(blocking=False):
            raise Conflict('Fan controller busy; refresh status')
        try:
            if operation == 'status':
                return self.status()
            if operation != 'set' or not isinstance(change, dict):
                raise ValueError('Operation not permitted')
            if set(change) not in ({'channel', 'mode', 'version', 'confirmed'}, {'channel', 'pwm', 'version', 'confirmed'}):
                raise ValueError('Unexpected control fields')
            channel = change['channel']
            if channel not in ('fan1', 'fan2') or change['confirmed'] is not True:
                raise ValueError('Unapproved channel or unconfirmed change')
            if change['version'] != self.version:
                raise Conflict('Control state changed; refresh status')
            if 'mode' in change and (not isinstance(change['mode'], str) or change['mode'] not in MODES):
                raise ValueError('Unapproved mode')
            if 'pwm' in change and (type(change['pwm']) is not int or not 0 <= change['pwm'] <= 255):
                raise ValueError('Output must be an integer from 0 to 255')
            self.guard()
            if not self.healthy and change.get('mode') != 'full':
                raise Conflict('Safety fallback active; refresh status')
            if 'pwm' in change and self.hardware.read(channel, 'enable') != 1:
                raise Conflict('Output editing requires Manual mode')
            try:
                if 'mode' in change:
                    # Enter Manual at the freshly observed output, never an old saved duty.
                    current = self.hardware.read(channel, 'duty')
                    self.hardware.write(channel, 'enable', MODES[change['mode']])
                    if change['mode'] == 'manual':
                        self.hardware.write(channel, 'duty', current)
                    if self.hardware.read(channel, 'enable') != MODES[change['mode']]:
                        raise ValueError('Mode readback mismatch')
                else:
                    self.hardware.write(channel, 'duty', change['pwm'])
                    if self.hardware.read(channel, 'duty') != change['pwm']:
                        raise ValueError('Output readback mismatch')
            except Exception:
                self.healthy = False
                self.fault = 'Full-speed fallback: fan write or readback failed.'
                self.hardware.full()
                self.revision += 1
                raise
            self.revision += 1
            if self.healthy: self.fault = None
            return self.status()
        finally:
            self.lock.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw = self.rfile.readline(2049)
            if len(raw) > 2048 or not raw.endswith(b'\n'):
                raise ValueError('Invalid request')
            request = json.loads(raw)
            operation = request.get('operation')
            fields = {'token', 'operation'} | ({'change'} if operation == 'set' else set())
            if set(request) != fields or not isinstance(request['token'], str) or not hmac.compare_digest(request['token'].encode(), self.server.key.encode()):
                raise ValueError('Invalid authentication or fields')
            result = self.server.controller.perform(operation, request.get('change'))
            reply = {'ok': True, 'result': result}
        except Conflict:
            reply = {'ok': False, 'conflict': True}
        except Exception:
            reply = {'ok': False}
        self.wfile.write(json.dumps(reply, allow_nan=False).encode() + b'\n')


class Server(socketserver.ThreadingMixIn, getattr(socketserver, 'UnixStreamServer', socketserver.TCPServer)):
    daemon_threads = False
    def __init__(self, path, controller, key):
        if os.name != 'posix': raise RuntimeError('Unix sockets are required')
        self.controller, self.key = controller, key
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(str(path), Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try: super().process_request_thread(request, address)
        finally: self.slots.release()


def notify(message):
    address = os.getenv('NOTIFY_SOCKET')
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.connect('\0' + address[1:] if address.startswith('@') else address)
            connection.sendall(message.encode())


@contextmanager
def socket_path(path):
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise ValueError('Expected private service-owned runtime directory')
    descriptor = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        if os.fstat(lock.fileno()).st_uid != os.getuid():
            raise ValueError('Unexpected lock owner')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('Unexpected socket path')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.5)
                try: probe.connect(str(path))
                except OSError as error:
                    if error.errno != errno.ECONNREFUSED: raise
                else: raise RuntimeError('Fan broker already running')
            path.unlink()
        try: yield
        finally:
            if path.exists() and stat.S_ISSOCK(path.lstat().st_mode) and path.lstat().st_uid == os.getuid(): path.unlink()


def main():
    if os.name != 'posix':
        raise SystemExit('The fan broker is Ubuntu-only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--failsafe', action='store_true')
    actions.add_argument('--restore-auto', action='store_true', help='Operator rollback only, after stopping the broker')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Install this isolated host service as root')
    hardware = Hardware()
    if args.failsafe:
        hardware.full()
        return
    if args.restore_auto:
        if max(gpu_temperatures()) >= 60:
            raise ValueError('Restore Auto only with both GPUs below 60C')
        try:
            for channel in ('fan1', 'fan2'):
                hardware.write(channel, 'enable', 2)
                if hardware.read(channel, 'enable') != 2: raise ValueError('Automatic mode not confirmed')
        except Exception:
            hardware.full()
            raise
        return
    if args.token_file is None:
        raise SystemExit('An IPC token file is required')
    info = args.token_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o027:
        raise SystemExit('Expected a sealed non-public IPC secret')
    key = args.token_file.read_text().strip()
    if not 32 <= len(key) <= 128 or not key.isascii() or not key.isprintable():
        raise SystemExit('Invalid IPC secret')
    controller = Controller(hardware)
    if any(item['mode'] == 1 for item in hardware.snapshot().values()):
        hardware.full()  # No orphaned manual outputs across broker restarts.
    controller.guard()
    path = Path('/run/p100-lab-fans/agent.sock')
    with socket_path(path):
        with Server(path, controller, key) as server:
            os.chmod(path, 0o660)
            os.chown(path, 0, 10001)
            stopped = threading.Event()
            def stop(*_): stopped.set()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            notify('READY=1')
            try:
                while not stopped.wait(2):
                    with controller.lock: controller.guard()
                    notify('WATCHDOG=1')
            finally:
                server.shutdown()
                server.server_close()
                with controller.lock: hardware.full()


if __name__ == '__main__':
    main()
