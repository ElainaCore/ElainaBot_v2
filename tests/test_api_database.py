"""API 测试: 数据库浏览模块 (database/*)"""

import json
import sqlite3
import threading

import pytest

from tests.helpers import assert_success_response
from web.tools._database import browser as database_browser
from web.tools._database.browser import _is_sqlite_path, _query_table_sync


@pytest.mark.parametrize(
    'path',
    ['data.db', 'data.sqlite', 'data.sqlite3', 'data.db3', 'data.s3db', 'data.sl3', 'DATA.SQLITE'],
)
def test_supported_sqlite_extensions(path):
    assert _is_sqlite_path(path)


@pytest.mark.parametrize('path', ['data.sql', 'data.db-wal', 'data.db-shm', 'data.txt', 'data'])
def test_rejects_non_database_extensions(path):
    assert not _is_sqlite_path(path)


class TestDatabaseList:
    """数据库列表接口测试"""

    async def test_list_databases(self, api_client, auth_cookies):
        resp = await api_client.get('/api/database/list', cookies=auth_cookies)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'databases' in data['data']

    async def test_list_databases_no_auth(self, api_client):
        resp = await api_client.get('/api/database/list')
        assert resp.status == 401


class TestDatabaseTables:
    """数据库表列表接口测试"""

    async def test_list_tables_missing_path(self, api_client, auth_cookies):
        resp = await api_client.post('/api/database/tables', json={}, cookies=auth_cookies)
        assert resp.status == 400

    async def test_list_tables_invalid_path(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/database/tables',
            json={'path': '../../../etc/passwd'},
            cookies=auth_cookies,
        )
        assert resp.status == 403


class TestDatabaseQuery:
    """数据库查询接口测试"""

    async def test_query_missing_params(self, api_client, auth_cookies):
        resp = await api_client.post('/api/database/query', json={}, cookies=auth_cookies)
        assert resp.status == 400

    async def test_query_invalid_path(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/database/query',
            json={'path': '/etc/passwd', 'table': 'test'},
            cookies=auth_cookies,
        )
        assert resp.status == 403

    async def test_query_invalid_table_name(self, api_client, auth_cookies):
        """表名含特殊字符应被拒绝"""
        resp = await api_client.post(
            '/api/database/query',
            json={'path': 'data/log/test.db', 'table': 'test;DROP TABLE--'},
            cookies=auth_cookies,
        )
        assert resp.status in (400, 403)

    def test_query_helper_preserves_paging_and_default_order(self, tmp_path):
        db_path = tmp_path / 'records.db'
        with sqlite3.connect(db_path) as conn:
            conn.execute('CREATE TABLE records (name TEXT)')
            conn.executemany('INSERT INTO records (name) VALUES (?)', [('first',), ('second',), ('third',)])

        result = _query_table_sync(str(db_path), 'records', page=1, page_size=2, order_by='', order_dir='DESC')

        assert result['total'] == 3
        assert result['page'] == 1
        assert result['page_size'] == 2
        assert [row['name'] for row in result['rows']] == ['third', 'second']
        assert result['columns'] == [{'name': 'name', 'type': 'TEXT'}]

    async def test_query_runs_sqlite_work_in_worker_thread(self, monkeypatch):
        main_thread = threading.get_ident()
        worker_threads = []

        class Request:
            async def json(self):
                return {'path': 'records.db', 'table': 'records'}

        def query_stub(*args):
            worker_threads.append(threading.get_ident())
            return {'rows': [], 'columns': [], 'total': 0, 'page': 1, 'page_size': 50}

        monkeypatch.setattr(database_browser, '_validate_db_path', lambda path: (True, path))
        monkeypatch.setattr(database_browser, '_query_table_sync', query_stub)

        response = await database_browser.handle_query_table(Request())
        payload = json.loads(response.text)

        assert response.status == 200
        assert payload['success'] is True
        assert worker_threads and worker_threads[0] != main_thread


class TestDatabaseSQL:
    """SQL 执行接口测试"""

    async def test_sql_missing_params(self, api_client, auth_cookies):
        resp = await api_client.post('/api/database/sql', json={}, cookies=auth_cookies)
        assert resp.status == 400

    async def test_sql_write_operation_blocked(self, api_client, auth_cookies):
        """写操作应被拦截"""
        resp = await api_client.post(
            '/api/database/sql',
            json={'path': 'data/log/test.db', 'sql': 'DROP TABLE log'},
            cookies=auth_cookies,
        )
        assert resp.status == 403

    async def test_sql_invalid_path(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/database/sql',
            json={'path': '/etc/shadow', 'sql': 'SELECT 1'},
            cookies=auth_cookies,
        )
        assert resp.status == 403


class TestDatabaseDelete:
    """数据库删除接口测试"""

    async def test_delete_missing_params(self, api_client, auth_cookies):
        resp = await api_client.post('/api/database/delete', json={}, cookies=auth_cookies)
        assert resp.status == 400

    async def test_delete_invalid_path(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/database/delete',
            json={'path': '/etc/hosts', 'table': 'test', 'rowids': [1]},
            cookies=auth_cookies,
        )
        assert resp.status == 403

    async def test_delete_invalid_rowids(self, api_client, auth_cookies):
        resp = await api_client.post(
            '/api/database/delete',
            json={
                'path': 'data/log/test.db',
                'table': 'log',
                'rowids': 'not_a_list',
            },
            cookies=auth_cookies,
        )
        assert resp.status == 400
