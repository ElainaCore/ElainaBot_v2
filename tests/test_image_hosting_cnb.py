import httpx
import pytest

from modules.image_hosting.beds.cnb import Bed


def _bed(handler):
    bed = Bed(
        {
            'enabled': True,
            'repo': 'demo/repo',
            'token': 'secret-token',
            'max_file_size': 1024,
            'timeout': 5,
        }
    )
    bed._transport = httpx.MockTransport(handler)
    bed.initialize()
    return bed


@pytest.mark.asyncio
async def test_upload_uses_cnb_openapi_without_plugin_dependency():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            return httpx.Response(
                200,
                json={
                    'upload_url': 'https://upload.example/object',
                    'form': {'x-cnb-signature': 'signed'},
                    'assets': {'id': '42', 'path': '/demo/repo/-/assets/aa/test.png'},
                },
            )
        if request.method == 'PUT':
            assert request.headers['x-cnb-signature'] == 'signed'
            assert request.content == b'png-data'
            return httpx.Response(200)
        raise AssertionError(f'unexpected request: {request.method} {request.url}')

    result = await _bed(handler).upload(b'png-data', 'test.png')

    assert result['asset_url'] == 'https://cnb.cool/demo/repo/-/assets/aa/test.png'
    assert result['verification'] is None
    assert [request.method for request in requests] == ['POST', 'PUT']
    assert requests[0].url.path == '/demo/repo/-/upload/imgs'
    assert requests[0].headers['authorization'] == 'Bearer secret-token'


@pytest.mark.asyncio
async def test_list_and_delete_assets():
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=[{'id': '42', 'path': '/demo/repo/-/assets/aa/test.png'}])
        if request.method == 'DELETE':
            assert request.url.path == '/demo/repo/-/assets/42'
            return httpx.Response(200)
        raise AssertionError(f'unexpected request: {request.method} {request.url}')

    bed = _bed(handler)
    records = await bed.list_assets(limit=10)

    assert records[0]['url'] == 'https://cnb.cool/demo/repo/-/assets/aa/test.png'
    assert await bed.delete(records[0]) is True


@pytest.mark.asyncio
async def test_list_all_assets_reads_every_page():
    pages = []

    def handler(request):
        if request.method != 'GET':
            raise AssertionError(f'unexpected request: {request.method} {request.url}')
        page = int(request.url.params['page'])
        pages.append(page)
        if page == 1:
            return httpx.Response(
                200,
                json=[
                    {'id': str(index), 'path': f'/demo/repo/-/assets/{index}.png'}
                    for index in range(100)
                ],
            )
        return httpx.Response(
            200,
            json=[{'id': '100', 'path': '/demo/repo/-/assets/100.png'}],
        )

    records = await _bed(handler).list_all_assets()

    assert records is not None
    assert len(records) == 101
    assert pages == [1, 2]
    assert records[-1]['url'] == 'https://cnb.cool/demo/repo/-/assets/100.png'
