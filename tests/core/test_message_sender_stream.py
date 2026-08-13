"""流式消息请求体与分片状态测试。"""

from types import SimpleNamespace

import pytest

from core.message.sender import MessageSender


class _TokenManager:
    appid = 'test-appid'


class _RecordingSender(MessageSender):
    def __init__(self):
        super().__init__(_TokenManager())
        self.requests = []

    async def post_json(self, endpoint, payload):
        self.requests.append((endpoint, payload))
        return True, {'id': 'stream-id', 'index': payload['index']}

    def _log_push(self, *args, **kwargs):
        pass

    def _log_sent(self, *args, **kwargs):
        pass


def _payloads(sender):
    return [payload for _, payload in sender.requests]


@pytest.mark.parametrize('content_type', ['text', 'markdown'])
async def test_stream_supports_text_and_markdown(content_type):
    sender = _RecordingSender()

    result = await sender.send_stream_to_user(
        'user-openid',
        ['第一段', '第二段'],
        content_type=content_type,
        input_mode='replace',
        msg_id='source-message',
        msg_seq=7,
        min_interval=999,
    )

    assert result == {'id': 'stream-id', 'index': 1}
    assert [endpoint for endpoint, _ in sender.requests] == [
        '/v2/users/user-openid/stream_messages',
        '/v2/users/user-openid/stream_messages',
    ]
    assert _payloads(sender) == [
        {
            'input_mode': 'replace',
            'input_state': 1,
            'index': 0,
            'content_type': content_type,
            'content_raw': '第一段',
            'msg_seq': 7,
            'msg_id': 'source-message',
        },
        {
            'input_mode': 'replace',
            'input_state': 10,
            'index': 1,
            'content_type': content_type,
            'content_raw': '第一段第二段',
            'msg_seq': 7,
            'stream_msg_id': 'stream-id',
            'msg_id': 'source-message',
        },
    ]


async def test_append_mode_keeps_only_unsent_suffix_after_replace_event():
    sender = _RecordingSender()

    await sender.send_stream_to_user(
        'user-openid',
        ['A', {'type': 'replace', 'text': 'AB'}, 'C'],
        content_type='text',
        input_mode='append',
        msg_id='source-message',
        min_interval=999,
    )

    payloads = _payloads(sender)
    assert [payload['content_raw'] for payload in payloads] == ['A', 'BC']
    assert [payload['input_state'] for payload in payloads] == [1, 10]


async def test_replace_event_cannot_rewrite_sent_prefix():
    sender = _RecordingSender()

    with pytest.raises(ValueError, match='正文前缀'):
        await sender.send_stream_to_user(
            'user-openid',
            ['已发送', {'type': 'replace', 'text': '被改写'}],
            content_type='text',
            msg_id='source-message',
            min_interval=999,
        )


async def test_reply_stream_only_accepts_c2c_event():
    sender = _RecordingSender()
    invalid_event = SimpleNamespace(
        event_type='DIRECT_MESSAGE_CREATE',
        raw_user_id='user-openid',
        user_id='user-openid',
    )

    with pytest.raises(ValueError, match='C2C_MESSAGE_CREATE'):
        await sender.reply_stream(invalid_event, '内容', content_type='text')


async def test_reply_stream_uses_event_message_id():
    sender = _RecordingSender()
    event = SimpleNamespace(
        event_type='C2C_MESSAGE_CREATE',
        raw_user_id='user-openid',
        user_id='user-openid',
        needs_msg_id=True,
        needs_event_id=False,
        message_id='source-message',
        event_id='event-id',
    )

    await sender.reply_stream(
        event,
        '内容',
        content_type='markdown',
        min_interval=0,
    )

    assert all(
        payload['msg_id'] == 'source-message'
        for payload in _payloads(sender)
    )
