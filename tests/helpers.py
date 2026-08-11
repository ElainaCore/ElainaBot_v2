"""测试辅助函数 — 断言工具 + 登录快捷方式"""


def assert_success_response(data, msg=''):
    """断言响应为成功"""
    assert data.get('success') is True, f'{msg}: {data}'


def assert_error_response(data, msg=''):
    """断言响应为失败"""
    assert data.get('success') is False, f'{msg}: {data}'


def assert_401(resp):
    """断言响应状态码为 401"""
    assert resp.status == 401, f'expected 401, got {resp.status}'


def assert_200(resp):
    """断言响应状态码为 200"""
    assert resp.status == 200, f'expected 200, got {resp.status}'


async def do_login(client, password='test_pass'):
    """登录并返回 (response, json_data)"""
    resp = await client.post('/api/auth/login', json={'password': password})
    return resp, await resp.json()
