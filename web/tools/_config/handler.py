"""配置文件管理 — YAML 配置读写(保留注释 + 按钮序列化) / 扫码快捷绑定机器人"""

import base64
import contextlib
import os
import re
import secrets
import time
from urllib.parse import quote

import aiohttp
import yaml
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from web.response import error, ok

_base_dir = ''


def set_context(base_dir: str):
    global _base_dir
    _base_dir = base_dir


def _config_dir():
    return os.path.join(_base_dir, 'config')


# ===== YAML 序列化工具 =====


def _yaml_scalar(v):
    """标量值序列化"""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int | float):
        return str(v)
    if not isinstance(v, str):
        return str(v)
    if not v:
        return "''"
    if v == '|':
        return "'|'"
    needs_quote = any(c in v for c in ':#[]{}|>&*!?,\'"') or v[0] in (' ', '-') or v[-1] == ' '
    return f'"{v}"' if needs_quote else v


def _serialize_buttons(buttons):
    """按钮序列化为单行 flow: [[{k: v,...},{k: v,...}],[{k: v,...}]]"""
    rows = []
    for row in buttons:
        if not isinstance(row, list):
            continue
        items = []
        for btn in row:
            if not isinstance(btn, dict):
                continue
            parts = [f'{k}: {_yaml_scalar(v)}' for k, v in btn.items()]
            items.append('{' + ', '.join(parts) + '}')
        rows.append('[' + ','.join(items) + ']')
    return '[' + ','.join(rows) + ']'


def _serialize_value(key, value, indent=1):
    """序列化单个键值对"""
    pad = '  ' * indent
    if key == 'buttons' and isinstance(value, list):
        return [f'{pad}{key}: {_serialize_buttons(value)}']
    if isinstance(value, str) and '\n' in value:
        lines = [f'{pad}{key}: |']
        for ln in value.rstrip('\n').split('\n'):
            lines.append(f'{pad}  {ln}' if ln.strip() else f'{pad}')
        return lines
    return [f'{pad}{key}: {_yaml_scalar(value)}']


def _serialize_template(key, value):
    """序列化单个模板条目"""
    if isinstance(value, dict):
        lines = [f'{key}:']
        for k, v in value.items():
            lines.extend(_serialize_value(k, v))
        return lines
    if isinstance(value, list):
        lines = [f'{key}:']
        for item in value:
            if not isinstance(item, dict):
                continue
            first = True
            for ik, iv in item.items():
                prefix = '  - ' if first else '    '
                first = False
                if ik == 'buttons' and isinstance(iv, list):
                    lines.append(f'{prefix}{ik}: {_serialize_buttons(iv)}')
                elif isinstance(iv, str) and '\n' in iv:
                    lines.append(f'{prefix}{ik}: |')
                    for ln in iv.rstrip('\n').split('\n'):
                        lines.append(f'      {ln}' if ln.strip() else '')
                else:
                    lines.append(f'{prefix}{ik}: {_yaml_scalar(iv)}')
        return lines
    if isinstance(value, str) and '\n' in value:
        lines = [f'{key}: |']
        for ln in value.rstrip('\n').split('\n'):
            lines.append(f'  {ln}' if ln.strip() else '')
        return lines
    return [f'{key}: {_yaml_scalar(value)}']


def _serialize_templates(data: dict) -> str:
    """将模板数据字典序列化为 YAML 文本"""
    lines = []
    for key, value in data.items():
        lines.extend(_serialize_template(key, value))
        lines.append('')
    return '\n'.join(lines)


# ===== 注释保留合并 =====

_TOP_KEY_RE = re.compile(r'^([A-Za-z_][\w]*)\s*:')


