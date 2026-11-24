"""Strict web contract and bounded IPC for the optional two-channel fan broker."""
import json
import socket
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    channel: Literal['fan1', 'fan2']
    mode: Literal['full', 'auto', 'manual'] | None = None
    pwm: int | None = Field(default=None, ge=0, le=255)
    version: str = Field(pattern=r'^[0-9a-f]{32}:[0-9]{1,12}$')
    confirmed: StrictBool

    @model_validator(mode='after')
    def operation(self):
        if (self.mode is None) == (self.pwm is None) or self.confirmed is not True:
            raise ValueError('Confirm exactly one mode or output change')
        return self


class Unavailable(RuntimeError):
    pass


class Conflict(RuntimeError):
    pass


def exchange(path, token, change=None):
    body = {'token': token, 'operation': 'status' if change is None else 'set'}
    if change is not None: body['change'] = change
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(6)
            connection.connect(str(path))
            connection.sendall(json.dumps(body).encode() + b'\n')
            with connection.makefile('rb') as stream: raw = stream.readline(8193)
        if len(raw) > 8192 or not raw.endswith(b'\n'):
            raise ValueError('Invalid fan reply')
        reply = json.loads(raw)
        if reply.get('conflict') is True:
            raise Conflict('Fan state changed or safety fallback active; refresh before changing it')
        if reply.get('ok') is not True or not isinstance(reply.get('result'), dict):
            raise ValueError('Fan broker refused request')
        return reply['result']
    except (OSError, ValueError, TypeError, AttributeError):
        raise Unavailable('Fan controller unavailable or refused; refresh observed state') from None
