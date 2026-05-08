"""框架更新 — 版本检查/更新日志/在线更新/上传更新"""

import os
import asyncio
import logging
from datetime import datetime

from aiohttp import web

log = logging.getLogger('ElainaBot.web.updater')

_base_dir = ''
_updater = None


def set_context(base_dir: str):
    global _base_dir, _updater
    _base_dir = base_dir
    from web.tools.updater import FrameworkUpdater
    _updater = FrameworkUpdater(base_dir)


def _get_updater():
    if not _updater:
        raise RuntimeError('更新器未初始化')
    return _updater


# ==================== 环境检测 ====================

async def handle_detect_environment(request: web.Request):
    from web.tools.updater import detect_environment
    return web.json_response({'success': True, 'data': detect_environment()})


# ==================== 更新日志 ====================

async def handle_get_changelog(request: web.Request):
    try:
        updater = _get_updater()
        commits = await updater.fetch_changelog()
        if commits is None:
            return web.json_response({'success': False, 'message': 'GitHub API 请求失败，请检查网络或稍后重试', 'data': []})

        result = []
        for c in (commits if isinstance(commits, list) else []):
            info = c.get('commit')
            if not info:
                continue
            author = info.get('author', {})
            date_str = author.get('date', '')
            try:
                fmt = datetime.fromisoformat(date_str.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                fmt = '未知时间'
            result.append({
                'sha': c.get('sha', '')[:8],
                'message': info.get('message', '').strip(),
                'author': author.get('name', '未知'),
                'date': fmt,
                'url': c.get('html_url', ''),
                'full_sha': c.get('sha', ''),
            })
        return web.json_response({
            'success': True, 'data': result, 'total': len(result),
            })
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 版本信息 ====================

async def handle_get_current_version(request: web.Request):
    try:
        info = _get_updater().get_version_info()
        return web.json_response({'success': True, 'data': info})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 检查更新 ====================

async def handle_check_update(request: web.Request):
    try:
        data = await _get_updater().check_for_updates()
        return web.json_response({'success': True, 'data': data})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 进度 ====================

async def handle_get_update_progress(request: web.Request):
    try:
        return web.json_response({'success': True, 'data': _get_updater().get_progress()})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


# ==================== 开始更新 ====================

async def handle_start_update(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    updater = _get_updater()

    # 设置自定义镜像
    if body.get('mirror'):
        updater.set_custom_mirror(body['mirror'])

    skip_backup = body.get('skip_backup', False)
    auto_restart = body.get('auto_restart', False)

    async def _do_update():
        try:
            if body.get('force'):
                await updater.force_update(skip_backup=skip_backup, auto_restart=auto_restart)
            elif body.get('version'):
                await updater.update_to_version(body['version'], skip_backup=skip_backup, auto_restart=auto_restart)
            else:
                await updater.update_to_latest(skip_backup=skip_backup, auto_restart=auto_restart)
        except Exception as e:
            updater._report('failed', f'更新出错: {e}', 0)

    asyncio.ensure_future(_do_update())
    return web.json_response({'success': True, 'message': '更新已开始'})


# ==================== 镜像管理 ====================

async def handle_get_mirrors(request: web.Request):
    """获取镜像列表 (含缓存的测速结果)"""
    try:
        from web.tools.updater import GITHUB_FILE_MIRRORS, _load_mirror_cache
        updater = _get_updater()
        cached = _load_mirror_cache()
        return web.json_response({'success': True, 'data': {
            'mirrors': list(GITHUB_FILE_MIRRORS),
            'fast_mirrors': cached,
            'custom_mirror': updater.custom_mirror,
        }})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


async def handle_test_mirrors(request: web.Request):
    """SSE 流式测速所有镜像, 每完成一个立即推送"""
    import json as _json
    from web.tools.updater import _test_one_mirror, GITHUB_FILE_MIRRORS, clear_mirror_cache

    clear_mirror_cache()
    resp = web.StreamResponse()
    resp.content_type = 'text/event-stream'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    await resp.prepare(request)

    all_results = []
    tasks = {asyncio.ensure_future(_test_one_mirror(m, 3)): m for m in GITHUB_FILE_MIRRORS}
    tasks[asyncio.ensure_future(_test_one_mirror('', 3))] = ''

    pending = set(tasks.keys())
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            result = task.result()
            all_results.append(result)
            try:
                await resp.write(f"data: {_json.dumps(result, ensure_ascii=False)}\n\n".encode())
            except ConnectionResetError:
                return resp

    # 保存测速结果到磁盘
    from web.tools.updater import _save_mirror_cache
    _save_mirror_cache(sorted(
        [r for r in all_results if r['success']],
        key=lambda r: r['latency']
    ))

    # 发送结束标记
    try:
        await resp.write(b"data: {\"done\": true}\n\n")
        await resp.write_eof()
    except Exception:
        pass
    return resp


async def handle_set_custom_mirror(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    mirror = body.get('mirror', '')
    _get_updater().set_custom_mirror(mirror)
    return web.json_response({'success': True, 'message': f'已设置自定义镜像: {mirror or "(自动选择)"}'})


# ==================== 上传更新 ====================

async def handle_upload_update(request: web.Request):
    """接收上传的 zip 压缩包并应用更新"""
    updater = _get_updater()

    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != 'file':
        return web.json_response({'success': False, 'message': '缺少文件'}, status=400)

    filename = field.filename or ''
    if not filename.lower().endswith('.zip'):
        return web.json_response({'success': False, 'message': '仅支持 zip 格式'}, status=400)

    # 保存到临时文件
    upload_dir = os.path.join(_base_dir, 'data', 'temp_update')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename or 'upload.zip')

    try:
        with open(filepath, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        return web.json_response({'success': False, 'message': f'保存文件失败: {e}'}, status=500)

    # 读取额外字段
    version_name = None
    skip_backup = False
    auto_restart = False
    while True:
        field = await reader.next()
        if field is None:
            break
        val = (await field.read()).decode('utf-8', errors='ignore')
        if field.name == 'version_name':
            version_name = val.strip() or None
        elif field.name == 'skip_backup':
            skip_backup = val.lower() in ('true', '1', 'yes')
        elif field.name == 'auto_restart':
            auto_restart = val.lower() in ('true', '1', 'yes')

    def _do():
        try:
            updater.update_from_upload(filepath, version_name, skip_backup=skip_backup, auto_restart=auto_restart)
        except Exception as e:
            updater._report('failed', f'更新出错: {e}', 0)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _do)
    return web.json_response({'success': True, 'message': '上传成功，开始更新'})
