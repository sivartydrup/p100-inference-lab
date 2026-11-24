from pathlib import Path
from unittest.mock import patch

import pytest

from lab.fans import fan_metrics
from lab.telemetry import sample


def controller(root, index=0):
    directory = root / f'hwmon{index}'
    directory.mkdir(parents=True)
    (directory / 'name').write_text('it8620\n')
    (directory / 'device').mkdir()
    return directory


def test_reads_only_rpm_and_identity_never_controls(tmp_path):
    directory = controller(tmp_path)
    (directory / 'fan1_input').write_text('4245\n')
    (directory / 'fan2_input').write_text('0\n')
    for name in ('pwm1', 'pwm1_enable', 'fan1_min', 'fan1_beep'):
        (directory / name).write_text('untouched')
    original = Path.open
    opened = []
    def recording(path, mode='r', *args, **kwargs):
        opened.append((path.name, mode))
        return original(path, mode, *args, **kwargs)
    with patch.object(Path, 'open', recording):
        result = fan_metrics(tmp_path)
    assert result['available'] and not result['controls_enabled']
    assert [d['rpm'] for d in result['devices']] == [4245, 0]
    assert {name for name, _ in opened} == {'name', 'fan1_input', 'fan2_input'}
    assert all(mode == 'r' for _, mode in opened)
    assert all((directory / name).read_text() == 'untouched' for name in ('pwm1', 'pwm1_enable', 'fan1_min', 'fan1_beep'))


@pytest.mark.parametrize('value', ['-1', 'nan', 'inf', '12.5', '1000001', '4' * 100, ''])
def test_bad_reading_is_unavailable_not_zero(tmp_path, value):
    directory = controller(tmp_path)
    (directory / 'fan1_input').write_text(value)
    result = fan_metrics(tmp_path)
    assert not result['available'] and result['partial']
    assert result['devices'][0]['rpm'] is None


def test_missing_driver_or_sysfs_and_removed_channel(tmp_path):
    assert not fan_metrics(tmp_path / 'missing')['available']
    assert not fan_metrics(tmp_path)['devices']
    directory = controller(tmp_path)
    path = directory / 'fan1_input'
    path.write_text('4200')
    assert fan_metrics(tmp_path)['devices'][0]['rpm'] == 4200
    path.unlink()
    result = fan_metrics(tmp_path)
    assert not result['available'] and result['devices'] == []


def test_unreadable_channel_does_not_hide_readable_channel(tmp_path):
    directory = controller(tmp_path)
    (directory / 'fan1_input').write_text('4200')
    (directory / 'fan2_input').write_text('4300')
    original = Path.open
    def restricted(path, *args, **kwargs):
        if path.name == 'fan2_input':
            raise PermissionError('Synthetic denied read')
        return original(path, *args, **kwargs)
    with patch.object(Path, 'open', restricted):
        result = fan_metrics(tmp_path)
    assert result['available'] and result['partial']
    assert [d['rpm'] for d in result['devices']] == [4200, None]


def test_channel_count_bounded_and_non_sensor_files_ignored(tmp_path):
    directory = controller(tmp_path)
    for index in range(1, 25):
        (directory / f'fan{index}_input').write_text('1')
    (directory / 'fan0_input').write_text('10')
    (directory / 'fan1_input_extra').write_text('10')
    result = fan_metrics(tmp_path)
    assert len(result['devices']) == 16 and result['truncated'] and result['partial']


def test_mock_sample_does_not_access_fan_hardware(tmp_path):
    with patch('lab.telemetry.fan_metrics', side_effect=AssertionError('Hardware accessed')):
        data = sample('mock', tmp_path)
    assert data['fans']['source'] == 'mock'
    assert not data['fans']['controls_enabled']


def test_total_fan_count_bounded_for_stream_frame_size(tmp_path):
    for index in range(5):
        directory = controller(tmp_path, index)
        for fan in range(1, 17):
            (directory / f'fan{fan}_input').write_text('4000')
    result = fan_metrics(tmp_path)
    assert len(result['devices']) == 64 and result['truncated']


def test_live_sample_includes_fans(tmp_path):
    fans = {'available': False, 'devices': [], 'controls_enabled': False}
    with patch('lab.telemetry.fan_metrics', return_value=fans), patch('lab.telemetry.gpu_metrics', return_value={}):
        assert sample('live', tmp_path)['fans'] == fans


def test_only_reviewed_controller_channels_get_live_mode_and_output(tmp_path):
    directory = controller(tmp_path)
    for channel in (1, 2, 3, 4):
        (directory / f'fan{channel}_input').write_text('4000')
        (directory / f'pwm{channel}').write_text('70')
        (directory / f'pwm{channel}_enable').write_text('2')
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        return Path('/sys/devices/platform/it87.2624') if path.name == 'device' else original(path, *args, **kwargs)
    with patch.object(Path, 'resolve', resolve):
        result = fan_metrics(tmp_path)
    assert [item.get('pwm') for item in result['devices']] == [70, 70, None, None]
    assert [item.get('pwm_enable') for item in result['devices']] == [2, 2, None, None]
    assert not result['controls_enabled']
