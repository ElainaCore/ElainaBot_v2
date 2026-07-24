"""示例功能: 媒体、卡片、按钮、交互、引用、主动消息、Web 面板等 (仅主人可用)"""

import asyncio
import os

from aiohttp import web

from core.plugin.decorators import handler, on_unload
from core.plugin.web_pages import register_page, register_route, unregister_page

IMG = "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"

# ==================== 媒体发送 ====================

@handler(r'^图片$', name='图片', desc='发送网络图片', owner_only=True)
async def send_image(event, match):
    await event.reply_image(IMG, "reply_image 方法发送")


@handler(r'^本地图片$', name='本地图片', desc='发送本地图片', owner_only=True)
async def send_local_image(event, match):
    path = os.path.join(os.path.dirname(__file__), "1.png")
    with open(path, 'rb') as f:
        await event.reply_image(f.read(), "📸 本地图片")


@handler(r'^语音$', name='语音', desc='发送语音', owner_only=True)
async def send_voice(event, match):
    await event.reply_voice(
        "https://act-upload.mihoyo.com/sr-wiki/2025/06/03/160045374/420e9ac5c0c9d2b2c44b91f453b65061_2267222992827173477.wav")


@handler(r'^视频$', name='视频', desc='发送视频', owner_only=True)
async def send_video(event, match):
    await event.reply_video("https://i.elaina.vin/1.mp4")


@handler(r'^文件$', name='文件', desc='发送文件', owner_only=True)
async def send_file(event, match):
    path = os.path.join(os.path.dirname(__file__), "1.txt")
    await event.reply_file(path, "📄 文件示例", file_name="test.txt")


# ==================== 消息类型 ====================

@handler(r'^强制md$', name='强制markdown', desc='msg_type=2 强制 markdown', owner_only=True)
async def force_markdown(event, match):
    await event.reply("# Markdown 标题\n**加粗** / [链接](https://i.elaina.vin/)", msg_type=2)


@handler(r'^强制文本$', name='强制纯文本', desc='msg_type=0 强制纯文本', owner_only=True)
async def force_text(event, match):
    await event.reply("**这段不会加粗**, markdown 语法原样显示", msg_type=0)


@handler(r'^无后缀$', name='跳过markdown后缀', desc='skip_suffix=True 跳过全局后缀', owner_only=True)
async def skip_suffix_demo(event, match):
    await event.reply("这条消息不带全局后缀", skip_suffix=True)


# ==================== 撤回 ====================

@handler(r'^撤回测试$', name='撤回测试', desc='手动撤回与自动撤回', owner_only=True)
async def test_recall(event, match):
    await event.reply("⏰ 5秒后自动撤回", auto_delete_time=5)
    await event.reply("⏰ 3秒后手动撤回...")
    await asyncio.sleep(3)
    await event.recall()


# ==================== 卡片 ====================

@handler(r'^ark23$', name='ark23', desc='列表卡片', owner_only=True)
async def send_ark23(event, match):
    await event.reply_ark(23, (
        "列表卡片示例", "ElainaBot",
        [['功能1: 图片'], ['功能2: 语音'], ['功能3: 视频', 'https://i.elaina.vin/api/']]))


@handler(r'^ark24$', name='ark24', desc='文本+图片卡片', owner_only=True)
async def send_ark24(event, match):
    await event.reply_ark(24, (
        "功能强大的QQ机器人", "机器人信息", "ElainaBot", "支持插件化开发",
        IMG, "https://i.elaina.vin/api/", "QQ Bot"))


@handler(r'^ark37$', name='ark37', desc='大图文卡片', owner_only=True)
async def send_ark37(event, match):
    await event.reply_ark(37, (
        "系统通知", "状态更新", "新功能上线", IMG, "https://i.elaina.vin/api/"))


@handler(r'^图文卡片$', name='图文卡片', desc='card 卡片消息 (tuwen)', owner_only=True)
async def send_card(event, match):
    # tuwen 元组简写: (标题, 描述, 图片URL, 跳转URL)
    await event.reply_card('tuwen', (
        "QQ开放平台", "2分钟完成注册并创建QQBot", IMG, "https://q.qq.com/#/"))
    # 也可传自定义 card_type 与字段 dict, 原样作为 card.content 发送:
    # await event.reply_card('xxx', {'title': '...', 'custom_field': '...'})


# ==================== 按钮 ====================

