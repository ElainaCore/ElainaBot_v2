"""Web 面板 API 路由"""

import asyncio
import logging

from aiohttp import web

import web.auth as auth
import web.tools._bot.info as robot_info
import web.tools._bot.restart as bot_restart
import web.tools._config.handler as config_handler
import web.tools._database.browser as database_browser
import web.tools._market.install as _market_install
import web.tools._market.local as _market_local
import web.tools._market.market as _market_market
import web.tools._market.shared as _market_shared
import web.tools._message.handlers as message_handler
import web.tools._openapi.handler as openapi_handler
import web.tools._plugin_mgr.config as _plugin_mgr_config
import web.tools._plugin_mgr.files as _plugin_mgr_files
import web.tools._plugin_mgr.module as _plugin_mgr_module
import web.tools._plugin_mgr.scan as _plugin_mgr_scan
import web.tools._plugin_mgr.shared as _plugin_mgr_shared
import web.tools._stats.log as log_query
import web.tools._stats.statistics as statistics_handler
import web.tools._stats.system as system_info
import web.tools._updater.handlers as update_handler
import web.ws as panel_ws
from web.response import error, json_body, ok
from web.tools._bots import configured_bot_payload, iter_bots, running_bot_payload

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
        web.get('/api/auth/password-status', _(handle_password_status)),
        # ── 机器人 ──
        web.get('/api/bots', _(handle_get_bots)),
        web.post('/api/bots/toggle', _(handle_toggle_bot)),
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
        web.get('/api/plugins/scan', _(_plugin_mgr_scan.handle_scan_plugins)),
        web.get('/api/plugins/scan-dirs', _(_plugin_mgr_scan.handle_scan_plugin_dirs)),
        web.post('/api/plugins/toggle', _(_plugin_mgr_files.handle_toggle_plugin)),
        web.post('/api/plugins/read', _(_plugin_mgr_files.handle_read_plugin)),
        web.post('/api/plugins/save', _(_plugin_mgr_files.handle_save_plugin)),
        web.post('/api/plugins/create', _(_plugin_mgr_files.handle_create_plugin)),
        web.post('/api/plugins/create-folder', _(_plugin_mgr_files.handle_create_folder)),
        web.get('/api/plugins/folders', _(_plugin_mgr_files.handle_get_folders)),
        web.post('/api/plugins/upload', _(_plugin_mgr_files.handle_upload_plugin)),
        web.post('/api/plugins/reload', _(_plugin_mgr_files.handle_reload_plugin)),
        web.post(
            '/api/plugins/config-files',
            _(_plugin_mgr_config.handle_plugin_config_files),
        ),
        web.get('/api/plugins/bots', _(_plugin_mgr_config.handle_get_plugin_bots)),
        web.post('/api/plugins/bots', _(_plugin_mgr_config.handle_set_plugin_bots)),
        # ── 模块管理 ──
        web.get('/api/modules/scan', _(_plugin_mgr_module.handle_scan_modules)),
        web.post('/api/modules/toggle', _(_plugin_mgr_module.handle_module_toggle)),
        web.post('/api/modules/upload', _(_plugin_mgr_module.handle_module_upload)),
        # ── 通用配置读写 (模块 + 插件) ──
        web.post('/api/config-file/read', _(_plugin_mgr_config.handle_read_config)),
        web.post('/api/config-file/save', _(_plugin_mgr_config.handle_save_config)),
        # ── 配置 ──
        web.get('/api/config', _(config_handler.handle_get_config)),
        web.post('/api/config/save', _(config_handler.handle_save_config)),
        web.post('/api/config/qr-bind/start', _(config_handler.handle_qr_bind_start)),
        web.post('/api/config/qr-bind/poll', _(config_handler.handle_qr_bind_poll)),
        # ── 消息 ──
        web.post('/api/message/chats', _(message_handler.handle_get_chats)),
        web.post('/api/message/history', _(message_handler.handle_get_chat_history)),
        web.post('/api/message/send', _(message_handler.handle_send_message)),
        web.post('/api/message/nickname', _(message_handler.handle_get_nickname)),
        web.post('/api/message/nicknames', _(message_handler.handle_get_nicknames_batch)),
        web.post('/api/message/recall', _(message_handler.handle_recall_message)),
        web.get('/api/message/remarks', _(message_handler.handle_get_remarks)),
        web.post('/api/message/remarks', _(message_handler.handle_set_remark)),
        web.post('/api/message/remarks/delete', _(message_handler.handle_delete_remark)),
        web.post('/api/message/group-roles', _(message_handler.handle_get_group_roles)),
        # ── 统计 ──
        web.get('/api/statistics', _(statistics_handler.handle_get_statistics)),
        web.get('/api/statistics/core', _(statistics_handler.handle_get_core)),
        web.get('/api/statistics/summary', _(statistics_handler.handle_get_summary)),
        web.get('/api/statistics/active', _(statistics_handler.handle_get_active)),
        web.get('/api/statistics/top', _(statistics_handler.handle_get_top)),
        web.get('/api/statistics/events', _(statistics_handler.handle_get_events)),
        web.get('/api/statistics/totals', _(statistics_handler.handle_get_totals)),
        web.get('/api/statistics/hourly', _(statistics_handler.handle_get_hourly_statistics)),
        web.get('/api/statistics/chart', _(statistics_handler.handle_get_chart_data)),
        web.get(
            '/api/statistics/task/{task_id}',
            _(statistics_handler.handle_get_task_status),
        ),
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
        web.get('/api/market/list', _(_market_market.handle_market_list)),
        web.get('/api/market/categories', _(_market_market.handle_market_categories)),
        web.post('/api/market/detail', _(_market_market.handle_market_detail)),
        web.post('/api/market/refresh', _(_market_market.handle_market_refresh)),
        web.post('/api/market/preview', _(_market_install.handle_market_preview)),
        web.post('/api/market/install', _(_market_install.handle_market_install)),
        web.post('/api/market/uninstall', _(_market_install.handle_market_uninstall)),
        web.get('/api/market/local', _(_market_local.handle_local_plugins)),
        web.post('/api/market/local/read', _(_market_local.handle_local_plugin_read)),
        web.post('/api/market/local/save', _(_market_local.handle_local_plugin_save)),
        web.get('/api/market/mirror', _(_market_market.handle_market_get_mirror)),
        web.post('/api/market/mirror', _(_market_market.handle_market_set_mirror)),
        web.post('/api/market/mirror/test', _(_market_market.handle_market_test_mirror)),
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
        web.post(
            '/api/openapi/whitelist/check-delete-auth',
            _(openapi_handler.handle_check_delete_auth),
        ),
        web.post(
            '/api/openapi/whitelist/execute-delete',
            _(openapi_handler.handle_execute_delete_ip),
        ),
        web.post(
            '/api/openapi/whitelist/batch-add',
            _(openapi_handler.handle_batch_add_whitelist),
        ),
        web.post('/api/openapi/events', _(openapi_handler.handle_get_event_list)),
        web.post('/api/openapi/events/auth-qr', _(openapi_handler.handle_get_event_auth_qr)),
        web.post('/api/openapi/events/modify', _(openapi_handler.handle_modify_event_subscription)),
        web.post('/api/openapi/webhook', _(openapi_handler.handle_get_webhook)),
        web.post('/api/openapi/webhook/suggest', _(openapi_handler.handle_webhook_suggest)),
        web.post('/api/openapi/webhook/check', _(openapi_handler.handle_check_webhook)),
        web.post('/api/openapi/webhook/auth-qr', _(openapi_handler.handle_get_webhook_auth_qr)),
        web.post('/api/openapi/webhook/set', _(openapi_handler.handle_set_webhook)),
        # ── 新版开放平台 (v2, 内测) ──
        web.post('/api/openapi/v2/start-login', _(openapi_handler.handle_v2_start_login)),
        web.post('/api/openapi/v2/check-login', _(openapi_handler.handle_v2_check_login)),
        web.post('/api/openapi/v2/status', _(openapi_handler.handle_v2_status)),
        web.post('/api/openapi/v2/developers', _(openapi_handler.handle_v2_developers)),
        web.post('/api/openapi/v2/switch-developer', _(openapi_handler.handle_v2_switch_developer)),
        web.post('/api/openapi/v2/proxy', _(openapi_handler.handle_v2_proxy)),
        web.post('/api/openapi/v2/upload-avatar', _(openapi_handler.handle_v2_upload_avatar)),
        # ── 自定义页面 ──
        web.get('/api/web-pages', _(handle_get_web_pages)),
        web.get('/api/web-pages/{key}', _(handle_get_web_page_html)),
        # ── 插件自定义路由 (动态分发, 鉴权由路由自身的 auth 标志决定) ──
        web.route('*', '/api/ext/{tail:.*}', handle_ext_route),
        # ── 数据库浏览 ──
        web.get('/api/database/list', _(database_browser.handle_list_databases)),
        web.post('/api/database/tables', _(database_browser.handle_list_tables)),
        web.post('/api/database/query', _(database_browser.handle_query_table)),
        web.post('/api/database/sql', _(database_browser.handle_execute_sql)),
        web.post('/api/database/delete', _(database_browser.handle_delete_rows)),
        web.post('/api/database/search', _(database_browser.handle_search_database)),
        web.post('/api/database/browse', _(database_browser.handle_browse_files)),
        web.post('/api/database/mount', _(database_browser.handle_mount_database)),
        web.post('/api/database/unmount', _(database_browser.handle_unmount_database)),
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
    _plugin_mgr_shared.set_context(base_dir, bot_manager)
    config_handler.set_context(base_dir)
    from web.tools._message.shared import set_context as _msg_set_ctx

    _msg_set_ctx(base_dir, bot_manager)
    statistics_handler.set_context(bot_manager, base_dir)
    update_handler.set_context(base_dir)
    bot_restart.set_context(base_dir)
    system_info.set_context(bot_manager)
    openapi_handler.set_context(base_dir, bot_manager)
    _market_shared.set_context(base_dir)
    database_browser.set_context(bot_manager, base_dir)


