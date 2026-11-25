"""Authenticated OpenAI chat and discovery for the host's configured engines."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import hmac
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask
import uvicorn

from .adapters import ENGINE_IDS
from .engine_agent import secret

class Upstream(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str
    key_file: Path

    @field_validator('url')
    @classmethod
    def local_http(cls, value):
        url = urlsplit(value)
        ip = ipaddress.IPv4Address(url.hostname)
        if (url.scheme != 'http' or not (ip.is_loopback or ip.is_private) or
                url.netloc != f'{ip}:{url.port}' or not 1024 <= url.port <= 65535 or
                url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('Expected a fixed private IPv4 HTTP endpoint')
        return value.rstrip('/')


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engine: str
    name: str = Field(min_length=1, max_length=160)
    context: int = Field(ge=512, le=1_000_000)
    max_output: int = Field(gt=0, le=65536)
    reasoning: bool = False
    vision: bool = False


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key_file: Path
    upstreams: dict[str, Upstream]
    models: dict[str, Model]


def error(status, message, code):
    return JSONResponse({'error': {'message': message, 'type': 'invalid_request_error' if status < 500
                                   else 'server_error', 'code': code}}, status_code=status)


def create_app(config, *, transport=None):
    key = secret(config.key_file).encode()
    upstream_keys = {name: secret(upstream.key_file) for name, upstream in config.upstreams.items()}
    if (not config.models or set(config.upstreams) - set(ENGINE_IDS) or
            any(model.engine not in config.upstreams or model.max_output >= model.context
                for model in config.models.values()) or
            any(hmac.compare_digest(key, value.encode()) for value in upstream_keys.values())):
        raise ValueError('Invalid gateway routes or overlapping credentials')

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(1800, connect=3, pool=3)) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def authorize(request, call_next):
        header = request.headers.get('authorization', '')
        if len(header) > 519 or not hmac.compare_digest(header.encode(), b'Bearer ' + key):
            return error(401, 'A valid inference key is required.', 'invalid_api_key')
        return await call_next(request)

    def request_headers(engine):
        return {'Authorization': 'Bearer ' + upstream_keys[engine], 'Accept-Encoding': 'identity'}

    async def current_models(engine):
        try:
            response = await app.state.client.get(config.upstreams[engine].url + '/v1/models',
                                                  headers=request_headers(engine), timeout=3)
            response.raise_for_status()
            return {row['id']: row for row in response.json()['data']
                    if isinstance(row.get('id'), str) and row.get('status') != {'value': 'unloaded'}}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    @app.get('/v1/models')
    async def models():
        engines = list(config.upstreams)
        snapshots = dict(zip(engines, await asyncio.gather(*(current_models(engine) for engine in engines))))
        # Kmic can stage a smaller operator-selected context for its 27B alias.
        kmic_context = None
        if 'qwen3.8-27b-p100' in snapshots.get('kmic', {}):
            try:
                response = await app.state.client.get(config.upstreams['kmic'].url + '/props',
                                                      headers=request_headers('kmic'), timeout=3)
                response.raise_for_status()
                kmic_context = response.json()['default_generation_settings']['n_ctx']
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
        rows = []
        for alias, model in config.models.items():
            context = model.context
            live = snapshots.get(model.engine, {}).get(alias)
            if live:
                metadata = live.get('meta')
                reported = [live.get('max_model_len'), live.get('context_length'),
                            metadata.get('n_ctx') if isinstance(metadata, dict) else None]
                if alias == 'qwen3.8-27b-p100':
                    reported.append(kmic_context)
                context = min([context, *(value for value in reported if type(value) is int and value >= 512)])
            rows.append({'id': alias, 'object': 'model', 'created': 0, 'owned_by': 'p100',
                         'name': model.name, 'engine': model.engine,
                         'available': live is not None,
                         'max_model_len': context,
                         'limits': {'max_output_tokens': min(model.max_output, context - 1)},
                         'reasoning': model.reasoning,
                         'architecture': {'input_modalities': ['text', 'image'] if model.vision else ['text'],
                                          'output_modalities': ['text']},
                         'supported_endpoint_types': ['openai']})
        return {'object': 'list', 'data': rows}

    @app.post('/v1/chat/completions')
    async def chat(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16 * 1024 * 1024:
                return error(413, 'The inference request exceeds 16 MiB.', 'request_too_large')
            body.extend(chunk)
        try:
            payload = json.loads(body)
            alias = payload['model']
            if not isinstance(alias, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return error(400, 'The request must specify a model ID.', 'invalid_model')
        model = config.models.get(alias)
        if model is None:
            return error(404, 'Unknown model ID.', 'model_not_found')
        if alias not in await current_models(model.engine):
            return error(503, f'{model.name} is offline. Select and start its engine in P100 Inference Lab.',
                         'model_unavailable')
        upstream = config.upstreams[model.engine]
        query = app.state.client.build_request('POST', upstream.url + '/v1/chat/completions',
                                               content=bytes(body), headers={
                                                   **request_headers(model.engine), 'Content-Type': 'application/json'})
        try:
            response = await app.state.client.send(query, stream=True)
        except httpx.HTTPError:
            return error(502, 'The engine connection failed.', 'engine_connection_failed')
        if not 200 <= response.status_code < 500 or 300 <= response.status_code < 400:
            await response.aclose()
            return error(502, 'The engine could not complete the request.', 'engine_error')

        async def stream():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        headers = {'Content-Type': response.headers.get('content-type', 'application/json'),
                   'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}
        return StreamingResponse(stream(), status_code=response.status_code, headers=headers,
                                 background=BackgroundTask(response.aclose))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.host)
    if address.is_unspecified or not address.is_private:
        parser.error('Bind to a specific private IPv4 address.')
    config = Configuration.model_validate_json(args.config.read_bytes())
    uvicorn.run(create_app(config), host=str(address), port=args.port, access_log=False)


if __name__ == '__main__':
    main()
