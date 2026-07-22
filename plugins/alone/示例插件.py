"""示例功能: 媒体发送、ark卡片、markdown、撤回、主动消息、按钮等 (仅主人可用)"""

import asyncio
import os

from aiohttp import web

from core.plugin.decorators import handler, on_unload
from core.plugin.web_pages import register_page, register_route, unregister_page

# ==================== 媒体发送示例 ====================

@handler(r'^图片$', name='图片', desc='发送网络图片示例', owner_only=True)
async def send_image(event, match):
    await event.reply_image(
        "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
        "reply_image 方法发送")


@handler(r'^本地图片$', name='本地图片', desc='发送本地图片示例', owner_only=True)
async def send_local_image(event, match):
    # 读取插件目录下的 1.png 以 bytes 发送
    path = os.path.join(os.path.dirname(__file__), "1.png")
    with open(path, 'rb') as f:
        await event.reply_image(f.read(), "📸 本地图片")


@handler(r'^语音$', name='语音', desc='发送语音示例', owner_only=True)
async def send_voice(event, match):
    await event.reply_voice(
        "https://act-upload.mihoyo.com/sr-wiki/2025/06/03/160045374/420e9ac5c0c9d2b2c44b91f453b65061_2267222992827173477.wav")


@handler(r'^视频$', name='视频', desc='发送视频示例', owner_only=True)
async def send_video(event, match):
    await event.reply_video("https://i.elaina.vin/1.mp4")


@handler(r'^文件$', name='文件', desc='发送文件示例', owner_only=True)
async def send_file(event, match):
    # file_name 是对方看到的文件名
    path = os.path.join(os.path.dirname(__file__), "1.txt")
    await event.reply_file(path, "📄 文件示例", file_name="test.txt")


# ==================== 消息类型示例 (强制 markdown / 纯文本) ====================
# 默认按 bot.yaml 的 message.use_markdown 决定发送类型, 传 msg_type 可单条强制覆盖。

@handler(r'^强制md$', name='强制markdown', desc='无视全局配置, 强制以 markdown 发送', owner_only=True)
async def force_markdown(event, match):
    # msg_type=2: markdown
    await event.reply(
        "# Markdown 标题\n**加粗** / *斜体* / [链接](https://i.elaina.vin/)",
        msg_type=2)


@handler(r'^强制文本$', name='强制纯文本', desc='无视全局配置, 强制以纯文本发送', owner_only=True)
async def force_text(event, match):
    # msg_type=0: 纯文本
    await event.reply("**这段不会加粗**, markdown 语法原样显示", msg_type=0)


@handler(r'^无后缀$', name='跳过markdown后缀', desc='跳过全局 markdown_suffix 后缀', owner_only=True)
async def skip_suffix_demo(event, match):
    # skip_suffix=True: 本条不拼接 bot.yaml 中 message.markdown_suffix 配置的全局后缀
    await event.reply("这条消息不带全局后缀", skip_suffix=True)


@handler(r'^主动md\s+(\S+)$', name='主动markdown', desc='向指定群强制发送 markdown 主动消息', owner_only=True)
async def proactive_markdown(event, match):
    # send_to_group / send_to_user 同样支持 msg_type / skip_suffix
    await event.send_to_group(match.group(1), "# 主动 Markdown 消息", msg_type=2)
    await event.reply("✅ 已发送")


# ==================== 撤回示例 ====================

@handler(r'^撤回测试$', name='撤回测试', desc='发送后3秒撤回', owner_only=True)
async def test_recall(event, match):
    await event.reply("⏰ 3秒后撤回...")
    await asyncio.sleep(3)
    await event.recall()


@handler(r'^自动撤回$', name='自动撤回', desc='使用 auto_delete_time 自动撤回', owner_only=True)
async def test_auto_recall(event, match):
    await event.reply("⏰ 5秒后自动撤回", auto_delete_time=5)
    await event.reply_image(
        "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
        "🖼️ 10秒后撤回", auto_delete_time=10)


