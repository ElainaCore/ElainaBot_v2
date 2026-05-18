"""会话管理与鉴权"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

from aiohttp import web

_COOKIE_SECRET = ''
_BAN_DURATION = 43200
_SESSION_CLEANUP_INTERVAL = 300
_IP_CLEANUP_INTERVAL = 3600
_FAIL_WINDOW = 86400
_MAX_SESSIONS = 10
_MAX_FAIL_COUNT = 5
_SESSION_DAYS = 7
_TOKEN_EXPIRY = 86400 * 7
_MAX_IP_RECORDS = 10000

valid_sessions: dict = {}
ip_access_data: dict = {}
_last_session_cleanup = 0
_last_ip_cleanup = 0
_data_dir = ''
_ip_file = ''
_session_file = ''
_secret_file = ''
_io_lock = threading.Lock()  # 串行化文件写入, 避免内容交错损坏


def init(base_dir: str):
    global _data_dir, _ip_file, _session_file, _secret_file, _COOKIE_SECRET
    _data_dir = os.path.join(base_dir, 'data', 'web')
    os.makedirs(_data_dir, exist_ok=True)
    _ip_file = os.path.join(_data_dir, 'ip.json')
    _session_file = os.path.join(_data_dir, 'sessions.json')
    _secret_file = os.path.join(_data_dir, '.cookie_secret')
    _COOKIE_SECRET = _load_or_create_secret()
    _load_ip_data()
    _load_session_data()


def _load_or_create_secret() -> str:
    """从文件加载 cookie secret, 不存在则随机生成并持久化"""
    if os.path.exists(_secret_file):
        try:
            with open(_secret_file, encoding='utf-8') as f:
                secret = f.read().strip()
                if len(secret) >= 32:
                    return secret
        except Exception:
            pass
    secret = base64.urlsafe_b64encode(os.urandom(48)).decode()
    try:
        with open(_secret_file, 'w', encoding='utf-8') as f:
            f.write(secret)
    except Exception:
        pass
    return secret


# ==================== 密码 hash ====================

_PWD_HASH_PREFIX = 'sha256:'


def hash_password(plain: str) -> str:
    """SHA-256 加盐 hash"""
    salt = os.urandom(16)
    h = hashlib.sha256(salt + plain.encode('utf-8')).hexdigest()
    return _PWD_HASH_PREFIX + base64.b64encode(salt).decode() + ':' + h


def verify_password(plain: str, stored: str) -> bool:
    """恒定时间比较, 兼容明文旧密码"""
    if stored.startswith(_PWD_HASH_PREFIX):
        try:
            rest = stored[len(_PWD_HASH_PREFIX) :]
            salt_b64, expected_hex = rest.split(':', 1)
            salt = base64.b64decode(salt_b64)
            actual_hex = hashlib.sha256(salt + plain.encode('utf-8')).hexdigest()
            return hmac.compare_digest(actual_hex, expected_hex)
        except Exception:
            return False
    # 明文回退 (旧配置兼容), 仍用恒定时间比较
    return hmac.compare_digest(plain, stored)


def is_hashed(stored: str) -> bool:
    return stored.startswith(_PWD_HASH_PREFIX)


# ==================== JSON IO ====================


def _read_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default or {}


def _write_text_sync(path, text):
    """同步写入文本 (在 executor 中调用)"""
    with _io_lock:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        except Exception:
            pass


def _write_json(path, data):
    """异步友好的 JSON 写入: 主线程序列化 (一致性), executor 写盘 (不阻塞 loop)"""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_text_sync, path, text)
    except RuntimeError:
        _write_text_sync(path, text)


# ==================== IP ====================


def _load_ip_data():
    global ip_access_data
    ip_access_data = _read_json(_ip_file, {})


def _save_ip_data():
    _write_json(_ip_file, ip_access_data)


def get_real_ip(request: web.Request) -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    if request.transport is None:
        return '127.0.0.1'
    peername = request.transport.get_extra_info('peername')
    return peername[0] if peername else '127.0.0.1'


def record_ip_access(ip, access_type='success'):
    now_iso = datetime.now().isoformat()
    if ip not in ip_access_data:
        ip_access_data[ip] = {
            'first_access': now_iso,
            'last_access': now_iso,
            'fail_count': 0,
            'fail_times': [],
            'is_banned': False,
            'ban_time': None,
        }
    d = ip_access_data[ip]
    d['last_access'] = now_iso
    if access_type == 'fail':
        d['fail_count'] = d.get('fail_count', 0) + 1
        d.setdefault('fail_times', []).append(now_iso)
        now = datetime.now()
        d['fail_times'] = [t for t in d['fail_times'] if (now - datetime.fromisoformat(t)).total_seconds() < _FAIL_WINDOW]
        if len(d['fail_times']) >= _MAX_FAIL_COUNT:
            d['is_banned'] = True
            d['ban_time'] = now_iso
    _save_ip_data()


def is_ip_banned(ip) -> bool:
    d = ip_access_data.get(ip)
    if not d or not d.get('is_banned'):
        return False
    ban_time = d.get('ban_time')
    if not ban_time:
        return True
    try:
        if (datetime.now() - datetime.fromisoformat(ban_time)).total_seconds() >= _BAN_DURATION:
            d['is_banned'] = False
            d['ban_time'] = None
            d['fail_times'] = []
            _save_ip_data()
            return False
        return True
    except Exception:
        return True


def get_remaining_attempts(ip) -> int:
    """返回该 IP 剩余登录尝试次数"""
    d = ip_access_data.get(ip)
    if not d:
        return _MAX_FAIL_COUNT
    now = datetime.now()
    recent = [t for t in d.get('fail_times', []) if (now - datetime.fromisoformat(t)).total_seconds() < _FAIL_WINDOW]
    return max(0, _MAX_FAIL_COUNT - len(recent))


def cleanup_expired_ip_bans():
    global _last_ip_cleanup
    now = time.time()
    if now - _last_ip_cleanup < _IP_CLEANUP_INTERVAL:
        return
    _last_ip_cleanup = now
    now_dt = datetime.now()
    for _ip, d in list(ip_access_data.items()):
        if d.get('is_banned') and d.get('ban_time'):
            try:
                if (now_dt - datetime.fromisoformat(d['ban_time'])).total_seconds() >= _BAN_DURATION:
                    d['is_banned'] = False
                    d['ban_time'] = None
                    d['fail_times'] = []
            except Exception:
                pass
    # 超限时淘汰最旧的无封禁记录
    if len(ip_access_data) > _MAX_IP_RECORDS:
        unbanned = sorted(
            ((k, v) for k, v in ip_access_data.items() if not v.get('is_banned')),
            key=lambda x: x[1].get('last_access', ''),
        )
        for k, _ in unbanned[: len(ip_access_data) - _MAX_IP_RECORDS]:
            del ip_access_data[k]
    _save_ip_data()


# ==================== Token ====================


def _generate_token() -> str:
    return base64.urlsafe_b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode().rstrip('=')


def _sign(value) -> str:
    sig = hmac.new(_COOKIE_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f'{value}.{sig}'


def _verify_sig(signed) -> tuple:
    try:
        value, sig = signed.rsplit('.', 1)
        expected = hmac.new(_COOKIE_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected), value
    except Exception:
        return False, None


# ==================== Session ====================


def _load_session_data():
    global valid_sessions
    raw = _read_json(_session_file, {})
    now = datetime.now()
    for token, info in raw.items():
        try:
            info['created'] = datetime.fromisoformat(info['created'])
            info['expires'] = datetime.fromisoformat(info['expires'])
            if now < info['expires']:
                valid_sessions[token] = info
        except Exception:
            pass


def _save_session_data():
    data = {}
    for t, info in valid_sessions.items():
        data[t] = {
            'created': info['created'].isoformat(),
            'expires': info['expires'].isoformat(),
            'ip': info.get('ip', ''),
        }
    _write_json(_session_file, data)


def _cleanup_sessions():
    global _last_session_cleanup
    now_t = time.time()
    if now_t - _last_session_cleanup < _SESSION_CLEANUP_INTERVAL:
        return
    _last_session_cleanup = now_t
    now = datetime.now()
    expired = [t for t, info in valid_sessions.items() if now >= info['expires']]
    for t in expired:
        del valid_sessions[t]
    if expired:
        _save_session_data()


def create_session(request: web.Request) -> str:
    """创建会话并返回 bearer token"""
    _cleanup_sessions()
    if len(valid_sessions) > _MAX_SESSIONS:
        oldest = sorted(valid_sessions, key=lambda t: valid_sessions[t]['created'])
        for t in oldest[: len(valid_sessions) - _MAX_SESSIONS]:
            valid_sessions.pop(t)

    ip = get_real_ip(request)
    now = datetime.now()
    token = _generate_token()
    valid_sessions[token] = {
        'created': now,
        'expires': now + timedelta(days=_SESSION_DAYS),
        'ip': ip,
    }
    _save_session_data()
    return token


def validate_token(request: web.Request) -> bool:
    """验证 Authorization: Bearer <token> 或 ?token= 查询参数"""
    _cleanup_sessions()
    # 优先从 Authorization 头获取
    token = ''
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    # 回退: 从 query 参数获取 (iframe/导航请求无法带 header)
    if not token:
        token = request.query.get('token', '')
    if not token or token not in valid_sessions:
        return False
    info = valid_sessions[token]
    if datetime.now() >= info['expires']:
        valid_sessions.pop(token, None)
        _save_session_data()
        return False
    return True


# ==================== 中间件 ====================


def require_auth(handler):
    """aiohttp 路由装饰器: 要求 Bearer token"""

    async def wrapped(request):
        if not validate_token(request):
            return web.json_response({'success': False, 'error': '未登录或会话已过期'}, status=401)
        return await handler(request)

    wrapped.__name__ = handler.__name__
    wrapped.__qualname__ = handler.__qualname__
    return wrapped


# ==================== 登录日志查询 ====================


def get_login_logs() -> list:
    raw = _read_json(_ip_file, {})
    logs = []
    for ip, d in raw.items():
        logs.append(
            {
                'ip': ip,
                'first_access': d.get('first_access', ''),
                'last_access': d.get('last_access', ''),
                'fail_count': d.get('fail_count', 0),
                'is_banned': d.get('is_banned', False),
                'ban_time': d.get('ban_time', ''),
            }
        )
    logs.sort(key=lambda x: x['last_access'] or '', reverse=True)
    return logs


def unban_ip(ip) -> bool:
    raw = _read_json(_ip_file, {})
    if ip in raw:
        raw[ip].update({'is_banned': False, 'ban_time': None, 'fail_times': [], 'fail_count': 0})
        _write_json(_ip_file, raw)
        if ip in ip_access_data:
            ip_access_data[ip].update(raw[ip])
        return True
    return False


def delete_ip_record(ip) -> bool:
    raw = _read_json(_ip_file, {})
    if ip in raw:
        del raw[ip]
        _write_json(_ip_file, raw)
        ip_access_data.pop(ip, None)
        return True
    return False
