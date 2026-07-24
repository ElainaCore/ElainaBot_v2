"""插件市场 — 本地插件管理"""

import os

from aiohttp import web

from web.tools._market.shared import _plugins_dir
from web.tools._zipsafe import is_within


async def handle_local_plugins(request: web.Request):
    plugins_dir = _plugins_dir()
    plugins = []
    if not os.path.isdir(plugins_dir):
        return web.json_response({'success': True, 'plugins': []})
    for item in os.listdir(plugins_dir):
        item_path = os.path.join(plugins_dir, item)
        if item.startswith(('.', '__')):
            continue
        if os.path.isdir(item_path):
            for f in os.listdir(item_path):
                if f.endswith('.py') and not f.startswith('__'):
                    plugins.append(
                        {
                            'name': f'{item}/{f[:-3]}',
                            'type': 'file',
                            'files': [f],
                            'path': f'{item}/{f}',
                        }
                    )
        elif item.endswith('.py'):
            plugins.append({'name': item[:-3], 'type': 'file', 'files': [item], 'path': item})
    return web.json_response({'success': True, 'plugins': plugins})


async def handle_local_plugin_read(request: web.Request):
    body = await request.json()
    path = body.get('path', '')
    if not path or '..' in path:
        return web.json_response({'success': False, 'message': '无效路径'}, status=400)
    full = os.path.join(_plugins_dir(), path)
    if not is_within(_plugins_dir(), full):
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)
    if os.path.isfile(full) and full.endswith('.py'):
        with open(full, encoding='utf-8') as f:
            content = f.read()
        return web.json_response(
            {
                'success': True,
                'type': 'single',
                'files': [
                    {
                        'name': os.path.basename(path),
                        'path': path,
                        'content': content,
                        'size': len(content),
                    }
                ],
            }
        )
    if os.path.isdir(full):
        files = []
        for root, dirs, fnames in os.walk(full):
            dirs[:] = [d for d in dirs if not d.startswith(('__', '.'))]
            for fn in fnames:
                if fn.startswith(('__', '.')):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, _plugins_dir())
                if fn.endswith('.py'):
                    with open(fp, encoding='utf-8') as f:
                        c = f.read()
                    files.append(
                        {
                            'name': fn,
                            'path': rel,
                            'content': c,
                            'size': len(c),
                            'editable': True,
                        }
                    )
                else:
                    files.append(
                        {
                            'name': fn,
                            'path': rel,
                            'size': os.path.getsize(fp),
                            'editable': False,
                        }
                    )
        return web.json_response({'success': True, 'type': 'folder', 'files': files})
    return web.json_response({'success': False, 'message': '不存在'}, status=404)


async def handle_local_plugin_save(request: web.Request):
    body = await request.json()
    files = body.get('files', [])
    if not files:
        return web.json_response({'success': False, 'message': '没有文件'}, status=400)
    saved, errors = [], []
    for fi in files:
        fp, content = fi.get('path', ''), fi.get('content')
        if not fp or content is None or '..' in fp or not fp.endswith('.py'):
            errors.append(f'{fp}: 无效')
            continue
        full = os.path.join(_plugins_dir(), fp)
        if not is_within(_plugins_dir(), full):
            errors.append(f'{fp}: 无效')
            continue
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)
            saved.append(fp)
        except Exception as e:
            errors.append(f'{fp}: {e}')
    return web.json_response(
        {
            'success': bool(saved),
            'message': f'已保存 {len(saved)} 个文件' + (f', {len(errors)} 个失败' if errors else ''),
            'saved': saved,
            'errors': errors,
        }
    )
