"""单元测试: 插件市场新安装模型 (complete / single 多文件 / 独立文件夹 / 一仓库多插件)"""

import io
import os
import tempfile
import zipfile

import pytest

from web.tools._market import install, shared


def _make_repo_zip(files: dict, root='repo-main') -> bytes:
    """构造类 GitHub archive 的 zip (带 repo-branch/ 根目录)"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for path, content in files.items():
            zf.writestr(f'{root}/{path}', content)
    return buf.getvalue()


@pytest.fixture
def base(monkeypatch):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'plugins'), exist_ok=True)
    shared.set_context(d)
    return d


# ==================== 类型规范化 ====================


def test_canonical_type():
    assert install._canonical_type('complete') == install.TYPE_COMPLETE
    assert install._canonical_type('single') == install.TYPE_SINGLE
    assert install._canonical_type('standalone') == install.TYPE_SINGLE
    assert install._canonical_type('module') == install.TYPE_MODULE
    # 未知/空 → 完整插件 (不再从 path 推断)
    assert install._canonical_type('') == install.TYPE_COMPLETE
    assert install._canonical_type('plugin') == install.TYPE_COMPLETE


# ==================== zip 子集解压 ====================


def test_extract_whole_repo(base):
    content = _make_repo_zip({'index.py': 'x', 'README.md': 'doc'})
    r = install._extract_zip_subset(content, 'P')
    assert r['success'], r
    assert os.path.isfile(os.path.join(base, 'plugins', 'P', 'index.py'))
    assert os.path.isfile(os.path.join(base, 'plugins', 'P', 'README.md'))


def test_extract_subdir_only(base):
    """一仓库多插件: 仅解压指定子目录, 不带其它插件"""
    content = _make_repo_zip(
        {
            '今日老婆/今日老婆.py': 'a',
            '今日老婆/wife_panel.html': '<html>',
            '关键词回复/关键词回复.py': 'b',
            '关键词回复/panel.html': '<html>',
        }
    )
    # path 指向子目录下的文件 → 取其父目录
    r = install._extract_zip_subset(content, '今日老婆', subdir_path='今日老婆/今日老婆.py')
    assert r['success'], r
    pdir = os.path.join(base, 'plugins', '今日老婆')
    assert os.path.isfile(os.path.join(pdir, '今日老婆.py'))
    assert os.path.isfile(os.path.join(pdir, 'wife_panel.html'))  # 同目录 html 一并下载
    # 不应混入另一个插件
    assert not os.path.exists(os.path.join(pdir, '关键词回复.py'))


def test_extract_subdir_missing(base):
    content = _make_repo_zip({'a/x.py': '1'})
    r = install._extract_zip_subset(content, 'P', subdir_path='不存在')
    assert not r['success']


# ==================== single: 共享 alone 目录 (alone=True 默认) ====================


async def test_single_alone_shared(base, monkeypatch):
    async def fake_dl(url, **kw):
        return b'"""x"""\n__plugin_meta__ = {"version": "1.0.0"}\n'

    monkeypatch.setattr(install, '_download_file', fake_dl)
    result, target = await install._install_single('https://github.com/u/r', '小工具', path='小工具.py', alone=True)
    assert result['success'], result
    assert target == install._ALONE_DIR
    assert os.path.isfile(os.path.join(base, 'plugins', 'alone', '小工具.py'))
    assert not os.path.isdir(os.path.join(base, 'plugins', '小工具'))


# ==================== single: 独立文件夹 (alone=False) ====================


async def test_single_dedicated_root_file(base, monkeypatch):
    async def fake_dl(url, **kw):
        return b'print(1)'

    monkeypatch.setattr(install, '_download_file', fake_dl)
    result, target = await install._install_single('https://github.com/u/r', '哈基米', path='哈基米.py', alone=False)
    assert result['success'], result
    assert target == '哈基米'
    assert os.path.isfile(os.path.join(base, 'plugins', '哈基米', '哈基米.py'))
    # 没有进共享 alone
    assert not os.path.exists(os.path.join(base, 'plugins', 'alone', '哈基米.py'))


async def test_single_dedicated_subdir_multifile(base, monkeypatch):
    content = _make_repo_zip({'今日老婆/今日老婆.py': 'a', '今日老婆/wife_panel.html': '<html>'})

    async def fake_dl(url, **kw):
        return content

    monkeypatch.setattr(install, '_download_file', fake_dl)
    result, target = await install._install_single('https://github.com/u/r', '今日老婆', path='今日老婆/今日老婆.py', alone=False)
    assert result['success'], result
    assert target == '今日老婆'
    pdir = os.path.join(base, 'plugins', '今日老婆')
    assert os.path.isfile(os.path.join(pdir, '今日老婆.py'))
    assert os.path.isfile(os.path.join(pdir, 'wife_panel.html'))


async def test_single_html_no_collision(base, monkeypatch):
    """两个独立插件各带同名 panel.html, 装到各自专属目录互不覆盖"""
    zip_a = _make_repo_zip({'A/a.py': 'a', 'A/panel.html': 'AAA'})
    zip_b = _make_repo_zip({'B/b.py': 'b', 'B/panel.html': 'BBB'})

    async def dl_a(url, **kw):
        return zip_a

    async def dl_b(url, **kw):
        return zip_b

    monkeypatch.setattr(install, '_download_file', dl_a)
    await install._install_single('https://github.com/u/ra', 'A', path='A/a.py', alone=False)
    monkeypatch.setattr(install, '_download_file', dl_b)
    await install._install_single('https://github.com/u/rb', 'B', path='B/b.py', alone=False)

    with open(os.path.join(base, 'plugins', 'A', 'panel.html')) as f:
        assert f.read() == 'AAA'
    with open(os.path.join(base, 'plugins', 'B', 'panel.html')) as f:
        assert f.read() == 'BBB'


# ==================== complete: 一仓库多插件 (子目录) ====================


async def test_complete_subdir(base, monkeypatch):
    content = _make_repo_zip({'pluginA/index.py': 'a', 'pluginA/x.html': 'h', 'pluginB/index.py': 'b'})

    async def fake_dl(url, **kw):
        return content

    monkeypatch.setattr(install, '_download_file', fake_dl)
    r = await install._install_complete('https://github.com/u/r', 'A', subdir_path='pluginA')
    assert r['success'], r
    pdir = os.path.join(base, 'plugins', 'A')
    assert os.path.isfile(os.path.join(pdir, 'index.py'))
    assert os.path.isfile(os.path.join(pdir, 'x.html'))
    assert not os.path.exists(os.path.join(pdir, 'index.py').replace('A', 'B'))


# ==================== single(alone): path 声明 requirements.txt ====================


async def test_alone_path_with_requirements(base, monkeypatch):
    """path 为数组: .py 装到 alone/<名>.py, 声明的 requirements.txt → alone/<名>_requirements.txt"""
    async def fake_dl(url, **kw):
        if url.endswith('requirements.txt'):
            return b'openai>=1.0.0\n'
        return b'"""x"""\n__plugin_meta__ = {"version": "1.0.0"}\n'

    monkeypatch.setattr(install, '_download_file', fake_dl)
    result, target = await install._install_single(
        'https://github.com/u/r', '今日猪猪',
        path=['alone/今日猪猪.py', 'alone/requirements.txt'], alone=True)
    assert result['success'], result
    assert target == install._ALONE_DIR
    alone = os.path.join(base, 'plugins', 'alone')
    assert os.path.isfile(os.path.join(alone, '今日猪猪.py'))
    # 默认名 requirements.txt 加前缀
    assert os.path.isfile(os.path.join(alone, '今日猪猪_requirements.txt'))
    assert not os.path.exists(os.path.join(alone, 'requirements.txt'))


async def test_alone_path_named_requirements_kept(base, monkeypatch):
    """作者声明的文件本就是 xxx_requirements.txt → 原样保存, 不加前缀"""
    async def fake_dl(url, **kw):
        if url.endswith('.txt'):
            return b'httpx>=0.27\n'
        return b'__plugin_meta__ = {"version": "1.0.0"}\n'

    monkeypatch.setattr(install, '_download_file', fake_dl)
    result, _ = await install._install_single(
        'https://github.com/u/r', '字符字',
        path='alone/字符字.py, alone/字符字_requirements.txt', alone=True)
    assert result['success'], result
    alone = os.path.join(base, 'plugins', 'alone')
    assert os.path.isfile(os.path.join(alone, '字符字.py'))
    assert os.path.isfile(os.path.join(alone, '字符字_requirements.txt'))


def test_split_paths_forms():
    assert install._split_paths(['a.py', 'b.txt']) == ['a.py', 'b.txt']
    assert install._split_paths('a.py, b.txt') == ['a.py', 'b.txt']
    assert install._split_paths('/alone/a.py/') == ['alone/a.py']
    assert install._split_paths('') == []


def test_alone_dep_dest_name():
    assert install._alone_dep_dest_name('requirements.txt', '猪猪') == '猪猪_requirements.txt'
    assert install._alone_dep_dest_name('字符字_requirements.txt', '猪猪') == '字符字_requirements.txt'
    assert install._alone_dep_dest_name('config.json', '猪猪') is None
