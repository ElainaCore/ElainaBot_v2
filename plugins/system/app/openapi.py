"""开放平台查询: 扫码登录 / bot通知 / bot列表 / bot数据 / 切换appid"""

import asyncio
import contextlib
import json
import os
import time

from core.base.config import cfg
from core.base.logger import PLUGIN, get_logger
from core.plugin.decorators import handler, on_load

from ._reply import reply, sender_reply

log = get_logger(PLUGIN, '开放平台')

# ==================== 数据管理 ====================

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_DIR = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
# 旧开放平台凭证: web/open/openapi.json (与 web 面板 handler 一致)
_OLD_OPEN_DIR = os.path.join(_BASE_DIR, 'web', 'open')
os.makedirs(_OLD_OPEN_DIR, exist_ok=True)

_DATA_FILE = os.path.join(_OLD_OPEN_DIR, 'openapi.json')

_user_data = {}  # {user_id: login_data}
_login_tasks = {}  # {user_id: (timestamp, qr)}
_last_login_time = {}  # {user_id: timestamp}  防重复登录

_api = None


def _get_api():
    global _api
    if _api is None:
        try:
            from web.tools._bot.api import get_bot_api

            _api = get_bot_api()
        except ImportError:
            _api = None
    return _api


def _load_data():
    global _user_data
    try:
        if os.path.exists(_DATA_FILE):
            with open(_DATA_FILE, encoding='utf-8') as f:
                _user_data = json.load(f)
    except Exception:
        _user_data = {}


