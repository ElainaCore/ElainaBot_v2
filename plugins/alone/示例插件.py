"""示例功能: 媒体发送、ark卡片、markdown、撤回、主动消息、按钮等 (仅主人可用)"""

import asyncio
import os

from core.plugin.decorators import handler, on_unload
from core.plugin.web_pages import register_page, unregister_page

# ==================== 媒体发送示例 ====================

@handler(r'^图片$', name='图片', desc='发送网络图片示例', owner_only=True)
async def send_image(event, match):
    await event.reply_image(
        "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
        "reply_image 方法发送")


@handler(r'^本地图片$', name='本地图片', desc='发送本地图片示例', owner_only=True)
async def send_local_image(event, match):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "1.png")
    if not os.path.exists(path):
        return await event.reply(f"❌ 图片不存在: {path}")
    try:
        with open(path, 'rb') as f:
            data = f.read()
        await event.reply_image(data, f"📸 本地图片 ({len(data)/1024/1024:.2f}MB)")
    except Exception as ex:
        await event.reply(f"❌ 读取失败: {ex}")


@handler(r'^语音$', name='语音', desc='发送语音示例', owner_only=True)
async def send_voice(event, match):
    await event.reply_voice(
        "https://act-upload.mihoyo.com/sr-wiki/2025/06/03/160045374/420e9ac5c0c9d2b2c44b91f453b65061_2267222992827173477.wav")


@handler(r'^视频$', name='视频', desc='发送视频示例', owner_only=True)
async def send_video(event, match):
    await event.reply_video("https://i.elaina.vin/1.mp4")


@handler(r'^文件$', name='文件', desc='发送文件示例', owner_only=True)
async def send_file(event, match):
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write("ElainaBot测试文件\n这是一个自动生成的文本文件示例")
        temp_path = f.name
    try:
        await event.reply_file(temp_path, "📄 自动生成的测试文件", file_name="elainabot_test.txt")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ==================== 撤回示例 ====================

@handler(r'^撤回测试$', name='撤回测试', desc='发送后3秒撤回', owner_only=True)
async def test_recall(event, match):
    data = await event.reply("⏰ 3秒后撤回...")
    if data:
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


# ==================== 召回功能 ====================

@handler(r'^指定召回\s+(\S+)$', name='指定召回', desc='向指定用户发送召回消息', owner_only=True)
async def wakeup_user(event, match):
    uid = match.group(1)
    ok, r = await event.send_wakeup(uid, "📢 召回消息测试")
    if ok:
        await event.reply(f"✅ 召回成功 {uid[:8]}**** ID:{r}")
    else:
        await event.reply(f"❌ {r}")


@handler(r'^强制召回\s+(\S+)$', name='强制召回', desc='强制向指定用户发送召回消息', owner_only=True)
async def force_wakeup_user(event, match):
    uid = match.group(1)
    ok, r = await event.sender.force_wakeup(uid, "📢 强制召回测试")
    if ok:
        await event.reply(f"✅ 强制召回成功 {uid[:8]}**** ID:{r}")
    else:
        await event.reply(f"❌ {r}")


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
    ok, data, _ = await event.send_to_user(uid, content)
    if ok:
        await event.reply(f"✅ 已发送主动私聊消息\n目标: {uid[:8]}****")
    else:
        await event.reply(f"❌ 发送失败: {data.get('message', '未知错误')}")


@handler(r'^主动群发\s+(\S+)\s+(.+)$', name='主动群发', desc='向指定群发送主动消息', owner_only=True)
async def test_send_to_group(event, match):
    gid, content = match.group(1), match.group(2)
    ok, data, _ = await event.send_to_group(gid, content)
    if ok:
        await event.reply(f"✅ 已发送主动群消息\n目标群: {gid[:8]}****")
    else:
        await event.reply(f"❌ 发送失败: {data.get('message', '未知错误')}")


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


# ==================== Web 面板示例 ====================

@handler(r'^面板推送$', name='面板推送', desc='向 Web 面板推送一条自定义日志', owner_only=True)
async def push_to_panel(event, match):
    try:
        from datetime import datetime

        import web.ws as ws
        entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': f'来自插件的推送测试 (用户: {event.user_id})',
            'source': 'plugin.examples',
            'level': 'INFO',
        }
        ws.push_log('framework', entry)
        await event.reply("✅ 已推送日志到 Web 面板\n请在面板「日志」页查看")
    except Exception as e:
        await event.reply(f"❌ 推送失败: {e}")


@handler(r'^面板状态$', name='面板状态', desc='查看 Web 面板运行状态', owner_only=True)
async def panel_status(event, match):
    try:
        import web.ws as ws
        from core.base.config import cfg
        port = cfg.get('settings', 'server.port', 5200)
        client_count = len(ws._clients) if hasattr(ws, '_clients') else '未知'
        await event.reply(
            f"📊 Web 面板状态\n"
            f"端口: {port}\n"
            f"WebSocket 连接数: {client_count}\n"
            f"面板路径: /web/")
    except Exception as e:
        await event.reply(f"❌ 获取状态失败: {e}")


@handler(r'^面板广播\s+(.+)$', name='面板广播', desc='向所有面板客户端广播消息', owner_only=True)
async def panel_broadcast(event, match):
    content = match.group(1)
    try:
        import web.ws as ws
        ws.broadcast({'type': 'notification', 'message': content})
        await event.reply(f"✅ 已广播到所有面板客户端: {content}")
    except Exception as e:
        await event.reply(f"❌ 广播失败: {e}")
