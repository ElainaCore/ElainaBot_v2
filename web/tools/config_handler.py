"""配置文件管理 (aiohttp 版) — YAML 配置读写"""

import os
from datetime import datetime

from aiohttp import web

_base_dir = ''


def set_context(base_dir: str):
    global _base_dir
    _base_dir = base_dir


def _config_dir():
    return os.path.join(_base_dir, 'config')


async def handle_get_config(request: web.Request):
    cdir = _config_dir()
    result = {}
    for name in ('bot', 'settings', 'templates'):
        path = os.path.join(cdir, f'{name}.yaml')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                result[name] = f.read()
        else:
            result[name] = ''
    return web.json_response({'success': True, **result})


async def handle_save_config(request: web.Request):
    try:
        body = await request.json()
        file_name = body.get('file', '')
        content = body.get('content', '')
        if file_name not in ('bot', 'settings', 'templates'):
            return web.json_response({'success': False, 'error': '无效的配置文件名'}, status=400)
        if not content:
            return web.json_response({'success': False, 'error': '内容不能为空'}, status=400)

        cdir = _config_dir()
        path = os.path.join(cdir, f'{file_name}.yaml')

        # 备份
        if os.path.exists(path):
            bak = path + '.bak'
            with open(path, 'r', encoding='utf-8') as f:
                with open(bak, 'w', encoding='utf-8') as fb:
                    fb.write(f.read())

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return web.json_response({'success': True, 'message': '配置已保存，部分更改需重启生效'})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)
