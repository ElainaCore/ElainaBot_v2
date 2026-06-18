"""单元测试: 单文件插件统一安装到 plugins/alone/ (不再为每个插件单独建目录)"""

import json
import os
import tempfile

from web.tools._market import install, shared


def test_single_file_plugin_goes_to_alone():
    base = tempfile.mkdtemp()
    os.makedirs(os.path.join(base, 'plugins'))
    shared.set_context(base)

    meta = b'"""x"""\n__plugin_meta__ = {"version": "2.1.0"}\n'
    r = install._install_py(meta, '测试插件', 'https://raw/u/repo/main/foo.py')
    assert r['success']
    # 文件落在 plugins/alone/, 未生成独立的 plugins/测试插件/ 目录
    assert os.path.isfile(os.path.join(base, 'plugins', 'alone', '测试插件.py'))
    assert not os.path.isdir(os.path.join(base, 'plugins', '测试插件'))
    # 已安装检测 + 版本读取均能识别 alone 下的单文件插件
    assert '测试插件' in install._get_installed_names()
    assert install._get_local_plugin_version('测试插件') == '2.1.0'

    # name 为空时回退到 URL 文件名
    r2 = install._install_py(b'x', '', 'https://x/y/bar.py')
    assert r2['success']
    assert os.path.isfile(os.path.join(base, 'plugins', 'alone', 'bar.py'))


class _FakeReq:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


async def test_uninstall_single_file_plugin():
    base = tempfile.mkdtemp()
    os.makedirs(os.path.join(base, 'plugins'))
    shared.set_context(base)
    install._install_py(b'x', '测试插件', 'https://x/foo.py')
    alone_py = os.path.join(base, 'plugins', 'alone', '测试插件.py')
    assert os.path.isfile(alone_py)

    resp = await install.handle_market_uninstall(_FakeReq({'name': '测试插件', 'type': 'plugin'}))
    body = json.loads(resp.text)
    assert body['success'], body
    assert not os.path.exists(alone_py)
