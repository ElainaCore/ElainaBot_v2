"""Web API JSON response helpers."""

from aiohttp import web

_OK_CODE = 0


def ok(data=None, *, message='', status=200):
    return web.json_response(
        {'success': True, 'code': _OK_CODE, 'message': message, 'data': data},
        status=status,
    )


def error(message, *, status=400, code=None, data=None):
    return web.json_response(
        {
            'success': False,
            'code': code if code is not None else status,
            'message': message,
            'data': data,
        },
        status=status,
    )


async def json_body(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None
