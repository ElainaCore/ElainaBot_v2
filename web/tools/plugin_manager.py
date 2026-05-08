"""插件管理 — 扫描/启禁/读写/创建/上传"""

import os
import re
import ast
import json
import logging
import traceback
import importlib.util
from datetime import datetime

import yaml

from aiohttp import web

log = logging.getLogger('ElainaBot.web.plugin_mgr')

_base_dir = ''
_bot_manager = None


def set_context(base_dir: str, bot_manager=None):
    global _base_dir, _bot_manager
    _base_dir = base_dir
    if bot_manager is not None:
        _bot_manager = bot_manager


def _plugins_dir():
    return os.path.join(_base_dir, 'plugins')


def _get_pm():
    """获取 PluginManager 实例 (无则返回 None)"""
    if not _bot_manager:
        return None
    return getattr(_bot_manager, '_plugin_manager', None) or getattr(_bot_manager, 'plugin_manager', None)


def _validate_path(path, plugins_dir):
    abs_p = os.path.abspath(path)
    return abs_p.startswith(os.path.abspath(plugins_dir)), abs_p


# ==================== 扫描 ====================

def _get_plugin_info():
    """从 PluginManager 获取已加载插件的注册命令和描述 (直接调用 PM 方法)"""
    pm = _get_pm()
    if not pm or not hasattr(pm, 'get_web_plugin_info'):
        return {}
    try:
        return pm.get_web_plugin_info()
    except Exception as e:
        log.error(f"获取插件信息失败: {e}")
        return {}


_ENTRY_CANDIDATES = ('index.py', 'app.py', 'main.py')


def _find_entry(plugin_dir):
    """查找插件入口文件 (与 PluginManager._find_large_entry 一致)"""
    for name in _ENTRY_CANDIDATES:
        path = os.path.join(plugin_dir, name)
        if os.path.isfile(path):
            return path
    return None


def _find_entry_or_ban(plugin_dir):
    """查找入口文件或其 .ban 版本"""
    for name in _ENTRY_CANDIDATES:
        path = os.path.join(plugin_dir, name)
        if os.path.isfile(path):
            return path, True
        ban = path + '.ban'
        if os.path.isfile(ban):
            return ban, False
    return None, None


def _scan_plugins():
    plugins_dir = _plugins_dir()
    result = []
    if not os.path.isdir(plugins_dir):
        return result

    plugin_info_map = _get_plugin_info()

    for dir_name in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, dir_name)
        if not os.path.isdir(plugin_dir) or dir_name.startswith(('_', '.')):
            continue
        is_system = dir_name == 'system'

        entry_path, enabled = _find_entry_or_ban(plugin_dir)
        if not entry_path:
            # 小型插件 (无入口文件): 检查是否有 .py 文件
            py_files = [f for f in os.listdir(plugin_dir)
                        if f.endswith('.py') and not f.startswith('_')]
            if not py_files:
                continue
            entry_path = os.path.join(plugin_dir, py_files[0])
            enabled = True

        pinfo = plugin_info_map.get(dir_name, {})
        mtime = datetime.fromtimestamp(os.path.getmtime(entry_path)).strftime('%Y-%m-%d %H:%M:%S')

        # 判断类型
        is_large = _find_entry(plugin_dir) is not None

        result.append({
            'name': dir_name,
            'status': 'loaded' if enabled else 'disabled',
            'path': entry_path.replace('\\', '/'),
            'directory': dir_name,
            'is_system': is_system,
            'is_large': is_large,
            'last_modified': mtime,
            'enabled': enabled,
            'commands': pinfo.get('commands', []),
            'description': pinfo.get('description', ''),
            'meta': pinfo.get('meta', {}),
        })

    result.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1))
    return result


async def handle_scan_plugins(request: web.Request):
    return web.json_response({'success': True, 'plugins': _scan_plugins()})


