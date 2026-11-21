"""Private, bounded Unix-socket transport. No host commands in the API."""
import json
import socket


class EngineUnavailable(RuntimeError):
    pass


def exchange(path, token, operation, selection=None):
    payload = {'token': token, 'operation': operation}
    if selection is not None:
        payload['selection'] = selection
    request = json.dumps(payload).encode() + b'\n'
    if len(request) > 4096:
        raise EngineUnavailable('Engine agent unavailable')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(8)
            connection.connect(str(path))
            connection.sendall(request)
            with connection.makefile('rb') as stream:
                reply = stream.readline(32769)
        if len(reply) > 32768 or not reply.endswith(b'\n'):
            raise ValueError('Invalid agent reply')
        data = json.loads(reply)
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise ValueError('Agent refused request')
        result = data['result']
        if not isinstance(result, dict):
            raise ValueError('Invalid agent result')
        return result
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        raise EngineUnavailable('Engine agent unavailable or refused request') from None
