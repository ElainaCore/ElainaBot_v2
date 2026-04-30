"""插件上下文、数据模型与辅助函数"""

import os
import asyncio
import yaml
from core.base.logger import get_logger, PLUGIN

# 当前正在加载的插件上下文 (由 PluginManager 在加载期间赋值)
ctx = None


class PluginContext:
    """插件上下文 — data/ 目录读写、配置管理"""

    __slots__ = ('name', 'plugin_dir', 'data_dir', 'log')

    def __init__(self, name, plugin_dir):
        self.name = name
        self.plugin_dir = plugin_dir
        self.data_dir = os.path.join(plugin_dir, 'data')
        self.log = get_logger(PLUGIN, name)
        os.makedirs(self.data_dir, exist_ok=True)

    def get_data_path(self, filename):
        """data/ 下文件的绝对路径"""
        return os.path.join(self.data_dir, filename)

    def get_resource_path(self, filename):
        """插件根目录下的文件路径"""
        return os.path.join(self.plugin_dir, filename)

    def read_config(self, filename='config.yaml'):
        """读取 data/ 下的 YAML 配置"""
        path = self.get_data_path(filename)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            self.log.warning(f"读取配置失败 [{filename}]: {e}")
            return {}

    def save_config(self, data, filename='config.yaml'):
        """保存配置到 data/"""
        path = self.get_data_path(filename)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            self.log.warning(f"保存配置失败 [{filename}]: {e}")

    def ensure_config(self, defaults, filename='config.yaml'):
        """确保配置存在且不缺项, 返回完整配置 dict"""
        current = self.read_config(filename)
        changed = False
        for key, value in defaults.items():
            if key not in current:
                current[key] = value
                changed = True
        if changed:
            self.save_config(current, filename)
            self.log.info(f"配置已自动补全: {filename}")
        return current

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
        return os.path.isfile(self.get_data_path(filename))

    def list_data(self):
        if not os.path.isdir(self.data_dir):
            return []
        return os.listdir(self.data_dir)


class PluginInfo:
    """已加载插件的信息"""
    __slots__ = ('name', 'plugin_dir', 'module', 'handlers', 'on_load_funcs',
                 'on_unload_funcs', 'interceptors', 'enabled', 'load_time',
                 'error', 'ctx', 'is_large')

    def __init__(self, name, plugin_dir):
        self.name = name
        self.plugin_dir = plugin_dir
        self.module = None
        self.handlers = []
        self.on_load_funcs = []
        self.on_unload_funcs = []
        self.interceptors = []
        self.enabled = True
        self.load_time = 0
        self.error = None
        self.ctx = None
        self.is_large = False


def _make_reply_log_cb(plugin_name, log_service):
    """创建插件回复日志回调 (避免在匹配循环中反复定义闭包)"""
    def cb(text, uid, gid, raw_message=''):
        if log_service:
            asyncio.ensure_future(log_service.add('message', {
                'type': 'plugin',
                'user_id': uid, 'group_id': gid,
                'content': text, 'plugin_name': plugin_name,
                'raw_message': raw_message,
            }))
    return cb
