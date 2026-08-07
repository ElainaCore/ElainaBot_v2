"""自身图床的存储、链接生成和公开读取测试。"""

from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modules.image_hosting import public_server
from modules.image_hosting.beds import discover_beds
from modules.image_hosting.beds import self_hosted as self_hosted_module
from modules.image_hosting.main import _order_bed_config
from web.api import handle_ext_route

_PNG = b'\x89PNG\r\n\x1a\n' + b'test-image-data'
_EXPECTED_ORDER = [
    'chatglm',
    'xingye',
    'nature',
    'qq_file',
    'cos',
    'bilibili',
    'qq_channel',
    'self_hosted',
]


def _make_bed(tmp_path, public_base_url='https://img.example.com/api/ext/image-hosting'):
    bed = self_hosted_module.Bed(
        {
            'enabled': True,
            'public_base_url': public_base_url,
            'storage_dir': str(tmp_path),
            'max_file_size': 1024,
            'permanent_cache': True,
        }
    )
    bed.initialize()
    return bed


def test_self_hosted_bed_is_last():
    beds = discover_beds()
    assert [bed.name for bed in beds] == _EXPECTED_ORDER
    assert beds[-1].defaults['enabled'] is False


def test_existing_config_is_reordered_and_retired_beds_are_removed():
    beds = discover_beds()
    current = {name: {'enabled': False} for name in reversed(_EXPECTED_ORDER)}
    current['qiniu'] = {'enabled': True}
    current['xinyew'] = {'enabled': True}
    current['custom_bed'] = {'token': 'preserved'}

    ordered = _order_bed_config(current, beds)

    assert list(ordered) == [*_EXPECTED_ORDER, 'custom_bed']
    assert ordered['custom_bed'] == {'token': 'preserved'}


def test_public_base_url_supports_auto_ip_domain_and_mapping(monkeypatch):
    monkeypatch.setattr(self_hosted_module, '_detect_local_ip', lambda: '192.0.2.10')
    monkeypatch.setattr(self_hosted_module, '_server_port', lambda: 5200)

    assert self_hosted_module._resolve_public_base_url('') == 'http://192.0.2.10:5200/api/ext/image-hosting'
    assert self_hosted_module._resolve_public_base_url('img.example.com') == 'http://img.example.com:5200/api/ext/image-hosting'
    assert self_hosted_module._resolve_public_base_url('https://cdn.example.com/bot-images/') == 'https://cdn.example.com/bot-images'


@pytest.mark.asyncio
async def test_upload_uses_content_hash_and_rejects_non_images(tmp_path):
    bed = _make_bed(tmp_path)

    first = await bed.upload(_PNG, '../../unsafe.png')
    second = await bed.upload(_PNG, 'other-name.jpg')

    assert first == second
    parsed = urlsplit(first)
    stored_name = parse_qs(parsed.query)['filename'][0]
    assert stored_name.endswith('.png')
    assert bed.resolve_file(stored_name) == str(tmp_path / stored_name)
    assert len(list(tmp_path.iterdir())) == 1

    failure = await bed.upload(b'<script>alert(1)</script>', 'attack.svg')
    assert failure[0] is False


@pytest.mark.asyncio
async def test_uploaded_mapping_survives_bed_restart(tmp_path):
    first_bed = _make_bed(tmp_path)
    image_url = await first_bed.upload(_PNG, 'image.png')
    filename = parse_qs(urlsplit(image_url).query)['filename'][0]

    restarted_bed = _make_bed(tmp_path)
    assert restarted_bed.resolve_file(filename) == str(tmp_path / filename)


@pytest.mark.asyncio
async def test_public_route_serves_image_without_authentication(tmp_path):
    bed = _make_bed(tmp_path)
    image_url = await bed.upload(_PNG, 'image.png')
    filename = parse_qs(urlsplit(image_url).query)['filename'][0]

    class Hosting:
        def get_bed(self, name):
            return bed if name == 'self_hosted' else None

    hosting = Hosting()
    app = web.Application()
    app.router.add_route('*', '/api/ext/{tail:.*}', handle_ext_route)
    public_server.attach(hosting)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get(f'{public_server.PUBLIC_ROUTE}?filename={filename}')
        assert response.status == 200
        assert await response.read() == _PNG
        assert response.headers['Access-Control-Allow-Origin'] == '*'
        assert response.headers['Cache-Control'] == 'public, max-age=31536000, immutable'

        bed._cfg['permanent_cache'] = False
        uncached = await client.get(f'{public_server.PUBLIC_ROUTE}?filename={filename}')
        assert uncached.status == 200
        assert uncached.headers['Cache-Control'] == 'no-store'

        missing = await client.get(f"{public_server.PUBLIC_ROUTE}?filename={'0' * 64}.png")
        assert missing.status == 404

        public_server.detach(hosting)
        disabled = await client.get(f'{public_server.PUBLIC_ROUTE}?filename={filename}')
        assert disabled.status == 404

        reloaded = Hosting()
        public_server.attach(reloaded)
        after_reload = await client.get(f'{public_server.PUBLIC_ROUTE}?filename={filename}')
        assert after_reload.status == 200
        assert await after_reload.read() == _PNG
    finally:
        await client.close()
        await server.close()
