#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拓展模块管理器 — 自动发现、依赖安装、启停管理

模块结构: modules/{name}/ → main.py(入口) + module.json(清单) + requirements.txt(可选) + data/(配置)
入口函数: async def setup(ctx: ModuleContext) / async def teardown()
安装即启用, 运行时可 disable(), 永久禁用删除目录
"""

import os
import sys
import json
import subprocess
import asyncio
import importlib
import importlib.util
import re
import yaml
import importlib.metadata as _metadata
from core.base.logger import get_logger, EXTENSION, report_error
from core.base.config import cfg as app_cfg
from core.module.hook import get_hook_manager

log = get_logger(EXTENSION, "管理器")


async def _await_if_coro(result):
    """await 协程结果, 否则直接返回"""
    return await result if asyncio.iscoroutine(result) else result


# module.json 字段默认值
_DEFAULT_MANIFEST = {
    'name': '',
    'description': '',
    'version': '1.0.0',
    'author': '',
    'github': '',
    'releases': '',
}


class ModuleContext:
    """模块上下文 — 数据目录访问、配置管理、Hook 注册"""

    __slots__ = ('name', 'module_dir', 'data_dir', 'log', '_hooks')

    def __init__(self, name, module_dir):
        self.name = name
        self.module_dir = module_dir
        self.data_dir = os.path.join(module_dir, 'data')
        self.log = get_logger(EXTENSION, name)
        self._hooks = get_hook_manager()
        os.makedirs(self.data_dir, exist_ok=True)

    # ---------- 路径 ----------

    def get_data_path(self, filename):
        """获取 data/ 下文件的绝对路径"""
        return os.path.join(self.data_dir, filename)

    def get_resource_path(self, filename):
        """获取模块根目录下的文件路径 (代码/静态资源)"""
        return os.path.join(self.module_dir, filename)

    # ---------- 配置读写 ----------

    def read_config(self, filename='config.yaml'):
        """读取 data/ 下的 YAML 配置, 文件不存在返回空 dict"""
        path = self.get_data_path(filename)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            self.log.warning(f"读取配置失败 [{filename}]: {e}")
            return {}

    def save_config(self, data, filename='config.yaml', comments=None):
        """保存配置到 data/, 可选写入注释"""
        path = self.get_data_path(filename)
        try:
            if comments:
                self._save_yaml_with_comments(path, data, comments)
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            self.log.warning(f"保存配置失败 [{filename}]: {e}")

    def _save_yaml_with_comments(self, path, data, comments, indent=0):
        """写入带注释的 YAML 配置"""
        lines = []
        self._render_yaml_lines(lines, data, comments, indent)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

    def _render_yaml_lines(self, lines, data, comments, indent=0):
        """递归渲染 YAML 行 (带注释)"""
        prefix = '  ' * indent
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            comment = comments.get(key, '') if comments else ''
            if isinstance(value, dict):
                sub_comments = comments.get(key) if isinstance(comments.get(key), dict) else {}
                if comment and isinstance(comment, str):
                    lines.append(f"{prefix}# {comment}")
                elif isinstance(sub_comments, dict) and '__desc__' in sub_comments:
                    lines.append(f"{prefix}# {sub_comments['__desc__']}")
                lines.append(f"{prefix}{key}:")
                actual_comments = sub_comments if isinstance(sub_comments, dict) else {}
                self._render_yaml_lines(lines, value, actual_comments, indent + 1)
            else:
                yaml_val = self._yaml_scalar(value)
                if comment:
                    lines.append(f"{prefix}{key}: {yaml_val}  # {comment}")
                else:
                    lines.append(f"{prefix}{key}: {yaml_val}")

    @staticmethod
    def _yaml_scalar(value):
        """将 Python 值转为 YAML 标量字符串"""
        if value is None:
            return 'null'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            if not value:
                return "''"
            if any(c in value for c in ':{}[]&*?|>!%@`,"\'') or value.strip() != value:
                return f"'{value}'"
            return value
        return yaml.dump(value, default_flow_style=True, allow_unicode=True).strip()

    def ensure_config(self, defaults, filename='config.yaml', comments=None):
        """确保配置存在且不缺项, 返回完整配置 dict"""
        current = self.read_config(filename)
        changed = False
        for key, value in defaults.items():
            if key not in current:
                current[key] = value
                changed = True
        if changed:
            self.save_config(current, filename, comments=comments)
            self.log.info(f"配置已自动补全: {filename}")
        return current

    # ---------- 数据文件 ----------

    def read_data(self, filename, encoding='utf-8'):
        """读取 data/ 下的文本文件"""
        path = self.get_data_path(filename)
        if not os.path.isfile(path):
            return None
        with open(path, 'r', encoding=encoding) as f:
            return f.read()

    def save_data(self, filename, content, encoding='utf-8'):
        """保存文本到 data/"""
        path = self.get_data_path(filename)
        with open(path, 'w', encoding=encoding) as f:
            f.write(content)

    def data_exists(self, filename):
        """检查 data/ 下文件是否存在"""
        return os.path.isfile(self.get_data_path(filename))

    def list_data(self):
        """列出 data/ 下所有文件"""
        if not os.path.isdir(self.data_dir):
            return []
        return os.listdir(self.data_dir)

    # ---------- Hook ----------

    def hook(self, hook_name, *, priority=100):
        """装饰器注册 hook: @ctx.hook('before_send')"""
        def decorator(func):
            self._hooks.register(hook_name, func, owner=self.name, priority=priority)
            return func
        return decorator

    def register_hook(self, hook_name, callback, *, priority=100):
        """直接注册 hook 回调 (非装饰器方式)"""
        self._hooks.register(hook_name, callback, owner=self.name, priority=priority)

    async def emit(self, hook_name, *args, **kwargs):
        """触发一个 hook (广播模式)"""
        await self._hooks.emit(hook_name, *args, **kwargs)

    async def pipeline(self, hook_name, data):
        """触发一个 hook (管道模式, 可修改/拦截数据)"""
        return await self._hooks.pipeline(hook_name, data)


class ModuleInfo:
    """已发现模块的信息"""
    __slots__ = ('name', 'display_name', 'description', 'module_dir',
                 'module', 'version', 'author', 'github', 'releases',
                 'instance', 'ctx', 'error')

    def __init__(self, name, module_dir):
        self.name = name
        self.module_dir = module_dir
        self.display_name = name
        self.description = ''
        self.version = '1.0.0'
        self.author = ''
        self.github = ''
        self.releases = ''
        self.module = None
        self.instance = None
        self.ctx = None
        self.error = None


class ModuleManager:
    """拓展模块管理器"""

    def __init__(self, modules_dir=None):
        if modules_dir:
            self._dir = os.path.abspath(modules_dir)
        else:
            self._dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'modules')
        self._modules = {}    # {name: ModuleInfo}
        self._lock = asyncio.Lock()
        self._enabled_file = os.path.join(self._dir, 'modules_enabled.json')
        self._enabled_map = self._load_enabled_map()  # {name: bool}

    # ==================== 发现 ====================

    def discover(self):
        """扫描 modules/ 下所有模块目录"""
        if not os.path.isdir(self._dir):
            os.makedirs(self._dir, exist_ok=True)
            return
        for name in sorted(os.listdir(self._dir)):
            mod_dir = os.path.join(self._dir, name)
            if not os.path.isdir(mod_dir) or name.startswith('_'):
                continue
            if not self._find_entry(mod_dir):
                continue
            info = ModuleInfo(name, mod_dir)
            meta = self._read_manifest(mod_dir)
            info.display_name = meta.get('name') or name
            for key in ('description', 'version', 'author', 'github', 'releases'):
                val = meta.get(key)
                if val is not None:
                    setattr(info, key, str(val))
            self._modules[name] = info
        log.info(f"发现 {len(self._modules)} 个模块: "
                 f"{', '.join(f'{n}@{m.version}' for n, m in self._modules.items())}")

    # ==================== 持久化开关 ====================

    def _load_enabled_map(self):
        """读取 modules_enabled.json, 不存在则返回空 dict"""
        if not os.path.isfile(self._enabled_file):
            return {}
        try:
            with open(self._enabled_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_enabled_map(self):
        """保存 modules_enabled.json"""
        os.makedirs(os.path.dirname(self._enabled_file), exist_ok=True)
        try:
            with open(self._enabled_file, 'w', encoding='utf-8') as f:
                json.dump(self._enabled_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"保存模块开关状态失败: {e}")

    def is_module_enabled_persist(self, name):
        """查询模块是否在持久化配置中标记为启用 (默认 False)"""
        return self._enabled_map.get(name, False)

    def set_module_enabled_persist(self, name, enabled):
        """设置模块持久化开关状态"""
        self._enabled_map[name] = bool(enabled)
        self._save_enabled_map()

    # ==================== 自动启动 ====================

    async def start_enabled(self):
        """启动持久化配置中标记为启用的模块"""
        to_start = [n for n in self._modules if self.is_module_enabled_persist(n)]
        if not to_start:
            log.info("无已启用模块, 跳过启动")
            return
        tasks = [self._install_requirements(n, self._modules[n].module_dir) for n in to_start]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for name in to_start:
            try:
                await self.enable(name, _skip_deps=True)
            except Exception as e:
                report_error(EXTENSION, name, e)

    # ==================== 启用/禁用 ====================

    async def enable(self, name, _skip_deps=False, _persist=True):
        """启用模块"""
        async with self._lock:
            info = self._modules.get(name)
            if not info:
                log.warning(f"模块不存在: {name}")
                return False
            if info.instance is not None:
                return True
            try:
                if not _skip_deps:
                    await self._install_requirements(name, info.module_dir)
                ctx = ModuleContext(info.display_name or name, info.module_dir)
                info.ctx = ctx
                module = self._import_module(name, info.module_dir)
                info.module = module
                setup_fn = getattr(module, 'setup', None)
                result = await _await_if_coro(setup_fn(ctx)) if setup_fn else None
                info.instance = result if result is not None else True
                info.error = None
                if _persist:
                    self.set_module_enabled_persist(name, True)
                return True
            except Exception as e:
                info.error = str(e)
                report_error(EXTENSION, name, e)
                return False

    async def disable(self, name, _persist=True):
        """禁用模块"""
        async with self._lock:
            info = self._modules.get(name)
            if not info or info.instance is None:
                if _persist:
                    self.set_module_enabled_persist(name, False)
                return False
            try:
                teardown_fn = getattr(info.module, 'teardown', None)
                if teardown_fn:
                    await _await_if_coro(teardown_fn())
            except Exception as e:
                report_error(EXTENSION, name, e)
            get_hook_manager().unregister_owner(info.display_name or name)
            info.instance = None
            info.ctx = None
            sys.modules.pop(f"modules.{name}", None)
            if _persist:
                self.set_module_enabled_persist(name, False)
            get_logger(EXTENSION, info.display_name).info("❌ 已禁用")
            return True

    # ==================== 查询 ====================

    def get(self, name):
        """获取已启用模块实例 (setup 返回值)"""
        info = self._modules.get(name)
        return info.instance if info and info.instance is not None else None

    def get_context(self, name):
        """获取模块上下文"""
        info = self._modules.get(name)
        return info.ctx if info else None

    def get_module(self, name):
        """获取模块 Python 对象"""
        info = self._modules.get(name)
        return info.module if info else None

    def is_enabled(self, name):
        info = self._modules.get(name)
        return info.instance is not None if info else False

    def list_modules(self):
        """获取所有模块状态"""
        return [{'name': i.name, 'display_name': i.display_name,
                 'description': i.description, 'version': i.version,
                 'author': i.author, 'github': i.github, 'releases': i.releases,
                 'enabled': i.instance is not None,
                 'persist_enabled': self.is_module_enabled_persist(i.name),
                 'error': i.error}
                for i in self._modules.values()]

    # ==================== 内部 ====================

    @staticmethod
    def _find_entry(mod_dir):
        """main.py 存在则返回路径"""
        path = os.path.join(mod_dir, 'main.py')
        return path if os.path.isfile(path) else None

    @staticmethod
    def _import_module(name, mod_dir):
        """动态导入模块"""
        entry = os.path.join(mod_dir, 'main.py')
        if not os.path.isfile(entry):
            raise FileNotFoundError(f"模块入口不存在: {mod_dir} (需要 main.py)")

        mod_name = f"modules.{name}"
        spec = importlib.util.spec_from_file_location(mod_name, entry,
                    submodule_search_locations=[mod_dir])
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _read_manifest(mod_dir):
        """读取 module.json 清单"""
        path = os.path.join(mod_dir, 'module.json')
        if not os.path.isfile(path):
            return dict(_DEFAULT_MANIFEST)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
        except Exception as e:
            log.warning(f"读取 module.json 失败 [{mod_dir}]: {e}")
            return dict(_DEFAULT_MANIFEST)

    async def _install_requirements(self, name, target_dir):
        """检查并安装 requirements.txt 依赖"""
        req_path = os.path.join(target_dir, 'requirements.txt')
        if not os.path.isfile(req_path):
            return
        if not app_cfg.get('settings', 'pip.auto_install', True):
            return
        if self._all_requirements_met(req_path):
            return
        mirror = app_cfg.get('settings', 'pip.mirror', '')
        cmd = [sys.executable, '-m', 'pip', 'install', '-r', req_path, '--quiet', '--no-cache-dir']
        if mirror:
            cmd.extend(['-i', mirror])

        loop = asyncio.get_running_loop()
        try:
            exit_code = await loop.run_in_executor(None, self._pip_install_sync, cmd, name)
            if exit_code != 0:
                log.warning(f"[{name}] 依赖安装可能失败 (exit={exit_code})")
        except Exception as e:
            log.warning(f"[{name}] 依赖安装异常: {e}")

    @staticmethod
    def _all_requirements_met(req_path):
        """检查 requirements.txt 中所有包是否已安装"""
        try:
            with open(req_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception:
            return False
        for raw in lines:
            pkg = re.split(r'[>=<!\[;]', raw.strip())[0].strip()
            if not pkg or pkg.startswith(('#', '-')):
                continue
            try:
                _metadata.distribution(pkg)
            except _metadata.PackageNotFoundError:
                return False
        return True

    @staticmethod
    def _pip_install_sync(cmd, name):
        log.info(f"[{name}] 正在安装依赖...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            log.info(f"[{name}] 依赖安装完成")
        else:
            stderr = result.stderr.strip()
            if stderr:
                log.warning(f"[{name}] pip: {stderr[:200]}")
        return result.returncode

    async def shutdown(self):
        """关闭所有已启用模块"""
        for name, info in list(self._modules.items()):
            if info.instance is not None:
                await self.disable(name)
