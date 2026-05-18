"""ConfigManager 集成测试 — 真实文件读写 + 环境变量替换 + 变更回调"""

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== Fixtures ====================


@pytest.fixture
def temp_config_dir():
    """创建临时配置目录 (不含任何文件, 由测试自行写入)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def clean_config_manager(temp_config_dir):
    """返回一个已初始化的 ConfigManager, 指向空配置目录"""
    from core.base.config import ConfigManager

    ConfigManager._instance = None
    mgr = ConfigManager()
    mgr.init(temp_config_dir)
    return mgr


# ==================== 配置读写集成测试 ====================


@pytest.mark.integration
class TestConfigReadWrite:
    """配置文件写入 → 重新读取完整流程"""

    def test_write_and_read_back(self, temp_config_dir, clean_config_manager):
        """写入配置值 → 重新初始化 ConfigManager → 读取验证"""
        import yaml

        from core.base.config import ConfigManager

        # 1. 写入初始配置
        settings_path = os.path.join(temp_config_dir, 'settings.yaml')
        with open(settings_path, 'w') as f:
            yaml.dump({'server': {'host': '127.0.0.1', 'port': 8080}}, f)

        # 2. 重新初始化读取
        ConfigManager._instance = None
        mgr2 = ConfigManager()
        mgr2.init(temp_config_dir)

        host = mgr2.get('settings', 'server.host')
        port = mgr2.get('settings', 'server.port')
        assert host == '127.0.0.1'
        assert port == 8080

    def test_multiple_config_files(self, temp_config_dir, clean_config_manager):
        """多个配置文件共存"""
        import yaml

        from core.base.config import ConfigManager

        # settings.yaml
        with open(os.path.join(temp_config_dir, 'settings.yaml'), 'w') as f:
            yaml.dump({'web': {'access_token': 'abc123'}}, f)

        # custom.yaml (非标准文件, 仍可读取)
        with open(os.path.join(temp_config_dir, 'custom.yaml'), 'w') as f:
            yaml.dump({'extra': {'key': 'value', 'list': [1, 2, 3]}}, f)

        ConfigManager._instance = None
        mgr2 = ConfigManager()
        mgr2.init(temp_config_dir)

        assert mgr2.get('settings', 'web.access_token') == 'abc123'
        custom = mgr2.get('custom')
        assert custom['extra']['key'] == 'value'
        assert custom['extra']['list'] == [1, 2, 3]

    def test_missing_file_returns_default(self, clean_config_manager):
        """缺失文件时返回默认值"""
        val = clean_config_manager.get('nonexistent', 'key', default='fallback')
        assert val == 'fallback'

    def test_missing_key_returns_default(self, temp_config_dir):
        """文件存在但 key 不存在时返回默认值"""
        import yaml

        from core.base.config import ConfigManager

        with open(os.path.join(temp_config_dir, 'settings.yaml'), 'w') as f:
            yaml.dump({'server': {}}, f)

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(temp_config_dir)

        val = mgr.get('settings', 'server.nonexistent', default='default_val')
        assert val == 'default_val'


@pytest.mark.integration
class TestConfigEnvVarIntegration:
    """环境变量替换集成测试"""

    def test_env_var_substitution_in_yaml(self, temp_config_dir):
        """YAML 中的 ${VAR} 被环境变量替换"""
        import yaml

        from core.base.config import ConfigManager

        with open(os.path.join(temp_config_dir, 'app.yaml'), 'w') as f:
            yaml.dump(
                {
                    'db_host': '${DB_HOST:localhost}',
                    'db_port': '${DB_PORT:5432}',
                    'secret': '${SECRET_KEY}',
                },
                f,
            )

        os.environ['DB_HOST'] = 'prod-db.example.com'
        os.environ['SECRET_KEY'] = 'my-secret'

        try:
            ConfigManager._instance = None
            mgr = ConfigManager()
            mgr.init(temp_config_dir)

            data = mgr.get('app')
            assert data['db_host'] == 'prod-db.example.com'  # env 覆盖默认值
            assert data['db_port'] == '5432'  # 无 env, 使用默认值
            assert data['secret'] == 'my-secret'  # env 有值
        finally:
            del os.environ['DB_HOST']
            del os.environ['SECRET_KEY']

    def test_env_var_with_default_only(self, temp_config_dir):
        """只有默认值的占位符"""
        import yaml

        from core.base.config import ConfigManager

        with open(os.path.join(temp_config_dir, 'app.yaml'), 'w') as f:
            yaml.dump({'timeout': '${TIMEOUT:30}'}, f)

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(temp_config_dir)

        data = mgr.get('app')
        assert data['timeout'] == '30'

    def test_env_var_no_default_no_env(self, temp_config_dir):
        """无默认值且无环境变量时返回空字符串"""
        import yaml

        from core.base.config import ConfigManager

        # 确保环境变量不存在
        os.environ.pop('MISSING_VAR_TEST', None)

        with open(os.path.join(temp_config_dir, 'app.yaml'), 'w') as f:
            yaml.dump({'key': '${MISSING_VAR_TEST}'}, f)

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(temp_config_dir)

        data = mgr.get('app')
        assert data['key'] == ''


@pytest.mark.integration
class TestConfigChangeCallback:
    """配置变更回调集成测试"""

    def test_on_change_callback_fires(self, temp_config_dir):
        """修改配置文件后触发回调"""
        import yaml

        from core.base.config import cfg

        with open(os.path.join(temp_config_dir, 'bot.yaml'), 'w') as f:
            yaml.dump(
                {
                    'bots': [
                        {'appid': '111', 'secret': 'old_secret'},
                    ]
                },
                f,
            )

        cfg._ready = False
        cfg.init(temp_config_dir)

        # 先触发一次加载 (设置 mtime 基准值), 否则 _do_reload 的 is_first=True 会跳过回调
        assert len(cfg.get_bot_configs()) == 1
        assert cfg.get_bot_configs()[0]['secret'] == 'old_secret'

        changes = []
        cfg.on_change('bot', lambda new_configs: changes.append(new_configs))

        # 修改 bot.yaml
        with open(os.path.join(temp_config_dir, 'bot.yaml'), 'w') as f:
            yaml.dump(
                {
                    'bots': [
                        {'appid': '111', 'secret': 'new_secret'},
                    ]
                },
                f,
            )

        # 绕过频率限制 + 强制触发重载 (mtime 秒级分辨率可能跳过变更检测)
        cfg._last_check.pop('bot', None)
        cfg._mtimes['bot'] = 0  # 标记为过时, 保证 mtime <= old_mtime 不成立
        cfg._maybe_reload('bot', force_sync=True)

        assert len(changes) == 1
        assert changes[0]['bots'][0]['secret'] == 'new_secret'

    def test_override_setting_at_runtime(self, temp_config_dir, clean_config_manager):
        """运行时 API 设置值, 立即反映到 get()"""
        clean_config_manager.set_value('settings', 'server.port', 9999)
        port = clean_config_manager.get('settings', 'server.port')
        assert port == 9999


@pytest.mark.integration
class TestBotConfigIntegration:
    """机器人配置读取集成测试"""

    def test_single_bot_config(self, temp_config_dir):
        """读取单 bot 完整配置"""
        import yaml

        from core.base.config import ConfigManager

        with open(os.path.join(temp_config_dir, 'bot.yaml'), 'w') as f:
            yaml.dump(
                {
                    'bots': [
                        {
                            'appid': '123456',
                            'secret': 'bot_secret_123',
                            'robot_qq': '987654321',
                            'message': {'use_markdown': True},
                        }
                    ],
                },
                f,
            )

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(temp_config_dir)

        bot = mgr.get_bot_config('123456')
        assert bot is not None
        assert bot['appid'] == '123456'
        assert bot['secret'] == 'bot_secret_123'
        assert bot['robot_qq'] == '987654321'

        bot_md = mgr.get_bot_setting('123456', 'message.use_markdown')
        assert bot_md is True

    def test_multiple_bot_configs(self, temp_config_dir):
        """多 bot 配置"""
        import yaml

        from core.base.config import ConfigManager

        with open(os.path.join(temp_config_dir, 'bot.yaml'), 'w') as f:
            yaml.dump(
                {
                    'bots': [
                        {'appid': 'A001', 'secret': 's1'},
                        {'appid': 'A002', 'secret': 's2'},
                        {'appid': 'A003', 'secret': 's3'},
                    ],
                },
                f,
            )

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(temp_config_dir)

        bots = mgr.get_bot_configs()
        assert len(bots) == 3

        assert mgr.get_bot_config('A001')['secret'] == 's1'
        assert mgr.get_bot_config('A002')['secret'] == 's2'
        assert mgr.get_bot_config('A003')['secret'] == 's3'
        assert mgr.get_bot_config('A999') is None
