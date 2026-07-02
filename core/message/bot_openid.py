"""Bot OpenID 缓存 — 记录各 appid 机器人的艾特 id, 持久化到 JSON"""

import json
import os
import re

_AT = re.compile(r'<@!?([^>]+)>')
_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data',
    'bot_openids.json',
)

# {appid: {'ids': set[str], 'done': bool}}
_data: dict[str, dict] = {}


def _entry(appid):
    return _data.setdefault(appid, {'ids': set(), 'done': False})


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        out = {a: {'ids': sorted(e['ids']), 'done': e['done']} for a, e in _data.items()}
        with open(_FILE, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass


def _load():
    """启动时加载 (兼容旧格式 "openid" 字符串 / ["id"...] 列表 / {"ids":[...],"done":bool})"""
    if not os.path.isfile(_FILE):
        return
    try:
        with open(_FILE, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return
    for appid, v in raw.items():
        if isinstance(v, str):
            ids, done = [v], False
        elif isinstance(v, dict):
            ids, done = v.get('ids', []), bool(v.get('done'))
        else:
            ids, done = v, False
        _data[appid] = {'ids': {i for i in ids if i}, 'done': done}


_load()


def add(appid, openid):
    """登记一个 bot id (真实 id, 来自 mentions[].id)"""
    if not appid or not openid:
        return
    e = _entry(appid)
    if openid not in e['ids']:
        e['ids'].add(openid)
        _save()


def first_id(appid):
    """取该 appid 的第一个 openid (按字典序), 用于请求群成员接口"""
    e = _data.get(appid)
    if not e or not e['ids']:
        return ''
    return sorted(e['ids'])[0]


def is_done(appid):
    """该 appid 是否已记录齐全, 无需再判断"""
    e = _data.get(appid)
    return bool(e and e['done'])


def learn(appid, content):
    """全量「仅艾特机器人」: 把 content 里的 (虚拟) id 一并记下并标记完成"""
    if not appid:
        return
    e = _entry(appid)
    e['ids'].update(_AT.findall(content))
    e['done'] = True
    _save()


def strip_self_at(appid, content):
    """移除 content 中本机器人的所有 <@id>"""
    e = _data.get(appid)
    if not e:
        return content
    for i in e['ids']:
        content = content.replace(f'<@{i}>', '')
    return content.strip()
