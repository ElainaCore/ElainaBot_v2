#!/usr/bin/env python
"""按钮 / 键盘构建辅助"""

from core.base.config import cfg


def build_keyboard(button_rows, appid=None):
    """按钮行列表 → QQ InlineKeyboard 结构"""
    button_enter_to_send = (
        cfg.get_bot_setting(appid, "message.button_enter_to_send", False)
        if appid
        else False
    )

    rows = []
    for row in button_rows:
        buttons = []
        for btn in row:
            b = {
                "id": str(len(buttons)),
                "render_data": {
                    "label": btn.get("text", ""),
                    "visited_label": btn.get("show", btn.get("text", "")),
                    "style": btn.get("style", 1),
                },
                "action": {
                    "type": btn.get("type", 2),
                    "data": btn.get("data", ""),
                },
            }
            # link 优先 (覆盖 type/data)
            if "link" in btn:
                b["action"]["type"] = 0
                b["action"]["data"] = btn["link"]

            # enter 行为: button_enter_to_send 配置开启时, type=2+enter -> type=1
            if btn.get("enter"):
                act = b["action"]
                if button_enter_to_send and act["type"] == 2:
                    act["type"] = 1
                else:
                    act["enter"] = True

            if btn.get("reply"):
                b["action"]["reply"] = True

            # 权限: 显式 permission > role > list > admin > 默认所有人
            if "permission" in btn:
                b["action"]["permission"] = btn["permission"]
            elif "role" in btn:
                b["action"]["permission"] = {"type": 3, "specify_role_ids": btn["role"]}
            elif "list" in btn:
                b["action"]["permission"] = {"type": 0, "specify_user_ids": btn["list"]}
            elif btn.get("admin"):
                b["action"]["permission"] = {"type": 1}
            else:
                b["action"]["permission"] = {"type": 2}

            # 点击次数限制
            if "limit" in btn:
                b["action"]["click_limit"] = btn["limit"]

            # 不支持时提示
            if "tips" in btn:
                b["action"]["unsupport_tips"] = btn["tips"]

            buttons.append(b)
        rows.append({"buttons": buttons})
    return {"content": {"rows": rows}}


def build_prompt_keyboard(prompt_buttons):
    """构建 prompt_keyboard 扩展按钮 (最多3个)"""
    if not prompt_buttons:
        return None
    if isinstance(prompt_buttons, dict):
        return {"keyboard": prompt_buttons}
    items = (
        [prompt_buttons]
        if not isinstance(prompt_buttons, list | tuple)
        else list(prompt_buttons)
    )
    action = {"type": 2, "data": "elaina", "enter": True}
    buttons = []
    for i, btn in enumerate(items[:3]):
        if isinstance(btn, str):
            buttons.append(
                {
                    "id": str(i + 1),
                    "render_data": {"label": btn, "visited_label": btn, "style": 1},
                    "action": action,
                }
            )
        elif isinstance(btn, list | tuple):
            label = btn[0] if btn else ""
            style = btn[1] if len(btn) > 1 else 1
            buttons.append(
                {
                    "id": str(i + 1),
                    "render_data": {
                        "label": label,
                        "visited_label": label,
                        "style": style,
                    },
                    "action": action,
                }
            )
        elif isinstance(btn, dict):
            btn.setdefault("id", str(i + 1))
            btn.setdefault("action", action)
            buttons.append(btn)
    if buttons:
        return {"keyboard": {"content": {"rows": [{"buttons": buttons}]}}}
    return None


_ARK_KEYS = {
    24: (
        "#DESC#",
        "#PROMPT#",
        "#TITLE#",
        "#METADESC#",
        "#IMG#",
        "#LINK#",
        "#SUBTITLE#",
    ),
    37: ("#PROMPT#", "#METATITLE#", "#METASUBTITLE#", "#METACOVER#", "#METAURL#"),
}


def convert_simple_ark_data(template_id, simple_data):
    """简化 ARK 数据 -> 完整 kv 格式 (template_id 23/24/37)"""
    keys = _ARK_KEYS.get(template_id)
    if keys:
        return [
            {"key": keys[i], "value": str(v)}
            for i, v in enumerate(simple_data)
            if i < len(keys) and v is not None
        ]
    if template_id == 23:
        return _build_ark23(simple_data)
    return simple_data


def _build_ark23(simple_data):
    kv = []
    for i, key in enumerate(("#DESC#", "#PROMPT#")):
        if len(simple_data) > i and simple_data[i] is not None:
            kv.append({"key": key, "value": str(simple_data[i])})
    if len(simple_data) > 2 and simple_data[2] is not None:
        obj_list = _build_ark23_list(simple_data[2])
        if obj_list:
            kv.append({"key": "#LIST#", "obj": obj_list})
    return kv


def _build_ark23_list(items):
    obj_list = []
    for item in items:
        if not item:
            continue
        obj_kv = []
        desc = str(item[0]).strip() if item[0] else ""
        if desc:
            obj_kv.append({"key": "desc", "value": desc})
        if len(item) > 1:
            link = str(item[1]).strip() if item[1] else ""
            if link:
                obj_kv.append({"key": "link", "value": link})
        if obj_kv:
            obj_list.append({"obj_kv": obj_kv})
    return obj_list