def _save_data():
    try:
        os.makedirs(_OLD_OPEN_DIR, exist_ok=True)
        with open(_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(_user_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _get_ud(user_id):
    """获取用户登录数据，不存在返回 None"""
    if not _user_data:
        _load_data()
    return _user_data.get(user_id)


def _save_ud(user_id, data):
    _user_data[user_id] = data
    _save_data()


def _use_md(event):
    return cfg.get_bot_setting(event.appid, 'message.use_markdown', True)


def _owner_ids(event):
    bot_cfg = cfg.get_bot_config(event.appid)
    return bot_cfg.get('owner_ids', []) if bot_cfg else []


def _nav_buttons():
    """通用导航按钮行"""
    return [
        [
            {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
            {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
            {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
        ]
    ]


def _login_button():
    return [[{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}]]


async def _reply_not_logged_in(event):
    content = f'<@{event.user_id}> 未查询到你的登录信息'
    if _use_md(event):
        await reply(event, content, buttons=_login_button())
    else:
        await reply(event, content)


async def _reply_expired(event):
    content = f'<@{event.user_id}>登录状态失效'
    if _use_md(event):
        await reply(event, content, buttons=_login_button())
    else:
        await reply(event, content)


@on_load
def _init():
    _load_data()
    log.info('开放平台查询插件已加载')


# ==================== 管理登录 ====================


@handler(r'^管理登录$', name='管理登录', desc='扫码登录QQ开放平台')
async def login(event, match):
    api = _get_api()
    if not api:
        await reply(event, 'bot_api 模块未加载，无法登录')
        return

    user_id = event.user_id
    now = time.time()

    # 防重复
    if user_id in _last_login_time and now - _last_login_time[user_id] < 20:
        return
    if user_id in _login_tasks and now - _login_tasks[user_id][0] < 15:
        await reply(event, '15秒内你已经申请一次登录了，请稍后重试。')
        return

    _login_tasks[user_id] = (now, None)

    data = await api.create_login_qr()
    url = data.get('url')
    qr = data.get('qr')
    if not url or not qr:
        await reply(event, '获取登录二维码失败，请稍后重试')
        _login_tasks.pop(user_id, None)
        return

    content = f'<@{user_id}>\n[QQ开发平台管理端登录]\n登录具有时效性，请尽快登录\n\n>当你选择登录，代表你已经同意将数据托管给伊蕾娜Bot。'

    if _use_md(event):
        login_btn = {'text': '点击登录', 'data': url, 'type': 0, 'style': 4}
        if event.is_group:
            login_btn['list'] = [user_id]
        await reply(event, content, buttons=[[login_btn]])
    else:
        display_url = url
        if '://' in url:
            protocol, rest = url.split('://', 1)
            domain, separator, path = rest.partition('/')
            if '.' in domain:
                segments = domain.split('.')
                segments[-1] = segments[-1].upper()
                domain = '.'.join(segments)
            display_url = f'{protocol}://{domain}{separator}{path}'
        await reply(event, f'{content}\n\n登录链接: {display_url}')

    # 后台轮询登录状态 (handler 返回后框架会清空 event._sender, 需在此提前捕获)
    sender = event._sender
    use_md = _use_md(event)
    asyncio.create_task(_poll_login(event, sender, user_id, qr, use_md))


async def _poll_login(event, sender, user_id, qr, use_md):
    api = _get_api()
    deadline = time.time() + 60
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            res = await api.get_qr_login_info(qrcode=qr)
            if res.get('code') == 0:
                login_data = res.get('data', {}).get('data', {})
                login_data['type'] = 'ok'
                _save_ud(user_id, login_data)

                app_type = login_data.get('appType')
                app_type_str = '小程序' if app_type == '0' else '机器人' if app_type == '2' else '未知'
                content = (
                    f'[{login_data.get("uin")}]登录成功\n\n'
                    f'>登录类型：{app_type_str}\nAppId：{login_data.get("appId")}\n切换+appid可以切换机器人'
                )
                buttons = _nav_buttons() if use_md else None
                try:
                    await sender_reply(sender, event, content, buttons=buttons)
                except Exception as e:
                    log.warning(f'登录成功回复失败: {e}')

                _last_login_time[user_id] = time.time()
                _login_tasks.pop(user_id, None)
                return
        except Exception as e:
            log.debug(f'轮询登录态异常: {e}')

    # 超时
    with contextlib.suppress(Exception):
        await sender_reply(sender, event, f'<@{user_id}>登录失效，请重新尝试')
    _login_tasks.pop(user_id, None)


# ==================== bot通知 ====================


@handler(r'^bot通知$', name='bot通知', desc='查看开放平台私信通知')
async def get_message(event, match):
    api = _get_api()
    if not api:
        return
    ud = _get_ud(event.user_id)
    if not ud:
        await _reply_not_logged_in(event)
        return

    res = await api.get_private_messages(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))

    if res.get('code') != 0:
        await _reply_expired(event)
        return

    msglist = [f'Uin:{ud.get("uin")}\nAppid:{ud.get("appId")}\n\n```python']
    messages = res.get('messages', [])
    for j in range(min(len(messages), 8)):
        if j > 0:
            msglist.append('——————')
        message_content = messages[j].get('content', '').split('\n\n')[0].strip()
        message_time = messages[j].get('send_time', '')
        msglist.append(message_content)
        msglist.append(message_time)
    msglist.append('\n```\n')
    content = '\n'.join(msglist)

    if _use_md(event):
        await reply(event, content, buttons=_nav_buttons())
    else:
        await reply(event, content)


# ==================== bot列表 ====================


@handler(r'^bot列表$', name='bot列表', desc='查看已绑定的机器人列表')
async def get_botlist(event, match):
    api = _get_api()
    if not api:
        return
    ud = _get_ud(event.user_id)
    if not ud:
        await _reply_not_logged_in(event)
        return

    res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))

    if res.get('code') != 0:
        await _reply_expired(event)
        return

    msglist = [f'Uin:{ud.get("uin")}']
    apps = res.get('data', {}).get('apps', [])
    for j in range(len(apps)):
        if j > 0:
            msglist.append('')
        app_name = apps[j].get('app_name', '')
        app_id = apps[j].get('app_id', '')
        app_desc = apps[j].get('app_desc', '')
        msglist.append(f'<qqbot-cmd-input text="切换appid+{app_id}" show="{app_id}/{app_name}" />')
        if app_desc:
            quoted_desc = app_desc.replace('\n', '\n> ')
            msglist.append(f'> 介绍：{quoted_desc}')
    content = '\n'.join(msglist)

    if _use_md(event):
        await reply(event, content, buttons=_nav_buttons())
    else:
        await reply(event, content)


# ==================== bot数据 ====================


@handler(r'^bot数据(\d+|max)$', name='bot数据', desc='查看bot消息/群/好友数据统计')
async def get_botdata(event, match):
    api = _get_api()
    if not api:
        return
    ud = _get_ud(event.user_id)
    if not ud:
        await _reply_not_logged_in(event)
        return

    days = match.group(1)
    cred = dict(
        uin=ud.get('uin'),
        quid=ud.get('developerId'),
        ticket=ud.get('ticket'),
        appid=ud.get('appId'),
    )

    data1 = await api.get_bot_data(**cred, data_type=1)
    data2 = await api.get_bot_data(**cred, data_type=2)
    data3 = await api.get_bot_data(**cred, data_type=3)

    if any(x.get('retcode', -1) != 0 for x in [data1, data2, data3]):
        await _reply_expired(event)
        return

    msg_data = data1.get('data', {}).get('msg_data', [])
    group_data = data2.get('data', {}).get('group_data', [])
    friend_data = data3.get('data', {}).get('friend_data', [])

    def fmt(data_list, index):
        item = data_list[index] if index < len(data_list) else {}
        return {
            '报告日期': item.get('报告日期', '0'),
            '上行消息量': item.get('上行消息量', '0'),
            '上行消息人数': item.get('上行消息人数', '0'),
            '下行消息量': item.get('下行消息量', '0'),
            '总消息量': item.get('总消息量', '0'),
            '现有群组': item.get('现有群组', '0'),
            '已使用群组': item.get('已使用群组', '0'),
            '新增群组': item.get('新增群组', '0'),
            '移除群组': item.get('移除群组', '0'),
            '现有好友数': item.get('现有好友数', '0'),
            '已使用好友数': item.get('已使用好友数', '0'),
            '新增好友数': item.get('新增好友数', '0'),
            '移除好友数': item.get('移除好友数', '0'),
        }

    def day_str(index):
        prefix = '' if index == 0 else '————————\n'
        m = fmt(msg_data, index)
        g = fmt(group_data, index)
        f = fmt(friend_data, index)
        return (
            f'{prefix}【日期：{m["报告日期"]}】\n'
            f'消息统计:\n上行：{m["上行消息量"]}  人数：{m["上行消息人数"]}\n'
            f'总量：{m["总消息量"]}  下行：{m["下行消息量"]}\n'
            f'群组统计：\n新增：{g["新增群组"]}  减少：{g["移除群组"]}\n'
            f'已有：{g["现有群组"]}  使用：{g["已使用群组"]}\n'
            f'好友统计：\n新增：{f["新增好友数"]}  减少：{f["移除好友数"]}\n'
            f'已有：{f["现有好友数"]}  使用：{f["已使用好友数"]}'
        )

    max_days = min(len(msg_data), len(group_data), len(friend_data))
    actual_days = max_days if days == 'max' else min(int(days), max_days)

    total_up = 0
    for i in range(len(msg_data)):
        total_up += int(fmt(msg_data, i)['上行消息人数'])
    avg_dau = f'{total_up / 30:.2f}' if msg_data else '0'

    day_list = [day_str(i) for i in range(actual_days)]
    msglist = [
        f'Uid：{ud.get("uin")}\nappid：{ud.get("appId")}\n30天平均DAU: {avg_dau}\n\n```python',
        *day_list,
        '\n```\n',
    ]
    content = '\n'.join(msglist)

    if _use_md(event):
        await reply(event, content, buttons=_nav_buttons())
    else:
        await reply(event, content)


# ==================== 切换appid ====================


@handler(r'^切换appid\s*(.+)$', name='切换appid', desc='切换当前操作的机器人AppID')
async def switch_appid(event, match):
    api = _get_api()
    if not api:
        return
    ud = _get_ud(event.user_id)
    if not ud:
        await _reply_not_logged_in(event)
        return

    new_appid = match.group(1).strip()
    if not new_appid:
        await reply(event, '请提供有效的AppID')
        return

    current_appid = ud.get('appId')
    if current_appid == new_appid:
        await reply(event, f'当前已经是使用AppID: {current_appid}')
        return

    # 验证 AppID 是否有效
    res = await api.get_bot_list(uin=ud.get('uin'), quid=ud.get('developerId'), ticket=ud.get('ticket'))

    if res.get('code') != 0:
        await _reply_expired(event)
        return

    apps = res.get('data', {}).get('apps', [])
    app_name = ''
    valid = False
    for app in apps:
        if app.get('app_id') == new_appid:
            valid = True
            app_name = app.get('app_name', '未命名机器人')
            break

    if not valid:
        lines = [f'{i}. {a.get("app_name", "未命名")}: {a.get("app_id")}' for i, a in enumerate(apps, 1)]
        await reply(event, f'提供的AppID无效，请从以下可用AppID中选择：\n\n```python\n{chr(10).join(lines)}\n```\n')
        return

    old_appid = ud.get('appId')
    ud['appId'] = new_appid
    _save_ud(event.user_id, ud)

    content = f'AppID已切换成功\n\n```python\n原AppID: {old_appid}\n新AppID: {new_appid}\n机器人: {app_name}\n```\n'

    if _use_md(event):
        await reply(event, content, buttons=_nav_buttons())
    else:
        await reply(event, content)
