"""API 测试: 消息管理模块 (message/chats, history, send, nickname, nicknames, recall)"""

from tests.helpers import assert_success_response


class TestMessageChats:
    """聊天列表接口测试"""

    async def test_get_chats(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/chats',
            json={'type': 'group'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data
        chat_data = data['data']
        assert 'chats' in chat_data
        assert 'total' in chat_data

    async def test_get_chats_with_search(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/chats',
            json={'type': 'group', 'search': 'nonexistent'},
            headers=auth_headers,
        )
        assert resp.status == 200

    async def test_get_chats_pagination(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/chats',
            json={'type': 'group', 'page': 1, 'page_size': 10},
            headers=auth_headers,
        )
        assert resp.status == 200

    async def test_get_chats_no_auth(self, api_client):
        resp = await api_client.post('/api/message/chats', json={'type': 'group'})
        assert resp.status == 401


class TestMessageHistory:
    """聊天记录接口测试"""

    async def test_get_history_missing_chat_id(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/history',
            json={},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        chat_data = data.get('data', {})
        assert chat_data.get('messages') == []

    async def test_get_history_with_chat_id(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/history',
            json={'chat_type': 'group', 'chat_id': 'test_group'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_get_history_with_before_date(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/history',
            json={
                'chat_type': 'group',
                'chat_id': 'test_group',
                'before_date': '2026-01-01',
            },
            headers=auth_headers,
        )
        assert resp.status == 200


class TestMessageSend:
    """发送消息接口测试"""

    async def test_send_missing_params(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/send',
            json={},
            headers=auth_headers,
        )
        assert resp.status in (400, 500)

    async def test_send_empty_content(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/send',
            json={'chat_type': 'group', 'chat_id': 'test', 'msg_type': 'text', 'content': ''},
            headers=auth_headers,
        )
        assert resp.status in (400, 500)

    async def test_send_no_auth(self, api_client):
        resp = await api_client.post('/api/message/send', json={})
        assert resp.status == 401


class TestMessageNickname:
    """昵称查询接口测试"""

    async def test_get_nickname_missing_uid(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/nickname',
            json={},
            headers=auth_headers,
        )
        assert resp.status == 400

    async def test_get_nickname(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/nickname',
            json={'user_id': 'test_user_001'},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)

    async def test_get_nicknames_batch_missing_uids(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/nicknames',
            json={},
            headers=auth_headers,
        )
        assert resp.status == 400

    async def test_get_nicknames_batch(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/nicknames',
            json={'user_ids': ['user_001', 'user_002']},
            headers=auth_headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data


class TestMessageRecall:
    """撤回消息接口测试"""

    async def test_recall_missing_params(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/message/recall',
            json={},
            headers=auth_headers,
        )
        assert resp.status in (400, 500)

    async def test_recall_no_auth(self, api_client):
        resp = await api_client.post('/api/message/recall', json={})
        assert resp.status == 401
