"""µHTTP - ASGI micro framework"""

import re
import ijson
import json
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qsl, unquote
from unicodedata import normalize
from asyncio import to_thread
from inspect import iscoroutinefunction
import multipart
from multipart.multipart import parse_options_header


async def asyncfy(func, /, *args, **kwargs):
    if iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return await to_thread(func, *args, **kwargs)


class MultiDict(dict):
    def __init__(self, mapping=None):
        if mapping is None:
            super().__init__()
        elif isinstance(mapping, MultiDict):
            super().__init__({k: v[:] for k, v in mapping.itemslist()})
        elif isinstance(mapping, dict):
            super().__init__({
                k: [v] if not isinstance(v, list) else v[:]
                for k, v in mapping.items()
            })
        elif isinstance(mapping, (tuple, list)):
            super().__init__()
            for key, value in mapping:
                self._setdefault(key, []).append(value)
        else:
            raise TypeError('Invalid mapping type')

    def __getitem__(self, key):
        return super().__getitem__(key)[-1]

    def __setitem__(self, key, value):
        super().setdefault(key, []).append(value)

    def _get(self, key, default=(None,)):
        return super().get(key, list(default))

    def get(self, key, default=None):
        return super().get(key, [default])[-1]

    def _items(self):
        return super().items()

    def items(self):
        return {k: v[-1] for k, v in super().items()}.items()

    def _pop(self, key, default=(None,)):
        return super().pop(key, list(default))

    def pop(self, key, default=None):
        values = super().get(key, [])
        return values.pop() if len(values) > 1 else super().pop(key, default)

    def _setdefault(self, key, default=(None,)):
        return super().setdefault(key, list(default))

    def setdefault(self, key, default=None):
        return super().setdefault(key, [default])[-1]

    def _values(self):
        return super().values()

    def values(self):
        return {k: v[-1] for k, v in super().items()}.values()

    def _update(self, *args, **kwargs):
        super().update(*args, **kwargs)

    def update(self, *args, **kwargs):
        new = {}
        new.update(*args, **kwargs)
        super().update(MultiDict(new))


class Headers(MultiDict):
    def __init__(self, mapping=None):
        if mapping is None:
            super().__init__()
        elif isinstance(mapping, MultiDict):
            super().__init__({k.lower(): v[:] for k, v in mapping.itemslist()})
        elif isinstance(mapping, dict):
            super().__init__({
                k.lower(): [v] if not isinstance(v, list) else v[:]
                for k, v in mapping.items()
            })
        elif isinstance(mapping, (tuple, list)):
            super().__init__()
            for key, value in mapping:
                self._setdefault(key, []).append(value)
        else:
            raise TypeError('Invalid mapping type')

    def __getitem__(self, key):
        return super().__getitem__(key.lower())[-1]

    def __setitem__(self, key, value):
        super().setdefault(key.lower(), []).append(value)

    def _get(self, key, default=(None,)):
        return super().get(key.lower(), list(default))

    def get(self, key, default=None):
        return super().get(key.lower(), [default])[-1]

    def _pop(self, key, default=(None,)):
        return super().pop(key.lower(), list(default))

    def pop(self, key, default=None):
        values = super().get(key.lower(), [])
        return values.pop() if len(values) > 1 else super().pop(key.lower(), default)

    def _setdefault(self, key, default=(None,)):
        return super().setdefault(key.lower(), list(default))

    def setdefault(self, key, default=None):
        return super().setdefault(key.lower(), [default])[-1]


class Request:
    def __init__(
        self,
        method,
        path,
        *,
        params=None,
        args=None,
        headers=None,
        cookies=None,
        body=b'',
        json=None,
        form=None,
        state=None
    ):
        self.method = method
        self.path = path
        self.params = params or {}
        self.args = MultiDict(args)
        self.headers = Headers(headers)
        self.cookies = SimpleCookie(cookies)
        self.body = body
        self.json = json or {}
        self.form = MultiDict(form)
        self.state = state or {}

    def __repr__(self):
        return f'{self.method} {self.path}'


class Response(Exception):
    def __init__(self, status, *, headers=None, cookies=None, body=b''):
        self.status = status
        try:
            self.description = HTTPStatus(status).phrase
        except ValueError:
            self.description = ''
        super().__init__(self.description)
        self.headers = MultiDict(headers)
        self.headers.setdefault('content-type', 'text/html; charset=utf-8')
        self.cookies = SimpleCookie(cookies)
        self.body = body
        if not self.body and status in range(400, 600):
            self.body = str(self).encode()

    def __repr__(self):
        return f'{self.status} {self.description}'

    @classmethod
    def from_any(cls, any):
        if isinstance(any, int):
            return cls(status=any, body=HTTPStatus(any).phrase.encode())
        elif isinstance(any, str):
            return cls(status=200, body=any.encode())
        elif isinstance(any, bytes):
            return cls(status=200, body=any)
        elif isinstance(any, dict):
            return cls(
                status=200,
                headers={'content-type': 'application/json'},
                body=json.dumps(any).encode()
            )
        elif isinstance(any, cls):
            return any
        elif any is None:
            return cls(status=204)
        else:
            raise TypeError


class Body:
    def __init__(self, receive):
        self.receive = receive
        self.buffer = b''
        self.finished = False

    async def read(self, n=-1):
        if n == 0:
            return b''

        if not self.finished:
            while len(self.buffer) == 0:
                event = await self.receive()
                self.buffer += event['body']
                if not event.get('more_body'):
                    self.finished = True
        result = self.buffer
        if n > 0:
            self.buffer = result[n:]
            result = result[:n]
        return result


