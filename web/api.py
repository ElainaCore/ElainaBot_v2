"""Web 面板 API 路由"""

import logging
from datetime import datetime

from aiohttp import web

import web.auth as auth
import web.ws as panel_ws
import web.tools.robot_info as robot_info
import web.tools.log_query as log_query
import web.tools.plugin_manager as plugin_manager
import web.tools.config_handler as config_handler
import web.tools.message_handler as message_handler
import web.tools.statistics_handler as statistics_handler
import web.tools.update_handler as update_handler
import web.tools.bot_restart as bot_restart
import web.tools.system_info as system_info
import web.tools.plugin_market_handler as plugin_market_handler
import web.tools.openapi_handler as openapi_handler
import web.tools.database_browser as database_browser

log = logging.getLogger('ElainaBot.web.api')

_bot_manager = None
_base_dir = ''


# ======================== 路由注册 ========================

def get_routes() -> list:
    """返回所有 API 路由"""
    _ = auth.require_auth  # 简写
    return [
        # ── 鉴权 ──
        web.post('/api/auth/login', handle_login),
        web.get('/api/auth/check', _(handle_auth_check)),

        # ── 机器人 ──
        web.get('/api/bots', _(handle_get_bots)),
        web.get('/api/robot/info', _(robot_info.handle_get_robot_info)),
        web.get('/api/robot/qrcode', robot_info.handle_get_robot_qrcode),

        # ── 系统信息 ──
        web.get('/api/system/info', _(system_info.handle_system_info)),

        # ── 日志 (具体路径必须在 {log_type} 之前) ──
        web.get('/api/logs/recent', _(handle_recent_logs)),
        web.get('/api/logs/login', _(log_query.handle_get_login_logs)),
        web.post('/api/logs/unban', _(log_query.handle_unban_ip)),
        web.post('/api/logs/delete-ip', _(log_query.handle_delete_ip)),
        web.get('/api/logs/{log_type}', _(log_query.handle_get_logs)),

        # ── 插件文件管理 ──
        web.get('/api/plugins/scan', _(plugin_manager.handle_scan_plugins)),
        web.get('/api/plugins/scan-dirs', _(plugin_manager.handle_scan_plugin_dirs)),
        web.post('/api/plugins/toggle', _(plugin_manager.handle_toggle_plugin)),
        web.post('/api/plugins/read', _(plugin_manager.handle_read_plugin)),
        web.post('/api/plugins/save', _(plugin_manager.handle_save_plugin)),
        web.post('/api/plugins/create', _(plugin_manager.handle_create_plugin)),
        web.post('/api/plugins/create-folder', _(plugin_manager.handle_create_folder)),
        web.get('/api/plugins/folders', _(plugin_manager.handle_get_folders)),
        web.post('/api/plugins/upload', _(plugin_manager.handle_upload_plugin)),
        web.post('/api/plugins/reload', _(plugin_manager.handle_reload_plugin)),
        web.post('/api/plugins/config-files', _(plugin_manager.handle_plugin_config_files)),
        web.get('/api/plugins/bots', _(plugin_manager.handle_get_plugin_bots)),
        web.post('/api/plugins/bots', _(plugin_manager.handle_set_plugin_bots)),

        # ── 模块管理 ──
        web.get('/api/modules/scan', _(plugin_manager.handle_scan_modules)),
        web.post('/api/modules/toggle', _(plugin_manager.handle_module_toggle)),
        web.post('/api/modules/upload', _(plugin_manager.handle_module_upload)),

        # ── 通用配置读写 (模块 + 插件) ──
        web.post('/api/config-file/read', _(plugin_manager.handle_read_config)),
        web.post('/api/config-file/save', _(plugin_manager.handle_save_config)),

        # ── 配置 ──
        web.get('/api/config', _(config_handler.handle_get_config)),
        web.post('/api/config/save', _(config_handler.handle_save_config)),

        # ── 消息 ──
        web.post('/api/message/chats', _(message_handler.handle_get_chats)),
        web.post('/api/message/history', _(message_handler.handle_get_chat_history)),
        web.post('/api/message/send', _(message_handler.handle_send_message)),
        web.post('/api/message/nickname', _(message_handler.handle_get_nickname)),
        web.post('/api/message/nicknames', _(message_handler.handle_get_nicknames_batch)),

        # ── 统计 ──
        web.get('/api/statistics', _(statistics_handler.handle_get_statistics)),
        web.get('/api/statistics/chart', _(statistics_handler.handle_get_chart_data)),
        web.get('/api/statistics/task/{task_id}', _(statistics_handler.handle_get_task_status)),
        web.get('/api/statistics/dates', _(statistics_handler.handle_get_available_dates)),

        # ── 更新 ──
        web.get('/api/update/changelog', _(update_handler.handle_get_changelog)),
        web.get('/api/update/version', _(update_handler.handle_get_current_version)),
        web.get('/api/update/check', _(update_handler.handle_check_update)),
        web.post('/api/update/start', _(update_handler.handle_start_update)),
        web.get('/api/update/progress', _(update_handler.handle_get_update_progress)),
        web.get('/api/update/mirrors', _(update_handler.handle_get_mirrors)),
        web.get('/api/update/test-mirrors', _(update_handler.handle_test_mirrors)),
        web.post('/api/update/mirror', _(update_handler.handle_set_custom_mirror)),
        web.post('/api/update/upload', _(update_handler.handle_upload_update)),
        web.get('/api/update/environment', _(update_handler.handle_detect_environment)),

        # ── 重启 ──
        web.post('/api/bot/restart', _(bot_restart.handle_restart)),

        # ── 插件市场 (GitHub 插件库) ──
        web.get('/api/market/list', _(plugin_market_handler.handle_market_list)),
        web.get('/api/market/categories', _(plugin_market_handler.handle_market_categories)),
        web.post('/api/market/detail', _(plugin_market_handler.handle_market_detail)),
        web.post('/api/market/refresh', _(plugin_market_handler.handle_market_refresh)),
        web.post('/api/market/preview', _(plugin_market_handler.handle_market_preview)),
        web.post('/api/market/install', _(plugin_market_handler.handle_market_install)),
        web.post('/api/market/uninstall', _(plugin_market_handler.handle_market_uninstall)),
        web.get('/api/market/local', _(plugin_market_handler.handle_local_plugins)),
        web.post('/api/market/local/read', _(plugin_market_handler.handle_local_plugin_read)),
        web.post('/api/market/local/save', _(plugin_market_handler.handle_local_plugin_save)),

        # ── OpenAPI ──
        web.post('/api/openapi/start-login', _(openapi_handler.handle_start_login)),
        web.post('/api/openapi/check-login', _(openapi_handler.handle_check_login)),
        web.post('/api/openapi/login-status', _(openapi_handler.handle_get_login_status)),
        web.post('/api/openapi/verify-login', _(openapi_handler.handle_verify_saved_login)),
        web.post('/api/openapi/logout', _(openapi_handler.handle_logout)),
        web.post('/api/openapi/botlist', _(openapi_handler.handle_get_botlist)),
        web.post('/api/openapi/botdata', _(openapi_handler.handle_get_botdata)),
        web.post('/api/openapi/notifications', _(openapi_handler.handle_get_notifications)),
        web.post('/api/openapi/whitelist', _(openapi_handler.handle_get_whitelist)),
        web.post('/api/openapi/whitelist/update', _(openapi_handler.handle_update_whitelist)),
        web.post('/api/openapi/whitelist/delete-qr', _(openapi_handler.handle_get_delete_qr)),
        web.post('/api/openapi/whitelist/check-delete-auth', _(openapi_handler.handle_check_delete_auth)),
        web.post('/api/openapi/whitelist/execute-delete', _(openapi_handler.handle_execute_delete_ip)),
        web.post('/api/openapi/whitelist/batch-add', _(openapi_handler.handle_batch_add_whitelist)),

        # ── 自定义页面 ──
        web.get('/api/web-pages', _(handle_get_web_pages)),
        web.get('/api/web-pages/{key}', _(handle_get_web_page_html)),

        # ── 数据库浏览 ──
        web.get('/api/database/list', _(database_browser.handle_list_databases)),
        web.post('/api/database/tables', _(database_browser.handle_list_tables)),
        web.post('/api/database/query', _(database_browser.handle_query_table)),
        web.post('/api/database/sql', _(database_browser.handle_execute_sql)),
        web.post('/api/database/delete', _(database_browser.handle_delete_rows)),

        # ── WebSocket / SSE ──
        web.get('/ws/panel', panel_ws.handle_ws),
        web.get('/api/sse/panel', panel_ws.handle_sse),
    ]