# ======================== 内联路由处理 ========================


async def handle_login(request: web.Request):
    ip = auth.get_real_ip(request)
    auth.cleanup_expired_ip_bans()
    if auth.is_ip_banned(ip):
        return error('IP 已被封禁', status=403)

    body = await json_body(request)
    if body is None:
        return error('请求格式错误', status=400)

    password = body.get('password', '')
    from core.base.config import cfg

    admin_pwd = cfg.get('settings', 'web.admin_password', '')
    if not admin_pwd:
        return error('未配置管理员密码', status=500)

    if not auth.verify_password(password, admin_pwd):
        auth.record_ip_access(ip, 'fail')
        remaining = auth.get_remaining_attempts(ip)
        if remaining <= 0:
            return error('IP 已被封禁，12小时后解除', status=403)
        return error(f'密码错误，还剩 {remaining} 次机会', status=401, data={'remaining': remaining})

    if not auth.is_hashed(admin_pwd):
        cfg.set_value('settings', 'web.admin_password', auth.hash_password(password))

    auth.record_ip_access(ip, 'success')
    token = auth.create_session(request)
    is_weak = password in _WEAK_PASSWORDS
    return ok({'token': token, 'is_weak': is_weak})


async def handle_auth_check(request: web.Request):
    return ok()


