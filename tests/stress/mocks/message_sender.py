"""MockMessageSender — simulated reply/push/send without HTTP calls.

Wraps the MessageSender interface for stress testing.
In SIMULATE mode: returns fake success responses, records metrics.
Configurable latency and failure rate.
"""

import asyncio
import random
import time

from tests.stress.config import MockConfig


class MockMessageSender:
    """Simulated MessageSender for stress testing.

    Mimics the interface of core.message.sender.MessageSender without
    making actual HTTP calls to QQ API.
    """

    def __init__(self, appid="0000", config=None):
        self._appid = str(appid)
        self._config = config or MockConfig()
        self._bot_name = ""
        self._bot_qq = ""
        self._log_service = None
        self._web_log_cb = None
        self._reply_count = 0
        self._send_count = 0
        self._error_count = 0
        self._total_latency = 0.0
        self._latencies = []

    @property
    def appid(self):
        return self._appid

    @property
    def reply_count(self):
        return self._reply_count

    @property
    def send_count(self):
        return self._send_count

    @property
    def error_count(self):
        return self._error_count

    def bind_instance(self, *, log_service=None, bot_name="", bot_qq="", media_dir=""):
        self._log_service = log_service
        self._bot_name = bot_name or self._appid
        self._bot_qq = bot_qq or ""

    async def reply(self, event, content=None, buttons=None, *,
                    media=None, msg_type=None, template_name=None,
                    template_vars=None, prompt_buttons=None,
                    auto_delete_time=None, **kwargs):
        """Simulated reply to event."""
        return await self._simulate_send("reply")

    async def reply_image(self, event, image_data, content="", **kw):
        return await self._simulate_send("reply_image")

    async def reply_voice(self, event, voice_data, content="", **kw):
        return await self._simulate_send("reply_voice")

    async def reply_video(self, event, video_data, content="", **kw):
        return await self._simulate_send("reply_video")

    async def reply_file(self, event, file_data, content="", *,
                         file_name=None, auto_delete_time=None,
                         target_user_id=None, target_group_id=None):
        return await self._simulate_send("reply_file")

    async def reply_ark(self, event, template_id, kv_data, content="",
                        *, auto_delete_time=None):
        return await self._simulate_send("reply_ark")

    async def reply_card(self, event, card_type="tuwen", data=None,
                         content="", *, auto_delete_time=None):
        return await self._simulate_send("reply_card")

    async def send_to_group(self, group_id, content=None, *,
                            msg_id=None, event_id=None, buttons=None,
                            media=None, msg_type=None, skip_suffix=False, **kwargs):
        return await self._simulate_send("send_to_group")

    async def send_to_user(self, user_id, content=None, *,
                           msg_id=None, event_id=None, buttons=None,
                           media=None, msg_type=None, skip_suffix=False, **kwargs):
        return await self._simulate_send("send_to_user")

    async def send_to_channel(self, channel_id, content=None, *,
                              msg_id=None, buttons=None, **kwargs):
        return await self._simulate_send("send_to_channel")

    async def send_wakeup(self, user_id, content="", buttons=None):
        return await self._simulate_send("send_wakeup")

    async def ack_interaction(self, event, code=0, *, interaction_id=None):
        return await self._simulate_send("ack_interaction")

    async def recall(self, event, message_id=None):
        return await self._simulate_send("recall")

    def set_web_log_cb(self, cb):
        self._web_log_cb = cb

    async def close(self):
        pass

    # ---- Internal ----

    async def _simulate_send(self, method_name):
        """Core simulation: inject latency, check failure rate, return result."""
        t0 = time.perf_counter()

        # Simulated work
        latency = self._config.inject_latency
        if latency > 0:
            await asyncio.sleep(latency)

        dt = time.perf_counter() - t0
        self._total_latency += dt
        self._latencies.append(dt)

        # Simulated failure
        if self._config.failure_rate > 0 and random.random() < self._config.failure_rate:
            self._error_count += 1
            return False, {"code": 500, "message": "simulated failure"}

        self._send_count += 1
        if method_name == "reply":
            self._reply_count += 1

        # Return fake success
        msg_id = f"mock_msg_{self._send_count}"
        return {"id": msg_id, "msg_id": msg_id, "timestamp": str(time.time())}

    def stats(self):
        return {
            "appid": self._appid,
            "send_count": self._send_count,
            "reply_count": self._reply_count,
            "error_count": self._error_count,
            "avg_latency_ms": round((self._total_latency / max(self._send_count, 1)) * 1000, 2),
        }


class InstrumentedMessageSender:
    """RECORD mode: wraps real MessageSender, records metrics."""

    def __init__(self, real_sender, config=None):
        self._real = real_sender
        self._config = config or MockConfig()
        self._appid = real_sender._appid if hasattr(real_sender, '_appid') else "0000"
        self._latencies = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        real_method = getattr(self._real, name)
        if not callable(real_method):
            return real_method

        async def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = real_method(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            finally:
                dt = time.perf_counter() - t0
                self._latencies.append(dt)

        return wrapper

    def stats(self):
        avg = sum(self._latencies) / max(len(self._latencies), 1)
        return {
            "count": len(self._latencies),
            "avg_latency_ms": round(avg * 1000, 2),
        }
