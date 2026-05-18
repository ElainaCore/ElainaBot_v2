"""ConfigManager 单元测试"""

import os


class TestConfigManagerBasic:
    """基本读写测试"""

    def test_init_sets_config_dir(self, config_manager, sample_config_dir):
        assert config_manager.config_dir == sample_config_dir
        assert config_manager._ready is True

    def test_get_top_level_dict(self, config_manager):
        data = config_manager.get('settings')
        assert isinstance(data, dict)
        assert 'server' in data
        assert data['server']['port'] == 15200

    def test_get_nested_key(self, config_manager):
        port = config_manager.get('settings', 'server.port')
        assert port == 15200

    def test_get_default_on_missing_key(self, config_manager):
        val = config_manager.get('settings', 'nonexistent.key', default=42)
        assert val == 42

    def test_get_default_on_missing_file(self, config_manager):
        val = config_manager.get('nonexistent', 'key', default='fallback')
        assert val == 'fallback'

    def test_get_bot_configs(self, config_manager):
        bots = config_manager.get_bot_configs()
        assert isinstance(bots, list)
        assert len(bots) == 1
        assert bots[0]['appid'] == '123456'

    def test_get_bot_config_by_appid(self, config_manager):
        bot = config_manager.get_bot_config('123456')
        assert bot is not None
        assert bot['secret'] == 'test_secret_123'

    def test_get_bot_config_missing(self, config_manager):
        bot = config_manager.get_bot_config('999999')
        assert bot is None

    def test_get_bot_setting(self, config_manager):
        val = config_manager.get_bot_setting('123456', 'message.use_markdown')
        assert val is True

    def test_get_bot_setting_default(self, config_manager):
        # 未在 bot.yaml 中配置的项回退到 _BOT_DEFAULTS
        val = config_manager.get_bot_setting('123456', 'maintenance.enabled')
        assert val is False  # _BOT_DEFAULTS['maintenance.enabled'] = False


class TestConfigEnvVars:
    """环境变量替换测试"""

    def test_env_var_replacement(self, tmp_path):
        """测试 ${VAR_NAME} 替换"""
        from core.base.config import ConfigManager

        # 写入带占位符的配置
        config_file = tmp_path / 'test.yaml'
        config_file.write_text('key1: ${TEST_VAR}\nkey2: ${TEST_VAR:default_val}\nkey3: ${MISSING_VAR:fallback}\nkey4: ${MISSING_VAR}\n')

        # 设置环境变量
        os.environ['TEST_VAR'] = 'env_value'

        # 重置单例状态以隔离测试
        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(str(tmp_path))

        data = mgr.get('test')
        assert data['key1'] == 'env_value'
        assert data['key2'] == 'env_value'  # env var 优先于默认值
        assert data['key3'] == 'fallback'  # 无 env var, 使用默认值

        # 无 env var 且无默认值时返回空字符串 (由下游校验)
        assert data['key4'] == ''

        # 清理
        del os.environ['TEST_VAR']
