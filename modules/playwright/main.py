#!/usr/bin/env python
"""Playwright 异步渲染模块

按需启动浏览器, 空闲自动关闭, 通过信号量控制并发页面数, 供所有插件共享。

插件中获取:
    pw = bot.module_manager.get("playwright")

    # 截图 URL → bytes
    img = await pw.screenshot_url("https://example.com", full_page=True)

    # 截图 HTML 字符串 → bytes
    img = await pw.screenshot_html("<h1>Hello</h1>", viewport=(800, 600))

    # 高级: 自行操作页面
    async with pw.new_page(viewport=(1200, 800)) as page:
        await page.goto("https://example.com")
        await page.click("#btn")
        img = await page.screenshot(full_page=True)

配置文件 (data/ 下自动生成):
    config.yaml → max_pages / headless / idle_timeout / timeout 等
"""

__module_meta__ = {
    "name": "Playwright 渲染引擎",
    "description": "异步 Playwright 浏览器渲染, 支持 URL/HTML 截图与 PDF 导出",
    "version": "1.1.0",
    "author": "ElainaBot",
}

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager

from core.base.logger import EXTENSION, get_logger

log = get_logger(EXTENSION, "Playwright")

_instance = None

_DEFAULTS = {
    "headless": True,
    "max_pages": 2,
    "idle_timeout": 300,
    "default_timeout": 30000,
    "default_viewport_width": 1280,
    "default_viewport_height": 720,
    "image_format": "jpeg",
    "image_quality": 90,
    "browser_type": "chromium",
    "close_after_use": False,
    "launch_args": [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
    ],
}

_COMMENTS = {
    "headless": "是否无头模式 (无界面)",
    "max_pages": "最大并发页面数",
    "idle_timeout": "浏览器空闲超时 (秒), 超时后自动关闭, 下次使用时重新启动",
    "default_timeout": "默认页面超时 (毫秒)",
    "default_viewport_width": "默认视口宽度",
    "default_viewport_height": "默认视口高度",
    "image_format": "截图格式: jpeg / png",
    "image_quality": "截图质量 (仅 jpeg, 1-100)",
    "browser_type": "浏览器类型: chromium / firefox / webkit",
    "close_after_use": "用完即关: 每次调用结束后完全关闭浏览器进程, 不保留常驻进程 (适合低内存环境)",
    "launch_args": "浏览器启动参数",
}


# ==================== 模块入口 ====================


async def setup(ctx):
    global _instance
    cfg = ctx.ensure_config(_DEFAULTS, comments=_COMMENTS)
    _instance = PlaywrightRenderer(cfg)
    log.info(f"✅ Playwright 就绪 [{cfg['browser_type']}] 首次调用时启动浏览器")
    return _instance


async def teardown():
    global _instance
    if _instance:
        await _instance.close()
        _instance = None


# ==================== PlaywrightRenderer ====================


