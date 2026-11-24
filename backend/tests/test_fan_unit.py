from pathlib import Path


def test_root_fan_unit_explicitly_protects_sysfs_with_one_writable_exception():
    unit = Path(__file__).resolve().parents[2] / 'deploy/fan-agent/p100-lab-fans.service.example'
    values = dict(line.split('=', 1) for line in unit.read_text().splitlines() if '=' in line and not line.startswith('#'))
    # ProtectSystem=strict excludes API filesystems; /sys must be explicit.
    assert {'/sys', '/proc/sys', '/proc/sysrq-trigger'} <= set(values['ReadOnlyPaths'].split())
    assert values['BindReadOnlyPaths'].split() == ['/sys']
    assert values['ReadWritePaths'].split() == ['/sys/devices/platform/it87.2624']
    assert values['CapabilityBoundingSet'] == '' and values['NoNewPrivileges'] == 'yes'
    assert '--failsafe' in values['ExecStopPost']