# ==================== Ark 卡片示例 ====================

@handler(r'^ark23$', name='ark23', desc='ark23列表卡片示例', owner_only=True)
async def send_ark23(event, match):
    await event.reply_ark(23, (
        "列表卡片示例", "ElainaBot",
        [['功能1: 图片'], ['功能2: 语音'], ['功能3: 视频', 'https://i.elaina.vin/api/']]))


@handler(r'^ark24$', name='ark24', desc='ark24文本+图片卡片示例', owner_only=True)
async def send_ark24(event, match):
    await event.reply_ark(24, (
        "功能强大的QQ机器人", "机器人信息", "ElainaBot", "支持插件化开发",
        "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0",
        "https://i.elaina.vin/api/", "QQ Bot"))


@handler(r'^ark37$', name='ark37', desc='ark37图文卡片示例', owner_only=True)
async def send_ark37(event, match):
    await event.reply_ark(37, (
        "系统通知", "状态更新", "新功能上线",
        "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0",
        "https://i.elaina.vin/api/"))


# ==================== 按钮示例 ====================

@handler(r'^按钮$', name='按钮示例', desc='发送带按钮的消息', owner_only=True)
async def send_buttons(event, match):
    buttons = [
        # 第一行: 回调按钮 + 输入框按钮
        [
            {'text': '点我回调', 'data': 'callback_1', 'type': 1},
            {'text': '输入框', 'data': '/帮助', 'type': 2},
        ],
        # 第二行: 链接按钮
        [
            {'text': '打开链接', 'link': 'https://i.elaina.vin/'},
        ],
    ]
    await event.reply("📌 按钮功能演示", buttons=buttons)


@handler(r'^小按钮$', name='小按钮示例', desc='发送小按钮 (键盘级 font_size)', owner_only=True)
async def send_small_buttons(event, match):
    # 小按钮: buttons 传 dict, rows 放原二维数组, font_size 取 small/middle/large
    rows = [
        [
            {'text': '点我回调', 'data': 'callback_1', 'type': 1},
            {'text': '输入框', 'data': '/帮助', 'type': 2},
        ],
        [
            {'text': '打开链接', 'link': 'https://i.elaina.vin/'},
        ],
    ]
    await event.reply("📌 小按钮演示 (font_size=small)", buttons={'rows': rows, 'font_size': 'small'})


@handler(r'^订阅按钮$', name='订阅按钮示例', desc='发送订阅按钮 (type=4) 与二次确认弹窗', owner_only=True)
async def send_subscribe_buttons(event, match):
    # ⚠️ 订阅按钮必须挂在 markdown 模板消息上发送, 原生 markdown / 纯文本消息无法携带订阅按钮。
    # 框架未适配 markdown 模板, 需通过 kwargs 透传自行构建 markdown 字段。
    buttons = [
        [
            {'text': '订阅', 'show': '已订阅',
             'subscribe': '你的AppID_模板ID',  # 替换为自己机器人的订阅模板 ID
             'modal': {'content': '确认订阅？', 'confirm_text': '✔️确认', 'cancel_text': '❌取消'},
             'tips': '请升级QQ版本'},
        ],
        # modal 不限于订阅按钮: 任何类型按钮都可加二次确认弹窗 (字符串等价于 {'content': ...})
        [
            {'text': '带确认的回调', 'data': 'callback_1', 'type': 1, 'modal': '确认执行？'},
        ],
    ]
    # content 仅用于通过非空检查/日志记录, 实际展示内容由 markdown 模板参数决定
    await event.reply(
        "🔔 订阅按钮 / 二次确认演示",
        buttons=buttons,
        msg_type=2,
        markdown={
            'custom_template_id': '102134274_1749040268',  # 替换为你自己的 markdown 模板 ID
            'params': [{'key': 'text', 'values': ['🔔 订阅按钮 / 二次确认演示']}],
        },
    )