class App:
    def __init__(
        self,
        *,
        routes=None,
        startup=None,
        shutdown=None,
        before=None,
        after=None,
        max_content=1048576
    ):
        self._routes = routes or {}
        self._startup = startup or []
        self._shutdown = shutdown or []
        self._before = before or []
        self._after = after or []
        self._max_content = max_content

    def mount(self, app, prefix=''):
        self._startup += app._startup
        self._shutdown += app._shutdown
        self._before += app._before
        self._after += app._after
        self._routes.update({prefix + k: v for k, v in app._routes.items()})
        self._max_content = max(self._max_content, app._max_content)

    def startup(self, func):
        self._startup.append(func)
        return func

    def shutdown(self, func):
        self._shutdown.append(func)
        return func

    def before(self, func):
        self._before.append(func)
        return func

    def after(self, func):
        self._after.append(func)
        return func

    def route(self, path, methods=('GET',)):
        def decorator(func):
            self._routes.setdefault(path, {}).update({
                method: func for method in methods
            })
            return func
        return decorator

    def get(self, path):
        return self.route(path, methods=('GET',))

    def head(self, path):
        return self.route(path, methods=('HEAD',))

    def post(self, path):
        return self.route(path, methods=('POST',))

    def put(self, path):
        return self.route(path, methods=('PUT',))

    def delete(self, path):
        return self.route(path, methods=('DELETE',))

    def connect(self, path):
        return self.route(path, methods=('CONNECT',))

    def options(self, path):
        return self.route(path, methods=('OPTIONS',))

    def trace(self, path):
        return self.route(path, methods=('TRACE',))

    def patch(self, path):
        return self.route(path, methods=('PATCH',))

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()

                if event['type'] == 'lifespan.startup':
                    try:
                        for func in self._startup:
                            await asyncfy(func, scope['state'])
                        self._routes = {
                            re.compile(k): v for k, v in self._routes.items()
                        }
                    except Exception as e:
                        await send({
                            'type': 'lifespan.startup.failed',
                            'message': f'{type(e).__name__}: {e}'
                        })
                        break
                    await send({'type': 'lifespan.startup.complete'})

                elif event['type'] == 'lifespan.shutdown':
                    try:
                        for func in self._shutdown:
                            await asyncfy(func, scope['state'])
                    except Exception as e:
                        await send({
                            'type': 'lifespan.shutdown.failed',
                            'message': f'{type(e).__name__}: {e}'
                        })
                        break
                    await send({'type': 'lifespan.shutdown.complete'})
                    break

        elif scope['type'] == 'http':
            request = Request(
                method=scope['method'],
                path=scope['path'],
                args=parse_qsl(unquote(scope['query_string'])),
                state=dict(scope.get('state', {})),
            )


            try:
                try:
                    request.headers = Headers([
                        [k.decode('ascii'), normalize('NFC', v.decode())]
                        for k, v in scope['headers']
                    ])
                except UnicodeDecodeError:
                    raise Response(400)

                try:
                    request.cookies.load(request.headers.get('cookie', ''))
                except CookieError:
                    raise Response(400)

                content_type, params = parse_options_header(request.headers.get('content-type', ''))
                if content_type.split(b'+')[0] == b'application/json':
                    try:
                        async for json in ijson.items(Body(receive), ''):
                            request.json = json
                    except (UnicodeDecodeError, ijson.JSONError):
                        raise Response(400)
                elif content_type in (b'application/x-www-form-urlencoded', b'multipart/form-data'):
                    request.form = form = MultiDict()
                    def on_field(field):
                        form[field.field_name.decode()] = field.value.decode()
                    def on_file(file):
                        form[file.field_name.decode()] = file

                    form_parser = multipart.multipart.create_form_parser(request.headers, on_field, on_file)
                    while True:
                        event = await receive()
                        form_parser.write(event['body'])
                        if len(request.body) > self._max_content:
                            raise HTTPException(413)
                        if not event.get('more_body'):
                            break
                    form_parser.finalize()

                for func in self._before:
                    if ret := await asyncfy(func, request):
                        raise Response.from_any(ret)

                for route, methods in self._routes.items():
                    if matches := route.fullmatch(request.path):
                        request.params = matches.groupdict()
                        if func := methods.get(request.method):
                            ret = await asyncfy(func, request)
                            response = Response.from_any(ret)
                        else:
                            response = Response(405)
                            response.headers['allow'] = ', '.join(methods)
                        break
                else:
                    response = Response(404)

            except Response as early_response:
                response = early_response

            try:
                for func in self._after:
                    if ret := await asyncfy(func, request, response):
                        raise Response.from_any(ret)
            except Response as late_response:
                response = late_response

            response.headers._update({'content-length': [len(response.body)]})
            if response.cookies:
                response.headers._update({
                    'set-cookie': [
                        header.split(': ', maxsplit=1)[1]
                        for header in response.cookies.output().splitlines()
                    ]
                })

            await send({
                'type': 'http.response.start',
                'status': response.status,
                'headers': [
                    [str(k).lower().encode(), str(v).encode()]
                    for k, l in response.headers._items() for v in l
                ]
            })
            await send({
                'type': 'http.response.body',
                'body': response.body
            })

        else:
            raise NotImplementedError(scope['type'], 'is not supported')
