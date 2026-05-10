#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板引擎 - 消息模板 + 按钮构建, 热加载

从 config/templates.yaml 加载系统消息模板,
支持变量替换和按钮自动构建。

用法:
    from core.template import tpl
    content, buttons = tpl.render("welcome", user_id="xxx", appid="123")
    content, buttons = tpl.render("user_welcome", user_id="xxx", use_markdown=True)
"""

from core.base.config import cfg
from core.base.logger import get_logger, FRAMEWORK

log = get_logger(FRAMEWORK, "模板引擎")


class TemplateEngine:
    """消息模板引擎

    模板格式:
        纯文本: "你好 {user_id}"
        带按钮:
            content: "你好"
            buttons: [[{text: "菜单", data: "/菜单", enter: true, style: 1},{text: "帮助"}],[{text: "链接", link: "..."}]]
        MD分离:
            markdown: "**你好**"
            text: "你好"
            buttons: ...
    """

    def render(self, template_name, use_markdown=False, appid=None, **variables):
        """渲染模板

        Args:
            template_name: 模板名 (templates.yaml 中的 key)
            use_markdown:  是否优先使用 markdown 版本
            appid:         机器人 APPID (用于匹配专属模板)
            **variables:   变量 (user_id, user_count, reason, appid 等)

        Returns:
            (content_str, buttons_list_or_None)
            buttons 格式: [[{text, data/link, enter, style, ...}, ...], ...]
        """
        if appid:
            variables.setdefault('appid', appid)
        raw = self._resolve_template(template_name, appid)
        if raw is None:
            return f"[模板缺失: {template_name}]", None

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
        """按 appid 解析模板: 列表形式按 appid 匹配, 无 appid 作为兜底

        templates.yaml 支持格式:
            welcome: "通用"                      # 所有机器人共用
            welcome:                             # 列表: 按 appid 匹配
              - appid: "102134274"
                content: "专属"
              - content: "兜底"                  # 无 appid 条目为通用
        """
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
        """选取 markdown 或 text 或 content"""
        order = ('markdown', 'text', 'content') if use_markdown else ('text', 'content', 'markdown')
        for key in order:
            if key in data:
                return data[key]
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
        try:
            text = text.format_map(_SafeDict(variables))
        except Exception:
            pass

    result = {'text': text}

    # 类型判断: link -> type=0, data -> type=2
    if 'link' in btn:
        result['type'] = 0
        result['data'] = btn['link']
    else:
        result['type'] = 2
        data = btn.get('data', text)
        if variables:
            try:
                data = data.format_map(_SafeDict(variables))
            except Exception:
                pass
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
