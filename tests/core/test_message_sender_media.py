"""媒体发送失败日志测试。"""

import logging
from types import SimpleNamespace

import pytest

import core.message._media_send as media_send
import core.message.media as media
from core.message.sender import MessageSender


class _TokenManager:
    appid = 'test-appid'


class _RecordingSender(MessageSender):
    def __init__(self):
        super().__init__(_TokenManager())
        self.errors = []

    def _report_send_error(self, content, data=None, payload=None):
        self.errors.append((content, data, payload))


def _event():
    return SimpleNamespace(
        media_upload_endpoint='/v2/users/user-openid/files',
        error=None,
    )


def test_voice_upload_issue_does_not_log(monkeypatch):
    monkeypatch.setattr(media.log, 'warning', lambda message: pytest.fail(message))
    monkeypatch.setattr(media.log, 'debug', lambda message: pytest.fail(message))

    media._log_upload_issue(3, 'voice upload failed')


@pytest.mark.asyncio
async def test_voice_url_download_failure_is_saved_without_warning(monkeypatch, caplog):
    sender = _RecordingSender()
    event = _event()

    async def download_failed(self, url, *, silent=False):
        assert silent is True
        return None

    monkeypatch.setattr(media_send._MediaSendMixin, 'download_media', download_failed)

    with caplog.at_level(logging.WARNING, logger='ElainaBot.框架.消息发送'):
        result = await sender.reply_voice(event, 'https://example.test/voice.m4a')

    assert result is None
    assert len(sender.errors) == 1
    assert 'URL直传与本地下载均失败' in sender.errors[0][0]
    assert not any('语音发送失败' in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_voice_upload_failure_is_saved_without_warning(monkeypatch, caplog):
    sender = _RecordingSender()
    event = _event()
    event.error = {'code': 40093013, 'message': '上传音频时长超过限制'}

    async def download_ok(self, url, *, silent=False):
        assert silent is True
        return b'raw-audio'

    async def upload_failed(*args, **kwargs):
        return None

    monkeypatch.setattr(media_send._MediaSendMixin, 'download_media', download_ok)

    async def convert_ok(data):
        return data

    monkeypatch.setattr(media_send, 'convert_to_silk', convert_ok)
    monkeypatch.setattr(media_send, 'upload_media_bytes', upload_failed)

    with caplog.at_level(logging.WARNING, logger='ElainaBot.框架.消息发送'):
        result = await sender.reply_voice(event, 'https://example.test/voice.m4a')

    assert result is None
    assert len(sender.errors) == 1
    assert 'URL直传与本地上传均失败' in sender.errors[0][0]
    assert sender.errors[0][1]['code'] == 40093013
    assert not any('URL直传与本地上传均失败' in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_local_voice_upload_failure_is_saved_without_warning(monkeypatch, caplog):
    sender = _RecordingSender()
    event = _event()

    async def upload_failed(*args, **kwargs):
        return None

    async def convert_ok(data):
        return data

    monkeypatch.setattr(media_send, 'convert_to_silk', convert_ok)
    monkeypatch.setattr(media_send, 'upload_media_bytes', upload_failed)

    with caplog.at_level(logging.WARNING, logger='ElainaBot.框架.消息发送'):
        result = await sender.reply_voice(event, b'raw-audio')

    assert result is None
    assert len(sender.errors) == 1
    assert '本地上传失败' in sender.errors[0][0]
    assert not any('语音发送失败' in record.getMessage() for record in caplog.records)