# 用户点击订阅按钮后, 平台下发 SUBSCRIBE_MESSAGE_STATUS 订阅事件, 事件中返回 subscribe_id
# (发送订阅消息的票据, 注意不是订阅事件本身的 event id)。
# 框架已自动把 (模板ID ↔ 群/用户, subscribe_id) 写入订阅表, 无需手动存储; 如需自行处理可订阅该事件:

@handler(r'', name='订阅状态事件', desc='用户订阅/取消订阅时触发', event_types=['SUBSCRIBE_MESSAGE_STATUS'])
async def on_subscribe_status(event, match):
    # event.subscribe_results: [{'custom_template_id': ..., 'op': 1允许/2拒绝, 'subscribe_id': ..., ...}, ...]
    for r in event.subscribe_results:
        _ = r.get('subscribe_id')  # 发送订阅消息用的票据 (框架已自动入库, 这里仅演示读取)


@handler(r'^订阅消息\s+(\S+)$', name='订阅消息推送示例', desc='向指定群推送订阅消息', owner_only=True)
async def send_subscribe_message(event, match):
    # 使用订阅事件返回并已入库的 subscribe_id 票据推送。
    # 推送时必须携带 subscribe_id, 不填写将按普通主动消息推送 (占用主动消息条数)。
    subscribe = '你的AppID_模板ID'  # 替换为订阅按钮 subscribe 字段使用的订阅模板 ID
    group_id = match.group(1)
    ls = _get_log_service(event)
    if not ls:
        return await event.reply('❌ 服务不可用')
    # 从订阅表中取该群的 subscribe_id
    targets = ls.subscribe_get_targets(subscribe)  # [{target_id, sub_type, subscribe_id}, ...]
    t = next((x for x in targets if x['target_id'] == group_id), None)
    if not t:
        return await event.reply('📭 该群未订阅此模板')
    ok, data, _ = await event.send_to_group(
        group_id, '🔔 这是一条订阅消息推送', subscribe_id=t['subscribe_id'])
    # 单次订阅 (sub_type='once') 发送后作废, 永久订阅可重复推送
    if ok and t['sub_type'] == 'once':
        await ls.subscribe_consume(subscribe, group_id)
    await event.reply('✅ 订阅消息已发送' if ok else f'❌ 发送失败: {data}')


# ==================== 交互回调示例 ====================
# 回调按钮 (type=1) 被点击时, 框架会下发 INTERACTION_CREATE 事件,
# event.content 就是按钮的 data。用 set_callback_code 应答这次点击。

@handler(r'^交互按钮$', name='交互按钮', desc='发送回调按钮', owner_only=True)
async def send_interaction_button(event, match):
    buttons = [[{'text': '点我回调', 'data': 'demo_ack', 'type': 1}]]
    await event.reply("📌 点击下方按钮触发交互回调", buttons=buttons)


@handler(r'^demo_ack$', name='交互回调演示', desc='处理回调按钮点击', event_types=['INTERACTION_CREATE'])
async def on_demo_ack(event, match):
    event.set_callback_code(0)  # 应答这次交互


# ==================== 用户入群回复示例 ====================
# 群内有新用户加入时, 框架下发 GROUP_MEMBER_ADD 生命周期事件 (用户退群为 GROUP_MEMBER_REMOVE),
# 用 event_types 订阅即可在用户入群时自动回复 (event.reply 会发到该群)。
#   - event.user_id  : 入群用户的 openid
#   - event.group_id : 群 openid
# 注意: 正则 r'' 对生命周期事件恒匹配; 这类事件无消息文本, 不要依赖 match 分组。

@handler(r'', name='用户入群回复', desc='有新成员加入群聊时的处理示例（默认不发送，避免影响正常使用）', event_types=['GROUP_MEMBER_ADD'])
async def on_group_member_add(event, match):
    # 如需启用入群欢迎，取消下方注释:
    # await event.reply(
    #     f"欢迎新成员加入本群！🎉\n你的群标识: {event.user_id}\n发送「菜单」即可查看我能做什么～"
    # )
    pass


