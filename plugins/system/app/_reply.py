from time import monotonic

_RETRY_NOTICE_INTERVAL = 10.0
_retry_notice_times: dict[tuple, float] = {}


def _allow_retry_notice(event):
    """同一机器人、会话和用户的重试提示，10 秒内最多发送一次。"""
    now = monotonic()
    key = (event.appid, event.chat_type, event.chat_id, event.user_id)
    last = _retry_notice_times.get(key)
    if last is not None and now - last < _RETRY_NOTICE_INTERVAL:
        return False
    for expired_key, timestamp in tuple(_retry_notice_times.items()):
        if now - timestamp >= _RETRY_NOTICE_INTERVAL:
            del _retry_notice_times[expired_key]
    # 发送前登记，避免并发触发时重复回复；被抑制的触发不延长间隔。
    _retry_notice_times[key] = now
    return True


async def reply(event, content=None, *, retry_notice=False, **kwargs):
    if retry_notice and not _allow_retry_notice(event):
        return None
    kwargs['skip_suffix'] = True
    return await event.reply(content, **kwargs)


async def sender_reply(sender, event, content=None, *, retry_notice=False, **kwargs):
    if retry_notice and not _allow_retry_notice(event):
        return None
    kwargs['skip_suffix'] = True
    return await sender.reply(event, content, **kwargs)
