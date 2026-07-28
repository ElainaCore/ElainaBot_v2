#!/usr/bin/env python
"""渲染引擎 — PIL 子进程渲染池 + Playwright 浏览器渲染 统一管理模块

通过配置文件独立开关两个渲染子引擎, 全局共享, 插件不必各自开渲染池。
启用了某个子引擎但其依赖未安装时报错并标记不可用, 不影响模块整体加载。

插件中获取:
    rd = bot.module_manager.get("renderer")

    # PIL 子进程渲染 (渲染函数须为模块级函数, 参数与返回值可 pickle)
    if rd.pil_available():
        img_data, w, h = await rd.pil.render(_render_sync, arg1, arg2)

    # Playwright 浏览器渲染
    if rd.playwright_available():
        img = await rd.playwright.screenshot_html("<h1>Hello</h1>")

配置文件 (data/ 下自动生成):
    config.yaml     → pil_enabled / playwright_enabled 开关
    pil.yaml        → PIL 渲染池参数
    playwright.yaml → Playwright 浏览器参数
"""

__module_meta__ = {
    'name': '渲染引擎',
    'description': 'PIL 子进程渲染池 + Playwright 浏览器渲染统一管理, 全局共享按需启停',
    'version': '2.0.0',
    'author': 'ElainaBot',
}

import importlib.util
import os

from core.base.logger import EXTENSION, get_logger
from core.base.pip_helper import install_requirements

log = get_logger(EXTENSION, '渲染引擎')

_instance = None

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

_DEFAULTS = {
    'pil_enabled': True,
    'playwright_enabled': True,
}

_COMMENTS = {
    'pil_enabled': '是否启用 PIL 子进程渲染池',
    'playwright_enabled': '是否启用 Playwright 浏览器渲染',
}


async def _ensure_engine_deps(engine, import_name, pip_name):
    """安装并检查子引擎依赖, 返回错误信息 (None 表示就绪)"""
    await install_requirements(f'renderer/{engine}', _MODULE_DIR, skip_if_met=True, no_cache=True)
    importlib.invalidate_caches()
    if importlib.util.find_spec(import_name) is None:
        return f'依赖 {pip_name} 未安装 (pip install {pip_name})'
    return None


# ==================== 模块入口 ====================


async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)

    from modules.renderer.pil.main import _COMMENTS as PIL_COMMENTS
    from modules.renderer.pil.main import _DEFAULTS as PIL_DEFAULTS
    from modules.renderer.pil.main import PILRenderPool
    from modules.renderer.playwright.main import _COMMENTS as PW_COMMENTS
    from modules.renderer.playwright.main import _DEFAULTS as PW_DEFAULTS
    from modules.renderer.playwright.main import PlaywrightRenderer

    pil_cfg = ctx.ensure_config(PIL_DEFAULTS, filename='pil.yaml', comments=PIL_COMMENTS)
    pw_cfg = ctx.ensure_config(PW_DEFAULTS, filename='playwright.yaml', comments=PW_COMMENTS)

    pil_inst = None
    pw_inst = None

    if cfg.get('pil_enabled', True):
        err = await _ensure_engine_deps('pil', 'PIL', 'Pillow')
        if err:
            log.error(f'PIL 渲染池已启用但不可用: {err}')
        else:
            pil_inst = PILRenderPool(pil_cfg)

    if cfg.get('playwright_enabled', True):
        err = await _ensure_engine_deps('playwright', 'playwright', 'playwright')
        if err:
            log.error(f'Playwright 渲染已启用但不可用: {err}')
        else:
            pw_inst = PlaywrightRenderer(pw_cfg)

    _instance = Renderer(pil_inst, pw_inst)

    parts = []
    if pil_inst:
        parts.append(f'PIL ✅ [常驻 {pil_cfg["min_workers"]} / 最大 {pil_cfg["max_workers"]} worker]')
    elif cfg.get('pil_enabled'):
        parts.append('PIL ❌')
    else:
        parts.append('PIL 关闭')

    if pw_inst:
        parts.append(f'Playwright ✅ [{pw_cfg["browser_type"]} 首次调用时启动]')
    elif cfg.get('playwright_enabled'):
        parts.append('Playwright ❌')
    else:
        parts.append('Playwright 关闭')

    log.info(f'{" | ".join(parts)}')
    return _instance


async def teardown():
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


# ==================== Renderer ====================


class Renderer:
    """统一渲染引擎 — 通过 .pil / .playwright 属性访问子引擎"""

    __slots__ = ('_pil', '_playwright')

    def __init__(self, pil_pool, playwright_renderer):
        self._pil = pil_pool
        self._playwright = playwright_renderer

    @property
    def pil(self):
        """PILRenderPool 实例, 不可用时返回 None"""
        return self._pil if self._pil and self._pil.is_available() else None

    @property
    def playwright(self):
        """PlaywrightRenderer 实例, 不可用时返回 None"""
        return self._playwright if self._playwright and self._playwright.is_available() else None

    def pil_available(self):
        return self._pil is not None and self._pil.is_available()

    def playwright_available(self):
        return self._playwright is not None and self._playwright.is_available()

    async def close(self):
        if self._pil:
            await self._pil.close()
        if self._playwright:
            await self._playwright.close()
