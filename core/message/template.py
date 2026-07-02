#!/usr/bin/env python
"""模板引擎 — 消息模板 + 按钮构建, 热加载"""

import contextlib

from core.base.config import cfg
from core.base.logger import FRAMEWORK, get_logger

log = get_logger(FRAMEWORK, '模板引擎')


class TemplateEngine:
    """消息模板引擎 — 变量替换 + 按钮构建"""

    def render(self, template_name, use_markdown=False, appid=None, **variables):
        """渲染模板, 返回 (content, buttons_or_None)"""
        if appid:
            variables.setdefault('appid', appid)
        raw = self._resolve_template(template_name, appid)
        if raw is None:
            return f'[模板缺失: {template_name}]', None

        # 纯字符串模板
        if isinstance(raw, str):
            return self._substitute(raw, variables), None

        # 字典模板
        if isinstance(raw, dict):
            content = self._pick_content(raw, use_markdown)
            content = self._substitute(content, variables)
            buttons = self._build_buttons(raw.get('buttons'), variables)
            return content, buttons

        return str(raw), None

    def render_error(self, error_code='', error_message='', appid=None, **variables):
        """渲染 API 错误模板"""
        variables.update(error_code=error_code, error_message=error_message)
        return self.render('api_error', appid=appid, **variables)

    def get_raw(self, template_name, appid=None):
        """获取原始模板数据(不渲染)"""
        return self._resolve_template(template_name, appid)

    # ---------- 内部方法 ----------

    @staticmethod
    def _resolve_template(template_name, appid=None):
        """按 appid 解析模板: 列表形式按 appid 匹配, 无 appid 条目作为兜底"""
        raw = cfg.get('templates', template_name)
        if raw is None:
            return None

        # 列表形式: 按 appid 匹配
        if isinstance(raw, list):
            fallback = None
            for item in raw:
                if not isinstance(item, dict):
                    continue
                item_appid = str(item.get('appid', ''))
                if item_appid and appid and item_appid == str(appid):
                    return item
                if not item_appid:
                    fallback = item
            return fallback

        # 字符串或字典: 直接返回
        return raw

    @staticmethod
    def _pick_content(data, use_markdown):
        """选取 markdown 或 text 或 content, 始终返回 str"""
        order = ('markdown', 'text', 'content') if use_markdown else ('text', 'content', 'markdown')
        for key in order:
            if key in data:
                v = data[key]
                return str(v) if v is not None else ''
        return ''

    @staticmethod
    def _substitute(text, variables):
        """变量替换: {user_id} -> 实际值"""
        if not variables:
            return text
        try:
            return text.format_map(_SafeDict(variables))
        except Exception:
            return text

    @staticmethod
    def _build_buttons(button_rows, variables):
        """构建按钮: [[{text,data,...},...],...]  →  [[{text,type,data,...},...],...]"""
        if not button_rows or not isinstance(button_rows, list):
            return None
        rows = []
        for row in button_rows:
            if not isinstance(row, list):
                continue
            built = [_build_single_button(b, variables) for b in row if isinstance(b, dict)]
            if built:
                rows.append(built)
        return rows or None


def _build_single_button(btn, variables):
    """构建单个按钮 dict (符合 QQ 平台 InlineKeyboard 格式)"""
    text = btn.get('text', '')
    if variables:
        with contextlib.suppress(Exception):
            text = text.format_map(_SafeDict(variables))

    result = {'text': text}

    # 类型判断: link -> type=0, 显式 type 优先, 默认 type=2
    if 'link' in btn:
        result['type'] = 0
        result['data'] = btn['link']
    else:
        result['type'] = btn.get('type', 2)
        data = btn.get('data', text)
        if variables:
            with contextlib.suppress(Exception):
                data = data.format_map(_SafeDict(variables))
        result['data'] = data

    # 可选属性
    if btn.get('enter'):
        result['enter'] = True
    if btn.get('reply'):
        result['reply'] = True
    if 'style' in btn:
        result['style'] = btn['style']
    if btn.get('admin'):
        result['permission'] = {'type': 1}
    elif 'list' in btn:
        result['permission'] = {'type': 0, 'specify_user_ids': btn['list']}

    return result


class _SafeDict(dict):
    """format_map 安全字典: 缺失的 key 保留原样 {key}"""

    def __missing__(self, key):
        return f'{{{key}}}'


# ===== 全局单例 =====
tpl = TemplateEngine()