# 退群同理: 订阅 GROUP_MEMBER_REMOVE 即可 (此事件无法回复该用户, 通常用于做记录/通知群管理)
@handler(r'', name='用户退群示例', desc='有成员退出群聊时的处理示例', event_types=['GROUP_MEMBER_REMOVE'])
async def on_group_member_remove(event, match):
    # 用户已离开, 无法私聊该用户; 这里仅作演示 (可改为写日志或通知管理群)
    pass


# ==================== 引用消息示例 ====================
# 引用回复传 message_reference_id (REFIDX), 来自:
#   - event.message_reference_id  ：引用用户当前这条消息
#   - 发送响应 data['ext_info']['ref_idx'] ：引用机器人刚发的消息

@handler(r'^引用当前$', name='引用当前消息', desc='引用你发的这条消息回复', owner_only=True)
async def reply_reference_current(event, match):
    await event.reply("这条回复引用了你刚发的消息", message_reference_id=event.message_reference_id)


@handler(r'^引用刚发送$', name='引用刚发送消息', desc='引用机器人刚发出的消息', owner_only=True)
async def reply_reference_sent(event, match):
    data = await event.reply("第一条消息")
    ref_id = (data or {}).get('ext_info', {}).get('ref_idx', '')
    await event.reply("这条引用了上面第一条", message_reference_id=ref_id)


# ==================== 分享链接功能 ====================

def _get_log_service(event):
    from core.bot.manager import _bot_manager_ref
    bot = _bot_manager_ref.get_bot(event.appid) if _bot_manager_ref else None
    return bot.log_service if bot else None


@handler(r'^申请邀请链接$', name='申请邀请链接', desc='生成专属邀请链接')
async def get_share_link(event, match):
    link = await event.sender.get_share_link(event.user_id)
    await event.reply(
        f"🔗 你的专属邀请链接：\n{link}\n\n📌 当其他用户通过此链接添加机器人时，将记录为你的邀请"
        if link else "❌ 生成邀请链接失败")


@handler(r'^申请邀请链接\s+(.+)$', name='自定义邀请链接', desc='生成自定义回调数据的邀请链接', owner_only=True)
async def get_share_link_custom(event, match):
    data = match.group(1).strip()
    link = await event.sender.get_share_link(data) if data else None
    await event.reply(
        f"🔗 自定义邀请链接：\n{link}\n\n📌 回调数据：{data}" if link else "❌ 生成失败")


@handler(r'^查询邀请数量$', name='查询邀请数量', desc='查看自己邀请了多少用户')
async def query_share_count(event, match):
    ls = _get_log_service(event)
    if not ls:
        return await event.reply("❌ 服务不可用")
    refs = await ls.share_get_referrals(event.user_id)
    if not refs:
        return await event.reply("📊 你还没有邀请任何用户\n\n💡 发送「申请邀请链接」获取链接")
    lines = [f"📊 你已成功邀请 {len(refs)} 位用户：\n"]
    for i, (oid, scene) in enumerate(refs.items(), 1):
        lines.append(f"{i}. {oid[:8]}****{oid[-4:] if len(oid) > 12 else ''}\n   来源：{ls.get_scene_name(scene)}")
    await event.reply('\n'.join(lines))


@handler(r'^查询我被谁邀请$', name='查询我被谁邀请', desc='查看谁邀请了自己')
async def query_my_sharer(event, match):
    ls = _get_log_service(event)
    if not ls:
        return await event.reply("❌ 服务不可用")
    sid = await ls.share_find_sharer(event.user_id)
    await event.reply(
        f"📌 分享者：{sid[:8]}****{sid[-4:] if len(sid) > 12 else ''}"
        if sid else "📌 你不是通过邀请链接添加的")


# ==================== 群成员查询示例 ====================

