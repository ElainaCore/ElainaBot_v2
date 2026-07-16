"""插件文件操作 — toggle/reload/read/save/create/upload"""

import contextlib
import os
import re
import shutil
import tempfile
import zipfile
from typing import cast

from aiohttp import BodyPartReader, web

from web.tools._plugin_mgr.shared import (
    get_pm,
    log,
    plugins_dir,
    validate_path,
)
from web.tools._zipsafe import safe_extractall

_PLUGIN_TEMPLATE = """from core.plugin.decorators import handler


@handler(r"^指令$", name="示例命令", desc="示例插件")
async def handle_command(event, match):
    await event.reply("Hello, World!")
"""


# ==================== 启用/禁用 ====================


async def handle_toggle_plugin(request: web.Request):
    """启用/禁用插件文件 (key 格式 dir/file_stem, 入口文件禁用即整个插件禁用)"""
    body = await request.json()
    name = body.get('name', '')
    file = body.get('file', '')
    action = body.get('action', '')
    if not name or not file or action not in ('enable', 'disable'):
        return web.json_response({'success': False, 'message': '参数不完整'}, status=400)
    if not os.path.isdir(os.path.join(plugins_dir(), name)):
        return web.json_response({'success': False, 'message': f'插件目录不存在: {name}'}, status=404)
    pm = get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '插件管理器未初始化'}, status=503)

    key = f'{name}/{file}'
    try:
        (pm.enable_plugin if action == 'enable' else pm.disable_plugin)(key)
        # 入口文件: 需加载/卸载整个插件; 子文件: 重载目录即可
        is_entry = f'{file}.py' in ('main.py', 'index.py', 'app.py')
        if is_entry:
            if action == 'enable' and name not in pm.plugins:
                await pm.reload(name)
            elif action == 'disable' and name in pm.plugins:
                await pm.unload(name)
        else:
            if name in pm.plugins:
                await pm.reload(name)
        label = '已启用' if action == 'enable' else '已禁用'
        return web.json_response({'success': True, 'message': f'{key} {label}', 'plugin_name': name})
    except Exception as e:
        log.error(f'插件 {action} [{key}] 失败: {e}')
        return web.json_response({'success': False, 'message': f'操作异常: {e}'}, status=500)


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
    file_data = None
    filename = None
    directory = 'alone'
    async for field in reader:
        field = cast(BodyPartReader, field)
        if field.name == 'file':
            filename = field.filename
            file_data = await field.read()
        elif field.name == 'directory':
            directory = (await field.text()).strip() or 'alone'

    if not file_data or not filename:
        return web.json_response({'success': False, 'message': '没有文件'}, status=400)
    is_zip = filename.lower().endswith('.zip')
    is_py = filename.lower().endswith('.py')
    if not is_zip and not is_py:
        return web.json_response({'success': False, 'message': '仅支持 .py 或 .zip 文件'}, status=400)

    pdir = plugins_dir()

    # ---------- .py 文件：放到 alone 目录 ----------
    if is_py:
        safe_name = re.sub(r'[^\w\u4e00-\u9fa5\-\.]', '_', filename)
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

        with open(dest, 'wb') as f:
            f.write(file_data)
        return web.json_response(
            {
                'success': True,
                'message': f'上传成功: {os.path.basename(dest)}',
                'path': dest.replace('\\', '/'),
            }
        )

    # ---------- .zip 文件：解压到插件目录 ----------
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp.write(file_data)

        if not zipfile.is_zipfile(tmp.name):
            return web.json_response({'success': False, 'message': '无效的 zip 文件'}, status=400)

        with zipfile.ZipFile(tmp.name, 'r') as zf:
            names = zf.namelist()
            if not names:
                return web.json_response({'success': False, 'message': 'zip 文件为空'}, status=400)

            # 检测顶层目录结构
            top_dirs = set()
            top_files = set()
            for n in names:
                parts = n.replace('\\', '/').strip('/').split('/')
                if len(parts) > 1 and parts[0]:
                    top_dirs.add(parts[0])
                elif len(parts) == 1 and parts[0]:
                    top_files.add(parts[0])

            os.makedirs(pdir, exist_ok=True)

            if len(top_dirs) == 1 and not top_files:
                # zip 内是一个整体文件夹 → 用该文件夹名作为插件目录名
                folder_name = list(top_dirs)[0]
                safe_folder = re.sub(r'[^\w\u4e00-\u9fa5\-]', '_', folder_name)
                target_dir = os.path.join(pdir, safe_folder)

                if os.path.exists(target_dir):
                    backup = target_dir + '.bak'
                    if os.path.exists(backup):
                        shutil.rmtree(backup)
                    shutil.move(target_dir, backup)

                extract_tmp = tempfile.mkdtemp()
                safe_extractall(zf, extract_tmp)
                src = os.path.join(extract_tmp, folder_name)
                shutil.move(src, target_dir)
                shutil.rmtree(extract_tmp, ignore_errors=True)
            else:
                # zip 内直接是文件 → 用压缩包名字作为插件目录名
                zip_stem = os.path.splitext(filename)[0]
                safe_folder = re.sub(r'[^\w\u4e00-\u9fa5\-]', '_', zip_stem)
                target_dir = os.path.join(pdir, safe_folder)

                if os.path.exists(target_dir):
                    backup = target_dir + '.bak'
                    if os.path.exists(backup):
                        shutil.rmtree(backup)
                    shutil.move(target_dir, backup)

                os.makedirs(target_dir, exist_ok=True)
                safe_extractall(zf, target_dir)

            plugin_name = os.path.basename(target_dir)

        return web.json_response(
            {
                'success': True,
                'message': f'插件 {plugin_name} 上传成功',
                'plugin_name': plugin_name,
            }
        )
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)
    finally:
        if tmp is not None:
            with contextlib.suppress(Exception):
                os.unlink(tmp.name)
