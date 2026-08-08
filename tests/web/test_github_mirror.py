"""GitHub 镜像统一配置测试。"""

import json

from web.tools._market import shared as market_shared
from web.tools._updater.framework import FrameworkUpdater
from web.tools._updater.shared import load_github_mirror


def test_updater_and_market_share_one_github_mirror(tmp_path):
    market_shared.set_context(str(tmp_path))
    updater = FrameworkUpdater(str(tmp_path))

    updater.set_custom_mirror('https://mirror.example/')

    assert updater.custom_mirror == 'https://mirror.example/'
    assert market_shared.get_github_mirror() == 'https://mirror.example/'
    assert load_github_mirror(tmp_path) == 'https://mirror.example/'

    settings = json.loads((tmp_path / 'data' / 'update_settings.json').read_text(encoding='utf-8'))
    assert settings['custom_mirror'] == 'https://mirror.example/'


def test_market_ranked_urls_prioritize_shared_mirror(tmp_path):
    market_shared.set_context(str(tmp_path))
    updater = FrameworkUpdater(str(tmp_path))
    updater.set_custom_mirror('https://mirror.example/')

    original = 'https://github.com/owner/repo/archive/main.zip'
    urls = market_shared._ranked_mirror_urls(original)

    assert urls[0] == 'https://mirror.example/https://github.com/owner/repo/archive/main.zip'
    assert original in urls
