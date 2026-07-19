"""QQ 开放平台 HTTP 公共常量与工具"""

import gzip

import aiohttp

_GZIP_MAGIC = b'\x1f\x8b\x08'
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_BASE_HEADERS = {
    'Content-Type': 'application/json',
    'User-Agent': ('Mozilla/5.0 (Linux; U; Android 14; zh-cn) AppleWebKit/537.36 Chrome/109.0.5414.118 Mobile Safari/537.36'),
}
_QQ_HEADERS = {
    'Host': 'q.qq.com',
    'Origin': 'https://q.qq.com',
    'Referer': 'https://q.qq.com/',
}
_BOT_HEADERS = {
    'Host': 'bot.q.qq.com',
    'Origin': 'https://q.qq.com',
    'Referer': 'https://q.qq.com/',
}


def decode_body(content: bytes) -> str:
    try:
        return gzip.decompress(content).decode('utf-8') if content[:3] == _GZIP_MAGIC else content.decode('utf-8')
    except Exception:
        return content.decode('utf-8', errors='ignore')


def qq_bkn(skey: str) -> int:
    """腾讯 bkn/g_tk 计算 (hash33 of skey, 初始值 5381)"""
    value = 5381
    for ch in skey or '':
        value += (value << 5) + ord(ch)
    return value & 2147483647