_WEAK_PASSWORDS = frozenset({'admin', '123456', 'password', 'admin123', '12345678'})


async def handle_password_status(request: web.Request):
    from core.base.config import cfg

    pwd = cfg.get('settings', 'web.admin_password', '')
    is_default = not pwd or (not auth.is_hashed(pwd) and pwd in _WEAK_PASSWORDS)
    return ok({'is_default': is_default})


async def handle_get_bots(request: web.Request):
    from core.base.config import cfg

    bots = []
    # 已启动的机器人
    running_appids = set()
    if _bot_manager:
        for appid, inst in _bot_manager._bots.items():
            running_appids.add(appid)
            bots.append(running_bot_payload(appid, inst))
    # 未启动的机器人 (已关闭)
    for bc in cfg.get_bot_configs():
        appid = str(bc.get('appid', ''))
        if appid and bc.get('secret') and appid not in running_appids:
            bots.append(configured_bot_payload(bc))
    return ok({'bots': bots})


async def handle_toggle_bot(request: web.Request):
    """切换机器人 enabled 开关"""
    from core.base.config import cfg

    body = await json_body(request)
    if body is None:
        return error('请求格式错误', status=400)

    appid = str(body.get('appid', ''))
    enabled = body.get('enabled')
    if not appid or enabled is None:
        return error('缺少 appid 或 enabled 参数', status=400)

    if not cfg.set_bot_setting(appid, 'enabled', bool(enabled)):
        return error('未找到该机器人', status=404)

    # 同步等待机器人启停完成, 保证前端 fetchBots 能拿到最新状态
    from core.application import get_app

    app = get_app()
    sync_error = ''
    if app and app.bot_registry is not None:
        try:
            await app.bot_registry._sync()
        except Exception as e:
            log.error(f'机器人同步失败: {e}')
            sync_error = str(e)

    status = '启用' if enabled else '关闭'
    if sync_error:
        return ok(message=f'机器人 {appid} 已{status} (同步异常: {sync_error})')
    return ok(message=f'机器人 {appid} 已{status}')


