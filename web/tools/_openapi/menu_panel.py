"""开放平台自定义菜单与指令面板的内置 Web API。"""

from __future__ import annotations

from aiohttp import web

_manager = None


def set_context(bot_manager):
    global _manager
    _manager = bot_manager


def _success(data=None, *, message=''):
    return web.json_response({
        'success': True,
        'code': 0,
        'message': message,
        'data': data,
    })


def _api_error(message):
    return {'message': message, 'code': -1}


def _failure(error, *, default='操作失败', status=None):
    if isinstance(error, dict):
        code = error.get('code', error.get('err_code', -1))
        message = error.get('message') or error.get('msg') or default
        detail = error
    else:
        code = -1
        message = str(error or default)
        detail = {'message': message, 'code': code}
    if status is None:
        status = 502 if str(code) == '-1' else 400
    return web.json_response(
        {
            'success': False,
            'code': code,
            'message': message,
            'data': detail,
        },
        status=status,
    )


async def _json_body(request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _get_sender(appid):
    manager = _manager
    if manager is None:
        return None
    getter = getattr(manager, 'get_bot', None)
    bot = (
        getter(str(appid))
        if callable(getter)
        else getattr(manager, '_bots', {}).get(str(appid))
    )
    return getattr(bot, 'sender', None) if bot is not None else None


def _require_sender(appid):
    if not appid:
        return None, _failure(_api_error('缺少 appid'), status=400)
    sender = _get_sender(appid)
    if sender is None:
        return None, _failure(_api_error('机器人未运行或不存在'), status=404)
    return sender, None


def _running_bots():
    manager = _manager
    if manager is None:
        return []
    bots = getattr(manager, '_bots', {}) or {}
    result = []
    for appid, bot in bots.items():
        robot_qq = str(getattr(bot, 'robot_qq', '') or '')
        result.append({
            'appid': str(appid),
            'name': str(getattr(bot, 'name', '') or appid),
            'robot_qq': robot_qq,
            'avatar': str(getattr(bot, 'avatar_url', '') or (
                f'http://q1.qlogo.cn/g?b=qq&nk={robot_qq}&s=100'
                if robot_qq else ''
            )),
        })
    return result


async def handle_bots(request):
    return _success({'bots': _running_bots()})


async def _body_sender(request):
    body = await _json_body(request)
    if body is None:
        return None, None, _failure('请求体必须为 JSON 对象', status=400)
    sender, denied = _require_sender(body.get('appid', ''))
    return body, sender, denied


async def handle_get_menu(request):
    sender, denied = _require_sender(request.query.get('appid', ''))
    if denied:
        return denied
    data, error = await sender.get_global_menu(return_error=True)
    return _failure(error) if error else _success(data)


async def handle_update_menu(request):
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    success, data = await sender.update_global_menu(body.get('menu'))
    return _success(data, message='菜单已更新') if success else _failure(data)


async def handle_get_panels(request):
    sender, denied = _require_sender(request.query.get('appid', ''))
    if denied:
        return denied
    data, error = await sender.get_panels(
        request.query.get('scope', ''),
        cursor=request.query.get('cursor', ''),
        limit=request.query.get('limit', 20),
        return_error=True,
    )
    return _failure(error) if error else _success(data)


async def handle_create_panel(request):
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    success, data = await sender.create_panel(
        body.get('scope'),
        body.get('panel'),
        target_type=body.get('target_type', 'all'),
        user_openids=body.get('user_openids'),
        group_openids=body.get('group_openids'),
    )
    return _success(data, message='面板已创建') if success else _failure(data)


async def handle_get_panel(request):
    sender, denied = _require_sender(request.query.get('appid', ''))
    if denied:
        return denied
    data, error = await sender.get_panel(
        request.query.get('panel_id', ''),
        return_error=True,
    )
    return _failure(error) if error else _success(data)


async def handle_update_panel(request):
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    success, data = await sender.update_panel(
        body.get('panel_id', ''),
        body.get('panel'),
    )
    return _success(data, message='面板已更新') if success else _failure(data)


async def handle_delete_panel(request):
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    success, data = await sender.delete_panel(body.get('panel_id', ''))
    return _success(data, message='面板已删除') if success else _failure(data)


async def handle_update_targets(request):
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    success, data = await sender.update_panel_targets(
        body.get('panel_id', ''),
        body.get('op', ''),
        user_openids=body.get('user_openids'),
        group_openids=body.get('group_openids'),
    )
    message = '关联对象已更新'
    return _success(data, message=message) if success else _failure(data)


async def _append_panel_items(
    sender,
    scope,
    target_type,
    items,
    *,
    user_openids=None,
    group_openids=None,
):
    """Append commands to the existing global panel for a scope."""
    if not isinstance(items, list) or not items:
        return False, _api_error('append_items 必须为非空数组'), ''
    if any(not isinstance(item, dict) for item in items):
        return False, _api_error('append_items 中的每项都必须为 JSON 对象'), ''

    scope = str(scope or '').strip().lower()
    target_type = str(target_type or 'all').strip().lower()
    if target_type == 'all':
        listing, error = await sender.get_panels(
            scope,
            limit=50,
            return_error=True,
        )
        if error:
            return False, error, ''
        records = listing.get('records', []) if isinstance(listing, dict) else []
        current = next((
            record for record in records
            if isinstance(record, dict)
            and str(record.get('target_type', 'all')).lower() == 'all'
        ), None)
        if current:
            panel_id = str(current.get('panel_id', '') or '')
            panel = current.get('panel')
            if not isinstance(panel, dict):
                detail, error = await sender.get_panel(panel_id, return_error=True)
                if error:
                    return False, error, ''
                panel = detail.get('panel', detail) if isinstance(detail, dict) else None
            if not isinstance(panel, dict):
                return False, _api_error('面板详情缺少 panel 对象'), ''
            existing_items = panel.get('items', [])
            if not isinstance(existing_items, list):
                return False, _api_error('面板 items 必须为数组'), ''
            merged_items = [*existing_items, *items]
            if len(merged_items) > 20:
                return False, _api_error('单个指令面板最多包含 20 条指令'), ''
            merged_panel = dict(panel)
            merged_panel['items'] = merged_items
            success, data = await sender.update_panel(panel_id, merged_panel)
            return success, data, 'updated'

    panel = {'items': items, 'remark': ''}
    success, data = await sender.create_panel(
        scope,
        panel,
        target_type=target_type,
        user_openids=user_openids,
        group_openids=group_openids,
    )
    return success, data, 'created'


def _validate_save_payload(changes, deleted_panel_ids):
    if not isinstance(changes, list) or not isinstance(deleted_panel_ids, list):
        return 'changes 和 deleted_panel_ids 必须为数组'
    if any(not str(panel_id or '') for panel_id in deleted_panel_ids):
        return 'deleted_panel_ids 不能包含空值'
    for change in changes:
        if not isinstance(change, dict):
            return '每个面板变更都必须为 JSON 对象'
        panel_id = str(change.get('panel_id', '') or '')
        if panel_id:
            panel = change.get('panel')
            targets = change.get('target_changes', [])
            if panel is not None and not isinstance(panel, dict):
                return 'panel 必须为 JSON 对象'
            if not isinstance(targets, list):
                return 'target_changes 必须为数组'
            if any(not isinstance(target, dict) for target in targets):
                return '关联对象变更必须为 JSON 对象'
            if panel is None and not targets:
                return '已有面板变更必须包含 panel 或 target_changes'
        elif change.get('append_items') is None and not isinstance(change.get('panel'), dict):
            return '新建面板必须包含 panel 对象'
    return ''


async def _apply_existing_change(sender, panel_id, change, result):
    panel = change.get('panel')
    if panel is not None:
        success, data = await sender.update_panel(panel_id, panel)
        if not success:
            return data
        result['updated'].append(data or {'panel_id': panel_id})

    for target in change.get('target_changes', []):
        success, data = await sender.update_panel_targets(
            panel_id,
            target.get('op', ''),
            user_openids=target.get('user_openids'),
            group_openids=target.get('group_openids'),
        )
        if not success:
            return data
        result['targets'].append(data or {'panel_id': panel_id})
    return None


async def _apply_new_change(sender, change, default_scope, result):
    append_items = change.get('append_items')
    if append_items is not None:
        success, data, operation = await _append_panel_items(
            sender,
            change.get('scope', default_scope),
            change.get('target_type', 'all'),
            append_items,
            user_openids=change.get('user_openids'),
            group_openids=change.get('group_openids'),
        )
        if success:
            result[operation].append(data or {})
        return None if success else data

    success, data = await sender.create_panel(
        change.get('scope', default_scope),
        change['panel'],
        target_type=change.get('target_type', 'all'),
        user_openids=change.get('user_openids'),
        group_openids=change.get('group_openids'),
    )
    if success:
        result['created'].append(data or {})
    return None if success else data


async def handle_save_panels(request):
    """Apply only the staged panel changes from the Web editor."""
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied

    changes = body.get('changes', [])
    deleted_panel_ids = body.get('deleted_panel_ids', [])
    validation_error = _validate_save_payload(changes, deleted_panel_ids)
    if validation_error:
        return _failure(validation_error, status=400)

    result = {'created': [], 'updated': [], 'deleted': [], 'targets': []}
    for raw_panel_id in dict.fromkeys(deleted_panel_ids):
        panel_id = str(raw_panel_id or '')
        success, data = await sender.delete_panel(panel_id)
        if not success:
            return _failure(data)
        result['deleted'].append(data or {'panel_id': panel_id})

    for change in changes:
        panel_id = str(change.get('panel_id', '') or '')
        if panel_id:
            error = await _apply_existing_change(sender, panel_id, change, result)
        else:
            error = await _apply_new_change(sender, change, body.get('scope'), result)
        if error:
            return _failure(error)

    return _success(result, message='指令面板更改已保存')