@handler(r'^成员信息\s+(\S+)$', name='成员信息', desc='查询指定群成员详情', owner_only=True, group_only=True)
async def query_group_member(event, match):
    member = await event.sender.get_group_member(event.group_id, match.group(1))
    if not member:
        return await event.reply("❌ 查询失败或成员不存在")
    await event.reply(
        f"👤 群昵称: {member.get('username', '')}\n"
        f"🎖️ 身份: {member.get('member_role', '')}\n"
        f"🤖 是否机器人: {member.get('bot', False)}\n"
        f"📅 入群时间: {member.get('joined_at', '')}")


@handler(r'^机器人信息$', name='机器人群内信息', desc='查询机器人自身在本群的成员信息', owner_only=True, group_only=True)
async def query_bot_member(event, match):
    member = await event.sender.get_bot_member(event.group_id)
    if not member:
        return await event.reply("❌ 查询失败")
    role = member.get('member_role', '')
    is_admin = role in ('admin', 'owner')
    await event.reply(
        f"🤖 群昵称: {member.get('username', '')}\n"
        f"🎖️ 身份: {role}\n"
        f"👮 是否管理员: {'是' if is_admin else '否'}\n"
        f"📅 入群时间: {member.get('joined_at', '')}")


# ==================== 召回功能 ====================

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


# ==================== 无视@配置示例 ====================
# ignore_at_check=True: 即使 bot 未被@, 该处理器也会匹配 GROUP_MESSAGE_CREATE 消息
# 不受 non_at_message.enabled 和群白名单配置影响

@handler(r'^全量签到$', name='签到', desc='无需@即可触发的签到指令', ignore_at_check=True)
async def check_in(event, match):
    await event.reply("✅ 签到成功！")


@handler(r'^主动测试$', name='全量主动测试', desc='无需@即可触发, 3秒后发送主动消息', ignore_at_check=True, owner_only=True)
async def non_at_active_test(event, match):
    target_id = event.group_id if event.is_group else event.user_id
    target_type = "群" if event.is_group else "用户"
    await event.reply(f"✅ 无需@触发\n{target_type} ID: {target_id}\n\n⏰ 3秒后发送主动消息...")
    await asyncio.sleep(3)
    if event.is_group:
        await event.send_to_group(event.group_id, "🎉 ignore_at_check + 主动群消息")
    else:
        await event.send_to_user(event.user_id, "🎉 ignore_at_check + 主动私聊消息")


# ==================== 主动消息示例 ====================

@handler(r'^主动测试$', name='主动测试', desc='3秒后发送主动消息', owner_only=True)
async def test_active_message(event, match):
    target_id = event.group_id if event.is_group else event.user_id
    target_type = "群" if event.is_group else "用户"
    await event.reply(f"✅ 检测到{target_type}消息\nID: {target_id}\n\n⏰ 3秒后将发送主动消息...")

    await asyncio.sleep(3)
    if event.is_group:
        await event.send_to_group(event.group_id, "🎉 主动群消息（通过event发送）")
    else:
        await event.send_to_user(event.user_id, "🎉 主动私聊消息（通过event发送）")


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


# ==================== 无 event 主动推送示例 ====================
# send_to_* 不读取 event 字段, event.send_to_* 只是便捷写法。
# 没有 event 的场景 (定时任务、@on_load 后台循环) 取一个 sender 直接调用即可。

def _get_any_sender():
    from core.bot.manager import _bot_manager_ref
    return next(iter(_bot_manager_ref._bots.values())).sender


@handler(r'^无event群发\s+(\S+)\s+(.+)$', name='无event主动群发',
         desc='不借助 event, 直接用 sender 推送', owner_only=True)
async def send_without_event(event, match):
    gid, content = match.group(1), match.group(2)
    sender = _get_any_sender()
    await sender.send_to_group(gid, content)
    await event.reply(f"✅ 已通过 sender 发送到 {gid[:8]}****")


@handler(r'^主动图片$', name='主动图片', desc='向当前会话主动发送图片', owner_only=True)
async def test_send_image_proactive(event, match):
    target_id = event.group_id if event.is_group else event.user_id
    if event.is_group:
        await event.reply_image(
            "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
            "📸 主动图片", target_group_id=target_id)
    else:
        await event.reply_image(
            "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
            "📸 主动图片", target_user_id=target_id)


