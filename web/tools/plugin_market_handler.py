"""插件市场 (aiohttp 版) — 远程插件列表/安装/上传/预览"""

import os
import re
import hashlib
import base64
import io
import zipfile
import logging

import aiohttp as _aiohttp
from aiohttp import web

log = logging.getLogger('ElainaBot.web.market')

PHP_API_URL = 'https://i.elaina.vin/api/elainabot/cjsc.php'
TIMEOUT = 120
_base_dir = ''
_appid = ''
_robot_qq = ''


def set_context(base_dir: str, appid: str = '', robot_qq: str = ''):
    global _base_dir, _appid, _robot_qq
    _base_dir = base_dir
    _appid = appid
    _robot_qq = robot_qq


def _plugins_dir():
    return os.path.join(_base_dir, 'plugins')


def _author_token():
    raw = f"{_appid}:{_robot_qq}"
    md5 = hashlib.md5(raw.encode()).hexdigest()
    return base64.b64encode(f"{_appid}_{md5[:16]}".encode()).decode()


async def _call_php(action, data=None, params=None, token=None):
    headers = {}
    if token:
        headers['X-Admin-Token'] = token
    url = f"{PHP_API_URL}?action={action}"
    if params:
        for k, v in params.items():
            url += f"&{k}={v}"
    try:
        async with _aiohttp.ClientSession() as session:
            if data:
                async with session.post(url, json=data, headers=headers,
                                        timeout=_aiohttp.ClientTimeout(total=TIMEOUT),
                                        ssl=False) as resp:
                    return await resp.json()
            else:
                async with session.get(url, headers=headers,
                                       timeout=_aiohttp.ClientTimeout(total=TIMEOUT),
                                       ssl=False) as resp:
                    return await resp.json()
    except Exception as e:
        return {'success': False, 'message': str(e)}


def _convert_github_url(url):
    if 'raw.githubusercontent.com' in url or '/raw/' in url:
        return url
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
    if m:
        user, repo, branch, path = m.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}'
    return url


# ==================== 市场列表/搜索 ====================

async def handle_market_list(request: web.Request):
    params = {k: v for k, v in {
        'category': request.query.get('category', ''),
        'status': request.query.get('status', ''),
        'search': request.query.get('search', ''),
    }.items() if v}
    return web.json_response(await _call_php('list', params=params))


async def handle_market_categories(request: web.Request):
    return web.json_response(await _call_php('categories'))


async def handle_market_detail(request: web.Request):
    body = await request.json()
    return web.json_response(await _call_php('plugin_detail', body))


# ==================== 下载/预览/安装 ====================

async def handle_market_download(request: web.Request):
    body = await request.json()
    return web.json_response(await _call_php('download', body))


async def handle_market_preview(request: web.Request):
    body = await request.json()
    url = body.get('url', '')
    use_proxy = body.get('use_proxy', False)
    if not url:
        return web.json_response({'success': False, 'message': '缺少 URL'}, status=400)

    url = _convert_github_url(url)
    if use_proxy and ('github.com' in url or 'githubusercontent.com' in url):
        url = re.sub(r'https://(raw\.)?githubusercontent\.com',
                     r'https://ghfast.top/https://\1githubusercontent.com', url)
        url = url.replace('https://github.com', 'https://ghfast.top/https://github.com')

    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(url, timeout=_aiohttp.ClientTimeout(total=30), ssl=False) as resp:
                if resp.status != 200:
                    return web.json_response({'success': False, 'message': f'下载失败: HTTP {resp.status}'})
                content = await resp.read()

        if b'<!doctype html' in content[:100].lower() or b'<html' in content[:100].lower():
            return web.json_response({'success': False, 'message': '下载链接无效'})

        if content[:4] == b'PK\x03\x04':
            return _preview_zip(content)

        is_py = url.endswith('.py') or any(k in content[:500] for k in [b'import ', b'def ', b'class '])
        if is_py:
            code = content.decode('utf-8', errors='replace')
            fname = url.split('/')[-1].split('?')[0]
            if not fname.endswith('.py'):
                fname = 'plugin.py'
            return web.json_response({'success': True, 'type': 'py', 'filename': fname,
                                      'content': code, 'size': len(code)})
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


def _preview_zip(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            py_files = [f for f in zf.namelist() if f.endswith('.py') and not f.startswith('__') and '/__pycache__/' not in f]
            files = []
            for pf in py_files[:10]:
                try:
                    fc = zf.read(pf).decode('utf-8', errors='replace')
                    files.append({'name': pf, 'content': fc[:5000], 'size': len(fc)})
                except Exception:
                    pass
            return web.json_response({'success': True, 'type': 'zip', 'files': files, 'total_files': len(py_files)})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


async def handle_market_install(request: web.Request):
    body = await request.json()
    url = body.get('url', '')
    plugin_name = body.get('name', 'unknown_plugin')
    use_proxy = body.get('use_proxy', False)
    if not url:
        return web.json_response({'success': False, 'message': '缺少 URL'}, status=400)

    url = _convert_github_url(url)
    if use_proxy and ('github.com' in url or 'githubusercontent.com' in url):
        url = re.sub(r'https://(raw\.)?githubusercontent\.com',
                     r'https://ghfast.top/https://\1githubusercontent.com', url)

    try:
        async with _aiohttp.ClientSession() as session:
            async with session.get(url, timeout=_aiohttp.ClientTimeout(total=60), ssl=False) as resp:
                if resp.status != 200:
                    return web.json_response({'success': False, 'message': f'HTTP {resp.status}'})
                content = await resp.read()

        if content[:4] == b'PK\x03\x04':
            return web.json_response(_install_zip(content, plugin_name))

        is_py = url.endswith('.py') or any(k in content[:500] for k in [b'import ', b'def ', b'class '])
        if is_py:
            return web.json_response(_install_py(content, plugin_name, url))
        return web.json_response({'success': False, 'message': '不支持的文件类型'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)})


