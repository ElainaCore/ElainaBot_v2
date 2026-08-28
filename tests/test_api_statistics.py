"""API 测试: 统计模块 (statistics, statistics/chart, statistics/task/{id}, statistics/dates)"""

import pytest

from tests.helpers import assert_success_response
from web.tools._stats import statistics as statistics_handler


class TestStatistics:
    """统计数据接口测试"""

    async def test_get_statistics(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics', cookies=auth_cookies)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_get_statistics_with_date(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics?date=2026-05-20', cookies=auth_cookies)
        assert resp.status == 200

    async def test_get_statistics_force_refresh(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics?force_refresh=true', cookies=auth_cookies)
        assert resp.status == 200

    async def test_get_statistics_no_auth(self, api_client):
        resp = await api_client.get('/api/statistics')
        assert resp.status == 401

    @pytest.mark.parametrize(
        ('endpoint', 'gatherer_name', 'expected_args'),
        [
            ('summary', '_gather_summary', ('2026-05-20', '1001')),
            ('active', '_gather_active', ('2026-05-20', '1001')),
            ('top', '_gather_top', ('2026-05-20', '1001')),
            ('events', '_gather_events', ('2026-05-20', '1001')),
            ('totals', '_gather_totals', ('1001',)),
        ],
    )
    async def test_split_statistics_queries(self, api_client, auth_cookies, monkeypatch, endpoint, gatherer_name, expected_args):
        calls = []

        def gatherer(*args):
            calls.append(args)
            return {'endpoint': endpoint}

        monkeypatch.setattr(statistics_handler, gatherer_name, gatherer)
        resp = await api_client.get(
            f'/api/statistics/{endpoint}?date=2026-05-20&appid=1001',
            cookies=auth_cookies,
        )

        assert resp.status == 200
        assert await resp.json() == {'success': True, 'data': {'endpoint': endpoint}}
        assert calls == [expected_args]

    async def test_split_statistics_error(self, api_client, auth_cookies, monkeypatch):
        def gatherer(*_args):
            raise RuntimeError('query failed')

        monkeypatch.setattr(statistics_handler, '_gather_summary', gatherer)
        resp = await api_client.get('/api/statistics/summary', cookies=auth_cookies)

        assert resp.status == 500
        assert await resp.json() == {'success': False, 'error': 'query failed'}


class TestChart:
    """折线图数据接口测试"""

    async def test_get_chart_data(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics/chart', cookies=auth_cookies)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_get_chart_data_with_days(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics/chart?days=14', cookies=auth_cookies)
        assert resp.status == 200

    async def test_get_chart_data_no_auth(self, api_client):
        resp = await api_client.get('/api/statistics/chart')
        assert resp.status == 401


class TestTaskStatus:
    """任务状态接口测试"""

    async def test_task_not_found(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics/task/nonexistent', cookies=auth_cookies)
        assert resp.status == 404


class TestDates:
    """可用日期接口测试"""

    async def test_get_available_dates(self, api_client, auth_cookies):
        resp = await api_client.get('/api/statistics/dates', cookies=auth_cookies)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'dates' in data
