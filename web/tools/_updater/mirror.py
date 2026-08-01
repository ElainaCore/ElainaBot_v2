"""框架更新 — 镜像测速, 环境检测"""

import asyncio
import os
import time

import aiohttp as _aiohttp

from web.tools._updater.shared import (
    GITHUB_FILE_MIRRORS,
    _build_mirror_url,
    _load_mirror_cache,
    _save_mirror_cache,
    clear_mirror_cache,  # noqa: F401  # re-export for handlers.py
)

_mirror_testing = None  # asyncio.Task (防重复测速)


async def _test_one_mirror(mirror, timeout=3):
    """HEAD 请求测试镜像延迟, 2xx/3xx 均视为成功"""
    test_url = _build_mirror_url('https://github.com/lengxi-root/napcat-plugin-lengxi/releases/latest', mirror)
    start = time.time()
    try:
        async with (
            _aiohttp.ClientSession() as session,
            session.head(
                test_url,
                headers={'User-Agent': 'ElainaBot-Mirror-Test'},
                timeout=_aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as resp,
        ):
            latency = time.time() - start
            # 2xx/3xx 成功, 405(不支持HEAD但镜像本身可用)也算成功
            ok = (200 <= resp.status < 400) or resp.status == 405
            return {
                'mirror': mirror,
                'latency': round(latency, 3),
                'success': ok,
                'status': resp.status,
            }
    except Exception as e:
        return {
            'mirror': mirror,
            'latency': round(time.time() - start, 3),
            'success': False,
            'error': type(e).__name__,
        }


async def test_all_mirrors(timeout=3):
    """并行测试所有镜像, 返回按延迟排序的结果列表"""
    tasks = [_test_one_mirror(m, timeout) for m in GITHUB_FILE_MIRRORS]
    # 加上 GitHub 直连
    tasks.append(_test_one_mirror('', timeout))
    results = await asyncio.gather(*tasks)
    results = sorted(results, key=lambda r: (not r['success'], r['latency']))
    return results


async def get_fast_mirrors(force=False):
    """获取按延迟排序的可用镜像列表 (磁盘缓存 30 分钟)"""
    global _mirror_testing
    if not force:
        cached = _load_mirror_cache()
        if cached:
            return cached
    if _mirror_testing and not _mirror_testing.done():
        return await _mirror_testing
    _mirror_testing = asyncio.ensure_future(test_all_mirrors())
    results = await _mirror_testing
    ok = [r for r in results if r['success']]
    _mirror_testing = None
    _save_mirror_cache(ok)
    return ok


# ==================== 环境检测 ====================


def detect_environment():
    """检测运行环境, 返回 {docker, writable, warning}"""
    info = {'docker': False, 'writable': True, 'warnings': []}
    # Docker 检测
    if os.path.exists('/.dockerenv'):
        info['docker'] = True
    else:
        try:
            with open('/proc/1/cgroup') as f:
                if 'docker' in f.read() or 'containerd' in f.read():
                    info['docker'] = True
        except OSError:
            pass
    # 可写性检测
    try:
        test_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            '.write_test',
        )
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
    except Exception:
        info['writable'] = False
        info['warnings'].append('项目目录不可写, 更新将失败')
    if info['docker']:
        info['warnings'].append('检测到 Docker 环境, 请确保项目目录已挂载 volume 以持久化更新')
    return info
