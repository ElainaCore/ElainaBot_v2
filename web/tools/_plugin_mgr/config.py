"""配置文件读写 (YAML 注释保留 + JSON) + 插件机器人绑定"""

import contextlib
import json
import os
import re
import shutil

import yaml
from aiohttp import web

from web.tools._plugin_mgr.shared import (
    detect_config_format,
    get_mm,
    get_pm,
    list_config_files,
    log,
    modules_dir,
    plugins_dir,
    validate_config_path,
)

# ==================== YAML 序列化 (保留注释) ====================


def _ys(v):
    """YAML 标量序列化, 必要时加引号"""
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if not isinstance(v, str):
        return str(v)
    if not v:
        return "''"
    return f"'{v}'" if any(c in v for c in ':#[]{}|>&*!?,') or v[0] == ' ' or v[-1] == ' ' else v


def _rebuild_yaml(data, cmt, pre='', ind=0):
    """以原始注释字典重建 YAML 文本 (保留行尾/上方注释)"""
    if not isinstance(data, dict):
        return []
    out, pad = [], '  ' * ind
    for k, v in data.items():
        p = f'{pre}.{k}' if pre else k
        c = cmt.get(p, '')
        if isinstance(v, dict):
            if c:
                out.append(f'{pad}# {c}')
            out.append(f'{pad}{k}:')
            out.extend(_rebuild_yaml(v, cmt, p, ind + 1))
        elif isinstance(v, list):
            if c:
                out.append(f'{pad}# {c}')
            if not v:
                out.append(f'{pad}{k}: []')
            else:
                out.append(f'{pad}{k}:')
                cp = '  ' * (ind + 1)
                for it in v:
                    if isinstance(it, dict):
                        for i, (ik, iv) in enumerate(it.items()):
                            out.append(f'{cp}{"- " if not i else "  "}{ik}: {_ys(iv)}')
                    else:
                        out.append(f'{cp}- {_ys(it)}')
        else:
            s = _ys(v)
            out.append(f'{pad}{k}: {s}  # {c}' if c else f'{pad}{k}: {s}')
    return out


def _extract_yaml_comments(raw_text):
    """从 YAML 原文提取注释 → {key_path: comment} 扁平 dict"""
    comments = {}
    pending_comment = None
    path_stack = []  # [(indent, key)]

    for line in raw_text.split('\n'):
        stripped = line.rstrip()
        if not stripped:
            pending_comment = None
            continue

        m_comment = re.match(r'^(\s*)#\s*(.*)', stripped)
        if m_comment:
            pending_comment = m_comment.group(2).strip()
            continue

        m_kv = re.match(r'^(\s*)([A-Za-z_][\w]*)\s*:', stripped)
        if not m_kv:
            pending_comment = None
            continue

        indent = len(m_kv.group(1))
        key = m_kv.group(2)
        while path_stack and path_stack[-1][0] >= indent:
            path_stack.pop()

        inline = ''
        m_inline = re.search(r'#\s*(.+)$', stripped)
        if m_inline:
            before_hash = stripped[: m_inline.start()].rstrip()
            if ':' in before_hash:
                inline = m_inline.group(1).strip()

        comment = inline or pending_comment or ''
        if comment:
            full_path = '.'.join([p[1] for p in path_stack] + [key])
            comments[full_path] = comment

        path_stack.append((indent, key))
        pending_comment = None
    return comments


# ==================== 配置读取/保存 ====================


async def handle_read_config(request: web.Request):
    body = await request.json()
    if not body.get('path', ''):
        return web.json_response({'success': False, 'message': '缺少路径'}, status=400)
    abs_path, err = validate_config_path(body['path'])
    if err:
        return err
    if not os.path.isfile(abs_path):
        return web.json_response({'success': False, 'message': '文件不存在'}, status=404)

    ext = os.path.splitext(abs_path)[1].lower()
    fmt = detect_config_format(ext)
    try:
        with open(abs_path, encoding='utf-8') as f:
            raw = f.read()
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    parsed, comments = None, {}
    if fmt == 'yaml':
        with contextlib.suppress(Exception):
            parsed = yaml.safe_load(raw)
            comments = _extract_yaml_comments(raw)
    elif fmt == 'json':
        with contextlib.suppress(Exception):
            parsed = json.loads(raw)

    return web.json_response(
        {
            'success': True,
            'format': fmt,
            'raw': raw,
            'parsed': parsed,
            'comments': comments,
            'filename': os.path.basename(abs_path),
        }
    )


async def handle_save_config(request: web.Request):
    body = await request.json()
    content = body.get('content')
    fmt = body.get('format', 'raw')
    if not body.get('path', '') or content is None:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    abs_path, err = validate_config_path(body['path'])
    if err:
        return err

    if fmt == 'yaml':
        try:
            parsed = yaml.safe_load(content)
        except Exception as e:
            return web.json_response({'success': False, 'message': f'YAML 格式错误: {e}'}, status=400)
        if isinstance(parsed, dict) and os.path.isfile(abs_path):
            try:
                with open(abs_path, encoding='utf-8') as f:
                    old_comments = _extract_yaml_comments(f.read())
                if old_comments:
                    content = '\n'.join(_rebuild_yaml(parsed, old_comments)) + '\n'
            except Exception as e:
                log.debug(f'YAML 注释保留失败: {e}')
    elif fmt == 'json':
        try:
            data = json.loads(content)
            content = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as e:
            return web.json_response({'success': False, 'message': f'JSON 格式错误: {e}'}, status=400)

    if os.path.isfile(abs_path):
        shutil.copy2(abs_path, abs_path + '.backup')

    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    # 自动重载所属模块
    reloaded = ''
    mdir = os.path.abspath(modules_dir())
    if abs_path.startswith(mdir):
        mm = get_mm()
        if mm:
            rel = os.path.relpath(abs_path, mdir)
            mod_name = rel.split(os.sep)[0]
            if mm.is_enabled(mod_name):
                try:
                    await mm.reload(mod_name)
                    reloaded = mod_name
                except Exception as e:
                    log.warning(f'模块 {mod_name} 重载失败: {e}')

    msg = f'配置已保存, 模块 {reloaded} 已重载' if reloaded else '配置已保存'
    return web.json_response({'success': True, 'message': msg})


# ==================== 插件 data/ 配置文件列表 ====================


async def handle_plugin_config_files(request: web.Request):
    body = await request.json()
    plugin_name = body.get('name', '')
    if not plugin_name:
        return web.json_response({'success': False, 'message': '缺少插件名'}, status=400)
    plugin_dir = os.path.join(plugins_dir(), plugin_name)
    files = list_config_files(os.path.join(plugin_dir, 'data'))
    return web.json_response({'success': True, 'config_files': files})


# ==================== 插件机器人绑定 ====================


async def handle_get_plugin_bots(request: web.Request):
    pm = get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    return web.json_response({'success': True, 'plugin_bots': pm.get_plugin_bots()})


async def handle_set_plugin_bots(request: web.Request):
    """body: {"plugin_bots": {"插件名或插件名/文件名": ["appid1", ...]}} 空列表=不限制"""
    pm = get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    body = await request.json()
    data = body.get('plugin_bots')
    if not isinstance(data, dict):
        return web.json_response({'success': False, 'message': 'plugin_bots 必须为 dict'}, status=400)
    pm.set_plugin_bots(data)
    return web.json_response({'success': True, 'message': '插件机器人绑定已保存'})
