"""开放平台自定义菜单与指令面板的内置 Web API。"""

from __future__ import annotations

from aiohttp import web

_manager = None

_CONFIG_FORMAT = 'elainabot-function-config'
_CONFIG_VERSION = 1
_PANEL_SCOPES = ('c2c', 'group', 'channel', 'dm')
_MENU_TYPES = {'send_message', 'link', 'switch', 'menu'}
_CHILD_MENU_TYPES = {'send_message', 'link'}


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


async def _list_all_panels(sender, scope):
    records = []
    cursor = ''
    for _ in range(100):
        data, error = await sender.get_panels(
            scope,
            cursor=cursor,
            limit=50,
            return_error=True,
        )
        if error:
            return None, error
        page = data if isinstance(data, dict) else {}
        page_records = page.get('records', [])
        if not isinstance(page_records, list):
            return None, _api_error('指令面板 records 必须为数组')
        records.extend(record for record in page_records if isinstance(record, dict))
        next_cursor = str(page.get('next_cursor') or '')
        if page.get('is_end') or not next_cursor:
            return records, None
        if next_cursor == cursor:
            return None, _api_error('指令面板分页游标未推进')
        cursor = next_cursor
    return None, _api_error('指令面板分页超过安全上限')


def _menu_item_for_import(item, *, child=False):
    if not isinstance(item, dict):
        raise ValueError('菜单项必须为 JSON 对象')
    item_type = str(item.get('type') or '').strip().lower()
    allowed = _CHILD_MENU_TYPES if child else _MENU_TYPES
    if item_type not in allowed:
        raise ValueError(f'不支持的菜单类型: {item_type or "空"}')
    name = str(item.get('name') or '').strip()
    if not name:
        raise ValueError('菜单项缺少名称')
    result = {'type': item_type, 'name': name}
    if item_type == 'send_message':
        value = str(item.get('send_message') or '').strip()
        if not value:
            raise ValueError(f'菜单“{name}”缺少指令')
        result['send_message'] = value
    elif item_type == 'link':
        value = str(item.get('link') or '').strip()
        if not value.lower().startswith('https://'):
            raise ValueError(f'菜单“{name}”的链接必须以 https:// 开头')
        result['link'] = value
    elif item_type == 'switch':
        switch = item.get('switch')
        if not isinstance(switch, dict) or not str(switch.get('switch_id') or '').strip():
            raise ValueError(f'菜单“{name}”缺少开关标识')
        result['switch'] = {
            'switch_id': str(switch['switch_id']).strip(),
            'default': bool(switch.get('default')),
        }
    else:
        children = item.get('sub_menu_items', [])
        if not isinstance(children, list) or len(children) > 5:
            raise ValueError(f'菜单“{name}”的子项必须为不超过 5 项的数组')
        result['sub_menu_items'] = [
            _menu_item_for_import(value, child=True) for value in children
        ]
    return result


def _menu_for_import(menu):
    if menu is None:
        return None
    if not isinstance(menu, dict):
        raise ValueError('menu 必须为 JSON 对象或 null')
    items = menu.get('items', [])
    if not isinstance(items, list) or len(items) > 10:
        raise ValueError('menu.items 必须为不超过 10 项的数组')
    return {'items': [_menu_item_for_import(item) for item in items]}


def _panel_item_for_import(item):
    if not isinstance(item, dict):
        raise ValueError('指令项必须为 JSON 对象')
    item_type = str(item.get('type') or '').strip().lower()
    if item_type not in ('command', 'link'):
        raise ValueError(f'不支持的指令类型: {item_type or "空"}')
    name = str(item.get('name') or '').strip()
    if not name:
        raise ValueError('指令项缺少名称')
    result = {'type': item_type, 'name': name}
    desc = str(item.get('desc') or '').strip()
    if desc:
        result['desc'] = desc
    if item.get('only_admin'):
        result['only_admin'] = True
    if item_type == 'link':
        link = str(item.get('link') or '').strip()
        if not link.lower().startswith('https://'):
            raise ValueError(f'指令“{name}”的链接必须以 https:// 开头')
        result['link'] = link
    return result


def _panel_for_import(record):
    if not isinstance(record, dict):
        raise ValueError('panels 中的每项都必须为 JSON 对象')
    scope = str(record.get('scope') or '').strip().lower()
    if scope not in _PANEL_SCOPES:
        raise ValueError(f'不支持的指令场景: {scope or "空"}')
    target_type = str(record.get('target_type') or 'all').strip().lower()
    if target_type not in ('all', 'specific'):
        raise ValueError('target_type 只能为 all 或 specific')
    if target_type == 'specific' and scope not in ('c2c', 'group'):
        raise ValueError('channel 和 dm 场景不支持指定对象')
    panel = record.get('panel')
    if not isinstance(panel, dict):
        raise ValueError('指令配置缺少 panel 对象')
    items = panel.get('items', [])
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError('panel.items 必须为 1 至 20 项的数组')
    result = {
        'scope': scope,
        'target_type': target_type,
        'panel': {
            'items': [_panel_item_for_import(item) for item in items],
            'remark': str(panel.get('remark') or '').strip(),
        },
    }
    if target_type == 'specific':
        key = 'user_openids' if scope == 'c2c' else 'group_openids'
        values = record.get(key, [])
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError(f'{key} 必须为 1 至 20 项的数组')
        result[key] = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if not result[key]:
            raise ValueError(f'{key} 不能为空')
    return result


