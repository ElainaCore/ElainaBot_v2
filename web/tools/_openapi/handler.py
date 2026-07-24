"""QQ 开放平台 API — 凭证存储与公共辅助 (处理器见 login / platform / v2 模块)"""

import json
import logging
import os

from aiohttp import web

log = logging.getLogger('ElainaBot.web.openapi')

# 旧开放平台 (机器人专用通道) 凭证: uin/developerId/ticket, 供系统插件与旧接口使用
_openapi_user_data: dict = {}
# 新开放平台 (q.qq.com) 凭证: b_token/qticket_lite 等, 与旧平台隔离存储
_openapi_v2_data: dict = {}
_openapi_login_tasks: dict = {}
_openapi_v2_login_tasks: dict = {}
_data_file = ''
_v2_dir = ''
_bot_api = None
_bot_manager = None


def set_context(base_dir: str, bot_manager=None):
    global _data_file, _v2_dir, _bot_manager
    # 旧平台凭证 (机器人专用通道): web/open/openapi.json
    _data_file = os.path.join(base_dir, 'web', 'open', 'openapi.json')
    # 新平台凭证 (q.qq.com): web/new_open/<user_id>.json, 与旧平台隔离
    _v2_dir = os.path.join(base_dir, 'web', 'new_open')
    _bot_manager = bot_manager
    _load_data()
    _load_v2()


def _load_data():
    global _openapi_user_data
    try:
        if os.path.exists(_data_file):
            with open(_data_file, encoding='utf-8') as f:
                _openapi_user_data = json.load(f)
    except Exception:
        _openapi_user_data = {}


def _save_data():
    try:
        os.makedirs(os.path.dirname(_data_file), exist_ok=True)
        with open(_data_file, 'w', encoding='utf-8') as f:
            json.dump(_openapi_user_data, f, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        pass


def _load_v2():
    """加载新开放平台凭证 (data/open/<user_id>.json)"""
    global _openapi_v2_data
    _openapi_v2_data = {}
    try:
        if not _v2_dir or not os.path.isdir(_v2_dir):
            return
        for name in os.listdir(_v2_dir):
            if not name.endswith('.json'):
                continue
            with open(os.path.join(_v2_dir, name), encoding='utf-8') as f:
                _openapi_v2_data[name[:-5]] = json.load(f)
    except Exception:
        _openapi_v2_data = {}


def _save_v2(user_id):
    try:
        os.makedirs(_v2_dir, exist_ok=True)
        path = os.path.join(_v2_dir, f'{user_id}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_openapi_v2_data.get(user_id, {}), f, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError):
        pass


def _remove_v2(user_id):
    _openapi_v2_data.pop(user_id, None)
    try:
        path = os.path.join(_v2_dir, f'{user_id}.json')
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _v2_get(user_id):
    return _openapi_v2_data.get(user_id)


def _get_user_data(user_id):
    return _openapi_user_data.get(user_id)


def _check_login(user_id='web_user'):
    return _openapi_user_data.get(user_id)


def _get_bot_api():
    global _bot_api
    if _bot_api is None:
        try:
            from web.tools._bot.api import get_bot_api

            _bot_api = get_bot_api()
        except ImportError:
            _bot_api = None
    return _bot_api


def _err(msg, status=200, **kwargs):
    return web.json_response({'success': False, 'message': msg, **kwargs}, status=status)


def _ok(**kwargs):
    return web.json_response({'success': True, **kwargs})


def _require_api_and_login(body):
    """api + 登录状态校验, 返回 (api, ud, err_resp)"""
    api = _get_bot_api()
    if not api:
        return None, None, _err('bot_api 模块未加载')
    ud = _check_login(body.get('user_id', 'web_user'))
    if not ud:
        return api, None, _err('未登录')
    return api, ud, None


def _apply_login(user_id, creds):
    """把扫码凭证隔离写入两个存储: 新平台 data/open/ (b_token/qticket/developer_id_lite/uin/skey), 旧平台 data/openapi.json (uin/developerId/ticket 供系统插件与旧接口)"""
    quid = creds.get('developer_id_lite') or creds.get('developerId') or ''
    uin = creds.get('uin') or ''
    # 新平台隔离存储
    v2 = {'type': 'ok'}
    for k in ('b_token', 'qticket_lite', 'qticket', 'developer_id_lite', 'uin', 'skey', 'p_skey'):
        if creds.get(k):
            v2[k] = creds[k]
    if quid:
        v2['developerId'] = quid
    _openapi_v2_data[user_id] = v2
    _save_v2(user_id)
    # 旧平台存储 (保留已选 appId)
    old = _openapi_user_data.get(user_id) or {}
    old.update(
        {
            'type': 'ok',
            'uin': uin,
            'developerId': quid,
            'ticket': creds.get('qticket') or old.get('ticket', ''),
        }
    )
    _openapi_user_data[user_id] = old
    _save_data()


# 处理器实现见各子模块, 此处统一 re-export 保持既有导入路径兼容
from web.tools._openapi.login import (  # noqa: E402
    handle_check_login,
    handle_get_login_status,
    handle_logout,
    handle_start_login,
    handle_v2_check_login,
    handle_v2_start_login,
    handle_verify_saved_login,
)
from web.tools._openapi.platform import (  # noqa: E402
    handle_batch_add_whitelist,
    handle_check_delete_auth,
    handle_check_webhook,
    handle_execute_delete_ip,
    handle_get_botdata,
    handle_get_botlist,
    handle_get_delete_qr,
    handle_get_event_auth_qr,
    handle_get_event_list,
    handle_get_notifications,
    handle_get_webhook,
    handle_get_webhook_auth_qr,
    handle_get_whitelist,
    handle_modify_event_subscription,
    handle_set_webhook,
    handle_update_whitelist,
    handle_webhook_suggest,
)
from web.tools._openapi.v2 import (  # noqa: E402
    _V2_ALLOWED_PREFIXES,
    _v2_cookie,
    _v2_ready,
    handle_v2_developers,
    handle_v2_proxy,
    handle_v2_status,
    handle_v2_switch_developer,
    handle_v2_upload_avatar,
)

__all__ = [
    '_V2_ALLOWED_PREFIXES',
    '_v2_cookie',
    '_v2_ready',
    'handle_batch_add_whitelist',
    'handle_check_delete_auth',
    'handle_check_login',
    'handle_check_webhook',
    'handle_execute_delete_ip',
    'handle_get_botdata',
    'handle_get_botlist',
    'handle_get_delete_qr',
    'handle_get_event_auth_qr',
    'handle_get_event_list',
    'handle_get_login_status',
    'handle_get_notifications',
    'handle_get_webhook',
    'handle_get_webhook_auth_qr',
    'handle_get_whitelist',
    'handle_logout',
    'handle_modify_event_subscription',
    'handle_set_webhook',
    'handle_start_login',
    'handle_update_whitelist',
    'handle_v2_check_login',
    'handle_v2_developers',
    'handle_v2_proxy',
    'handle_v2_start_login',
    'handle_v2_status',
    'handle_v2_switch_developer',
    'handle_v2_upload_avatar',
    'handle_verify_saved_login',
    'handle_webhook_suggest',
    'set_context',
]
