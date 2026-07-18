"""API 测试: 数据库浏览模块 (database/*)"""

from tests.helpers import assert_success_response


class TestDatabaseList:
    """数据库列表接口测试"""

    async def test_list_databases(self, api_client, auth_headers):
        resp = await api_client.get('/api/database/list', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'databases' in data['data']

    async def test_list_databases_no_auth(self, api_client):
        resp = await api_client.get('/api/database/list')
        assert resp.status == 401


class TestDatabaseTables:
    """数据库表列表接口测试"""

    async def test_list_tables_missing_path(self, api_client, auth_headers):
        resp = await api_client.post('/api/database/tables', json={}, headers=auth_headers)
        assert resp.status == 400

    async def test_list_tables_invalid_path(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/database/tables',
            json={'path': '../../../etc/passwd'},
            headers=auth_headers,
        )
        assert resp.status == 403


class TestDatabaseQuery:
    """数据库查询接口测试"""

    async def test_query_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/database/query', json={}, headers=auth_headers)
        assert resp.status == 400

    async def test_query_invalid_path(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/database/query',
            json={'path': '/etc/passwd', 'table': 'test'},
            headers=auth_headers,
        )
        assert resp.status == 403

    async def test_query_invalid_table_name(self, api_client, auth_headers):
        """表名含特殊字符应被拒绝"""
        resp = await api_client.post(
            '/api/database/query',
            json={'path': 'data/log/test.db', 'table': 'test;DROP TABLE--'},
            headers=auth_headers,
        )
        assert resp.status in (400, 403)


class TestDatabaseSQL:
    """SQL 执行接口测试"""

    async def test_sql_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/database/sql', json={}, headers=auth_headers)
        assert resp.status == 400

    async def test_sql_write_operation_blocked(self, api_client, auth_headers):
        """写操作应被拦截"""
        resp = await api_client.post(
            '/api/database/sql',
            json={'path': 'data/log/test.db', 'sql': 'DROP TABLE log'},
            headers=auth_headers,
        )
        assert resp.status == 403

    async def test_sql_invalid_path(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/database/sql',
            json={'path': '/etc/shadow', 'sql': 'SELECT 1'},
            headers=auth_headers,
        )
        assert resp.status == 403


class TestDatabaseDelete:
    """数据库删除接口测试"""

    async def test_delete_missing_params(self, api_client, auth_headers):
        resp = await api_client.post('/api/database/delete', json={}, headers=auth_headers)
        assert resp.status == 400

    async def test_delete_invalid_path(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/database/delete',
            json={'path': '/etc/hosts', 'table': 'test', 'rowids': [1]},
            headers=auth_headers,
        )
        assert resp.status == 403

    async def test_delete_invalid_rowids(self, api_client, auth_headers):
        resp = await api_client.post(
            '/api/database/delete',
            json={
                'path': 'data/log/test.db',
                'table': 'log',
                'rowids': 'not_a_list',
            },
            headers=auth_headers,
        )
        assert resp.status == 400
