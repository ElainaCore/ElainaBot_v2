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


# ==================== Phase 2: API 测试 Fixtures ====================


def _reset_web_singletons():
    """重置 web 模块的所有模块级全局状态, 确保测试隔离"""
    import web.api as _api
    import web.auth as _auth
    from core.base.config import ConfigManager, cfg

    # 重置 ConfigManager 单例
    ConfigManager._instance = None
    cfg._ready = False

    # 重置 auth 模块全局状态
    _auth.valid_sessions.clear()
    _auth.ip_access_data.clear()
    _auth._last_session_cleanup = 0
    _auth._last_ip_cleanup = 0
    _auth._data_dir = ''
    _auth._ip_file = ''
    _auth._session_file = ''

    # 重置 api 模块全局状态
    _api._bot_manager = None
    _api._base_dir = ''


@pytest.fixture
def api_config_dir():
    """创建临时 API 配置目录, 包含 settings.yaml + bot.yaml, 并初始化 web auth"""
    import os
    import tempfile

    import yaml

    _reset_web_singletons()

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

        # 初始化 web auth (需要 base_dir 下存在 data/web/ 目录)
        os.makedirs(os.path.join(tmpdir, 'data', 'web'), exist_ok=True)
        import web.auth as _auth

        _auth.init(tmpdir)

        # 初始化 ConfigManager
        from core.base.config import ConfigManager

        ConfigManager._instance = None
        mgr = ConfigManager()
        mgr.init(tmpdir)

        yield tmpdir


@pytest.fixture
def setup_app(api_config_dir):
    """创建 aiohttp Application 并注册 API 路由"""
    from aiohttp import web

    import web.api as _api
    import web.auth as _auth
    from core.base.config import cfg

    # 初始化 cfg (conftest 中 _reset_web_singletons 已清理)
    cfg._ready = False
    cfg.init(api_config_dir)

    # 重新初始化 auth (使用测试配置目录)
    _auth.init(api_config_dir)

    # 设置 api 上下文
    _api._bot_manager = None
    _api._base_dir = api_config_dir

    app = web.Application()
    app.router.add_routes(_api.get_routes())
    return app


@pytest.fixture
async def api_client(setup_app):
    """返回 aiohttp TestClient (不依赖 pytest-aiohttp)"""
    from aiohttp.test_utils import TestClient, TestServer

    server = TestServer(setup_app)
    await server.start_server()
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()
    await server.close()


@pytest.fixture
async def auth_headers(api_client):
    """登录获取 token, 返回带 Authorization 的请求头"""
    resp = await api_client.post('/api/auth/login', json={'password': 'test_pass'})
    data = await resp.json()
    token = (data.get('data') or {}).get('token', '')
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
async def auth_token(api_client):
    """仅返回 token 字符串"""
    resp = await api_client.post('/api/auth/login', json={'password': 'test_pass'})
    data = await resp.json()
    return (data.get('data') or {}).get('token', '')