def _merge_preserving_comments(original_text: str, new_data: dict) -> str:
    """将新数据合并到原始文件，保留注释和空行结构"""
    if not original_text.strip():
        return _serialize_templates(new_data)

    sections: list[tuple[str, str | None, list[str], list[str]]] = []
    current_comments: list[str] = []
    current_key = None
    current_lines = []

    for line in original_text.split('\n'):
        m = _TOP_KEY_RE.match(line)
        if m:
            if current_key is not None:
                sections.append(('key', current_key, current_comments, current_lines))
                current_comments = []
            elif current_comments or (not sections):
                sections.append(('header', None, current_comments, []))
                current_comments = []
            current_key = m.group(1)
            current_lines = [line]
        elif current_key is not None:
            if line == '' or line.startswith('  ') or line.startswith('\t'):
                current_lines.append(line)
            elif line.startswith('#'):
                sections.append(('key', current_key, [], current_lines))
                current_key = None
                current_lines = []
                current_comments = [line]
            else:
                current_lines.append(line)
        else:
            current_comments.append(line)

    if current_key is not None:
        sections.append(('key', current_key, current_comments, current_lines))
    elif current_comments:
        sections.append(('tail', None, current_comments, []))

    output: list[str] = []
    used_keys = set()

    for sec_type, key, comments, lines in sections:
        if sec_type in ('header', 'tail'):
            output.extend(comments)
            continue

        output.extend(comments)
        used_keys.add(key)

        if key in new_data:
            output.extend(_serialize_template(key, new_data[key]))
        else:
            output.extend(lines)

        trailing_blank = lines and lines[-1] == ''
        if trailing_blank and (not output or output[-1] != ''):
            output.append('')

    for key, value in new_data.items():
        if key not in used_keys:
            output.append('')
            block = _serialize_templates({key: value}).rstrip('\n')
            output.append(block)

    result = '\n'.join(output)
    if not result.endswith('\n'):
        result += '\n'
    return result


# ===== 路由处理 =====


async def handle_get_config(request: web.Request):
    """返回配置文件的原始内容（含环境变量占位符已解析）"""
    from core.base.config import cfg

    cdir = _config_dir()
    result = {}
    for name in ('bot', 'settings', 'templates'):
        path = os.path.join(cdir, f'{name}.yaml')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                raw_text = f.read()
            # 解析 ${VAR_NAME:default} 环境变量占位符，避免前端显示原始占位符
            result[name] = cfg._resolve_env_vars(raw_text)
        else:
            result[name] = ''
    return ok(result)


async def handle_save_config(request: web.Request):
    try:
        body = await request.json()
        file_name = body.get('file', '')
        content = body.get('content', '')
        if file_name not in ('bot', 'settings', 'templates'):
            return error('无效的配置文件名', status=400)
        if not content:
            return error('内容不能为空', status=400)

        cdir = _config_dir()
        path = os.path.join(cdir, f'{file_name}.yaml')

        # 备份
        if os.path.exists(path):
            bak = path + '.bak'
            with open(path, encoding='utf-8') as f:
                original_text = f.read()
            with open(bak, 'w', encoding='utf-8') as fb:
                fb.write(original_text)

            # templates.yaml: 保留注释 + 正确序列化按钮
            if file_name == 'templates':
                try:
                    new_data = yaml.safe_load(content)
                    if isinstance(new_data, dict):
                        content = _merge_preserving_comments(original_text, new_data)
                except Exception:
                    pass

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        # bot 配置保存后立即触发热重载, 新增/移除的机器人无需重启即可连接
        if file_name == 'bot':
            from core.base.config import cfg

            mtime = os.path.getmtime(path)
            cfg._do_reload('bot', path, mtime)

        return ok(message='配置已保存')
    except Exception as e:
        return error(str(e), status=500)


# ===== 扫码快捷绑定机器人 =====

_BIND_URL = 'https://q.qq.com/qqbot/openclaw/connect.html?task_id={task_id}&source=elainabot&_wv=2'
_BIND_TASK_TTL = 600
_bind_tasks: dict = {}  # task_id -> (创建时间戳, 解密 key)


def _get_bind_api():
    from web.tools._bot.api import get_bot_api

    return get_bot_api()


def _prune_bind_tasks():
    now = time.time()
    for tid in [t for t, (ts, _) in _bind_tasks.items() if now - ts > _BIND_TASK_TTL]:
        _bind_tasks.pop(tid, None)


def _decrypt_bind_secret(encrypted_b64: str, key_b64: str) -> str:
    """AES-256-GCM 解密: 密文 = nonce(12) + 正文 + tag(16)"""
    raw = base64.b64decode(encrypted_b64)
    key = base64.b64decode(key_b64)
    nonce, body = raw[:12], raw[12:]
    decrypted: bytes = AESGCM(key).decrypt(nonce, body, None)
    return decrypted.decode('utf-8')


