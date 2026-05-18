"""pytest 全局 fixtures"""

import os
import sys
import tempfile

import pytest

# 确保项目根在 sys.path 中
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ==================== 配置 Fixtures ====================


@pytest.fixture
def sample_config_dir():
    """创建临时配置目录, 包含测试用 YAML 文件"""
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        # settings.yaml
        settings = {
            'server': {'host': '0.0.0.0', 'port': 15200},
            'web': {'access_token': 'test_token', 'admin_password': 'test_pass'},
            'logging': {
                'dir': 'log',
                'insert_interval': 2,
                'batch_size': 0,
                'retention_days': 5,
                'wal_mode': True,
            },
            'pip': {'auto_install': False, 'mirror': ''},
        }
        with open(os.path.join(tmpdir, 'settings.yaml'), 'w') as f:
            yaml.dump(settings, f)

        # bot.yaml
        bots = {
            'bots': [
                {
                    'appid': '123456',
                    'secret': 'test_secret_123',
                    'robot_qq': '987654321',
                    'owner_ids': [''],
                    'websocket': {'enabled': False},
                    'message': {'use_markdown': True},
                    'identity': {
                        'use_union_id_for_group': False,
                        'use_union_id_for_channel': False,
                    },
                    'welcome': {
                        'group_welcome': False,
                        'new_user_welcome': False,
                        'friend_add_message': False,
                    },
                    'maintenance': {'enabled': False},
                    'dedup': {'enabled': False},
                    'blacklist': {
                        'user_enabled': False,
                        'group_enabled': False,
                        'user_list': [],
                        'group_list': [],
                    },
                    'non_at_message': {
                        'enabled': False,
                        'group_whitelist': [],
                        'ignore_at_other_bot': False,
                    },
                }
            ],
        }
        with open(os.path.join(tmpdir, 'bot.yaml'), 'w') as f:
            yaml.dump(bots, f)

        yield tmpdir


@pytest.fixture
def config_manager(sample_config_dir):
    """初始化后的 ConfigManager (独立实例, 避免单例污染)"""
    from core.base.config import ConfigManager

    # 重置单例状态以隔离测试
    ConfigManager._instance = None
    mgr = ConfigManager()
    mgr.init(sample_config_dir)
    return mgr


# ==================== Event Fixtures ====================


@pytest.fixture
def sample_group_at_payload():
    """群聊 @ 消息原始 payload"""
    return {
        'id': 'evt_001',
        'op': 0,
        'd': {
            'id': 'msg_001',
            'author': {
                'id': 'user_abc',
                'member_openid': 'member_xyz',
            },
            'content': '/help',
            'timestamp': '2026-05-17T10:00:00+08:00',
            'group_openid': 'group_001',
            'message_reference': {},
            'attachments': [],
        },
    }


@pytest.fixture
def sample_c2c_payload():
    """私聊消息原始 payload"""
    return {
        'id': 'evt_002',
        'op': 0,
        'd': {
            'id': 'msg_002',
            'author': {
                'id': 'user_def',
            },
            'content': 'hello',
            'timestamp': '2026-05-17T10:01:00+08:00',
            'message_reference': {},
            'attachments': [],
        },
    }


@pytest.fixture
def sample_event():
    """构造 Event 对象"""
    from core.message.event import Event

    evt = Event()
    evt.event_type = 'GROUP_AT_MESSAGE_CREATE'
    evt.appid = '123456'
    evt.message_id = 'msg_001'
    evt.user_id = 'user_abc'
    evt.content = '/help'
    evt.group_id = 'group_001'
    evt.timestamp = '2026-05-17T10:00:00+08:00'
    evt.is_at_self = True
    evt.is_group = True
    evt.is_direct = False
    evt.raw_content = '/help'
    evt.member_openid = 'member_xyz'
    return evt


# ==================== Mock Fixtures ====================


@pytest.fixture
def mock_token_response():
    """模拟 QQ Bot API Token 响应"""
    return {
        'access_token': 'mock_access_token_xxx',
        'expires_in': 7200,
    }


# ==================== Phase 2 Fixtures ====================


@pytest.fixture(autouse=True)
def reset_app_global():
    """每个测试前重置 Application 全局单例"""
    import core.application as _app_mod

    _app_mod._app = None
    yield
    _app_mod._app = None


@pytest.fixture
def app_instance():
    """创建独立的 Application 实例 (不启动)"""
    from core.application import Application

    return Application()
