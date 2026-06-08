#!/usr/bin/env python
"""配置管理器"""

import asyncio
import contextlib
import copy
import logging
import os
import re
import threading
import time

try:
    import yaml
except ImportError as err:
    raise ImportError('需要安装 pyyaml: pip install pyyaml') from err

logger = logging.getLogger('ElainaBot.config')

_CHECK_INTERVAL = 5.0
_ENV_PATTERN = re.compile(r'\$\{(\w+)(?::([^}]*))?}')


def _env_replacer(m):
    return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else '')


_MISSING = object()

# 机器人设置内置默认值 (bot.yaml 中未配置时使用)
_BOT_DEFAULTS = {
    'message.use_markdown': True,
    'message.markdown_suffix': '',
    'message.button_enter_to_send': False,
    'message.send_default_response': False,
    'message.default_response_excluded_regex': [],
    'message.suppress_bot_system_reply': False,
    'non_at_message.ignore_at_other_bot': True,
    'non_at_message.ignore_at_other_user': True,
    'non_at_message.ignore_bot_sender': False,
    'non_at_message.quiet_at_self': False,
    'non_at_message.strip_bot_name_at': False,
    'identity.use_union_id_for_group': False,
    'identity.use_union_id_for_channel': False,
    'welcome.group_welcome': False,
    'welcome.new_user_welcome': False,
    'welcome.friend_add_message': False,
    'maintenance.enabled': False,
    'blacklist.user_enabled': False,
    'blacklist.group_enabled': False,
    'blacklist.user_list': [],
    'blacklist.group_list': [],
    'non_at_message.enabled': False,
    'non_at_message.group_whitelist': [],
}


