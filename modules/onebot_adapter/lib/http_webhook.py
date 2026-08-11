"""OneBot 11 反向 HTTP (HTTP POST 事件上报)

将事件以 HTTP POST 推送到外部框架的上报地址, 支持:
  - X-Signature: HMAC-SHA1 签名 (secret 配置)
  - Authorization: Bearer token (access_token 配置)
  - 快速操作: 响应体中的 reply / delete 字段
遵循 OneBot 11 HTTP POST 通信规范: https://github.com/botuniverse/onebot-11
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json

import aiohttp


class OneBotHTTPWebhook:
    """OneBot 11 HTTP POST 上报器 (反向 HTTP)"""

    __slots__ = ('_entries', '_on_action', '_log', '_debug', '_session', '_status', '_tasks')

    def __init__(self, *, entries, on_action, log, debug=False):
        # [{'name': str, 'url': str, 'appid': str, 'token': str, 'secret': str, 'timeout': int}]
        self._entries = entries or []
        self._on_action = on_action
        self._log = log
        self._debug = debug
        self._session: aiohttp.ClientSession | None = None
        self._status: dict[str, dict] = {}  # name -> {ok: bool, error: str}
        self._tasks: set[asyncio.Task] = set()

    @property
    def has_targets(self) -> bool:
        return bool(self._entries)

    async def start(self):
        if self._entries:
            self._session = aiohttp.ClientSession()
            for entry in self._entries:
                self._log.info(f'HTTP 上报目标已注册: {entry["url"]} (appid={entry.get("appid", "") or "全部"})')

    async def stop(self):
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        if self._session:
            await self._session.close()
            self._session = None

    def status(self) -> dict:
        """返回各上报目标的运行状态 (供面板展示)"""
        return {e['name']: self._status.get(e['name'], {'ok': None, 'error': ''}) for e in self._entries}

    def push(self, event: dict, appid: str = ''):
        """异步推送事件到所有匹配的上报目标 (不阻塞调用方)"""
        if not self._session:
            return
        for entry in self._entries:
            if entry.get('appid') and appid and entry['appid'] != appid:
                continue
            task = asyncio.create_task(self._post(entry, event, appid))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _post(self, entry: dict, event: dict, appid: str = ''):
        name = entry['name']
        url = entry['url']
        body = json.dumps(event, ensure_ascii=False).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'X-Self-ID': str(event.get('self_id', '')),
            'User-Agent': 'ElainaBot/OneBot11',
        }
        token = entry.get('token', '')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        secret = entry.get('secret', '')
        if secret:
            sig = hmac.new(secret.encode('utf-8'), body, hashlib.sha1).hexdigest()
            headers['X-Signature'] = f'sha1={sig}'

        timeout = aiohttp.ClientTimeout(total=int(entry.get('timeout', 10) or 10))
        try:
            if self._debug and event.get('post_type') != 'meta_event':
                self._log.info(f'[HTTP上报→] {url} {body.decode("utf-8")[:500]}')
            async with self._session.post(url, data=body, headers=headers, timeout=timeout) as resp:
                self._status[name] = {'ok': resp.status < 400, 'error': '' if resp.status < 400 else f'HTTP {resp.status}'}
                if resp.status >= 400:
                    self._log.warning(f'HTTP 上报失败 [{url}]: HTTP {resp.status}')
                    return
                text = await resp.text()
                if text.strip():
                    await self._handle_quick_operation(event, text, appid)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._status[name] = {'ok': False, 'error': str(e)}
            self._log.warning(f'HTTP 上报失败 [{url}]: {e}')

    async def _handle_quick_operation(self, event: dict, text: str, appid: str = ''):
        """处理上报响应中的快速操作 (reply / delete)"""
        with contextlib.suppress(json.JSONDecodeError):
            op = json.loads(text)
            if not isinstance(op, dict) or not op:
                return
            if op.get('reply'):
                params: dict = {'message': op['reply']}
                if event.get('message_type') == 'group' and event.get('group_id'):
                    params['group_id'] = event['group_id']
                elif event.get('user_id'):
                    params['user_id'] = event['user_id']
                if op.get('auto_escape'):
                    params['auto_escape'] = True
                await self._on_action('send_msg', params, None, appid)
            if op.get('delete') and event.get('message_id'):
                await self._on_action('delete_msg', {'message_id': event['message_id']}, None, appid)