def _install_py(content, plugin_name, url):
    plugins_dir = _plugins_dir()
    fname = url.split('/')[-1].split('?')[0]
    if not fname.endswith('.py'):
        fname = f"{plugin_name}.py"
    safe = "".join(c for c in plugin_name if c.isalnum() or c in ('_', '-', ' ')).strip() or fname.replace('.py', '')
    dest_dir = os.path.join(plugins_dir, safe)
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, fname), 'wb') as f:
        f.write(content)
    return {'success': True, 'message': f'已安装到 plugins/{safe}/{fname}'}


def _install_zip(content, plugin_name):
    plugins_dir = _plugins_dir()
    safe = "".join(c for c in plugin_name if c.isalnum() or c in ('_', '-', ' ')).strip() or 'unknown'
    dest_dir = os.path.join(plugins_dir, safe)
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            flist = zf.namelist()
            if not flist:
                return {'success': False, 'message': '空压缩包'}
            roots = {f.split('/')[0] for f in flist if '/' in f and f.split('/')[0]}
            strip_root = len(roots) == 1
            root_prefix = list(roots)[0] + '/' if strip_root else ''
            os.makedirs(dest_dir, exist_ok=True)
            extracted = []
            for fp in flist:
                if fp.endswith('/') or '__pycache__' in fp:
                    continue
                rel = fp[len(root_prefix):] if strip_root and fp.startswith(root_prefix) else fp
                if not rel:
                    continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(fp) as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel)
            py_count = sum(1 for f in extracted if f.endswith('.py'))
            return {'success': True, 'message': f'已安装到 plugins/{safe}/ ({py_count} 个 Python 文件)'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ==================== 提交/用户 ====================

async def handle_market_submit(request: web.Request):
    body = await request.json()
    body['author_token'] = _author_token()
    body['submit_appid'] = _appid
    return web.json_response(await _call_php('submit', body))


async def handle_market_register(request: web.Request):
    body = await request.json()
    body['robot_qq'] = _robot_qq
    body['appid'] = _appid
    return web.json_response(await _call_php('register', body))


async def handle_market_login(request: web.Request):
    return web.json_response(await _call_php('login', await request.json()))


async def handle_market_user_info(request: web.Request):
    return web.json_response(await _call_php('user_info', await request.json()))


# ==================== 本地插件管理 ====================

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
                    plugins.append({'name': f'{item}/{f[:-3]}', 'type': 'file',
                                    'files': [f], 'path': f'{item}/{f}'})
        elif item.endswith('.py'):
            plugins.append({'name': item[:-3], 'type': 'file',
                            'files': [item], 'path': item})
    return web.json_response({'success': True, 'plugins': plugins})


async def handle_local_plugin_read(request: web.Request):
    body = await request.json()
    path = body.get('path', '')
    if not path or '..' in path:
        return web.json_response({'success': False, 'message': '无效路径'}, status=400)
    full = os.path.join(_plugins_dir(), path)
    if os.path.isfile(full) and full.endswith('.py'):
        with open(full, 'r', encoding='utf-8') as f:
            content = f.read()
        return web.json_response({'success': True, 'type': 'single',
                                  'files': [{'name': os.path.basename(path), 'path': path,
                                             'content': content, 'size': len(content)}]})
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
                    with open(fp, 'r', encoding='utf-8') as f:
                        c = f.read()
                    files.append({'name': fn, 'path': rel, 'content': c, 'size': len(c), 'editable': True})
                else:
                    files.append({'name': fn, 'path': rel, 'size': os.path.getsize(fp), 'editable': False})
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
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(content)
            saved.append(fp)
        except Exception as e:
            errors.append(f'{fp}: {e}')
    return web.json_response({
        'success': bool(saved),
        'message': f'已保存 {len(saved)} 个文件' + (f', {len(errors)} 个失败' if errors else ''),
        'saved': saved, 'errors': errors,
    })


# ==================== 其它市场操作 ====================

async def handle_market_upload_local(request: web.Request):
    body = await request.json()
    path = body.get('plugin_path', '')
    if not path or not body.get('name') or not body.get('description'):
        return web.json_response({'success': False, 'message': '参数不完整'}, status=400)
    full = os.path.join(_plugins_dir(), path)
    if not os.path.isfile(full) or not full.endswith('.py'):
        return web.json_response({'success': False, 'message': '仅支持 .py 文件'}, status=400)
    with open(full, 'rb') as f:
        data64 = base64.b64encode(f.read()).decode()
    body['author_token'] = _author_token()
    body['submit_appid'] = _appid
    body['upload_type'] = 'local'
    body['plugin_data'] = data64
    body['plugin_filename'] = os.path.basename(path)
    return web.json_response(await _call_php('submit_local', body))


async def handle_market_upload_direct(request: web.Request):
    body = await request.json()
    if not body.get('name') or not body.get('description') or not body.get('plugin_data'):
        return web.json_response({'success': False, 'message': '参数不完整'}, status=400)
    body['author_token'] = _author_token()
    body['submit_appid'] = _appid
    body['upload_type'] = 'direct'
    return web.json_response(await _call_php('submit_local', body))


async def handle_market_author_update(request: web.Request):
    return web.json_response(await _call_php('author_update', await request.json()))


async def handle_market_author_delete(request: web.Request):
    return web.json_response(await _call_php('author_delete', await request.json()))
