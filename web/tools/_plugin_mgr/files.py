"""插件文件操作 — toggle/reload/read/save/create/upload"""

import os
import re
import shutil

from aiohttp import web

from web.tools._plugin_mgr.shared import (
    get_pm,
    log,
    plugins_dir,
    validate_path,
)

_PLUGIN_TEMPLATE = """from core.plugin.decorators import handler


@handler(r"^指令$", name="示例命令", desc="示例插件")
async def handle_command(event, match):
    await event.reply("Hello, World!")
"""


# ==================== 启用/禁用 ====================


async def handle_toggle_plugin(request: web.Request):
    body = await request.json()
    plugin_path = body.get('path', '')
    action = body.get('action', '')
    if not plugin_path or action not in ('enable', 'disable'):
        return web.json_response({'success': False, 'message': '参数错误'}, status=400)

    plugin_path = os.path.normpath(plugin_path)
    pdir = plugins_dir()
    valid, abs_path = validate_path(plugin_path, pdir)
    if not valid:
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)

    is_disable = action == 'disable'
    expect_ext = '.py' if is_disable else '.py.ban'
    if not abs_path.endswith(expect_ext):
        return web.json_response({'success': False, 'message': f'只能操作 {expect_ext}'}, status=400)
    new_abs = (abs_path + '.ban') if is_disable else abs_path[:-4]
    if os.path.exists(new_abs):
        return web.json_response({'success': False, 'message': '目标文件已存在'}, status=409)
    os.rename(abs_path, new_abs)
    await _try_reload_plugin(new_abs if not is_disable else abs_path, pdir)
    label = '已禁用' if is_disable else '已启用'
    return web.json_response(
        {
            'success': True,
            'message': f'插件{label}',
            'new_path': new_abs.replace('\\', '/'),
        }
    )


async def _try_reload_plugin(file_path, pdir):
    """根据文件路径推导插件名并触发运行时热重载"""
    pm = get_pm()
    if not pm:
        return
    try:
        rel = os.path.relpath(file_path, pdir)
        plugin_name = rel.split(os.sep)[0]
        if plugin_name and plugin_name in pm.plugins:
            await pm.reload(plugin_name)
            log.info(f'插件文件变更触发热重载: {plugin_name}')
    except Exception as e:
        log.warning(f'自动热重载失败: {e}')


# ==================== 热重载 ====================


async def handle_reload_plugin(request: web.Request):
    body = await request.json()
    plugin_name = body.get('name', '')
    if not plugin_name:
        return web.json_response({'success': False, 'message': '缺少插件名'}, status=400)
    pm = get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    try:
        result = await pm.reload(plugin_name)
        if result:
            info = pm.plugins.get(plugin_name)
            count = len(info.handlers) if info else 0
            return web.json_response(
                {
                    'success': True,
                    'message': f'重载完成: {count} 个处理器',
                    'handler_count': count,
                }
            )
        return web.json_response({'success': False, 'message': '重载失败 (大型插件不支持热重载)'})
    except Exception as e:
        log.error(f'热重载 [{plugin_name}] 失败: {e}')
        return web.json_response({'success': False, 'message': f'重载异常: {e}'}, status=500)


# ==================== 读取/保存 ====================


async def handle_read_plugin(request: web.Request):
    body = await request.json()
    plugin_path = os.path.normpath(body.get('path', ''))
    if not plugin_path:
        return web.json_response({'success': False, 'message': '缺少路径'}, status=400)
    valid, abs_path = validate_path(plugin_path, plugins_dir())
    if not valid or not os.path.isfile(abs_path):
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)
    with open(abs_path, encoding='utf-8') as f:
        content = f.read()
    return web.json_response(
        {
            'success': True,
            'content': content,
            'path': plugin_path.replace('\\', '/'),
            'filename': os.path.basename(plugin_path),
        }
    )


async def handle_save_plugin(request: web.Request):
    body = await request.json()
    plugin_path = os.path.normpath(body.get('path', ''))
    content = body.get('content')
    if not plugin_path or content is None:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    valid, abs_path = validate_path(plugin_path, plugins_dir())
    if not valid:
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)
    if os.path.exists(abs_path):
        shutil.copy2(abs_path, abs_path + '.backup')
    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return web.json_response({'success': True, 'message': '插件已保存'})


# ==================== 创建 ====================


async def handle_create_plugin(request: web.Request):
    body = await request.json()
    directory = body.get('directory', '')
    filename = body.get('filename', '')
    if not directory or not filename:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    if not filename.endswith('.py'):
        filename += '.py'
    pdir = plugins_dir()
    target_dir = os.path.join(pdir, directory)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(pdir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    plugin_path = os.path.join(target_dir, filename)
    if os.path.exists(plugin_path):
        return web.json_response({'success': False, 'message': '文件已存在'}, status=409)
    os.makedirs(target_dir, exist_ok=True)
    with open(plugin_path, 'w', encoding='utf-8') as f:
        f.write(_PLUGIN_TEMPLATE)
    return web.json_response(
        {
            'success': True,
            'message': '插件已创建',
            'path': plugin_path.replace('\\', '/'),
        }
    )


async def handle_create_folder(request: web.Request):
    body = await request.json()
    folder_name = body.get('folder_name', '')
    parent_dir = body.get('parent_dir', '')
    if not folder_name:
        return web.json_response({'success': False, 'message': '缺少文件夹名'}, status=400)
    pdir = plugins_dir()
    target = os.path.join(pdir, parent_dir, folder_name) if parent_dir else os.path.join(pdir, folder_name)
    if not os.path.abspath(target).startswith(os.path.abspath(pdir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    if os.path.exists(target):
        return web.json_response({'success': False, 'message': '文件夹已存在'}, status=409)
    os.makedirs(target, exist_ok=True)
    return web.json_response({'success': True, 'message': '文件夹已创建'})


async def handle_get_folders(request: web.Request):
    pdir = plugins_dir()
    folders = []
    if os.path.isdir(pdir):
        for item in sorted(os.listdir(pdir)):
            if os.path.isdir(os.path.join(pdir, item)) and not item.startswith(('.', '__')):
                folders.append({'name': item, 'path': item})
    return web.json_response({'success': True, 'folders': folders})


# ==================== 上传 ====================


async def handle_upload_plugin(request: web.Request):
    reader = await request.multipart()
    file_field = None
    directory = 'alone'
    async for field in reader:
        if field.name == 'file':
            file_field = field
        elif field.name == 'directory':
            directory = (await field.text()).strip() or 'alone'

    if not file_field or not file_field.filename:
        return web.json_response({'success': False, 'message': '没有文件'}, status=400)
    filename = file_field.filename
    if not filename.endswith('.py'):
        return web.json_response({'success': False, 'message': '只能上传 .py'}, status=400)
    safe_name = re.sub(r'[^\w\u4e00-\u9fa5\-\.]', '_', filename)

    pdir = plugins_dir()
    target_dir = os.path.join(pdir, directory)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(pdir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    os.makedirs(target_dir, exist_ok=True)

    dest = os.path.join(target_dir, safe_name)
    if os.path.exists(dest):
        base = safe_name[:-3]
        c = 1
        while os.path.exists(dest):
            dest = os.path.join(target_dir, f'{base}_{c}.py')
            c += 1

    content = await file_field.read()
    with open(dest, 'wb') as f:
        f.write(content)
    return web.json_response(
        {
            'success': True,
            'message': f'上传成功: {os.path.basename(dest)}',
            'path': dest.replace('\\', '/'),
        }
    )
