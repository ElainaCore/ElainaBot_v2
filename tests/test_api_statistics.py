"""API 测试: 统计模块 (statistics, statistics/chart, statistics/task/{id}, statistics/dates)"""

from tests.helpers import assert_success_response


class TestStatistics:
    """统计数据接口测试"""

    async def test_get_statistics(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_get_statistics_with_date(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics?date=2026-05-20', headers=auth_headers)
        assert resp.status == 200

    async def test_get_statistics_force_refresh(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics?force_refresh=true', headers=auth_headers)
        assert resp.status == 200

    async def test_get_statistics_no_auth(self, api_client):
        resp = await api_client.get('/api/statistics')
        assert resp.status == 401


class TestChart:
    """折线图数据接口测试"""

    async def test_get_chart_data(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics/chart', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'data' in data

    async def test_get_chart_data_with_days(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics/chart?days=14', headers=auth_headers)
        assert resp.status == 200

    async def test_get_chart_data_no_auth(self, api_client):
        resp = await api_client.get('/api/statistics/chart')
        assert resp.status == 401


class TestTaskStatus:
    """任务状态接口测试"""

    async def test_task_not_found(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics/task/nonexistent', headers=auth_headers)
        assert resp.status == 404


class TestDates:
    """可用日期接口测试"""

    async def test_get_available_dates(self, api_client, auth_headers):
        resp = await api_client.get('/api/statistics/dates', headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert_success_response(data)
        assert 'dates' in data