BUTTONS = [
    # type: 0=跳转链接 / 1=回调 / 2=输入指令 / 4=订阅 (link 等同 type=0)
    [{'text': '点我回调', 'data': 'callback_1', 'type': 1},
     {'text': '输入框', 'data': '/帮助', 'type': 2}],
    [{'text': '打开链接', 'link': 'https://i.elaina.vin/'}],
    # style: 0=灰框 / 1=蓝框蓝字(默认) / 2=黑框(PC 端气泡) / 3=黑框红字 / 4=蓝底白字
    [{'text': '灰框', 'data': 's0', 'type': 1, 'style': 0},
     {'text': '蓝框蓝字', 'data': 's1', 'type': 1, 'style': 1},
     {'text': '黑框', 'data': 's2', 'type': 1, 'style': 2},
     {'text': '黑框红字', 'data': 's3', 'type': 1, 'style': 3},
     {'text': '蓝底白字', 'data': 's4', 'type': 1, 'style': 4}],
    # 订阅按钮 (type=4): 需挂在 markdown 消息 (msg_type=2) 上发送
    [{'text': '订阅', 'show': '已订阅',
      'subscribe': '102134274_1749040268',  # 替换为机器人 Markdown 模板 id
      'modal': {'content': '确认订阅？', 'confirm_text': '✔️确认', 'cancel_text': '❌取消'},
      'tips': '请升级QQ版本'}],
]


@handler(r'^按钮$', name='按钮示例', desc='发送带按钮的消息 (含订阅按钮)', owner_only=True)
async def send_buttons(event, match):
    await event.reply("📌 按钮功能演示", buttons=BUTTONS, msg_type=2)


@handler(r'^小按钮$', name='小按钮示例', desc='键盘级 font_size=small', owner_only=True)
async def send_small_buttons(event, match):
    await event.reply("📌 小按钮演示", buttons={'rows': BUTTONS, 'font_size': 'small'}, msg_type=2)


@handler(r'', name='订阅状态事件', desc='用户订阅/取消订阅时触发', event_types=['SUBSCRIBE_MESSAGE_STATUS'])
async def on_subscribe_status(event, match):
    # 框架已自动把 (模板ID ↔ 群/用户, subscribe_id) 入库, 这里仅演示读取
    for r in event.subscribe_results:
        _ = r.get('subscribe_id')


@handler(r'^订阅消息\s+(\S+)$', name='订阅消息推送示例', desc='向指定群推送订阅消息', owner_only=True)
async def send_subscribe_message(event, match):
    # 推送订阅消息 = 发消息时带上 subscribe_id, 不带则按普通主动消息推送
    markdown_id = '102134274_1749040268'
    group_id = match.group(1)
    ls = _get_log_service(event)
    if not ls:
        return await event.reply('❌ 服务不可用')
    subs = ls.subscribe_get_by_target(group_id)
    t = next((x for x in subs if x['template_id'] == markdown_id), None)
    if not t:
        return await event.reply('📭 该群未订阅此模板')
    ok, data, _ = await event.send_to_group(
        group_id, '🔔 这是一条订阅消息推送', subscribe_id=t['subscribe_id'])
    if ok and t['sub_type'] == 'once':  # 单次订阅发送后作废
        await ls.subscribe_consume(markdown_id, group_id)
    await event.reply('✅ 订阅消息已发送' if ok else f'❌ 发送失败: {data}')


# ==================== 交互回调 ====================
# 回调按钮 (type=1) 被点击时下发 INTERACTION_CREATE 事件, event.content 即按钮 data

@handler(r'^交互按钮$', name='交互按钮', desc='发送回调按钮', owner_only=True)
async def send_interaction_button(event, match):
    await event.reply("📌 点击下方按钮触发交互回调",
                      buttons=[[{'text': '点我回调', 'data': 'demo_ack', 'type': 1}]])


@handler(r'^demo_ack$', name='交互回调演示', desc='处理回调按钮点击', event_types=['INTERACTION_CREATE'])
async def on_demo_ack(event, match):
    event.set_callback_code(0)  # 应答这次交互


# ==================== 生命周期事件 ====================
# 正则 r'' 对生命周期事件恒匹配; 这类事件无消息文本, 不要依赖 match 分组

@handler(r'', name='用户入群回复', desc='新成员入群处理示例（默认不发送）', event_types=['GROUP_MEMBER_ADD'])
async def on_group_member_add(event, match):
    # 如需启用入群欢迎，取消下方注释:
    # await event.reply(f"欢迎新成员加入本群！🎉 你的群标识: {event.user_id}")
    pass


