"""会话管理与鉴权"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

from aiohttp import web

from core.base.config import cfg
from web.response import error

_BAN_DURATION = 43200
_SESSION_CLEANUP_INTERVAL = 300
_IP_CLEANUP_INTERVAL = 3600
_FAIL_WINDOW = 86400
_MAX_SESSIONS = 10
_MAX_FAIL_COUNT = 5
_SESSION_DAYS = 7
_MAX_IP_RECORDS = 10000
_SESSION_COOKIE_PREFIX = 'elaina_session'
SESSION_COOKIE = _SESSION_COOKIE_PREFIX

valid_sessions: dict = {}
ip_access_data: dict = {}
_last_session_cleanup = 0
_last_ip_cleanup = 0
_data_dir = ''
_ip_file = ''
_session_file = ''
_instance_file = ''
_password_changed_file = ''
_io_lock = threading.Lock()  # 串行化文件写入, 避免内容交错损坏


def init(base_dir: str):
    global _data_dir, _ip_file, _session_file, _instance_file, _password_changed_file, SESSION_COOKIE
    _data_dir = os.path.join(base_dir, 'data', 'web')
    os.makedirs(_data_dir, exist_ok=True)
    _ip_file = os.path.join(_data_dir, 'ip.json')
    _session_file = os.path.join(_data_dir, 'sessions.json')
    _instance_file = os.path.join(_data_dir, 'instance_id')
    _password_changed_file = os.path.join(_data_dir, 'password_changed')
    SESSION_COOKIE = f'{_SESSION_COOKIE_PREFIX}_{_load_or_create_instance_id()}'
    _load_ip_data()
    _load_session_data()


# ==================== 密码哈希 ====================

_PWD_HASH_PREFIX = 'sha256:'


def hash_password(plain: str) -> str:
    """生成加盐密码哈希。"""
    salt = os.urandom(16)
    h = hashlib.sha256(salt + plain.encode('utf-8')).hexdigest()
    return _PWD_HASH_PREFIX + base64.b64encode(salt).decode() + ':' + h


def verify_password(plain: str, stored: str) -> bool:
    """恒定时间比较, 兼容明文旧密码"""
    if not isinstance(plain, str) or not isinstance(stored, str):
        return False
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
    return hmac.compare_digest(plain.encode('utf-8'), stored.encode('utf-8'))


def is_hashed(stored: str) -> bool:
    return stored.startswith(_PWD_HASH_PREFIX)


# 初始默认密码：只要密码不是这个值且已设置，就视为已修改，不再做弱密码判定。
DEFAULT_PASSWORD = 'admin'
_LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def _password_changed_marker() -> bool:
    try:
        return os.path.isfile(_password_changed_file)
    except OSError:
        return False


def set_password_changed(changed: bool) -> None:
    """记录密码是否已脱离默认/弱密码；旧版自动哈希无法判断，需要此标记。"""
    try:
        if changed:
            os.makedirs(os.path.dirname(_password_changed_file), exist_ok=True)
            with open(_password_changed_file, 'w', encoding='ascii') as f:
                f.write('1')
        elif os.path.exists(_password_changed_file):
            os.remove(_password_changed_file)
    except OSError:
        pass


def record_password_changed(plain: str) -> None:
    """登录成功或保存密码时调用：只要不是初始默认密码，就视为已修改。"""
    value = str(plain or '')
    set_password_changed(bool(value) and value != DEFAULT_PASSWORD)


def password_is_default(stored: str | None = None) -> bool:
    """判断管理员密码是否仍为初始默认密码。

    明文：与 DEFAULT_PASSWORD 比较（允许随意设置其他密码）；
    哈希：无法反推，依据修改标记判断（旧版自动哈希的默认密码会保持锁定）。
    """
    if stored is None:
        try:
            stored = cfg.get('settings', 'web.admin_password', '')
        except Exception:
            return True
    if not isinstance(stored, str) or not stored:
        return True
    if is_hashed(stored):
        return not _password_changed_marker()
    return stored == DEFAULT_PASSWORD


def is_loopback_host(host: str) -> bool:
    value = str(host or '').strip().strip('[]').lower()
    if not value:
        return True
    return value in _LOOPBACK_HOSTS or value.startswith('127.')


# 新密码强度规则：至少 6 位，必须同时包含数字和字母，且只允许字母和数字。
_PASSWORD_CHARSET = re.compile(r'^[A-Za-z0-9]+$')


def validate_new_password(plain: str) -> str | None:
    """校验新设置的明文密码；返回错误提示，通过则返回 None。

    仅用于"设置新密码"入口；默认密码 admin 与已存在的旧密码不受影响。
    """
    value = str(plain or '')
    if not _PASSWORD_CHARSET.fullmatch(value):
        return '密码只能由数字和字母组成'
    if len(value) < 6:
        return '密码长度至少 6 位'
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return '密码必须同时包含数字和字母'
    return None


def hosts_require_password(hosts) -> bool:
    """监听地址中是否存在必须先修改管理员密码才能使用的非回环地址。"""
    if isinstance(hosts, str):
        hosts = [hosts]
    for host in hosts or []:
        if not is_loopback_host(host):
            return True
    return False


# ==================== JSON 文件读写 ====================


def _read_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return default or {}


def _write_text_sync(path, text):
    """同步写入文本。"""
    with _io_lock:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError:
            pass


def _write_json(path, data):
    """写入 JSON 文件。"""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _write_text_sync, path, text)
    except RuntimeError:
        _write_text_sync(path, text)


def _load_or_create_instance_id() -> str:
    """读取或创建框架实例标识。"""
    try:
        with open(_instance_file, encoding='utf-8') as f:
            instance_id = f.read().strip()
        if instance_id.isascii() and instance_id.isalnum():
            return instance_id
    except OSError:
        pass
    instance_id = uuid.uuid4().hex
    _write_text_sync(_instance_file, instance_id)
    return instance_id


# ==================== IP 地址 ====================


def _load_ip_data():
    global ip_access_data
    ip_access_data = _read_json(_ip_file, {})


def _save_ip_data():
    _write_json(_ip_file, ip_access_data)


def _peer_ip(request: web.Request) -> str:
    if request.transport is None:
        return '127.0.0.1'
    peername = request.transport.get_extra_info('peername')
    return peername[0] if peername else '127.0.0.1'


def _trust_forwarded() -> bool:
    try:
        return bool(cfg.get('settings', 'web.trust_forwarded_headers', False))
    except Exception:
        return False


def get_real_ip(request: web.Request) -> str:
    """获取客户端真实 IP; 转发头可伪造, 默认只信任连接对端, 反代后需开启 web.trust_forwarded_headers"""
    if _trust_forwarded():
        forwarded = request.headers.get('X-Forwarded-For')
        if isinstance(forwarded, str) and forwarded.split(',')[0].strip():
            return forwarded.split(',')[0].strip()
        real_ip = request.headers.get('X-Real-IP')
        if isinstance(real_ip, str) and real_ip:
            return real_ip.strip()
    return _peer_ip(request)


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
            except (TypeError, ValueError):
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


# ==================== 令牌 ====================


def _generate_token() -> str:
    return base64.urlsafe_b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes).decode().rstrip('=')
# ==================== 会话 ====================


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
        except (KeyError, TypeError, ValueError):
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
    """创建会话。"""
    _cleanup_sessions()
    if len(valid_sessions) >= _MAX_SESSIONS:
        oldest = sorted(valid_sessions, key=lambda t: valid_sessions[t]['created'])
        for t in oldest[: len(valid_sessions) - _MAX_SESSIONS + 1]:
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


def get_request_token(request: web.Request) -> str:
    """读取会话凭据。"""
    token = request.cookies.get(SESSION_COOKIE, '')
    return token if isinstance(token, str) else ''


def set_session_cookie(response: web.StreamResponse, request: web.Request, token: str) -> None:
    secure = request.secure or request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip() == 'https'
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite='Strict',
        max_age=_SESSION_DAYS * 86400,
        path='/',
    )


def delete_session_cookie(response: web.StreamResponse) -> None:
    """清除会话凭据。"""
    response.del_cookie(SESSION_COOKIE, path='/')


def validate_token(request: web.Request) -> bool:
    """验证会话凭据。"""
    _cleanup_sessions()
    token = get_request_token(request)
    if not token or token not in valid_sessions:
        return False
    info = valid_sessions[token]
    if datetime.now() >= info['expires']:
        valid_sessions.pop(token, None)
        _save_session_data()
        return False
    return True


def revoke_session(request: web.Request) -> None:
    token = get_request_token(request)
    if token:
        valid_sessions.pop(token, None)
        _save_session_data()


def is_same_origin(request: web.Request) -> bool:
    """拒绝跨站写请求。"""
    origin = request.headers.get('Origin')
    if not origin:
        return True
    return hmac.compare_digest(urlsplit(origin).netloc.lower(), request.host.lower())


def authorize_request(request: web.Request):
    if not validate_token(request):
        return error('未登录或会话已过期', status=401)
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not is_same_origin(request):
        return error('跨站请求已拒绝', status=403)
    return None


# ==================== 中间件 ====================


def require_auth(handler):
    """要求请求已登录。"""

    @wraps(handler)
    async def wrapped(request):
        denied = authorize_request(request)
        if denied is not None:
            return denied
        return await handler(request)

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
