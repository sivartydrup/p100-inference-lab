"""Bounded hwmon reads only. Optional output/mode reads never write controls."""
from itertools import islice
from pathlib import Path
import re


def _text(path, limit=80):
    with path.open('r', encoding='ascii') as stream:
        value = stream.read(limit + 1).strip()
    if len(value) > limit or not value.isprintable():
        raise ValueError('Invalid sensor text')
    return value


def fan_metrics(root=Path('/sys/class/hwmon')):
    devices = []
    partial = False
    truncated = False
    try:
        entries = list(islice(root.iterdir(), 257))
        truncated = len(entries) > 256
        controllers = sorted((p for p in entries[:256] if re.fullmatch(r'hwmon[0-9]+', p.name)), key=lambda p: p.name)
        truncated |= len(controllers) > 32
    except OSError:
        controllers = []
    for directory in controllers[:32]:
        if len(devices) >= 64:
            truncated = True
            break
        try:
            entries = list(islice(directory.iterdir(), 257))
            truncated |= len(entries) > 256
            inputs = sorted((p for p in entries[:256] if re.fullmatch(r'fan[1-9][0-9]*_input', p.name)), key=lambda p: p.name)
            truncated |= len(inputs) > 16
        except OSError:
            partial = True
            continue
        if not inputs:
            continue
        try:
            controller = _text(directory / 'name')
            if not controller:
                raise ValueError('Missing controller name')
            device = (directory / 'device').resolve(strict=True).name[:80]
        except (OSError, ValueError, RuntimeError):
            partial = True
            controller, device = 'Unknown controller', directory.name
        for path in inputs[:16]:
            if len(devices) >= 64:
                truncated = True
                break
            channel = path.name.removesuffix('_input')
            rpm = None
            try:
                raw = _text(path, 12)
                if not re.fullmatch(r'[0-9]+', raw):
                    raise ValueError('Invalid RPM')
                rpm = int(raw)
                if rpm > 1_000_000:
                    raise ValueError('Invalid RPM')
            except (OSError, ValueError):
                rpm = None
                partial = True
            item = {'id': f'{controller}:{device}:{channel}', 'controller': controller,
                    'device': device, 'channel': channel, 'rpm': rpm}
            if controller == 'it8620' and device == 'it87.2624' and channel in ('fan1', 'fan2'):
                try:
                    output, mode = _text(directory / ('pwm' + channel[-1]), 3), _text(directory / ('pwm' + channel[-1] + '_enable'), 1)
                    if not output.isdigit() or not 0 <= int(output) <= 255 or mode not in ('0', '1', '2'):
                        raise ValueError('Invalid PWM state')
                    item.update(pwm=int(output), pwm_enable=int(mode))
                except (OSError, ValueError):
                    item.update(pwm=None, pwm_enable=None)
                    partial = True
            devices.append(item)
    available = any(d['rpm'] is not None for d in devices)
    message = ('Some fan readings are unavailable.' if partial or truncated else
               'Readings use sensor channel names; physical fan assignments are unverified.') if available else (
               'No readable fan RPM sensors are exposed to the dashboard.')
    return {'available': available, 'source': 'hwmon', 'devices': devices,
            'message': message, 'partial': partial or truncated, 'truncated': truncated,
            'controls_enabled': False}