@handler(r'', name='用户退群示例', desc='成员退群处理示例', event_types=['GROUP_MEMBER_REMOVE'])
async def on_group_member_remove(event, match):
    pass  # 用户已离开无法回复, 通常用于记录/通知管理群


# ==================== 引用消息 ====================

@handler(r'^引用当前$', name='引用当前消息', desc='引用你发的这条消息回复', owner_only=True)
async def reply_reference_current(event, match):
    await event.reply("这条回复引用了你刚发的消息", message_reference_id=event.message_reference_id)


@handler(r'^引用刚发送$', name='引用刚发送消息', desc='引用机器人刚发出的消息', owner_only=True)
async def reply_reference_sent(event, match):
    data = await event.reply("第一条消息")
    ref_id = (data or {}).get('ext_info', {}).get('ref_idx', '')
    await event.reply("这条引用了上面第一条", message_reference_id=ref_id)


# ==================== 分享链接 ====================

def _get_log_service(event):
    from core.bot.manager import _bot_manager_ref
    bot = _bot_manager_ref.get_bot(event.appid) if _bot_manager_ref else None
    return bot.log_service if bot else None


@handler(r'^申请邀请链接$', name='申请邀请链接', desc='生成专属邀请链接')
async def get_share_link(event, match):
    link = await event.sender.get_share_link(event.user_id)
    await event.reply(f"🔗 你的专属邀请链接：\n{link}" if link else "❌ 生成邀请链接失败")


@handler(r'^查询邀请数量$', name='查询邀请数量', desc='查看自己邀请了多少用户')
async def query_share_count(event, match):
    ls = _get_log_service(event)
    if not ls:
        return await event.reply("❌ 服务不可用")
    refs = await ls.share_get_referrals(event.user_id)
    if not refs:
        return await event.reply("📊 你还没有邀请任何用户")
    lines = [f"📊 你已成功邀请 {len(refs)} 位用户：\n"]
    lines += [f"{i}. {oid[:8]}****  来源：{ls.get_scene_name(scene)}"
              for i, (oid, scene) in enumerate(refs.items(), 1)]
    await event.reply('\n'.join(lines))


@handler(r'^查询我被谁邀请$', name='查询我被谁邀请', desc='查看谁邀请了自己')
async def query_my_sharer(event, match):
    ls = _get_log_service(event)
    if not ls:
        return await event.reply("❌ 服务不可用")
    sid = await ls.share_find_sharer(event.user_id)
    await event.reply(f"📌 分享者：{sid[:8]}****" if sid else "📌 你不是通过邀请链接添加的")


# ==================== 群成员查询 ====================

@handler(r'^成员信息\s+(\S+)$', name='成员信息', desc='查询指定群成员详情', owner_only=True, group_only=True)
async def query_group_member(event, match):
    member = await event.sender.get_group_member(event.group_id, match.group(1))
    if not member:
        return await event.reply("❌ 查询失败或成员不存在")
    await event.reply(
        f"👤 群昵称: {member.get('username', '')}\n"
        f"🎖️ 身份: {member.get('member_role', '')}\n"
        f"📅 入群时间: {member.get('joined_at', '')}")


@handler(r'^机器人信息$', name='机器人群内信息', desc='查询机器人自身群内信息', owner_only=True, group_only=True)
async def query_bot_member(event, match):
    member = await event.sender.get_bot_member(event.group_id)
    if not member:
        return await event.reply("❌ 查询失败")
    role = member.get('member_role', '')
    await event.reply(
        f"🤖 群昵称: {member.get('username', '')}\n"
        f"🎖️ 身份: {role}\n"
        f"👮 是否管理员: {'是' if role in ('admin', 'owner') else '否'}")


# ==================== 召回 ====================

@handler(r'^指定召回\s+(\S+)$', name='指定召回', desc='向指定用户发送召回消息', owner_only=True)
async def wakeup_user(event, match):
    uid = match.group(1)
    await event.send_wakeup(uid, "📢 召回消息测试")
    await event.reply(f"✅ 已向 {uid[:8]}**** 发送召回")


