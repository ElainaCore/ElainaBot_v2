"""Webhook 请求处理 — 验证 / 转发 / 交互响应"""

import json
import logging

from aiohttp import web

from core.base.sign import verify_and_respond
from core.message.event import Event

log = logging.getLogger('ElainaBot.webhook')


def _finish_interaction(event, task):
    """交互事件分发结束回调: 取出异常 + 若插件未设置 code 则用默认 code 返回。"""
    try:
        exc = task.exception()
    except Exception:
        exc = None
    if exc is not None:
        log.warning(f'[Webhook] 交互事件分发异常: {exc!r}')
    event.finish_dispatch()


class WebhookHandler:
    """处理 QQ Bot webhook 回调: 签名验证 → 事件构造 → 分发"""

    def __init__(self, bot_registry, on_event, on_error=None):
        self._bot_registry = bot_registry
        self._on_event = on_event
        self._on_error = on_error or (lambda ctx: log.error(f'Webhook error: {ctx}'))

    async def handle(self, request: web.Request) -> web.Response:
        raw_body = await request.read()
        headers = dict(request.headers)

        appid = headers.get('X-Bot-Appid', headers.get('x-bot-appid', '')) or request.query.get('appid', '')
        if not appid:
            return web.json_response({'error': 'missing appid'}, status=400)

        bot = self._bot_registry.get(appid)
        if not bot:
            return web.json_response({'error': f'unknown bot: {appid}'}, status=404)

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return web.json_response({'error': 'invalid JSON'}, status=400)

        # 签名验证 (op=13)
        if body.get('op') == 13:
            sig_resp = verify_and_respond(body, bot.secret)
            success = bool(sig_resp)
            log.info(f'[Webhook] 签名校验{"成功" if success else "失败"} appid={appid}')
            return web.Response(text=sig_resp, content_type='application/json') if success else web.json_response({'error': 'invalid validation'}, status=400)

        # 事件构造与分发
        import asyncio

        is_interaction = body.get('t') == 'INTERACTION_CREATE'
        try:
            event = Event.from_webhook(headers, body)
            if is_interaction:
                # 启动 ACK 倒计时, 分发结束后用插件设置的 code 兜底/返回
                event.start_ack_countdown()
                task = asyncio.create_task(self._on_event(event))
                task.add_done_callback(lambda t, ev=event: _finish_interaction(ev, t))
            else:
                asyncio.create_task(self._on_event(event))
        except Exception as e:
            self._on_error({'appid': appid, 'source': 'Webhook', 'error': e})
            if is_interaction:
                return web.json_response({'op': 12, 'code': 0})
            return web.json_response({})

        # 交互事件: 等待插件设置 code (或分发结束/超时), 随 op12 ACK 返回
        if is_interaction:
            code = await event.wait_ack_code()
            return web.json_response({'op': 12, 'code': code})
        return web.json_response({})