# ======================== 初始化 ========================

def set_context(bot_manager, base_dir: str):
    """注入运行时上下文到所有工具模块"""
    global _bot_manager, _base_dir
    _bot_manager = bot_manager
    _base_dir = base_dir

    robot_info.set_context(bot_manager)
    plugin_manager.set_context(base_dir, bot_manager)
    config_handler.set_context(base_dir)
    message_handler.set_context(base_dir, bot_manager)
    statistics_handler.set_context(bot_manager)
    update_handler.set_context(base_dir)
    bot_restart.set_context(base_dir)
    system_info.set_context(bot_manager)
    openapi_handler.set_context(base_dir)
    plugin_market_handler.set_context(base_dir)
    database_browser.set_context(bot_manager, base_dir)


# ======================== 内联路由处理 ========================

async def handle_login(request: web.Request):
    ip = auth.get_real_ip(request)
    auth.cleanup_expired_ip_bans()
    if auth.is_ip_banned(ip):
        return web.json_response({'success': False, 'error': 'IP 已被封禁'}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'success': False, 'error': '请求格式错误'}, status=400)

    password = body.get('password', '')
    from core.base.config import cfg
    admin_pwd = cfg.get('settings', 'web.admin_password', '')
    if not admin_pwd:
        return web.json_response({'success': False, 'error': '未配置管理员密码'}, status=500)

    if password != admin_pwd:
        auth.record_ip_access(ip, 'fail')
        remaining = auth.get_remaining_attempts(ip)
        if remaining <= 0:
            return web.json_response({
                'success': False, 'error': 'IP 已被封禁，12小时后解除'}, status=403)
        return web.json_response({
            'success': False, 'error': f'密码错误，还剩 {remaining} 次机会',
            'remaining': remaining}, status=401)

    auth.record_ip_access(ip, 'success')
    token = auth.create_session(request)
    return web.json_response({'success': True, 'token': token})