def _scan_py_files(dir_path, prefix=''):
    """扫描目录中的 .py / .py.ban 文件, prefix 用于子目录显示"""
    files = []
    for fname in sorted(os.listdir(dir_path)):
        if fname.startswith('_'):
            continue
        if fname.endswith('.py'):
            enabled = True
        elif fname.endswith('.py.ban'):
            enabled = False
        else:
            continue
        fpath = os.path.join(dir_path, fname)
        if not os.path.isfile(fpath):
            continue
        display = f'{prefix}{fname}' if prefix else fname
        if not enabled and display.endswith('.ban'):
            display = display[:-4]
        stat = os.stat(fpath)
        files.append({
            'name': display, 'path': fpath.replace('\\', '/'),
            'enabled': enabled, 'size': stat.st_size,
            'last_modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return files


def _get_plugin_bots_map():
    """从 PluginManager 获取插件机器人绑定配置"""
    pm = _get_pm()
    if not pm or not hasattr(pm, 'get_plugin_bots'):
        return {}
    try:
        return pm.get_plugin_bots()
    except Exception:
        return {}


def _scan_plugin_dirs():
    """按目录分组扫描所有 .py / .py.ban 文件"""
    plugins_dir = _plugins_dir()
    dirs = []
    if not os.path.isdir(plugins_dir):
        return dirs

    plugin_info_map = _get_plugin_info()
    bots_map = _get_plugin_bots_map()

    for dir_name in sorted(os.listdir(plugins_dir)):
        dir_path = os.path.join(plugins_dir, dir_name)
        if not os.path.isdir(dir_path) or dir_name.startswith(('.', '__')):
            continue

        is_system = dir_name == 'system'
        pinfo = plugin_info_map.get(dir_name, {})

        # 扫描顶层 .py 文件
        files = _scan_py_files(dir_path)

        # 为每个文件注入机器人绑定信息
        for f in files:
            fname = f['name']
            if fname.endswith('.py'):
                fname = fname[:-3]
            file_key = f"{dir_name}/{fname}"
            f['allowed_bots'] = bots_map.get(file_key, [])

        # 判断目录整体状态: 有入口文件 → 大型, 否则 → 小型
        has_entry = any(f['name'] in _ENTRY_CANDIDATES for f in files)

        # 大型插件: 额外扫描 app/ 子目录
        if has_entry:
            app_dir = os.path.join(dir_path, 'app')
            if os.path.isdir(app_dir):
                files.extend(_scan_py_files(app_dir, prefix='app/'))

        if not files:
            continue

        entry_enabled = any(
            f['name'] in _ENTRY_CANDIDATES and f['enabled'] for f in files)
        # 小型插件只要有任何 .py 文件就算启用
        is_enabled = entry_enabled if has_entry else any(f['enabled'] for f in files)

        dirs.append({
            'directory': dir_name,
            'is_system': is_system,
            'enabled': is_enabled,
            'is_large': has_entry,
            'files': files,
            'allowed_bots': bots_map.get(dir_name, []),
            'commands': pinfo.get('commands', []),
            'description': pinfo.get('description', ''),
            'meta': pinfo.get('meta', {}),
        })

    return dirs


async def handle_scan_plugin_dirs(request: web.Request):
    return web.json_response({'success': True, 'dirs': _scan_plugin_dirs()})


# ==================== 启用/禁用 ====================

async def handle_toggle_plugin(request: web.Request):
    body = await request.json()
    plugin_path = body.get('path', '')
    action = body.get('action', '')
    if not plugin_path or action not in ('enable', 'disable'):
        return web.json_response({'success': False, 'message': '参数错误'}, status=400)

    plugin_path = os.path.normpath(plugin_path)
    plugins_dir = _plugins_dir()
    valid, abs_path = _validate_path(plugin_path, plugins_dir)
    if not valid:
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)

    # 统一 enable/disable 逻辑
    is_disable = action == 'disable'
    expect_ext = '.py' if is_disable else '.py.ban'
    if not abs_path.endswith(expect_ext):
        return web.json_response({'success': False, 'message': f'只能操作 {expect_ext}'}, status=400)
    new_abs = (abs_path + '.ban') if is_disable else abs_path[:-4]
    if os.path.exists(new_abs):
        return web.json_response({'success': False, 'message': '目标文件已存在'}, status=409)
    os.rename(abs_path, new_abs)
    await _try_reload_plugin(new_abs if not is_disable else abs_path, plugins_dir)
    label = '已禁用' if is_disable else '已启用'
    return web.json_response({'success': True, 'message': f'插件{label}', 'new_path': new_abs.replace('\\', '/')})


async def _try_reload_plugin(file_path, plugins_dir):
    """根据文件路径推导插件名并触发运行时热重载"""
    pm = _get_pm()
    if not pm:
        return
    # 从文件路径推导插件目录名: plugins/<name>/xxx.py → name
    try:
        rel = os.path.relpath(file_path, plugins_dir)
        plugin_name = rel.split(os.sep)[0]
        if plugin_name and plugin_name in pm.plugins:
            await pm.reload(plugin_name)
            log.info(f"插件文件变更触发热重载: {plugin_name}")
    except Exception as e:
        log.warning(f"自动热重载失败: {e}")


# ==================== 热重载 ====================

async def handle_reload_plugin(request: web.Request):
    body = await request.json()
    plugin_name = body.get('name', '')
    if not plugin_name:
        return web.json_response({'success': False, 'message': '缺少插件名'}, status=400)
    pm = _get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    try:
        result = await pm.reload(plugin_name)
        if result:
            info = pm.plugins.get(plugin_name)
            count = len(info.handlers) if info else 0
            return web.json_response({'success': True, 'message': f'重载完成: {count} 个处理器',
                                      'handler_count': count})
        else:
            return web.json_response({'success': False, 'message': '重载失败 (大型插件不支持热重载)'})
    except Exception as e:
        log.error(f"热重载 [{plugin_name}] 失败: {e}")
        return web.json_response({'success': False, 'message': f'重载异常: {e}'}, status=500)


# ==================== 读取/保存 ====================

async def handle_read_plugin(request: web.Request):
    body = await request.json()
    plugin_path = os.path.normpath(body.get('path', ''))
    if not plugin_path:
        return web.json_response({'success': False, 'message': '缺少路径'}, status=400)
    valid, abs_path = _validate_path(plugin_path, _plugins_dir())
    if not valid or not os.path.isfile(abs_path):
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)
    with open(abs_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return web.json_response({'success': True, 'content': content,
                              'path': plugin_path.replace('\\', '/'),
                              'filename': os.path.basename(plugin_path)})


async def handle_save_plugin(request: web.Request):
    body = await request.json()
    plugin_path = os.path.normpath(body.get('path', ''))
    content = body.get('content')
    if not plugin_path or content is None:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    valid, abs_path = _validate_path(plugin_path, _plugins_dir())
    if not valid:
        return web.json_response({'success': False, 'message': '无效路径'}, status=403)
    if os.path.exists(abs_path):
        import shutil
        shutil.copy2(abs_path, abs_path + '.backup')
    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return web.json_response({'success': True, 'message': '插件已保存'})


# ==================== 创建 ====================

_PLUGIN_TEMPLATE = '''from core.plugin.decorators import handler


@handler(r"^指令$", name="示例命令", desc="示例插件")
async def handle_command(event, match):
    await event.reply("Hello, World!")
'''


async def handle_create_plugin(request: web.Request):
    body = await request.json()
    directory = body.get('directory', '')
    filename = body.get('filename', '')
    if not directory or not filename:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    if not filename.endswith('.py'):
        filename += '.py'
    plugins_dir = _plugins_dir()
    target_dir = os.path.join(plugins_dir, directory)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(plugins_dir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    plugin_path = os.path.join(target_dir, filename)
    if os.path.exists(plugin_path):
        return web.json_response({'success': False, 'message': '文件已存在'}, status=409)
    os.makedirs(target_dir, exist_ok=True)
    with open(plugin_path, 'w', encoding='utf-8') as f:
        f.write(_PLUGIN_TEMPLATE)
    return web.json_response({'success': True, 'message': '插件已创建',
                              'path': plugin_path.replace('\\', '/')})


async def handle_create_folder(request: web.Request):
    body = await request.json()
    folder_name = body.get('folder_name', '')
    parent_dir = body.get('parent_dir', '')
    if not folder_name:
        return web.json_response({'success': False, 'message': '缺少文件夹名'}, status=400)
    plugins_dir = _plugins_dir()
    target = os.path.join(plugins_dir, parent_dir, folder_name) if parent_dir else os.path.join(plugins_dir, folder_name)
    if not os.path.abspath(target).startswith(os.path.abspath(plugins_dir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    if os.path.exists(target):
        return web.json_response({'success': False, 'message': '文件夹已存在'}, status=409)
    os.makedirs(target, exist_ok=True)
    return web.json_response({'success': True, 'message': '文件夹已创建'})


async def handle_get_folders(request: web.Request):
    plugins_dir = _plugins_dir()
    folders = []
    if os.path.isdir(plugins_dir):
        for item in sorted(os.listdir(plugins_dir)):
            if os.path.isdir(os.path.join(plugins_dir, item)) and not item.startswith(('.', '__')):
                folders.append({'name': item, 'path': item})
    return web.json_response({'success': True, 'folders': folders})


# ==================== 上传 ====================

async def handle_upload_plugin(request: web.Request):
    reader = await request.multipart()
    file_field = None
    directory = 'alone'
    async for field in reader:
        if field.name == 'file':
            file_field = field
        elif field.name == 'directory':
            directory = (await field.text()).strip() or 'alone'

    if not file_field or not file_field.filename:
        return web.json_response({'success': False, 'message': '没有文件'}, status=400)
    filename = file_field.filename
    if not filename.endswith('.py'):
        return web.json_response({'success': False, 'message': '只能上传 .py'}, status=400)
    safe_name = re.sub(r'[^\w\u4e00-\u9fa5\-\.]', '_', filename)

    plugins_dir = _plugins_dir()
    target_dir = os.path.join(plugins_dir, directory)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(plugins_dir)):
        return web.json_response({'success': False, 'message': '无效目录'}, status=403)
    os.makedirs(target_dir, exist_ok=True)

    dest = os.path.join(target_dir, safe_name)
    if os.path.exists(dest):
        base = safe_name[:-3]
        c = 1
        while os.path.exists(dest):
            dest = os.path.join(target_dir, f"{base}_{c}.py")
            c += 1

    content = await file_field.read()
    with open(dest, 'wb') as f:
        f.write(content)
    return web.json_response({'success': True, 'message': f'上传成功: {os.path.basename(dest)}',
                              'path': dest.replace('\\', '/')})


# ==================== 模块管理 ====================

def _modules_dir():
    return os.path.join(_base_dir, 'modules')


def _read_module_meta(entry_path):
    """通过 AST 读取 main.py 中的 __module_meta__"""
    try:
        with open(entry_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.iter_child_nodes(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == '__module_meta__'):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return {}


def _scan_modules():
    """扫描所有模块, 包含运行时状态"""
    modules_dir = _modules_dir()
    result = []
    if not os.path.isdir(modules_dir):
        return result

    # 运行时模块状态
    runtime = {}
    persist_map = {}
    if _bot_manager:
        mm = getattr(_bot_manager, 'module_manager', None)
        if mm:
            for m in mm.list_modules():
                runtime[m['name']] = m
    # 直接读取持久化开关文件 (不依赖运行时)
    enabled_file = os.path.join(modules_dir, 'modules_enabled.json')
    if os.path.isfile(enabled_file):
        try:
            with open(enabled_file, 'r', encoding='utf-8') as f:
                persist_map = json.load(f) or {}
        except Exception:
            pass

    for name in sorted(os.listdir(modules_dir)):
        mod_dir = os.path.join(modules_dir, name)
        if not os.path.isdir(mod_dir) or name.startswith('_'):
            continue
        entry = os.path.join(mod_dir, 'main.py')
        if not os.path.isfile(entry):
            continue

        # 读取 __module_meta__
        meta = _read_module_meta(entry)

        # 读取配置文件列表
        data_dir = os.path.join(mod_dir, 'data')
        config_files = _list_config_files(data_dir)

        rt = runtime.get(name, {})
        mtime = datetime.fromtimestamp(os.path.getmtime(entry)).strftime('%Y-%m-%d %H:%M:%S')

        result.append({
            'name': name,
            'display_name': meta.get('name') or rt.get('display_name') or name,
            'description': meta.get('description') or rt.get('description', ''),
            'version': meta.get('version') or rt.get('version', '1.0.0'),
            'author': meta.get('author') or rt.get('author', ''),
            'enabled': rt.get('enabled', False),
            'persist_enabled': rt.get('persist_enabled', persist_map.get(name, False)),
            'error': rt.get('error'),
            'last_modified': mtime,
            'config_files': config_files,
        })

    return result


_CONFIG_EXTS = frozenset({'.yaml', '.yml', '.json', '.toml', '.ini', '.cfg', '.conf', '.txt', '.md', '.backup'})

def _list_config_files(data_dir):
    """列出 data/ 下可编辑的配置文件 (排除 .db/.backup 等)"""
    files = []
    if not os.path.isdir(data_dir):
        return files
    for fname in sorted(os.listdir(data_dir)):
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _CONFIG_EXTS:
            continue
        files.append({
            'name': fname,
            'path': fpath.replace('\\', '/'),
            'format': _detect_config_format(ext),
            'size': os.path.getsize(fpath),
        })
    return files


_FORMAT_MAP = {
    '.yaml': 'yaml', '.yml': 'yaml', '.json': 'json', '.toml': 'toml',
    '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
    '.txt': 'text', '.log': 'text', '.md': 'text',
}


def _detect_config_format(ext):
    """检测配置文件格式"""
    return _FORMAT_MAP.get(ext, 'raw')


async def handle_scan_modules(request: web.Request):
    return web.json_response({'success': True, 'modules': _scan_modules()})


def _validate_config_path(raw_path):
    """校验配置文件路径是否在 modules/ 或 plugins/ 下, 返回 (abs_path, error_response)"""
    abs_path = os.path.abspath(os.path.normpath(raw_path))
    allowed = (os.path.abspath(_modules_dir()), os.path.abspath(_plugins_dir()))
    if not any(abs_path.startswith(d) for d in allowed):
        return None, web.json_response({'success': False, 'message': '无效路径'}, status=403)
    return abs_path, None


def _extract_yaml_comments(raw_text):
    """从 YAML 原始文本提取注释, 返回 {key_path: comment} 扁平 dict
    支持顶层和嵌套 key 的行内注释 (key: value  # 注释)
    以及 key 上方一行的注释 (# 注释\\nkey: value)
    """
    comments = {}
    lines = raw_text.split('\n')
    pending_comment = None
    path_stack = []  # [(indent, key)]

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            pending_comment = None
            continue

        # 纯注释行
        m_comment = re.match(r'^(\s*)#\s*(.*)', stripped)
        if m_comment:
            pending_comment = m_comment.group(2).strip()
            continue

        # key: value 行
        m_kv = re.match(r'^(\s*)([A-Za-z_][\w]*)\s*:', stripped)
        if not m_kv:
            pending_comment = None
            continue

        indent = len(m_kv.group(1))
        key = m_kv.group(2)

        # 维护路径栈
        while path_stack and path_stack[-1][0] >= indent:
            path_stack.pop()

        # 行内注释
        inline = ''
        m_inline = re.search(r'#\s*(.+)$', stripped)
        if m_inline:
            # 确保不是 value 中的 #
            before_hash = stripped[:m_inline.start()].rstrip()
            if ':' in before_hash:
                inline = m_inline.group(1).strip()

        comment = inline or pending_comment or ''
        if comment:
            full_path = '.'.join([p[1] for p in path_stack] + [key])
            comments[full_path] = comment

        path_stack.append((indent, key))
        pending_comment = None

    return comments


async def handle_read_config(request: web.Request):
    """读取模块或插件的配置文件"""
    body = await request.json()
    if not body.get('path', ''):
        return web.json_response({'success': False, 'message': '缺少路径'}, status=400)
    abs_path, err = _validate_config_path(body['path'])
    if err:
        return err
    if not os.path.isfile(abs_path):
        return web.json_response({'success': False, 'message': '文件不存在'}, status=404)

    ext = os.path.splitext(abs_path)[1].lower()
    fmt = _detect_config_format(ext)

    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    # 尝试解析为结构化数据
    parsed = None
    comments = {}
    if fmt == 'yaml':
        try:
            parsed = yaml.safe_load(raw)
            comments = _extract_yaml_comments(raw)
        except Exception:
            pass
    elif fmt == 'json':
        try:
            parsed = json.loads(raw)
        except Exception:
            pass

    return web.json_response({
        'success': True,
        'format': fmt,
        'raw': raw,
        'parsed': parsed,
        'comments': comments,
        'filename': os.path.basename(abs_path),
    })


async def handle_save_config(request: web.Request):
    """保存模块或插件的配置文件"""
    body = await request.json()
    content = body.get('content')
    fmt = body.get('format', 'raw')
    if not body.get('path', '') or content is None:
        return web.json_response({'success': False, 'message': '缺少参数'}, status=400)
    abs_path, err = _validate_config_path(body['path'])
    if err:
        return err

    # 验证格式
    if fmt == 'yaml':
        try:
            yaml.safe_load(content)  # 仅验证语法, 不重新序列化, 保留注释
        except Exception as e:
            return web.json_response({'success': False, 'message': f'YAML 格式错误: {e}'}, status=400)
    elif fmt == 'json':
        try:
            data = json.loads(content)
            content = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as e:
            return web.json_response({'success': False, 'message': f'JSON 格式错误: {e}'}, status=400)

    # 备份
    if os.path.isfile(abs_path):
        import shutil
        shutil.copy2(abs_path, abs_path + '.backup')

    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)

    # 自动重载所属模块
    reloaded = ''
    if abs_path.startswith(modules_dir) and _bot_manager:
        mm = getattr(_bot_manager, 'module_manager', None)
        if mm:
            rel = os.path.relpath(abs_path, modules_dir)
            mod_name = rel.split(os.sep)[0]
            if mm.is_enabled(mod_name):
                try:
                    await mm.reload(mod_name)
                    reloaded = mod_name
                except Exception as e:
                    log.warning(f"模块 {mod_name} 重载失败: {e}")

    msg = f'配置已保存, 模块 {reloaded} 已重载' if reloaded else '配置已保存'
    return web.json_response({'success': True, 'message': msg})


async def handle_module_toggle(request: web.Request):
    """启用/禁用模块 (运行时)"""
    body = await request.json()
    name = body.get('name', '')
    action = body.get('action', '')
    if not name or action not in ('enable', 'disable'):
        return web.json_response({'success': False, 'message': '参数错误'}, status=400)
    if not _bot_manager:
        return web.json_response({'success': False, 'message': '框架未启动'}, status=503)
    mm = getattr(_bot_manager, 'module_manager', None)
    if not mm:
        return web.json_response({'success': False, 'message': '模块管理器未初始化'}, status=503)
    try:
        if action == 'enable':
            ok = await mm.enable(name)
        else:
            ok = await mm.disable(name)
            if not ok:
                # 模块未运行但需要持久化关闭状态
                mm.set_module_enabled_persist(name, False)
                return web.json_response({'success': True, 'message': f'模块 {name} 已关闭'})
        if ok:
            return web.json_response({'success': True, 'message': f'模块 {name} 已{"开启" if action == "enable" else "关闭"}'})
        else:
            return web.json_response({'success': False, 'message': '操作失败'})
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)


async def handle_module_upload(request: web.Request):
    """上传模块 (zip 格式, 必须含 .py 和 .json)"""
    import zipfile
    import shutil
    import tempfile

    reader = await request.multipart()
    field = await reader.next()
    if not field or field.name != 'file':
        return web.json_response({'success': False, 'message': '缺少文件'}, status=400)

    filename = field.filename or ''
    if not filename.lower().endswith('.zip'):
        return web.json_response({'success': False, 'message': '仅支持 zip 格式'}, status=400)

    # 读取到临时文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    try:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            tmp.write(chunk)
        tmp.close()

        # 验证 zip 结构
        if not zipfile.is_zipfile(tmp.name):
            return web.json_response({'success': False, 'message': '无效的 zip 文件'}, status=400)

        with zipfile.ZipFile(tmp.name, 'r') as zf:
            names = zf.namelist()
            has_py = any(n.endswith('.py') for n in names)
            has_json = any(n.endswith('.json') for n in names)
            if not has_py or not has_json:
                return web.json_response({
                    'success': False,
                    'message': f'zip 必须包含 .py 和 .json 文件 (当前: py={has_py}, json={has_json})'
                }, status=400)

            # 检测模块名: 优先用 module.json 中的 name, 否则用 zip 文件名
            mod_name = os.path.splitext(filename)[0]
            for n in names:
                if os.path.basename(n) == 'module.json':
                    try:
                        meta = json.loads(zf.read(n))
                        if meta.get('name'):
                            mod_name = meta['name']
                    except Exception:
                        pass
                    break

            # 检测是否有顶层目录 (zip 内是 modname/xxx 还是直接 xxx)
            top_dirs = set()
            for n in names:
                parts = n.replace('\\', '/').split('/')
                if len(parts) > 1 and parts[0]:
                    top_dirs.add(parts[0])

            modules_dir = _modules_dir()
            os.makedirs(modules_dir, exist_ok=True)
            target_dir = os.path.join(modules_dir, mod_name)

            if os.path.exists(target_dir):
                # 备份旧模块
                backup = target_dir + '.bak'
                if os.path.exists(backup):
                    shutil.rmtree(backup)
                shutil.move(target_dir, backup)

            # 解压: 如果 zip 内有唯一顶层目录, 解压后重命名; 否则解压到 target_dir
            if len(top_dirs) == 1:
                # zip 有单个顶层目录
                extract_tmp = tempfile.mkdtemp()
                zf.extractall(extract_tmp)
                src = os.path.join(extract_tmp, list(top_dirs)[0])
                shutil.move(src, target_dir)
                shutil.rmtree(extract_tmp, ignore_errors=True)
            else:
                # zip 内直接是文件
                os.makedirs(target_dir, exist_ok=True)
                zf.extractall(target_dir)

        # 验证解压结果: 必须有 main.py
        if not os.path.isfile(os.path.join(target_dir, 'main.py')):
            py_files = [f for f in os.listdir(target_dir) if f.endswith('.py')]
            if not py_files:
                shutil.rmtree(target_dir, ignore_errors=True)
                return web.json_response({
                    'success': False, 'message': '解压后未找到 .py 文件'}, status=400)

        return web.json_response({
            'success': True,
            'message': f'模块 {mod_name} 上传成功，重启后生效',
            'module_name': mod_name,
        })
    except Exception as e:
        return web.json_response({'success': False, 'message': str(e)}, status=500)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


# ==================== 插件配置 (大型插件 data/ 目录) ====================

def _scan_plugin_configs(plugin_name):
    """扫描大型插件的 data/ 目录下可配置文件"""
    plugin_dir = os.path.join(_plugins_dir(), plugin_name)
    data_dir = os.path.join(plugin_dir, 'data')
    return _list_config_files(data_dir)


async def handle_plugin_config_files(request: web.Request):
    """获取插件的配置文件列表"""
    body = await request.json()
    plugin_name = body.get('name', '')
    if not plugin_name:
        return web.json_response({'success': False, 'message': '缺少插件名'}, status=400)
    files = _scan_plugin_configs(plugin_name)
    return web.json_response({'success': True, 'config_files': files})


# ==================== 插件机器人绑定 ====================

async def handle_get_plugin_bots(request: web.Request):
    """获取插件机器人绑定配置"""
    pm = _get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    return web.json_response({'success': True, 'plugin_bots': pm.get_plugin_bots()})


async def handle_set_plugin_bots(request: web.Request):
    """设置插件机器人绑定配置

    body: {"plugin_bots": {"插件名或插件名/文件名": ["appid1", "appid2", ...]}}
    空列表 = 不限制 (所有机器人均可触发)
    """
    pm = _get_pm()
    if not pm:
        return web.json_response({'success': False, 'message': '框架未启动或插件管理器未初始化'}, status=503)
    body = await request.json()
    data = body.get('plugin_bots')
    if not isinstance(data, dict):
        return web.json_response({'success': False, 'message': 'plugin_bots 必须为 dict'}, status=400)
    pm.set_plugin_bots(data)
    return web.json_response({'success': True, 'message': '插件机器人绑定已保存'})