class PlaywrightRenderer:
    """异步 Playwright 浏览器渲染器 (按需启动, 空闲关闭)"""

    __slots__ = (
        "_cfg",
        "_pw",
        "_browser",
        "_semaphore",
        "_lock",
        "_active_pages",
        "_last_release",
        "_cleanup_task",
        "_closed",
        "_last_error",
    )

    def __init__(self, cfg):
        self._cfg = cfg
        self._pw = None
        self._browser = None
        self._semaphore = asyncio.Semaphore(cfg.get("max_pages", 2))
        self._lock = asyncio.Lock()
        self._active_pages = 0
        self._last_release = 0.0
        self._cleanup_task = None
        self._closed = False
        self._last_error = None

    def is_available(self):
        return not self._closed

    async def close(self):
        self._closed = True
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        await self._close_browser()
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None

    async def _close_browser(self):
        """关闭浏览器进程"""
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None

    async def _shutdown_all(self):
        """关闭浏览器 + Playwright 进程 (用完即关模式)"""
        await self._close_browser()
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None

    async def _ensure_browser(self):
        """按需启动浏览器, 崩溃时自动重启"""
        if self._browser and self._browser.is_connected():
            return True
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return True
            restarting = self._browser is not None
            log.info(
                "浏览器已断开, 正在重启..." if restarting else "正在按需启动浏览器..."
            )
            try:
                if not self._pw:
                    from playwright.async_api import async_playwright

                    self._pw = await async_playwright().start()
                launcher = getattr(
                    self._pw,
                    self._cfg.get("browser_type", "chromium"),
                    self._pw.chromium,
                )
                self._browser = await launcher.launch(
                    headless=self._cfg.get("headless", True),
                    args=self._cfg.get("launch_args", []),
                )
                log.info("✅ 浏览器已启动" if not restarting else "✅ 浏览器已重启")
                if not self._cfg.get("close_after_use", False) and (
                    not self._cleanup_task or self._cleanup_task.done()
                ):
                    self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())
                return True
            except Exception as e:
                self._last_error = str(e)
                log.error(f"浏览器启动失败: {e}", exc_info=True)
                return False

    async def _idle_cleanup_loop(self):
        """定时检查并关闭空闲浏览器"""
        timeout = self._cfg.get("idle_timeout", 300)
        while True:
            try:
                await asyncio.sleep(30)
                if (
                    self._browser
                    and self._active_pages == 0
                    and self._last_release > 0
                    and (time.monotonic() - self._last_release) > timeout
                ):
                    async with self._lock:
                        if self._active_pages == 0 and self._browser:
                            log.info(f"浏览器空闲超过 {timeout}s, 自动关闭")
                            await self._close_browser()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ---------- 核心 API ----------

    @asynccontextmanager
    async def new_page(self, viewport=None):
        """获取一个新页面 (async context manager), 自动限制并发

        用法:
            async with pw.new_page(viewport=(1200, 800)) as page:
                await page.goto(url)
                data = await page.screenshot()
        """
        if self._closed:
            raise RuntimeError("Playwright 已关闭")

        async with self._semaphore:
            if not await self._ensure_browser():
                raise RuntimeError(
                    f"Playwright 浏览器不可用: {self._last_error or '未知原因'}"
                )

            self._active_pages += 1
            vw = (
                viewport[0]
                if viewport
                else self._cfg.get("default_viewport_width", 1280)
            )
            vh = (
                viewport[1]
                if viewport
                else self._cfg.get("default_viewport_height", 720)
            )

            try:
                page = await self._browser.new_page(
                    viewport={"width": vw, "height": vh},
                )
            except Exception as e:
                if "Connection closed" in str(e) or not (
                    self._browser and self._browser.is_connected()
                ):
                    log.warning(f"浏览器连接已断开, 尝试重启: {e}")
                    await self._close_browser()
                    if not await self._ensure_browser():
                        self._active_pages -= 1
                        raise RuntimeError(
                            f"Playwright 浏览器重启失败: {self._last_error or '未知原因'}"
                        ) from e
                    page = await self._browser.new_page(
                        viewport={"width": vw, "height": vh},
                    )
                else:
                    self._active_pages -= 1
                    raise
            page.set_default_timeout(self._cfg.get("default_timeout", 30000))
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await page.close()
                self._active_pages -= 1
                if self._active_pages <= 0:
                    self._active_pages = 0
                    self._last_release = time.monotonic()
                    if self._cfg.get("close_after_use", False):
                        await self._shutdown_all()
                    elif self._cfg.get("idle_timeout", 300) == 0:
                        await self._close_browser()

    async def screenshot_url(
        self,
        url,
        *,
        viewport=None,
        full_page=True,
        image_format=None,
        quality=None,
        wait_until="networkidle",
        wait_ms=0,
        selector=None,
        timeout=None,
    ):
        """截图指定 URL, 返回图片 bytes

        参数:
            url         — 目标 URL
            viewport    — (width, height) 元组, None 则用默认值
            full_page   — 是否全页截图
            image_format— 'jpeg' / 'png', None 则用配置默认值
            quality     — jpeg 质量 1-100, None 则用配置默认值
            wait_until  — 页面加载等待策略: 'load' / 'domcontentloaded' / 'networkidle' / 'commit'
            wait_ms     — 页面加载完成后额外等待毫秒
            selector    — CSS 选择器, 指定则只截取该元素
            timeout     — 页面 goto 超时 (毫秒), None 则用默认
        """
        fmt = image_format or self._cfg.get("image_format", "jpeg")
        q = quality or self._cfg.get("image_quality", 90)
        to = timeout or self._cfg.get("default_timeout", 30000)

        async with self.new_page(viewport=viewport) as page:
            await page.goto(url, wait_until=wait_until, timeout=to)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            return await self._take_screenshot(page, full_page, fmt, q, selector)

    async def screenshot_html(
        self,
        html,
        *,
        viewport=None,
        full_page=True,
        image_format=None,
        quality=None,
        wait_ms=0,
        selector=None,
        base_url=None,
    ):
        """截图 HTML 字符串, 返回图片 bytes

        参数:
            html        — HTML 内容字符串
            viewport    — (width, height) 元组
            full_page   — 是否全页截图
            image_format— 'jpeg' / 'png'
            quality     — jpeg 质量 1-100
            wait_ms     — set_content 后额外等待毫秒
            selector    — CSS 选择器, 指定则只截取该元素
            base_url    — HTML 中相对路径的基础 URL
        """
        fmt = image_format or self._cfg.get("image_format", "jpeg")
        q = quality or self._cfg.get("image_quality", 90)

        async with self.new_page(viewport=viewport) as page:
            kw = {}
            if base_url:
                kw["base_url"] = base_url
            await page.set_content(html, wait_until="networkidle", **kw)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            return await self._take_screenshot(page, full_page, fmt, q, selector)

    async def screenshot_file(self, file_path, **kwargs):
        """截图本地 HTML 文件, 返回图片 bytes

        参数同 screenshot_url, file_path 为本地文件绝对路径
        """
        url = f"file:///{os.path.abspath(file_path).replace(os.sep, '/')}"
        return await self.screenshot_url(url, **kwargs)

    async def pdf_url(
        self,
        url,
        *,
        viewport=None,
        wait_until="networkidle",
        wait_ms=0,
        timeout=None,
        **pdf_kwargs,
    ):
        """将 URL 渲染为 PDF, 返回 bytes (仅 Chromium)"""
        to = timeout or self._cfg.get("default_timeout", 30000)
        async with self.new_page(viewport=viewport) as page:
            await page.goto(url, wait_until=wait_until, timeout=to)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            return await page.pdf(**pdf_kwargs)

    # ---------- 内部方法 ----------

    @staticmethod
    async def _take_screenshot(page, full_page, fmt, quality, selector):
        """统一截图逻辑"""
        kwargs = {"type": fmt, "full_page": full_page}
        if fmt == "jpeg":
            kwargs["quality"] = quality
        if selector:
            element = await page.query_selector(selector)
            if element:
                return await element.screenshot(
                    **{k: v for k, v in kwargs.items() if k != "full_page"}
                )
        return await page.screenshot(**kwargs)