def _config_for_import(config):
    if not isinstance(config, dict):
        raise ValueError('导入文件必须为 JSON 对象')
    if config.get('format') != _CONFIG_FORMAT:
        raise ValueError('不是 ElainaBot 功能配置文件')
    if config.get('version') != _CONFIG_VERSION:
        raise ValueError(f'不支持的配置版本: {config.get("version")}')
    menu = _menu_for_import(config.get('menu'))
    raw_panels = config.get('panels', [])
    if not isinstance(raw_panels, list):
        raise ValueError('panels 必须为数组')
    panels = [_panel_for_import(record) for record in raw_panels]
    global_scopes = [record['scope'] for record in panels if record['target_type'] == 'all']
    if len(global_scopes) != len(set(global_scopes)):
        raise ValueError('每个场景最多包含一个全局指令面板')
    return {'menu': menu, 'panels': panels}


async def handle_export_config(request):
    """导出不绑定机器人的完整功能配置。"""
    sender, denied = _require_sender(request.query.get('appid', ''))
    if denied:
        return denied
    menu_data, error = await sender.get_global_menu(return_error=True)
    if error:
        return _failure(error)
    menu = menu_data.get('menu') if isinstance(menu_data, dict) else None
    exported_panels = []
    for scope in _PANEL_SCOPES:
        records, error = await _list_all_panels(sender, scope)
        if error:
            return _failure(error)
        for record in records:
            detail = record
            panel_id = str(record.get('panel_id') or '')
            if panel_id:
                loaded, error = await sender.get_panel(panel_id, return_error=True)
                if error:
                    return _failure(error)
                if isinstance(loaded, dict):
                    detail = {**record, **loaded}
            panel = detail.get('panel')
            if not isinstance(panel, dict):
                return _failure(_api_error('面板详情缺少 panel 对象'))
            portable = {
                'scope': scope,
                'target_type': str(detail.get('target_type') or 'all').lower(),
                'panel': {
                    'items': panel.get('items', []),
                    'remark': str(panel.get('remark') or ''),
                },
            }
            if portable['target_type'] == 'specific':
                key = 'user_openids' if scope == 'c2c' else 'group_openids'
                portable[key] = detail.get(key, [])
            exported_panels.append(portable)
    try:
        config = _config_for_import({
            'format': _CONFIG_FORMAT,
            'version': _CONFIG_VERSION,
            'menu': menu,
            'panels': exported_panels,
        })
    except ValueError as error:
        return _failure(_api_error(f'平台配置无法导出: {error}'))
    return _success({
        'format': _CONFIG_FORMAT,
        'version': _CONFIG_VERSION,
        **config,
    })


async def handle_import_config(request):
    """使用可移植 JSON 覆盖当前指定机器人的完整功能配置。"""
    body, sender, denied = await _body_sender(request)
    if denied:
        return denied
    try:
        config = _config_for_import(body.get('config'))
    except ValueError as error:
        return _failure(str(error), status=400)

    existing_ids = []
    for scope in _PANEL_SCOPES:
        records, error = await _list_all_panels(sender, scope)
        if error:
            return _failure(error)
        existing_ids.extend(str(record.get('panel_id') or '') for record in records)
    existing_ids = list(dict.fromkeys(panel_id for panel_id in existing_ids if panel_id))

    deleted = 0
    for panel_id in existing_ids:
        success, data = await sender.delete_panel(panel_id)
        if not success:
            return _failure({
                'message': f'导入未完成，删除原指令面板失败: {data.get("message", "未知错误") if isinstance(data, dict) else data}',
                'detail': data,
            })
        deleted += 1

    created = 0
    for record in config['panels']:
        success, data = await sender.create_panel(
            record['scope'],
            record['panel'],
            target_type=record['target_type'],
            user_openids=record.get('user_openids'),
            group_openids=record.get('group_openids'),
        )
        if not success:
            return _failure({
                'message': f'导入未完成，创建指令面板失败: {data.get("message", "未知错误") if isinstance(data, dict) else data}',
                'detail': data,
            })
        created += 1

    success, data = await sender.update_global_menu(config['menu'])
    if not success:
        return _failure({
            'message': f'指令已导入，但菜单更新失败: {data.get("message", "未知错误") if isinstance(data, dict) else data}',
            'detail': data,
        })
    return _success(
        {'deleted_panels': deleted, 'created_panels': created},
        message='功能配置已导入当前机器人',
    )


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
