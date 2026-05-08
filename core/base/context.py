"""通用上下文基类 — data/ 目录读写、YAML 配置管理

PluginContext 和 ModuleContext 均继承此类, 避免重复代码。
"""

import os
import yaml
from core.base.logger import get_logger


def _yaml_scalar(value):
    """Python 值 → YAML 标量字符串"""
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


def _render_yaml_lines(lines, data, comments, indent=0):
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
            _render_yaml_lines(lines, value, actual_comments, indent + 1)
        elif isinstance(value, list):
            _render_yaml_list(lines, key, value, comment, prefix, indent)
        else:
            yaml_val = _yaml_scalar(value)
            if comment:
                lines.append(f"{prefix}{key}: {yaml_val}  # {comment}")
            else:
                lines.append(f"{prefix}{key}: {yaml_val}")


def _render_yaml_list(lines, key, value, comment, prefix, indent):
    """渲染列表值: 空列表用 [], 列表-字典用 block 风格"""
    if comment:
        lines.append(f"{prefix}# {comment}")
    if not value:
        lines.append(f"{prefix}{key}: []")
        return
    lines.append(f"{prefix}{key}:")
    child = '  ' * (indent + 1)
    for item in value:
        if isinstance(item, dict):
            first = True
            for k, v in item.items():
                tag = '- ' if first else '  '
                lines.append(f"{child}{tag}{k}: {_yaml_scalar(v)}")
                first = False
        else:
            lines.append(f"{child}- {_yaml_scalar(item)}")


class BaseContext:
    """通用上下文 — data/ 读写、YAML 配置管理"""

    __slots__ = ('name', '_root_dir', 'data_dir', 'log')

    def __init__(self, name, root_dir, log_type):
        self.name = name
        self._root_dir = root_dir
        self.data_dir = os.path.join(root_dir, 'data')
        self.log = get_logger(log_type, name)
        os.makedirs(self.data_dir, exist_ok=True)

    # ---------- 路径 ----------

    def get_data_path(self, filename):
        """data/ 下文件的绝对路径"""
        return os.path.join(self.data_dir, filename)

    def get_resource_path(self, filename):
        """根目录下的文件路径"""
        return os.path.join(self._root_dir, filename)

    # ---------- YAML 配置 ----------

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

    def save_config(self, data, filename='config.yaml', comments=None):
        """保存配置到 data/, 可选写入注释"""
        path = self.get_data_path(filename)
        try:
            if comments:
                lines = []
                _render_yaml_lines(lines, data, comments)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines) + '\n')
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
        except Exception as e:
            self.log.warning(f"保存配置失败 [{filename}]: {e}")

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
        return os.path.isfile(self.get_data_path(filename))

    def list_data(self):
        if not os.path.isdir(self.data_dir):
            return []
        return os.listdir(self.data_dir)