# ==================== Web 面板页面示例 ====================

register_page(
    key='examples-page',
    label='示例页面',
    source='plugin',
    source_name='alone',
    html='''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f0f23; color: #e0e0e0; padding: 32px; }
  .card { background: #1a1a2e; border: 1px solid #2a2a3e; border-radius: 12px;
          padding: 24px; max-width: 600px; margin: 0 auto; }
  h1 { font-size: 20px; margin-bottom: 8px; color: #7c8aff; }
  p { font-size: 14px; color: #aaa; line-height: 1.6; margin-bottom: 12px; }
  .info { background: #16213e; border-radius: 8px; padding: 16px; margin-top: 16px;
          font-size: 13px; line-height: 1.8; }
  .info code { background: #2a2a3e; padding: 2px 6px; border-radius: 4px; color: #7c8aff; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
           background: rgba(124,138,255,.15); color: #7c8aff; margin-left: 6px; }
</style>
</head>
<body>
  <div class="card">
    <h1>自定义侧边栏页面 <span class="badge">示例</span></h1>
    <p>这是 <code>plugins/alone/examples.py</code> 自动注册的面板页面。</p>
    <p>模块 import 时注册, 插件卸载时自动注销。</p>
    <div class="info">
      <strong>使用方法:</strong><br>
      1. 导入: <code>from core.plugin.web_pages import register_page</code><br>
      2. 注册: <code>register_page(key, label, html=...)</code><br>
      3. 注销: <code>unregister_page(key)</code><br><br>
      <strong>参数:</strong><br>
      - <code>key</code> — 页面唯一标识 (URL 路径)<br>
      - <code>label</code> — 侧边栏显示名称<br>
      - <code>html</code> — HTML 内容字符串<br>
      - <code>html_file</code> — 或指定 HTML 文件路径<br>
      - <code>source</code> — 来源类型 (plugin / module)
    </div>
  </div>
</body>
</html>''',
)


@on_unload
def _unload_web_pages():
    unregister_page('examples-page')


# ==================== 自定义 HTTP 路由示例 ====================
# 插件可以注册自己的 HTTP 接口, 路径需以 /api/ext/ 开头。默认 auth=True 复用
# 后台登录 token; 设 auth=False 即开放免验证。插件卸载时框架自动注销这些路由。

# 免验证: 浏览器直接访问 /api/ext/alone/ping 即可
@register_route('GET', '/api/ext/alone/ping', auth=False)
async def api_ping(request):
    return web.json_response({'ok': True, 'plugin': 'alone'})


# 需要 token (auth=True 是默认值, 可省略): 请求头带 Authorization: Bearer <token>
@register_route('POST', '/api/ext/alone/echo')
async def api_echo(request):
    body = await request.json()
    return web.json_response({'ok': True, 'you_sent': body})


# ==================== Web 面板示例 ====================

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


@handler(r'^面板广播\s+(.+)$', name='面板广播', desc='向所有面板客户端广播消息', owner_only=True)
async def panel_broadcast(event, match):
    import web.ws as ws
    ws.broadcast({'type': 'notification', 'message': match.group(1)})
    await event.reply(f"✅ 已广播: {match.group(1)}")


# block 示例: 两个处理器同注册 "^拦截示例$", 高优先级设 block=True 命中即拦截, 低优先级不会触发
@handler(r'^拦截示例$', name='拦截示例-高优先级', desc='block=True 命中即拦截后续插件',
         priority=10, block=True)
async def block_demo_high(event, match):
    await event.reply("🛑 高优先级处理器 (block=True): 已拦截, 低优先级不会再触发")


@handler(r'^拦截示例$', name='拦截示例-低优先级', desc='被高优先级 block 拦截, 不会触发',
         priority=0)
async def block_demo_low(event, match):
    await event.reply("⬇️ 低优先级处理器: 你不应该看到这条 (已被 block 拦截)")
