import json
import uhttp
import urllib.parse
import pytest
import async_asgi_testclient

@pytest.fixture
def app():
    return uhttp.App()


pytestmark = pytest.mark.asyncio

@pytest.mark.parametrize('value', [{'a': 'b'}, ['a'] * 100000])
async def test_json(app, value):
    @app.post('/')
    def root(request):
        assert request.json == value
        return {'request.json': request.json}

    async with async_asgi_testclient.TestClient(app) as client:
        resp = await client.post('/', headers={'Content-Type': 'application/json'}, data=json.dumps(value))
        assert resp.status_code == 200
        assert json.loads(resp.content) == {'request.json': value}


async def test_json_parsing_error(app):
    @app.post('/')
    def root(request):
        return {'request.json': request.json}

    async with async_asgi_testclient.TestClient(app) as client:
        resp = await client.post('/', headers={'Content-Type': 'application/json'}, data=b'{"a')
        assert resp.status_code == 400


async def test_form_urlencoded(app):
    value = [('a', 'b'), ('foo', 'bar'), ('a', '2')]

    @app.post('/')
    def root(request):
        assert request.form == {'a': ['b', '2'], 'foo': ['bar']}
        return {'request.form': request.form}

    async with async_asgi_testclient.TestClient(app) as client:
        resp = await client.post('/', headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=urllib.parse.urlencode(value))
        assert resp.status_code == 200
        assert json.loads(resp.content) == {'request.form': {'a': '2', 'foo': 'bar'}}


async def test_form_multipart_formdata(app):
    payload = b'''--9051914041544843365972754266\r
Content-Disposition: form-data; name="text"\r
\r
text default\r
--9051914041544843365972754266\r
Content-Disposition: form-data; name="file1"; filename="a.txt"\r
Content-Type: text/plain\r
\r
Content of a.txt.
\r
--9051914041544843365972754266\r
Content-Disposition: form-data; name="file2"; filename="a.html"\r
Content-Type: text/html\r
\r
<!DOCTYPE html><title>Content of a.html.</title>
\r
--9051914041544843365972754266--'''

    @app.post('/')
    def root(request):
        assert set(request.form) == {'text', 'file1', 'file2'}
        for k, v in request.form.items():
            if k == 'text':
                assert v == 'text default'
            if k == 'file1':
                assert v.file_name == b'a.txt'
                assert v.file_object.getvalue() == b'Content of a.txt.\n'
            if k == 'file2':
                assert v.file_name == b'a.html'
                assert v.file_object.getvalue() == b'<!DOCTYPE html><title>Content of a.html.</title>\n'
        return ''

    async with async_asgi_testclient.TestClient(app) as client:
        resp = await client.post('/', headers={'Content-Type': 'multipart/form-data; boundary=9051914041544843365972754266'}, data=payload)
        assert resp.status_code == 200
