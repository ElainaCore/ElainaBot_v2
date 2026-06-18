# ElainaBot 插件开发文档

> 面向开发者的完整插件开发指南 — 从最简单的 "Hello World" 到复杂的多文件插件、Web 面板扩展、主动消息推送、生命周期钩子等。

---

## 目录

- [1. 快速开始](#1-快速开始)
- [2. 插件目录结构](#2-插件目录结构)
- [3. 核心装饰器](#3-核心装饰器)
  - [3.1 `@handler` 消息处理器](#31-handler-消息处理器)
  - [3.2 `@on_load` / `@on_unload` 生命周期钩子](#32-on_load--on_unload-生命周期钩子)
  - [3.3 `@interceptor` 消息拦截器](#33-interceptor-消息拦截器)
- [4. Event 事件对象](#4-event-事件对象)
- [5. 消息发送 API](#5-消息发送-api)
- [6. 插件上下文 `ctx`](#6-插件上下文-ctx)
- [7. 插件元数据 `__plugin_meta__`](#7-插件元数据-__plugin_meta__)
- [8. Web 面板扩展](#8-web-面板扩展)
- [9. 配置项与全量环境](#9-配置项与全量环境)
- [10. 调试与最佳实践](#10-调试与最佳实践)
- [11. 完整示例](#11-完整示例)

---

## 1. 快速开始

在 `plugins/` 下新建文件 `plugins/hello/main.py`：

```python
"""Hello 插件 — 最小示例"""
from core.plugin.decorators import handler


@handler(r'^你好$', name='打招呼', desc='回复一句问候')
async def say_hello(event, match):
    await event.reply(f"你好, {event.user_id[:8]}****!")
```

**完成。** 框架启动时会自动扫描 `plugins/` 目录加载插件，热更新也已内置。

| 元素 | 说明 |
| --- | --- |
| `@handler(r'^你好$')` | 正则匹配用户消息 |
| `event` | 当前消息事件对象 (`core.message.event.Event`) |
| `match` | `re.Match` 对象 (匹配结果) |
| `event.reply(...)` | 回复当前会话 |

---

## 2. 插件目录结构

ElainaBot 支持两种插件形态：

### 2.1 简单插件 (单文件)

```
plugins/
└── hello/
    ├── 任意文件名.py       # 入口文件 
    ├── requirements.txt   # 依赖 (可选, 自动 pip install)
    └── data/              # 持久化数据 (可选, 由 ctx 管理)
```

### 2.2 大型插件 (多文件 + 子模块)

```
plugins/
└── my_plugin/
    ├── main.py            # 入口（以下入口仅作推荐命名，不强制）
    ├── app/               # 子插件目录
    │   └── **.py
    ├── mod/               # 业务模块
    │   └── **.py
    ├── data/              # 数据存储
    │   └── **.yaml
    ├── necessary/         # 资源文件
    └── requirements.txt
```

> **入口文件命名**: `index.py` / `app.py` / `main.py` 任选其一  
> **子目录访问**: `from .mod.core import xxx`  
> **自由组织**: 框架只识别入口文件，内部目录结构、文件数量和命名完全自由，只需在入口文件中 import 即可生效。

---

## 3. 核心装饰器

所有装饰器都从 `core.plugin.decorators` 导入：

```python
from core.plugin.decorators import handler, on_load, on_unload, interceptor
```

### 3.1 `@handler` 消息处理器

**签名**:

```python
@handler(pattern, *, name='', desc='', priority=0, owner_only=False,
         group_only=False, direct_only=False, channel_only=False,
         event_types=None, cooldown=0, ignore_at_check=False)
```

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `pattern` | `str` | — | 正则表达式 (使用 `re.DOTALL` 编译) |
| `name` | `str` | 函数名 | 处理器显示名称 (Web 面板 / 日志) |
| `desc` | `str` | `''` | 功能描述 |
| `priority` | `int` | `0` | 优先级 (数字越大越先匹配) |
| `owner_only` | `bool` | `False` | 仅主人可触发 |
| `group_only` | `bool` | `False` | 仅群聊 |
| `direct_only` | `bool` | `False` | 仅私聊 |
| `channel_only` | `bool` | `False` | 仅频道 |
| `event_types` | `list[str]` | `None` | 仅响应指定事件类型 (见下表) |
| `cooldown` | `int` | `0` | 冷却时间 (秒, 0 = 无冷却) |
| `ignore_at_check` | `bool` | `False` | 全量模式: 不需@机器人也触发 |

**事件类型常量** (`event_types` 可选值):

| 常量 | 含义 |
| --- | --- |
| `GROUP_AT_MESSAGE_CREATE` | 群聊 @ 机器人 |
| `GROUP_MESSAGE_CREATE` | 群聊全量消息 |
| `C2C_MESSAGE_CREATE` | 私聊消息 |
| `DIRECT_MESSAGE_CREATE` | 频道私信 |
| `AT_MESSAGE_CREATE` | 频道 @ 机器人 |
| `MESSAGE_CREATE` | 频道公开消息 |
| `INTERACTION_CREATE` | 按钮/交互回调 |
| `GROUP_ADD_ROBOT` / `GROUP_DEL_ROBOT` | 机器人加群/退群 |
| `GROUP_MEMBER_ADD` / `GROUP_MEMBER_REMOVE` | 群成员入群/退群 |
| `GROUP_MSG_REJECT` / `GROUP_MSG_RECEIVE` | 群消息拒绝/恢复 |
| `FRIEND_ADD` / `FRIEND_DEL` | 加好友/删好友 |
| `MESSAGE_REACTION_ADD` / `MESSAGE_REACTION_REMOVE` | 表态(表情回应)添加/移除 |

**示例**:

```python
@handler(r'^/?菜单$', name='主菜单', desc='显示功能列表', priority=10)
async def menu(event, match):
    await event.reply("📋 功能列表:\n1. 签到\n2. 抽卡")


@handler(r'^管理\s+(\S+)$', name='管理命令', owner_only=True, group_only=True)
async def admin(event, match):
    target = match.group(1)
    await event.reply(f"✅ 已处理: {target}")


@handler(r'^签到$', name='签到', ignore_at_check=True)  # 无需@即可触发
async def check_in(event, match):
    await event.reply("✅ 签到成功!")
```

### 3.2 `@on_load` / `@on_unload` 生命周期钩子

```python
from core.plugin.decorators import on_load, on_unload


@on_load
async def init():
    """插件加载完成时执行 (支持 async/sync)"""
    print("插件已加载")


@on_unload
def cleanup():
    """插件卸载/重载时执行 — 清理资源"""
    print("插件已卸载")
```

> **使用场景**: 启动后台任务、连接数据库、注册 Web 页面、注销定时器等。

### 3.3 `@interceptor` 消息拦截器

```python
@interceptor(priority=100)
async def filter_keywords(event):
    """返回 True 阻止后续 handler 匹配, 否则继续"""
    if '违禁词' in (event.content or ''):
        await event.reply("⛔ 消息包含违禁词")
        return True
    return False
```

| 参数 | 说明 |
| --- | --- |
| `priority` | 拦截器优先级 (数字越大越先执行) |
| 返回值 | `True` 阻止后续处理, 其他值继续 |

---

## 4. Event 事件对象

`event` 是所有 handler 的第一个参数, 提供事件的全部上下文。

### 4.1 常用字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `event.user_id` | `str` | 用户 ID (OpenID, 或 union_id 取决于配置) |
| `event.username` | `str` | 用户昵称 (可能为空) |
| `event.group_id` | `str` | 群 ID (仅群聊) |
| `event.channel_id` | `str` | 频道 ID |
| `event.content` | `str` | 消息文本 (已去除 @机器人 标记) |
| `event.raw_content` | `str` | 原始消息内容 |
| `event.message_id` | `str` | 消息 ID |
| `event.event_type` | `str` | 事件类型 |
| `event.appid` | `str` | 机器人 AppID |
| `event.attachments` | `list` | 附件列表 (图片/文件等) |
| `event.image_url` | `str` | 图片 URL (若消息含图片) |
| `event.raw` | `dict` | 原始 payload 字典 |
| `event.timestamp` | `str` | 消息时间戳 |
| `event.event_id` | `str` | 事件 ID |
| `event.guild_id` | `str` | 频道服务器 ID (频道场景) |
| `event.interaction_data` | `dict` | 交互回调数据 (仅 INTERACTION 事件) |
| `event.callback_code` | `int` / `None` | 交互回调状态码, 见 5.8 |
| `event.message_reference_id` | `str` | 可引用的 REFIDX (用于引用回复, 见 5.7) |
| `event.message_scene` | `dict` | 消息场景信息 (`source` / `ext` 等) |

### 4.2 场景标识 (布尔属性)

| 属性 | 说明 |
| --- | --- |
| `event.is_group` | 群聊 |
| `event.is_direct` | 私聊 |
| `event.is_channel` | 频道 |
| `event.is_interaction` | 按钮交互回调 |
| `event.is_lifecycle` | 生命周期事件 (加群/加好友) |
| `event.is_bot` | 消息发送者是机器人 | 

### 4.3 @ 相关 (仅群聊)

| 属性 | 说明 |
| --- | --- |
| `event.is_at_self` | 是否 @ 了当前机器人 |
| `event.is_at_other_bot` | 是否 @ 了其他机器人 |
| `event.is_at_other_user` | 是否 @ 了其他普通用户 |
| `event.is_at_all` | 是否 @ 了全体成员 |
| `event.mentions` | @ 列表原始数据 |

### 4.4 派生属性

| 属性 | 说明 |
| --- | --- |
| `event.chat_id` | 自动返回 `group_id` / `user_id` / `channel_id` |
| `event.chat_type` | 返回 `'group'` / `'direct'` / `'channel'` / `'unknown'` |
| `event.get(path)` | JSON 路径取值, 如 `event.get('d/author/id')` |
| `event.sender` | 底层 `MessageSender` 实例 (高级用法) |

---

## 5. 消息发送 API

`event` 通过代理表自动转发到 `MessageSender`, 调用形如 `await event.reply(...)`。

### 5.1 文本与媒体回复

```python
# 文本回复
await event.reply("Hello!")

# 带按钮回复 (完整字段参考见 5.2 节)
buttons = [
    [{'text': '回调', 'data': 'cb_1', 'type': 1},      # 回调按钮
     {'text': '输入', 'data': '/帮助', 'type': 2}],    # 填充指令到输入框
    [{'text': '链接', 'link': 'https://example.com'}],  # 链接按钮 (等同 type=0)
]
await event.reply("📌 选择操作", buttons=buttons)

# 自动撤回 (秒)
await event.reply("⏰ 5秒后撤回", auto_delete_time=5)

# 图片 (URL 或 bytes)
await event.reply_image("https://i.elaina.vin/1.png", "图片说明")
await event.reply_image(open('local.png', 'rb').read(), "本地图片")

# 语音 / 视频 / 文件
await event.reply_voice("https://example.com/audio.wav")
await event.reply_video("https://example.com/video.mp4")
await event.reply_file('/path/to/file.txt', "📄 文档", file_name="custom.txt")
```

### 5.2 按钮完整字段参考

按钮是二维数组 `list[list[dict]]` (行 × 列), 每个按钮是一个字典。

#### 核心字段

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `text` | `str` | `''` | 按钮显示文字 (**必填**) |
| `type` | `int` | `2` | 按钮类型: `0`=跳转链接 / `1`=回调 / `2`=输入指令 |
| `data` | `str` | `text` | type=0: URL; type=1: 回调标识; type=2: 填充到输入框的内容 |
| `link` | `str` | — | 快捷方式: 设置后自动设为 `type=0 + data=link` |
| `show` | `str` | `text` | 点击后显示的文字 (visited_label) |
| `style` | `int` | `1` | 样式: `0`=灰框 / `1`=蓝框蓝字 / `2`=黑框(PC 端气泡) / `3`=黑框红字 / `4`=蓝底白字 |

#### 行为字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `enter` | `bool` | 已失效，无论填写不填写，最终都会被开放平台删掉|
| `reply` | `bool` | 点击后作为引用回复发送 |
| `limit` | `int` | 点击次数限制 (`click_limit`)可能无效 |
| `tips` | `str` | 不支持时的提示文字 (`unsupport_tips`) |

#### 权限字段 (五者二选一, 优先级从上到下)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `permission` | `dict` | 显式权限对象, 如 `{'type': 1}` |
| `role` | `list[str]` | 指定身份组 ID 列表 (频道场景) → `type=3` |
| `list` | `list[str]` | 指定用户 ID 列表 → `type=0` |
| `admin` | `bool` | 仅管理员可点 → `type=1` |
| _默认_ | — | 所有人可点 → `type=2` |

#### 按钮完整示例

```python
buttons = [
    # 第一行: 三种基础类型
    [
        {'text': '跳转官网', 'link': 'https://example.com'},        # 链接
        {'text': '点我回调', 'data': 'cb_action_1', 'type': 1},     # 回调
        {'text': '/帮助', 'type': 2},               # 输入后自动发送
    ],
    # 第二行: 权限与限制
    [
        {'text': '仅管理员', 'data': 'admin_only', 'type': 1, 'admin': True},
        {'text': '指定用户', 'data': 'specific', 'type': 1,
         'list': ['user_id_1', 'user_id_2']},
        {'text': '点击一次', 'data': 'once', 'type': 1, 'limit': 1},
    ],
    # 第三行: 样式与提示
    [
        {'text': '灰框', 'data': 's0', 'type': 1, 'style': 0},
        {'text': '黑框红字', 'data': 's3', 'type': 1, 'style': 3},
        {'text': '蓝底白字', 'data': 's4', 'type': 1, 'style': 4},
        {'text': '不支持提示', 'data': 'oops', 'type': 1,
         'tips': '该功能仅 PC 端可用'},
    ],
]
await event.reply("📌 多功能按钮面板", buttons=buttons)
```

#### 小按钮 (键盘级字号)

通过键盘级样式 `content.style.font_size` 控制整组按钮的大小 (对应官方 botgo
`CustomKeyboard.Style.FontSize`), 取值 `small` / `middle` / `large`, `small`
即「小按钮」。两种用法:

```python
# 方式一: reply 关键字 button_font_size
await event.reply("📌 小按钮面板", buttons=buttons, button_font_size='small')

# 方式二: buttons 用 dict 包装 (适用于所有发送入口, 含主动推送/频道)
await event.reply("📌 小按钮面板", buttons={'rows': buttons, 'font_size': 'small'})
```

不传则保持原默认大小。

#### 附: 扩展 prompt 按钮 (最多 3 个)

```python
# 字符串简写 (点击后自动发送 'elaina')
await event.reply("选择:", prompt_buttons=['选项A', '选项B', '选项C'])

# (文本, 样式) 元组
await event.reply("选择:", prompt_buttons=[('确认', 1), ('取消', 0)])
```

### 5.3 Ark 卡片

```python
# ark23 — 列表卡片
await event.reply_ark(23, (
    "列表卡片标题", "提示文本",
    [['项目1'], ['项目2', 'https://link.com']]))

# ark24 — 文本+图片
await event.reply_ark(24, (
    "提示", "标题", "副标题", "描述", "图片URL", "跳转URL", "图片副标题"))

# ark37 — 大图文
await event.reply_ark(37, (
    "提示", "标题", "副标题", "图片URL", "跳转URL"))
```

### 5.4 模板消息

```python
# 引用 templates 目录下的模板
await event.reply(template_name='maintenance',
                  template_vars={'user_id': event.user_id})
```

### 5.5 主动推送图片

```python
# 主动推送图片到指定群/用户 (不关联消息)
await event.send_image('group', event.group_id, "https://...", "图片说明")
await event.send_image('user', event.user_id, image_bytes, "说明")
```

### 5.6 主动消息推送

```python
# ---- 向当前会话发送主动消息 (自动从 event 获取目标) ----

# 向当前群发送主动消息
await event.send_to_group(event.group_id, "主动群消息")

# 向当前用户发送主动私聊
await event.send_to_user(event.user_id, "主动私聊消息")

# 自动判断群/私聊
if event.is_group:
    await event.send_to_group(event.group_id, "来自群")
else:
    await event.send_to_user(event.user_id, "来自私聊")

# 主动发图片到当前群
await event.reply_image("https://...", "说明", target_group_id=event.group_id)

# ---- 向指定目标发送 (手动填写 ID) ----

await event.send_to_group("指定群ID", "通知内容")
await event.send_to_user("指定用户ID", "私信内容")
await event.send_to_channel("指定频道ID", "频道消息")
```

> **`event.reply()` vs `event.send_to_*()`**: `reply` 是被动回复 (关联当前消息 msg_id), `send_to_*` 是主动推送 (不关联消息)。日常使用 `reply` 即可, 延迟场景 (如定时任务、sleep 后) 可以用 `send_to_*`。

#### 不依赖 event 的主动推送

`send_to_*` 不读取 event 字段, `event.send_to_*` 只是便捷写法。没有 event 时 (定时任务、`@on_load` 后台循环) 取一个 `sender` 直接调用:

```python
from core.bot.manager import _bot_manager_ref

# 取任意可用 bot 的 sender (指定 appid: _bot_manager_ref.get_bot(appid).sender)
sender = next(iter(_bot_manager_ref._bots.values())).sender

await sender.send_to_group("群ID", "通知内容")
await sender.send_to_user("用户ID", "私信内容")
await sender.send_to_channel("频道ID", "频道消息")
```

### 5.7 引用消息与发送返回值

#### 发送返回值

| 方法 | 返回值 |
| --- | --- |
| `reply` / `reply_image` / `reply_ark` 等 | 平台响应 `dict` (失败为 `None`) |
| `send_to_group` / `send_to_user` | `(ok, data, payload)` 三元组 |
| `send_to_channel` | `(ok, data)` |
| `send_wakeup` | `(ok, 消息ID或原因)` |

响应 `dict` 常用字段: `data['id']` (平台消息 ID)、`data['ext_info']['ref_idx']` (本条消息的可引用 REFIDX)。

#### 引用消息 (message_reference_id)

引用回复需要传 **REFIDX** (`REFIDX_xxx`), 而不是平台消息 ID (`ROBOT1.0_xxx`):

```python
# 1) 引用"用户当前这条消息"
await event.reply("引用你刚发的消息", message_reference_id=event.message_reference_id)

# 2) 引用"机器人刚发出的那条消息" (从发送响应里取 ref_idx)
data = await event.reply("第一条")
ref = (data or {}).get('ext_info', {}).get('ref_idx', '')
if ref:
    await event.reply("引用上面那条", message_reference_id=ref)

# 3) 主动消息也可引用
await event.send_to_group(event.group_id, "通知", message_reference_id=ref)
```

> 框架会自动组装为 `{"message_reference": {"message_id": "REFIDX_xxx", "ignore_get_message_error": true}}`。只有显式传 `message_reference_id` 才会引用。

### 5.8 撤回与交互回调

```python
# 撤回当前消息
await event.recall()

# 撤回指定消息
await event.recall(message_id="xxx")
```

**交互回调** — 回调按钮 (`type=1`) 被点击时下发 `INTERACTION_CREATE` 事件, 此时 `event.content` 即按钮的 `data`。用 `set_callback_code` 应答这次点击:

```python
@handler(r'^my_btn$', event_types=['INTERACTION_CREATE'])
async def on_click(event, match):
    event.set_callback_code(0)     # 应答这次交互
    # event.set_ack_timeout(15)    # 需更久处理时, 先延长等待(秒)再设置 code
    # 未调用时, 框架会自动用默认 code 应答

# 旧式 REST 应答 (会额外发一次请求, 一般无需使用)
await event.ack_interaction(code=0)
```

### 5.9 唤醒消息 (召回功能)

```python
# 智能召回 (按规则发送)
ok, reason = await event.send_wakeup(user_id, "📢 召回提示")

# 强制召回 (跳过条件)
ok, result = await event.sender.force_wakeup(user_id, "强制召回")
```

### 5.10 高级工具方法 (通过 `event.sender`)

```python
# 生成分享链接
url = await event.sender.get_share_link(callback_data='my_data')

# 获取图片尺寸 (URL 或 bytes)
width, height = await event.sender.get_image_size("https://...")

# 手动上传媒体文件 (返回 file_info)
file_info = await event.sender.upload_media(event, file_bytes, file_type=1)
# file_type: 1=图片, 2=视频, 3=语音, 4=文件
```

---

## 6. 插件上下文 `ctx`

`ctx` 在插件加载时由框架注入, 提供 **数据目录管理 + YAML 配置**:

```python
import core.plugin.context as _ctx
ctx = _ctx.ctx  # 在模块顶层捕获

# 读写文本
ctx.save_data('log.txt', 'hello')
content = ctx.read_data('log.txt')

# YAML 配置 (推荐)
config = ctx.ensure_config({
    'enabled': True,
    'timeout': 30,
}, filename='config.yaml')

ctx.save_config({'enabled': False}, filename='config.yaml')

# 异步版本
await ctx.read_config_async()
await ctx.save_config_async({'k': 'v'})

# 路径辅助
ctx.get_data_path('foo.json')      # data/ 下文件
ctx.get_resource_path('image.png') # 插件根目录文件
```

| 方法 | 说明 |
| --- | --- |
| `read_config(filename)` | 读取 YAML 配置 |
| `save_config(data, filename, comments)` | 保存 YAML (可带注释) |
| `ensure_config(defaults, ...)` | 缺项自动补齐, 返回完整配置 |
| `read_data` / `save_data` | 文本文件读写 |
| `read_data_async` / `save_data_async` | 异步版本 |
| `data_exists(filename)` | 文件是否存在 |
| `list_data()` | 列出 data/ 下所有文件 |

---

## 7. 插件元数据 `__plugin_meta__`

在入口模块顶层声明, **Web 面板将展示这些信息**:

```python
__plugin_meta__ = {
    'name': '我的插件',
    'author': 'YourName',
    'description': '插件功能说明',
    'version': '1.0.0',
    'github': 'https://github.com/xxx/repo',
    'homepage': 'https://example.com',
    'license': 'MIT',
}
```

| 字段 | 说明 |
| --- | --- |
| `name` | 显示名称 |
| `author` | 作者 |
| `description` | 简介 |
| `version` | 版本号 |
| `github` | 仓库地址 |
| `homepage` | 主页 |
| `license` | 许可证 |

---

## 8. Web 面板扩展

插件可注册自定义页面到 Web 面板侧边栏：

```python
from core.plugin.web_pages import register_page, unregister_page
from core.plugin.decorators import on_unload


# 内联 HTML 注册
register_page(
    key='my-page',          # 唯一标识 (URL)
    label='我的页面',        # 侧边栏显示名
    source='plugin',        # 来源类型
    source_name='my_plugin',
    html='<h1>Hello Panel</h1>',
    icon='settings',        # 侧边栏图标 (可选)
)

# 或指定 HTML 文件
register_page(key='my-page', label='我的页面',
              html_file='/abs/path/to/page.html')


@on_unload
def _cleanup():
    """插件卸载时清理"""
    unregister_page('my-page')
```

### 8.1 自定义 HTTP 路由

除了页面, 插件还能注册自己的 HTTP 接口。路径**必须以 `/api/ext/` 开头**(建议用 `/api/ext/{插件名}/` 避免冲突)。默认 `auth=True` 复用后台登录 token; 设 `auth=False` 则开放免验证(如对外回调、健康检查)。

```python
from aiohttp import web
from core.plugin.web_pages import register_route


# 免验证路由: 任何人可直接访问
@register_route('GET', '/api/ext/myplugin/ping', auth=False)
async def ping(request):
    return web.json_response({'ok': True})


# 需要 token (auth=True 是默认值, 可省略): 请求头带 Authorization: Bearer <token>
@register_route('POST', '/api/ext/myplugin/echo')
async def echo(request):
    body = await request.json()
    return web.json_response({'you_sent': body})
```

| 参数 | 说明 |
| --- | --- |
| `method` | `'GET'` / `'POST'` / `'PUT'` / `'DELETE'` 等 |
| `path` | 路由路径, 必须以 `/api/ext/` 开头 (精确匹配, 不支持路径参数; 可变部分用查询串/请求体传) |
| `handler` | `async def handler(request)`, 返回 `web.json_response(...)` / `web.Response(...)` |
| `auth` | 是否要求登录 token, 默认 `True` |

> 路由由 web 层动态查表执行, 插件**热重载/卸载即时生效**; 插件卸载时框架会自动注销其全部路由, 无需手动清理。也可直接调用 `register_route('POST', '/api/ext/x/do', handler)` (非装饰器写法)。

---

## 9. 配置项与全量环境

`bot.yaml` 中 `non_at_message` 区块控制 "未 @ 机器人" 时的消息处理：

```yaml
non_at_message:
  enabled: false                 # 是否响应未@消息 (开启后全量正则匹配)
  group_whitelist: []            # 未开启全量时, 仅白名单群触发插件
  ignore_at_other_bot: true      # 忽略仅@其他机器人的消息
  ignore_at_other_user: true     # 忽略仅@其他用户的消息
  ignore_bot_sender: true        # 屏蔽其他机器人发出的消息
  quiet_at_self: false           # @机器人时抑制默认黑名单/维护回复
```

### 在 handler 中使用

```python
# 永远响应 (即使全量未开启, 即使未@机器人)
@handler(r'^签到$', ignore_at_check=True)
async def check_in(event, match):
    await event.reply("✅ 签到成功")

# 仅响应 @机器人 的消息 (默认行为)
@handler(r'^菜单$')
async def menu(event, match):
    await event.reply("📋 菜单")
```

### 读取自定义配置

```python
from core.base.config import cfg

# 读取当前机器人配置项
value = cfg.get_bot_setting(event.appid, 'message.use_markdown', True)

# 读取全局 settings
port = cfg.get('settings', 'server.port', 5200)

# 获取单个机器人完整配置
bot_cfg = cfg.get_bot_config(event.appid)

# 写入配置
cfg.set_value('bot', 'bots.0.message.use_markdown', False)

# 监听配置变更
def on_bot_changed(new_data):
    print('配置已变更', new_data)
cfg.on_change('bot', on_bot_changed)
# cfg.off_change('bot', on_bot_changed)  # 移除监听
```

---

## 10. 调试与最佳实践

### 10.1 异常报错

```python
from core.base.logger import get_logger, PLUGIN, report_error

log = get_logger(PLUGIN, '我的插件')

try:
    await risky_operation()
except Exception as e:
    report_error(PLUGIN, '我的插件', e,
                 context={'user_id': event.user_id, 'extra': '...'})
    await event.reply("❌ 操作失败")
```

> **超时**: 框架对 handler 强制 300 秒超时, 超时会自动取消并记录错误。 

### 10.2 异步规范

```python
# 推荐 — async/await
@handler(r'^test$')
async def test(event, match):
    await event.reply("hi")

# 也支持同步 (会自动跑在 executor 中)
@handler(r'^test$')
def test_sync(event, match):
    import time
    time.sleep(1)
    return  # 同步函数无法 await reply, 应当避免
```

### 10.3 命名规范

| 规则 | 推荐 |
| --- | --- |
| handler 函数名 | snake_case, 体现功能 |
| `name=` 参数 | 中文短名, 用于面板展示 |
| `desc=` 参数 | 一句话描述功能 |
| 正则锚定 | 始终使用 `^` 和 `$` 避免误匹配 |
| 资源清理 | `on_unload` 中关闭文件/连接/页面 |

### 10.4 性能要点

- **避免阻塞**: 不要在 async handler 中调用同步 IO (用 `asyncio.to_thread` / `run_in_executor`)
- **延迟导入**: 体积大的依赖在 handler 内 `import`, 加快插件加载
- **冷却限流**: 高频指令加 `cooldown=N`
- **大型插件**: 子模块放 `app/` / `mod/` 目录, 按需 import

---

## 11. 完整示例

一个具备 **元数据 + 配置 + 多 handler + Web 页面 + 生命周期** 的完整插件：

```python
"""签到插件 — 带积分、配置、Web 面板"""

import asyncio
import core.plugin.context as _ctx_mod
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_page, unregister_page
from core.base.logger import get_logger, PLUGIN

__plugin_meta__ = {
    'name': '签到插件',
    'author': 'YourName',
    'description': '每日签到 + 积分系统',
    'version': '1.0.0',
    'license': 'MIT',
}

log = get_logger(PLUGIN, '签到')
ctx = _ctx_mod.ctx

DEFAULT_CONFIG = {
    'reward_min': 10,
    'reward_max': 100,
    'cooldown_hours': 24,
}


@on_load
async def init():
    config = ctx.ensure_config(DEFAULT_CONFIG)
    log.info(f"签到插件已加载, 配置: {config}")
    register_page(
        key='checkin-stats',
        label='签到统计',
        source='plugin',
        source_name='checkin',
        html='<h1>签到统计</h1><p>开发中...</p>',
    )


@on_unload
def cleanup():
    unregister_page('checkin-stats')
    log.info("签到插件已卸载")


@handler(r'^签到$', name='每日签到', desc='获取随机积分', ignore_at_check=True)
async def check_in(event, match):
    import random
    config = ctx.read_config()
    reward = random.randint(config['reward_min'], config['reward_max'])
    await event.reply(f"✅ {event.user_id[:8]}**** 签到成功!\n获得积分: {reward}")


@handler(r'^签到排行$', name='签到排行', desc='查看签到排行榜', group_only=True)
async def ranking(event, match):
    await event.reply("🏆 签到排行榜:\n1. 用户A\n2. 用户B\n3. 用户C")


@handler(r'^签到设置\s+(\d+)\s+(\d+)$', name='签到设置',
         desc='设置签到奖励范围', owner_only=True)
async def set_reward(event, match):
    min_val, max_val = int(match.group(1)), int(match.group(2))
    config = ctx.read_config()
    config['reward_min'] = min_val
    config['reward_max'] = max_val
    ctx.save_config(config)
    await event.reply(f"✅ 已设置奖励范围: {min_val} ~ {max_val}")
```

---

## 附录: 项目示例插件

| 路径 | 功能 |
| --- | --- |
| `plugins/alone/示例插件.py` | 媒体/ark/按钮/交互回调/引用消息/撤回/主动消息/Web 面板综合示例 |
| `plugins/system/main.py` | 内置系统插件 (信息、管理) |
| `plugins/game_services/main.py` | 大型插件示例 (子模块组织) |

> **启用/禁用插件**: 在 Web 面板「插件」页切换即可, 状态持久化到 `data/plugins_disabled.json`。旧版以 `.py.ban` 后缀禁用的方式已废弃, 框架启动时会自动迁移为新机制。

---

## 反馈与贡献

- 提交 Issue: 项目 GitHub 仓库
- 插件市场: Web 面板 → 市场 页面

**Happy Coding!** 🎉
