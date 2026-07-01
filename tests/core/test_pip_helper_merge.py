"""单元测试: pip_helper 多依赖清单发现 + 合并 + 版本冲突取最高版本"""

import os
import tempfile

from core.base import pip_helper


def _write(d, name, text):
    with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
        f.write(text)


def test_parse_req():
    assert pip_helper._parse_req('openai>=1.0.0')[0] == 'openai'
    assert pip_helper._parse_req('openai>=1.0.0')[1] == (1, 0, 0)
    assert pip_helper._parse_req('# comment') is None
    assert pip_helper._parse_req('-r other.txt') is None
    assert pip_helper._parse_req('  ') is None
    # extras + marker 不影响包名
    assert pip_helper._parse_req('uvicorn[standard]>=0.30; python_version>="3.8"')[0] == 'uvicorn'


def test_norm_pkg():
    assert pip_helper._norm_pkg('Nonebot_Plugin.Foo') == 'nonebot-plugin-foo'


def test_discover_and_merge_highest_version():
    d = tempfile.mkdtemp()
    # 共享 alone 目录: 两个插件各一份, 都声明 openai 但版本不同
    _write(d, '今日猪猪_requirements.txt', 'openai>=1.0.0\nhttpx>=0.27\n')
    _write(d, '字符字_requirements.txt', 'openai>=1.3.0\nnumpy>=1.24\n')
    _write(d, 'requirements.txt', 'pillow>=10.0\n')
    # 非依赖清单不应被发现
    _write(d, 'config.txt', 'ignore me\n')

    files = pip_helper._discover_req_files(d)
    names = {os.path.basename(f) for f in files}
    assert names == {'今日猪猪_requirements.txt', '字符字_requirements.txt', 'requirements.txt'}

    merged = pip_helper._merge_requirements(files)
    # openai 取最高版本 1.3.0
    assert 'openai>=1.3.0' in merged
    assert 'openai>=1.0.0' not in merged
    # 其它包保留
    assert any(s.startswith('httpx') for s in merged)
    assert any(s.startswith('numpy') for s in merged)
    assert any(s.startswith('pillow') for s in merged)
    # openai 只出现一次
    assert sum(1 for s in merged if s.startswith('openai')) == 1