class ConfigManager:
    """YAML 配置管理器 (单例, 惰性热加载, 线程安全)"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._ready = False
            return cls._instance

    # ------ 初始化 ------

    def init(self, config_dir):
        if self._ready:
            return
        self._config_dir = os.path.abspath(config_dir)
        self._cache = {}  # {name: parsed_dict}
        self._mtimes = {}  # {name: float}
        self._last_check = {}  # {name: float} 限频
        self._callbacks = {}  # {name: [callable]}
        self._bot_cfg_map = {}  # {appid_str: bot_dict} 加速查找
        self._bot_setting_cache = {}  # {(appid, key): value} 设置值缓存
        self._path_cache = {}  # {name: filepath} 解析路径缓存
        self._rw_lock = threading.RLock()
        self._ready = True
        logger.info(f'配置目录: {self._config_dir}')

    @property
    def config_dir(self):
        return self._config_dir

    # ------ 读取 ------

    def get(self, name, key=None, default=None):
        """获取配置值

        Args:
            name:    配置文件名(不含 .yaml), 如 'settings', 'bot', 'extension/redis'
            key:     点号分隔路径, 如 'server.port', None 返回整个 dict
            default: 默认值
        """
        self._maybe_reload(name)
        with self._rw_lock:
            data = self._cache.get(name, {})
        if key is None:
            return data
        return self._deep_get(data, key, default)

    def get_bot_configs(self):
        """获取所有机器人配置列表"""
        return self.get('bot', 'bots') or []

    def get_bot_config(self, appid):
        """按 appid 获取单个机器人配置 (带缓存)"""
        key = str(appid)
        # 缓存命中
        if key in self._bot_cfg_map:
            return self._bot_cfg_map[key]
        # 遍历查找并缓存
        for bot in self.get_bot_configs():
            aid = str(bot.get('appid', ''))
            self._bot_cfg_map[aid] = bot
        return self._bot_cfg_map.get(key)

    def get_bot_setting(self, appid, key, default=None):
        """获取某个机器人的设置值 (bot 配置 > 内置默认值 > default 参数)"""
        cache_key = (str(appid), key)
        cached = self._bot_setting_cache.get(cache_key, _MISSING)
        if cached is not _MISSING:
            return cached
        bot_cfg = self.get_bot_config(appid)
        val = self._deep_get(bot_cfg, key) if bot_cfg else None
        if val is not None:
            self._bot_setting_cache[cache_key] = val
            return val
        return _BOT_DEFAULTS.get(key, default)

    # ------ 热加载 ------

    def _resolve_path(self, name):
        """解析配置文件名 -> 绝对路径 (带缓存)

        查找顺序: name.yaml > name.yml > name.example.yaml(自动复制) > name.yaml(占位)
        当实际配置文件不存在时, 自动从 .example.yaml 复制生成, 避免示例文件被本地修改污染版本控制.
        """
        cached = self._path_cache.get(name)
        if cached:
            return cached
        base = os.path.join(self._config_dir, name)
        # 1) 优先查找实际配置文件 (.yaml / .yml / 无扩展名)
        for ext in ('.yaml', '.yml', ''):
            p = base + ext
            if os.path.isfile(p):
                self._path_cache[name] = p
                return p
        # 2) 尝试从 .example.yaml 自动复制
        example_p = base + '.example.yaml'
        if os.path.isfile(example_p):
            import shutil

            target = base + '.yaml'
            shutil.copy2(example_p, target)
            logger.info(f'从示例文件创建配置: {os.path.basename(base)}.yaml <- {os.path.basename(example_p)}')
            self._path_cache[name] = target
            return target
        # 2b) Docker 环境 fallback: 挂载卷可能清空了 config 目录，回退到 config.defaults
        fallback_p = os.path.join(self._config_dir + '.defaults', os.path.basename(base) + '.example.yaml')
        if os.path.isfile(fallback_p):
            import shutil

            target = base + '.yaml'
            shutil.copy2(fallback_p, target)
            logger.info(f'[Docker] 从备份创建配置: {os.path.basename(base)}.yaml <- config.defaults/{os.path.basename(fallback_p)}')
            self._path_cache[name] = target
            return target
        # 3) 都不存在, 返回默认路径 (后续写入时创建)
        p = base + '.yaml'
        self._path_cache[name] = p
        return p

    def _maybe_reload(self, name, force_sync=False):
        now = time.monotonic()
        last = self._last_check.get(name, 0)
        if now - last < _CHECK_INTERVAL:
            return
        self._last_check[name] = now

        filepath = self._resolve_path(name)
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return

        old_mtime = self._mtimes.get(name)
        if old_mtime is not None and mtime <= old_mtime:
            return

        # 首次加载 / 强制同步: 直接加载; 后续变更: 后台线程加载, 不阻塞事件循环
        if old_mtime is None or force_sync:
            self._do_reload(name, filepath, mtime, is_first=(old_mtime is None))
        else:
            threading.Thread(target=self._do_reload, args=(name, filepath, mtime), daemon=True).start()

    def _do_reload(self, name, filepath, mtime, is_first=False):
        """实际加载配置文件 (可在后台线程执行)"""
        try:
            with open(filepath, encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            data = self._resolve_env_vars_recursive(data)
        except Exception as e:
            logger.error(f'解析配置失败 [{name}]: {e}')
            return

        with self._rw_lock:
            self._cache[name] = data
            self._mtimes[name] = mtime
            # bot 配置变更时清除缓存
            if name == 'bot':
                self._bot_cfg_map.clear()
                self._bot_setting_cache.clear()

        if not is_first:
            logger.info(f'配置热加载: {name}')
            self._fire_callbacks(name, data)

    async def reload_if_changed(self, *names):
        """异步预加载配置, 文件 I/O 在线程池执行, 适用于 watch loop 等 async 上下文"""
        loop = asyncio.get_running_loop()
        for name in names:
            await loop.run_in_executor(None, self._maybe_reload, name)

    def _fire_callbacks(self, name, data):
        for cb in self._callbacks.get(name, []):
            try:
                cb(data)
            except Exception as e:
                logger.warning(f'配置回调异常 [{name}]: {e}')

    # ------ 回调注册 ------

    def on_change(self, name, callback):
        """注册配置变更回调: callback(new_data_dict)"""
        self._callbacks.setdefault(name, []).append(callback)

    def off_change(self, name, callback=None):
        """移除回调, callback=None 移除该文件所有回调"""
        if callback is None:
            self._callbacks.pop(name, None)
            return
        cbs = self._callbacks.get(name)
        if cbs:
            self._callbacks[name] = [c for c in cbs if c is not callback]

    # ------ 写入 / 补全 ------

    def set_value(self, name, key, value):
        """设置配置值并写入文件"""
        self._maybe_reload(name, force_sync=True)
        with self._rw_lock:
            data = self._cache.get(name, {})
            self._deep_set(data, key, value)
            self._cache[name] = data
        self._write_file(name, data)

    def ensure(self, name, defaults):
        """确保配置文件存在且不缺项, 缺失项用 defaults 补全"""
        filepath = self._resolve_path(name)
        if not os.path.isfile(filepath):
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self._write_file(name, defaults)
            with self._rw_lock:
                self._cache[name] = copy.deepcopy(defaults)
                self._mtimes[name] = time.time()
            logger.info(f'配置创建: {name}')
            return True
        self._maybe_reload(name, force_sync=True)
        with self._rw_lock:
            current = self._cache.get(name, {})
        changed = self._merge_defaults(current, defaults)
        if not changed:
            return False
        self._write_file(name, current)
        with self._rw_lock:
            self._cache[name] = current
        logger.info(f'配置补全: {name} (新增 {changed} 项)')
        return True

    def _write_file(self, name, data):
        filepath = self._resolve_path(name)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        tmp = filepath + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            os.replace(tmp, filepath)
            self._mtimes[name] = os.path.getmtime(filepath)
        except Exception as e:
            logger.error(f'写入配置失败 [{name}]: {e}')
            with contextlib.suppress(OSError):
                os.remove(tmp)

    # ------ 工具方法 ------

    @staticmethod
    def _deep_get(data, key, default=None):
        try:
            for k in key.split('.'):
                data = data[k]
            return data if data is not None else default
        except (KeyError, TypeError):
            return default

    @staticmethod
    def _deep_set(data, key, value):
        *path, leaf = key.split('.')
        for k in path:
            sub = data.get(k)
            if not isinstance(sub, dict):
                sub = data[k] = {}
            data = sub
        data[leaf] = value

    @staticmethod
    def _merge_defaults(current, defaults, _prefix=''):
        """递归合并, 返回新增项数"""
        added = 0
        for k, v in defaults.items():
            if k not in current:
                current[k] = copy.deepcopy(v)
                added += 1
            elif isinstance(v, dict) and isinstance(current[k], dict):
                added += ConfigManager._merge_defaults(current[k], v, f'{_prefix}{k}.')
        return added

    @staticmethod
    def _resolve_env_vars(text: str) -> str:
        """解析 ${VAR_NAME:default} 环境变量占位符"""
        if '${' not in text:
            return text
        return _ENV_PATTERN.sub(_env_replacer, text)

    @classmethod
    def _resolve_env_vars_recursive(cls, data):
        """递归遍历数据, 对所有字符串值应用 _resolve_env_vars"""
        if isinstance(data, dict):
            return {k: cls._resolve_env_vars_recursive(v) for k, v in data.items()}
        if isinstance(data, list):
            return [cls._resolve_env_vars_recursive(v) for v in data]
        if isinstance(data, str):
            return cls._resolve_env_vars(data)
        return data

    def reload_all(self):
        """强制重新加载所有已缓存的配置"""
        with self._rw_lock:
            names = list(self._cache.keys())
            self._mtimes.clear()
            self._last_check.clear()
        for name in names:
            self._maybe_reload(name)
        logger.info(f'全部配置已重新加载 ({len(names)} 个)')


# ===== 全局单例 =====
cfg = ConfigManager()