@handler(r'^强制召回\s+(\S+)$', name='强制召回', desc='强制向指定用户发送召回消息', owner_only=True)
async def force_wakeup_user(event, match):
    uid = match.group(1)
    await event.sender.force_wakeup(uid, "📢 强制召回测试")
    await event.reply(f"✅ 已强制召回 {uid[:8]}****")


# ==================== 主动消息 ====================

@handler(r'^全量签到$', name='签到', desc='ignore_at_check=True 无需@即可触发', ignore_at_check=True)
async def check_in(event, match):
    await event.reply("✅ 签到成功！")


@handler(r'^主动测试$', name='主动测试', desc='3秒后发送主动消息', owner_only=True, ignore_at_check=True)
async def test_active_message(event, match):
    await event.reply("⏰ 3秒后将发送主动消息...")
    await asyncio.sleep(3)
    if event.is_group:
        await event.send_to_group(event.group_id, "🎉 主动群消息")
    else:
        await event.send_to_user(event.user_id, "🎉 主动私聊消息")


@handler(r'^主动私聊\s+(\S+)\s+(.+)$', name='主动私聊', desc='向指定用户发送主动消息', owner_only=True)
async def test_send_to_user(event, match):
    uid, content = match.group(1), match.group(2)
    await event.send_to_user(uid, content)
    await event.reply(f"✅ 已发送给 {uid[:8]}****")


@handler(r'^主动群发\s+(\S+)\s+(.+)$', name='主动群发', desc='向指定群发送主动消息', owner_only=True)
async def test_send_to_group(event, match):
    gid, content = match.group(1), match.group(2)
    await event.send_to_group(gid, content)
    await event.reply(f"✅ 已发送到群 {gid[:8]}****")


@handler(r'^无event群发\s+(\S+)\s+(.+)$', name='无event主动群发',
         desc='不借助 event, 直接用 sender 推送', owner_only=True)
async def send_without_event(event, match):
    # 没有 event 的场景 (定时任务、@on_load 后台循环) 取一个 sender 直接调用
    from core.bot.manager import _bot_manager_ref
    sender = next(iter(_bot_manager_ref._bots.values())).sender
    await sender.send_to_group(match.group(1), match.group(2))
    await event.reply("✅ 已通过 sender 发送")


@handler(r'^主动图片$', name='主动图片', desc='向当前会话主动发送图片', owner_only=True)
async def test_send_image_proactive(event, match):
    kw = {'target_group_id': event.group_id} if event.is_group else {'target_user_id': event.user_id}
    await event.reply_image(IMG, "📸 主动图片", **kw)


# ==================== Web 面板扩展 ====================

register_page(
    key='examples-page',
    label='示例页面',
    source='plugin',
    source_name='alone',
    html='<h1>自定义侧边栏页面</h1>'
         '<p>由 plugins/alone/示例插件.py 通过 register_page(key, label, html=...) 注册, '
         '插件卸载时自动注销。</p>',
)


@on_unload
def _unload_web_pages():
    unregister_page('examples-page')


# 自定义 HTTP 路由: 路径需以 /api/ext/ 开头, 插件卸载时自动注销
@register_route('GET', '/api/ext/alone/ping', auth=False)  # 免验证
async def api_ping(request):
    return web.json_response({'ok': True, 'plugin': 'alone'})


@register_route('POST', '/api/ext/alone/echo')  # 默认 auth=True, 需 Authorization: Bearer <token>
async def api_echo(request):
    body = await request.json()
    return web.json_response({'ok': True, 'you_sent': body})


@handler(r'^面板推送$', name='面板推送', desc='向 Web 面板推送一条自定义日志', owner_only=True)
async def push_to_panel(event, match):
    from datetime import datetime

    import web.ws as ws
    ws.push_log('framework', {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': '来自插件的推送测试',
        'source': 'plugin.examples',
        'level': 'INFO',
    })
    await event.reply("✅ 已推送日志到 Web 面板「日志」页")


# ==================== block 拦截 ====================
# 相同指令多处理器时, 高优先级 block=True 命中即拦截低优先级

@handler(r'^拦截示例$', name='拦截示例-高优先级', desc='block=True 命中即拦截', priority=10, block=True)
async def block_demo_high(event, match):
    await event.reply("🛑 高优先级 (block=True): 已拦截, 低优先级不会触发")


@handler(r'^拦截示例$', name='拦截示例-低优先级', desc='被高优先级 block 拦截', priority=0)
async def block_demo_low(event, match):
    await event.reply("⬇️ 你不应该看到这条 (已被 block 拦截)")