# 使用 id (AUTOINCREMENT 主键) 排序, 走 B-tree 倒序扫描, O(LIMIT) 不全表扫描
_LOG_SQL = 'SELECT * FROM log ORDER BY id DESC LIMIT 50'


def _query_bot_logs(log_type, appid_filter, post_fn=None):
    """从各机器人 SQLite 查询日志, 返回按 id 排序的最近 50 条 (同步, 由 executor 调用)"""
    results = []
    for appid, inst in iter_bots(_bot_manager, appid_filter):
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
    # id 是 AUTOINCREMENT, 按 (appid, id) 视为时间顺序, 取最新 50 条
    results.sort(key=lambda r: r.get('id', 0))
    return results[-50:]


def _tag_direction(r):
    if r.get('direction') == 'send':
        r['is_bot'] = True


def _tag_lifecycle_extra(r):
    if r.get('extra'):
        r['raw_message'] = r['extra']


def _gather_recent_logs_sync(appid_filter):
    """同步聚合所有日志查询 (在 executor 中执行, 避免阻塞事件循环)"""
    from core.base import console as _console
    from core.storage.log import SharedLogService

    messages = _query_bot_logs('message', appid_filter, _tag_direction)
    lifecycle = _query_bot_logs('lifecycle', appid_filter, _tag_lifecycle_extra)
    shared = SharedLogService._instance
    if shared:
        framework = shared.query('framework', _LOG_SQL)
        framework.reverse()
        errors = shared.query('error', _LOG_SQL)
        errors.reverse()
    else:
        framework = []
        errors = []
    return {
        'message': messages,
        'framework': framework,
        'error': errors,
        'lifecycle': lifecycle,
        'console': _console.get_lines(),
    }


async def handle_recent_logs(request: web.Request):
    """最近日志 — SQLite 同步查询放到 executor, 不阻塞事件循环"""
    appid_filter = request.query.get('appid', '')
    loop = asyncio.get_running_loop()
    payload = await loop.run_in_executor(None, _gather_recent_logs_sync, appid_filter)
    return web.json_response(payload)


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


# ======================== 插件自定义路由 ========================


async def handle_ext_route(request: web.Request):
    """动态分发插件用 register_route 注册的 /api/ext/ 路由 (查表执行, 支持热重载)。"""
    from core.plugin.web_pages import match_route

    entry = match_route(request.method, request.path)
    if entry is None:
        return web.json_response({'success': False, 'error': '路由不存在'}, status=404)
    if entry['auth'] and not auth.validate_token(request):
        return web.json_response({'success': False, 'error': '未登录或会话已过期'}, status=401)
    return await entry['handler'](request)
