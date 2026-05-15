"""配置文件管理 — YAML 配置读写(保留注释 + 按钮序列化)"""

import os
import re
import yaml

from aiohttp import web

_base_dir = ''


def set_context(base_dir: str):
    global _base_dir
    _base_dir = base_dir


def _config_dir():
    return os.path.join(_base_dir, 'config')


# ===== YAML 序列化工具 =====

def _yaml_scalar(v):
    """标量值序列化"""
    if v is None: return 'null'
    if isinstance(v, bool): return 'true' if v else 'false'
    if isinstance(v, (int, float)): return str(v)
    if not isinstance(v, str): return str(v)
    if not v: return "''"
    if v == '|': return "'|'"
    needs_quote = any(c in v for c in ':#[]{}|>&*!?,\'\"') or v[0] in (' ', '-') or v[-1] == ' '
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


def _serialize_templates(data):
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

    sections = []
    current_comments = []
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

    output = []
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
    cdir = _config_dir()
    result = {}
    for name in ('bot', 'settings', 'templates'):
        path = os.path.join(cdir, f'{name}.yaml')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                result[name] = f.read()
        else:
            result[name] = ''
    return web.json_response({'success': True, **result})


async def handle_save_config(request: web.Request):
    try:
        body = await request.json()
        file_name = body.get('file', '')
        content = body.get('content', '')
        if file_name not in ('bot', 'settings', 'templates'):
            return web.json_response({'success': False, 'error': '无效的配置文件名'}, status=400)
        if not content:
            return web.json_response({'success': False, 'error': '内容不能为空'}, status=400)

        cdir = _config_dir()
        path = os.path.join(cdir, f'{file_name}.yaml')

        # 备份
        if os.path.exists(path):
            bak = path + '.bak'
            with open(path, 'r', encoding='utf-8') as f:
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
        return web.json_response({'success': True, 'message': '配置已保存，部分更改需重启生效'})
    except Exception as e:
        return web.json_response({'success': False, 'error': str(e)}, status=500)