async def _fetch_bot_profile(appid: str, secret: str) -> dict:
    """用绑定到的凭据请求 /users/@me, 提取机器人昵称和 QQ 号"""
    profile = {'name': '', 'robot_qq': ''}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            'https://bots.qq.com/app/getAppAccessToken',
            json={'appId': appid, 'clientSecret': secret},
        ) as r:
            token = (await r.json(content_type=None)).get('access_token', '')
        if not token:
            return profile
        async with session.get(
            'https://api.sgroup.qq.com/users/@me',
            headers={'Authorization': f'QQBot {token}'},
        ) as r:
            data = await r.json(content_type=None)
    if not isinstance(data, dict):
        return profile
    profile['name'] = str(data.get('username', '') or '')
    for field in ('avatar', 'share_url'):
        m = re.search(r'[?&](?:nk|dst_uin|robot_uin|uin)=(\d+)', str(data.get(field, '') or ''))
        if m:
            profile['robot_qq'] = m.group(1)
            break
    return profile


def _apply_bound_bot(appid: str, secret: str, robot_qq: str = '') -> bool:
    """将绑定结果写入 bot.yaml 并热重载, 返回是否为新增机器人"""
    path = os.path.join(_config_dir(), 'bot.yaml')
    data: dict[str, object] = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            original = f.read()
        with open(path + '.bak', 'w', encoding='utf-8') as fb:
            fb.write(original)
        data = yaml.safe_load(original) or {}
    bots = data.get('bots')
    if not isinstance(bots, list):
        bots = []
    data['bots'] = bots
    created = True
    for bot in bots:
        if isinstance(bot, dict) and str(bot.get('appid') or '').strip() == appid:
            bot['secret'] = secret
            bot['enabled'] = True
            if robot_qq and not str(bot.get('robot_qq') or '').strip():
                bot['robot_qq'] = robot_qq
            if not isinstance(bot.get('websocket'), dict):
                bot['websocket'] = {'enabled': True}
            created = False
            break
    if created:
        bots.append(
            {
                'enabled': True,
                'appid': appid,
                'secret': secret,
                'robot_qq': robot_qq,
                'owner_ids': [],
                'websocket': {'enabled': True},
            }
        )
    content = '# ===== 机器人配置 =====\n# 支持多机器人，每个机器人独立配置\n# 修改后自动热加载，无需重启\n\n' + yaml.dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

    from core.base.config import cfg

    cfg._do_reload('bot', path, os.path.getmtime(path))
    return created


async def handle_qr_bind_start(request: web.Request):
    """创建扫码绑定任务, 返回二维码内容 URL"""
    api = _get_bind_api()
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    resp = await api.create_bind_task(key)
    task_id = (resp.get('data') or {}).get('task_id') if resp.get('retcode') == 0 else None
    if not task_id:
        return error(resp.get('msg') or '创建绑定任务失败')
    _prune_bind_tasks()
    task_id = str(task_id)
    _bind_tasks[task_id] = (time.time(), key)
    return ok({'task_id': task_id, 'url': _BIND_URL.format(task_id=quote(task_id))})


async def handle_qr_bind_poll(request: web.Request):
    """轮询绑定结果; 扫码完成后解密 Secret 并自动写入 bot.yaml"""
    body = await request.json()
    task_id = str(body.get('task_id') or '')
    entry = _bind_tasks.get(task_id)
    if not entry:
        return error('绑定任务不存在或已过期', status=200, data={'status': 'not_found'})
    api = _get_bind_api()
    resp = await api.poll_bind_result(task_id)
    if resp.get('retcode') != 0:
        return error(resp.get('msg') or '查询绑定结果失败', status=200, data={'status': 'error'})
    d = resp.get('data') or {}
    status = d.get('status')
    if status == 2:  # COMPLETED
        _bind_tasks.pop(task_id, None)
        appid = str(d.get('bot_appid') or '')
        encrypted = d.get('bot_encrypt_secret') or ''
        if not appid or not encrypted:
            return error('绑定结果缺少 AppID/Secret', status=200, data={'status': 'error'})
        try:
            secret = _decrypt_bind_secret(encrypted, entry[1])
        except Exception as e:
            return error(f'解密 Secret 失败: {e}', status=200, data={'status': 'error'})
        robot_qq = ''
        with contextlib.suppress(Exception):
            robot_qq = (await _fetch_bot_profile(appid, secret)).get('robot_qq', '')
        try:
            created = _apply_bound_bot(appid, secret, robot_qq)
        except Exception as e:
            return error(f'写入配置失败: {e}', status=200, data={'status': 'error'})
        return ok({'status': 'completed', 'appid': appid, 'created': created})
    if status == 3:  # EXPIRED
        _bind_tasks.pop(task_id, None)
        return ok({'status': 'expired'})
    return ok({'status': 'waiting'})
