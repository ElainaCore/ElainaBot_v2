"""模块管理 — scan/toggle/upload + 元数据 + 配置文件列表"""

import contextlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from typing import cast

from aiohttp import BodyPartReader, web

from web.tools._plugin_mgr.shared import (
    bot_manager,
    get_mm,
    list_config_files,
    modules_dir,
)
from web.tools._python_source import read_dict_assignment
from web.tools._zipsafe import safe_extractall

# ==================== 元数据读取 ====================


def _read_module_meta(entry_path):
    """通过 AST 读取 main.py 中的 __module_meta__"""
    return read_dict_assignment(entry_path, '__module_meta__') or {}


# ==================== 扫描 ====================


def _scan_modules():
    """扫描所有模块, 包含运行时状态"""
    mdir = modules_dir()
    result = []
    if not os.path.isdir(mdir):
        return result

    runtime, persist_map = {}, {}
    mm = get_mm()
    if mm:
        for m in mm.list_modules():
            runtime[m['name']] = m
    enabled_file = os.path.join(mdir, 'modules_enabled.json')
    if os.path.isfile(enabled_file):
        try:
            with open(enabled_file, encoding='utf-8') as f:
                persist_map = json.load(f) or {}
        except Exception:
            pass

    for name in sorted(os.listdir(mdir)):
        mod_dir = os.path.join(mdir, name)
        if not os.path.isdir(mod_dir) or name.startswith('_'):
            continue
        entry = os.path.join(mod_dir, 'main.py')
        if not os.path.isfile(entry):
            continue

        meta = _read_module_meta(entry)
        config_files = list_config_files(os.path.join(mod_dir, 'data'))
        rt = runtime.get(name, {})
        mtime = datetime.fromtimestamp(os.path.getmtime(entry)).strftime('%Y-%m-%d %H:%M:%S')

        result.append(
            {
                'name': name,
                'display_name': meta.get('name') or rt.get('display_name') or name,
                'description': meta.get('description') or rt.get('description', ''),
                'version': meta.get('version') or rt.get('version', '1.0.0'),
                'author': meta.get('author') or rt.get('author', ''),
                'enabled': rt.get('enabled', False),
                'persist_enabled': rt.get('persist_enabled', persist_map.get(name, False)),
                'error': rt.get('error'),
                'last_modified': mtime,
                'config_files': config_files,
            }
        )
    return result


async def handle_scan_modules(request: web.Request):
    return web.json_response({'success': True, 'modules': _scan_modules()})


# ==================== 启停 ====================


async def handle_module_toggle(request: web.Request):
    body = await request.json()
    name = body.get('name', '')
    action = body.get('action', '')
    if not name or action not in ('enable', 'disable'):
        return web.json_response({'success': False, 'message': '参数错误'}, status=400)
    if not bot_manager():
        return web.json_response({'success': False, 'message': '框架未启动'}, status=503)
    mm = get_mm()
    if not mm:
        return web.json_response({'success': False, 'message': '模块管理器未初始化'}, status=503)
    try:
        if action == 'enable':
            ok = await mm.enable(name)
        else:
            ok = await mm.disable(name)
            if not ok:
                mm.set_module_enabled_persist(name, False)
                return web.json_response({'success': True, 'message': f'模块 {name} 已关闭'})
        if ok:
            verb = '开启' if action == 'enable' else '关闭'
            return web.json_response({'success': True, 'message': f'模块 {name} 已{verb}'})
        return web.json_response({'success': False, 'message': '操作失败'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 上传 ====================


async def handle_module_upload(request: web.Request):
    """上传模块 (zip 格式, 必须含 .py 和 .json)"""
    reader = await request.multipart()
    field = cast(BodyPartReader, await reader.next())
    if not field or field.name != 'file':
        return web.json_response({'success': False, 'message': '缺少文件'}, status=400)

    filename = field.filename or ''
    if not filename.lower().endswith('.zip'):
        return web.json_response({'success': False, 'message': '仅支持 zip 格式'}, status=400)

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                tmp.write(chunk)

        if not zipfile.is_zipfile(tmp.name):
            return web.json_response({'success': False, 'message': '无效的 zip 文件'}, status=400)

        with zipfile.ZipFile(tmp.name, 'r') as zf:
            names = zf.namelist()
            has_py = any(n.endswith('.py') for n in names)
            has_json = any(n.endswith('.json') for n in names)
            if not has_py or not has_json:
                return web.json_response(
                    {
                        'success': False,
                        'message': f'zip 必须包含 .py 和 .json 文件 (当前: py={has_py}, json={has_json})',
                    },
                    status=400,
                )

            mod_name = os.path.splitext(filename)[0]
            for n in names:
                if os.path.basename(n) == 'module.json':
                    with contextlib.suppress(Exception):
                        meta = json.loads(zf.read(n))
                        if meta.get('name'):
                            mod_name = meta['name']
                    break

            top_dirs = set()
            for n in names:
                parts = n.replace('\\', '/').split('/')
                if len(parts) > 1 and parts[0]:
                    top_dirs.add(parts[0])

            mdir = modules_dir()
            os.makedirs(mdir, exist_ok=True)
            target_dir = os.path.join(mdir, mod_name)

            if os.path.exists(target_dir):
                backup = target_dir + '.bak'
                if os.path.exists(backup):
                    shutil.rmtree(backup)
                shutil.move(target_dir, backup)

            if len(top_dirs) == 1:
                extract_tmp = tempfile.mkdtemp()
                safe_extractall(zf, extract_tmp)
                src = os.path.join(extract_tmp, list(top_dirs)[0])
                shutil.move(src, target_dir)
                shutil.rmtree(extract_tmp, ignore_errors=True)
            else:
                os.makedirs(target_dir, exist_ok=True)
                safe_extractall(zf, target_dir)

        if not os.path.isfile(os.path.join(target_dir, 'main.py')):
            py_files = [f for f in os.listdir(target_dir) if f.endswith('.py')]
            if not py_files:
                shutil.rmtree(target_dir, ignore_errors=True)
                return web.json_response({'success': False, 'message': '解压后未找到 .py 文件'}, status=400)

        return web.json_response(
            {
                'success': True,
                'message': f'模块 {mod_name} 上传成功，重启后生效',
                'module_name': mod_name,
            }
        )
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)
    finally:
        if tmp is not None:
            with contextlib.suppress(Exception):
                os.unlink(tmp.name)
