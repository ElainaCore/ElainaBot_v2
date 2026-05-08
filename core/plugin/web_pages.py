"""Web 面板自定义页面注册表 — 插件/模块通过 register_page 注册侧边栏页面"""
from __future__ import annotations

_registry: dict = {}  # {key: page_info}


def register_page(key: str, label: str, *,
                  source: str = 'plugin',
                  source_name: str = '',
                  html: str = '',
                  html_file: str = '',
                  icon: str = ''):
    """注册自定义 Web 面板页面"""
    _registry[key] = {
        'key': key,
        'label': label,
        'source': source,
        'source_name': source_name,
        'html': html,
        'html_file': html_file,
        'icon': icon,
    }


def unregister_page(key: str):
    """注销页面"""
    _registry.pop(key, None)


def get_pages() -> list:
    """获取所有已注册页面 (不含 html 内容)"""
    return [
        {k: v for k, v in p.items() if k not in ('html', 'html_file')}
        for p in _registry.values()
    ]


def get_page_html(key: str) -> str | None:
    """获取指定页面的 HTML 内容"""
    info = _registry.get(key)
    if not info:
        return None
    if info.get('html'):
        return info['html']
    path = info.get('html_file')
    if not path:
        return '<p>空页面</p>'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return '<p style="color:red">页面文件加载失败</p>'
