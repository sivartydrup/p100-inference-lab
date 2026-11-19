"""Manual Ubuntu acceptance: harmless CPU program, no model, CUDA or GPU access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

from lab.native_process import SystemdSupervisor, unit_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path, help='New private evidence directory')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() == 0:
        raise SystemExit('Use the intended non-root Ubuntu worker account')
    supervisor = SystemdSupervisor()
    supervisor.available()
    if supervisor.listed_units():
        raise SystemExit('Existing benchmark units: refuse to interfere')
    root = args.directory.resolve()
    root.mkdir(mode=0o700, exist_ok=False)
    source, binary = root / 'probe.c', root / 'llama-bench'
    source.write_text('#include <stdio.h>\nint main(void){puts("ISOLATED_CPU_PROBE_EXECUTED");return 0;}\n')
    subprocess.run(['/usr/bin/cc', '-O0', '-o', str(binary), str(source)], check=True)
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'CUDA_DEVICE_ORDER': 'PCI_BUS_ID',
                   'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': '1', 'LD_LIBRARY_PATH': str(root)}
    preset = SimpleNamespace(memory_max_mib=256, threads=1, environment=lambda: environment,
                             argv=lambda: [str(binary)])
    unit = unit_name(str(uuid4()), 'measured', 0)
    report = {'probe_only': True, 'gpu_used': False, 'unit': unit, 'execution_accepted': False}
    try:
        result = supervisor.run(preset, unit, time.monotonic() + 10, lambda: None)
        marker = b'ISOLATED_CPU_PROBE_EXECUTED' in result.stdout
        report.update(returncode=result.returncode, marker_seen=marker)
        report['execution_accepted'] = result.returncode == 0 and marker
        report['isolation_refused'] = b'Native isolation not confirmed' in result.stderr and not marker
    except Exception as error:
        report['error'] = getattr(error, 'code', type(error).__name__)
    finally:
        try:
            supervisor.stop(unit)
            report['cleanup_confirmed'] = True
        except Exception:
            report['cleanup_confirmed'] = False
        report['remaining_units'] = sorted(supervisor.listed_units())
        (root / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report['execution_accepted'] and report['cleanup_confirmed'] and not report['remaining_units'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
