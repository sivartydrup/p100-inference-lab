"""Administrator-started system units. Never called by the HTTP application."""
import math
import os
import time
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .admin_security import preset_artifacts
from .native_config import NativeError
from .native_process import SystemdSupervisor, capture


class AdminSystemdSupervisor(SystemdSupervisor):
    def __init__(self, user, hardware):
        if os.name != 'posix' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
            raise NativeError('root_linux_required')
        import pwd
        account = pwd.getpwnam(user)
        if account.pw_uid == 0 or account.pw_gid == 0:
            raise NativeError('non_root_payload_required')
        self.user_id, self.group_id = account.pw_uid, account.pw_gid
        self.hardware = hardware
        self.env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}

    def control(self, *args):
        return capture(['/usr/bin/systemctl', '--no-ask-password', *args], self.env,
                       time.monotonic() + 5, limit=65536)

    def command(self, preset, unit, runtime_seconds, gate):
        argv = super().command(preset, unit, runtime_seconds, gate)
        argv.remove('--user')
        position = argv.index('--')
        argv[position:position] = [f'--property=User={self.user_id}', f'--property=Group={self.group_id}',
                                  '--property=CapabilityBoundingSet=']
        argv.insert(argv.index('--environment'), '--require-empty-capabilities')
        return argv

    def attest(self, unit, preset, remaining):
        if not super().attest(unit, preset, remaining):
            return False
        result = self.control('show', unit, '--property=User', '--property=Group', '--property=CapabilityBoundingSet')
        values = dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)
        if result.returncode or values != {'User': str(self.user_id), 'Group': str(self.group_id), 'CapabilityBoundingSet': ''}:
            raise NativeError('non_root_payload_not_confirmed')
        return True

    def run(self, preset, unit, deadline, check):
        preset_artifacts(preset)
        if preset.max_temperature_c > 75:
            raise NativeError('admin_temperature_policy_required')
        last = -math.inf
        def monitored_check():
            nonlocal last
            check()
            if time.monotonic() - last < .5:
                return
            rows = self.hardware.query('uuid,temperature.gpu,power.limit,enforced.power.limit', 'gpu', check)
            selected = [row for row in rows if row and row[0].strip() == preset.gpu_uuid]
            if len(selected) != 1 or len(selected[0]) != 4:
                raise NativeError('admin_gpu_policy_unavailable')
            try:
                temperature, requested, enforced = map(float, selected[0][1:])
                if not all(math.isfinite(value) for value in (temperature, requested, enforced)):
                    raise ValueError
            except (TypeError, ValueError):
                raise NativeError('admin_gpu_policy_unavailable') from None
            if not 0 <= temperature < preset.max_temperature_c:
                raise NativeError('temperature_cutoff')
            if requested != 150 or enforced != 150:
                raise NativeError('admin_150w_policy_required')
            last = time.monotonic()
        return super().run(preset, unit, deadline, monitored_check)

    @contextmanager
    def gate_context(self):
        with tempfile.TemporaryDirectory(prefix='p100-lab-admin-gate-', dir='/run') as directory:
            os.chown(directory, 0, self.group_id)
            os.chmod(directory, 0o710)
            yield directory

    def approve_gate(self, gate):
        temporary = Path(gate).with_name('approval-new')
        with temporary.open('xb') as stream:
            stream.write(b'approved\n')
            stream.flush()
            os.fchown(stream.fileno(), 0, self.group_id)
            os.fchmod(stream.fileno(), 0o640)
        os.replace(temporary, gate)