async def handle_auth_check(request: web.Request):
    return web.json_response({'success': True})


async def handle_get_bots(request: web.Request):
    bots = []
    if _bot_manager:
        for appid, inst in _bot_manager._bots.items():
            ws_connected = False
            if inst.ws_client:
                ws_connected = bool(getattr(inst.ws_client, '_session_id', None))
            avatar = getattr(inst, 'avatar_url', '') or ''
            robot_qq = getattr(inst, 'robot_qq', '') or ''
            if not avatar and robot_qq:
                avatar = f'http://q1.qlogo.cn/g?b=qq&nk={robot_qq}&s=100'
            bots.append({
                'appid': appid,
                'name': getattr(inst, 'name', '') or appid,
                'robot_qq': robot_qq,
                'bot_id': getattr(inst, 'bot_id', ''),
                'avatar': avatar,
                'connected': ws_connected,
                'connection_type': 'WebSocket' if inst.ws_client else 'Webhook',
            })
    return web.json_response({'success': True, 'bots': bots})


def _iter_bots(appid_filter=''):
    """按 appid 过滤机器人迭代器; 空字符串=全部"""
    if not _bot_manager:
        return []
    if appid_filter and appid_filter in _bot_manager._bots:
        return [(appid_filter, _bot_manager._bots[appid_filter])]
    return list(_bot_manager._bots.items())


_SEND_TYPES = frozenset(('plugin', 'onebot_send'))
_LOG_SQL = "SELECT * FROM log ORDER BY timestamp DESC, id DESC LIMIT 50"


def _query_bot_logs(log_type, appid_filter, post_fn=None):
    """从各机器人 SQLite 查询日志, 返回按时间排序的最近 50 条"""
    results = []
    for appid, inst in _iter_bots(appid_filter):
        try:
            rows = inst.log_service.query(log_type, _LOG_SQL)
            for r in rows:
                r['appid'] = appid
                r['bot_name'] = getattr(inst, 'name', appid)
                if post_fn:
                    post_fn(r)
            results.extend(rows)
        except Exception:
            pass
    results.sort(key=lambda r: (r.get('timestamp', ''), r.get('id', 0)))
    return results[-50:]


async def handle_recent_logs(request: web.Request):
    """最近日志 — 全部从 SQLite 读取, 不使用内存缓冲"""
    from core.storage.log import SharedLogService
    appid_filter = request.query.get('appid', '')

    def _tag_direction(r):
        if r.get('type') in _SEND_TYPES:
            r['is_bot'] = True
            r['direction'] = 'send'
        else:
            r['direction'] = 'receive'

    messages = _query_bot_logs('message', appid_filter, _tag_direction)
    lifecycle = _query_bot_logs('lifecycle', appid_filter)

    shared = SharedLogService._instance
    if shared:
        framework = shared.query('framework', _LOG_SQL)
        framework.reverse()
        errors = shared.query('error', _LOG_SQL)
        errors.reverse()
    else:
        framework = []
        errors = []

    return web.json_response({
        'message': messages,
        'framework': framework,
        'error': errors,
        'lifecycle': lifecycle,
    })


# ======================== 自定义页面 ========================

async def handle_get_web_pages(request: web.Request):
    from core.plugin.web_pages import get_pages
    return web.json_response({'success': True, 'pages': get_pages()})


async def handle_get_web_page_html(request: web.Request):
    from core.plugin.web_pages import get_page_html
    key = request.match_info['key']
    html = get_page_html(key)
    if html is None:
        return web.json_response({'success': False, 'error': '页面不存在'}, status=404)
    return web.Response(text=html, content_type='text/html', charset='utf-8')
