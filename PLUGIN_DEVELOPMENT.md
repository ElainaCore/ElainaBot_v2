# ElainaBot v2 插件开发文档

本文档说明插件结构、配置、事件处理、消息 API、群管理 API、Web 面板扩展和模块接入。

> 开发插件前，请先完成项目根目录 [README.md](README.md) 中的运行配置，并确认机器人已正常连接。

## 目录

- [1. 快速开始](#1-快速开始)
- [2. 插件结构与元数据](#2-插件结构与元数据)
  - [2.1 加载规则](#21-加载规则)
  - [2.2 普通插件](#22-普通插件)
  - [2.3 包式插件](#23-包式插件)
  - [2.4 插件元数据](#24-插件元数据)
- [3. 上下文、配置与生命周期](#3-上下文配置与生命周期)
  - [3.1 插件上下文](#31-插件上下文)
  - [3.2 机器人配置](#32-机器人配置)
  - [3.3 生命周期钩子](#33-生命周期钩子)
- [4. 事件与处理器](#4-事件与处理器)
  - [4.1 handler 装饰器](#41-handler-装饰器)
  - [4.2 事件类型](#42-事件类型)
  - [4.3 Event 字段](#43-event-字段)
  - [4.4 拦截、阻断与兜底](#44-拦截阻断与兜底)
- [5. 消息 API](#5-消息-api)
  - [5.1 回复文本与媒体](#51-回复文本与媒体)
  - [5.2 私聊流式消息](#52-私聊流式消息)
  - [5.3 消息类型](#53-消息类型)
  - [5.4 按钮与交互回调](#54-按钮与交互回调)
  - [5.5 Ark 与卡片消息](#55-ark-与卡片消息)
  - [5.6 主动消息](#56-主动消息)
  - [5.7 引用、撤回与返回值](#57-引用撤回与返回值)
  - [5.8 订阅消息](#58-订阅消息)
  - [5.9 Sender 工具方法](#59-sender-工具方法)
  - [5.10 自定义菜单与指令面板](#510-自定义菜单与指令面板)
- [6. 群管理 API](#6-群管理-api)
  - [6.1 权限与接口概览](#61-权限与接口概览)
  - [6.2 群资料与本地记录](#62-群资料与本地记录)
  - [6.3 入群申请事件](#63-入群申请事件)
  - [6.4 查询与审批入群申请](#64-查询与审批入群申请)
  - [6.5 群禁言](#65-群禁言)
  - [6.6 返回值与错误处理](#66-返回值与错误处理)
- [7. Web 面板扩展](#7-web-面板扩展)
- [8. Image Hosting 模块](#8-image-hosting-模块)
- [9. 调试与运行限制](#9-调试与运行限制)
- [10. 参考实现](#10-参考实现)

---

## 1. 快速开始

在 `plugins/hello/main.py` 中创建插件入口：

~~~python
from core.plugin.decorators import handler


@handler(r'^你好$', name='打招呼', desc='回复一句问候')
async def say_hello(event, match):
    await event.reply('你好！')
~~~

框架启动时会扫描 `plugins/` 下的插件目录并自动加载。保存插件文件后，文件监视器会触发热重载。

| 元素 | 说明 |
| --- | --- |
| `@handler(...)` | 声明消息或事件处理器 |
| `event` | 当前 `core.message.event.Event` 对象 |
| `match` | 正则表达式的 `re.Match` 结果 |
| `event.reply(...)` | 回复当前会话 |
| `event.reply_stream(...)` | 在 QQ 单聊中发送流式回复 |
| `event.send_stream_to_user(...)` | 向指定用户 OpenID 主动发送流式消息 |

`handler` 使用 `search()` 匹配；`^` 和 `$` 可将命令限制为整条消息匹配。

---

## 2. 插件结构与元数据

### 2.1 加载规则

框架只扫描 `plugins/` 下的一级目录，不直接加载 `plugins/` 根目录中的 Python 文件。

| 目录情况 | 加载方式 |
| --- | --- |
| 存在 `index.py`、`app.py` 或 `main.py` | 作为包式插件加载；按该顺序选择第一个存在的入口文件 |
| 不存在入口文件 | 加载目录根部所有非下划线开头的 `*.py` 文件 |
| 存在 `requirements.txt` | 加载前自动检查并安装依赖 |
| 目录或文件名以 `_`、`.` 开头 | 扫描时忽略 |

包式插件只自动执行入口文件。需要使用子模块时，应在入口中显式导入。普通插件不会递归扫描子目录。

### 2.2 普通插件

普通插件适合独立命令或少量功能，单文件插件通常位于 `plugins/alone/`：

~~~text
plugins/
└── alone/
    ├── weather.py
    ├── translate.py
    └── requirements.txt    # 可选，目录内插件共享
~~~

### 2.3 包式插件

包式插件适合包含多个业务模块、资源文件或 Web 页面的功能：

~~~text
plugins/
└── my_plugin/
    ├── main.py             # 入口，也可使用 index.py 或 app.py
    ├── handlers/
    │   ├── __init__.py
    │   └── commands.py
    ├── services/
    │   └── client.py
    ├── assets/
    │   └── panel.html
    ├── data/               # 运行数据，不应提交敏感配置
    └── requirements.txt
~~~

入口文件中显式导入注册处理器的子模块：

~~~python
# plugins/my_plugin/main.py
from .handlers import commands  # noqa: F401
~~~

不要在插件目录内自行修改 `sys.path`。包式插件已注册为 `plugins.<插件目录名>` 包，可直接使用相对导入。

### 2.4 插件元数据

在入口模块顶层声明 `__plugin_meta__`。Web 面板会显示受支持的字段：

~~~python
__plugin_meta__ = {
    'name': '我的插件',
    'author': 'YourName',
    'description': '插件功能说明',
    'version': '1.0.0',
    'github': 'https://github.com/example/repo',
    'homepage': 'https://example.com',
    'license': 'MIT',
}
~~~

| 字段 | 说明 |
| --- | --- |
| `name` | 展示名称 |
| `author` | 作者 |
| `description` | 简介 |
| `version` | 插件版本 |
| `github` | 源码仓库 |
| `homepage` | 项目主页 |
| `license` | 许可证 |

未列出的字段会被忽略。普通插件目录包含多个 Python 文件时，元数据取第一个成功加载的模块；包式插件可在入口中统一声明。

---

## 3. 上下文、配置与生命周期

### 3.1 插件上下文

框架在导入插件时注入 `ctx`，用于访问插件目录、持久化数据和 YAML 配置：

~~~python
import core.plugin.context as plugin_context

ctx = plugin_context.ctx

config = ctx.ensure_config({'enabled': True, 'timeout': 30})
ctx.save_data('state.txt', 'ready')
state = ctx.read_data('state.txt')
asset_path = ctx.get_resource_path('assets/panel.html')
~~~

| 方法 | 说明 |
| --- | --- |
| `read_config(filename='config.yaml')` | 读取 `data/` 下的 YAML；不存在或读取失败时返回空字典 |
| `save_config(data, filename='config.yaml', comments=None)` | 保存 YAML，可附带字段注释 |
| `ensure_config(defaults, filename='config.yaml', comments=None)` | 补齐缺少的顶层字段并返回配置 |
| `read_config_async(...)` / `save_config_async(...)` | 配置读写的异步版本 |
| `read_data(...)` / `save_data(...)` | 文本文件读写 |
| `read_data_async(...)` / `save_data_async(...)` | 文本读写的异步版本 |
| `data_exists(filename)` | 判断数据文件是否存在 |
| `list_data()` | 列出 `data/` 下的文件 |
| `get_data_path(filename)` | 返回数据文件绝对路径 |
| `get_resource_path(filename)` | 返回插件资源绝对路径 |

`ensure_config()` 只补齐第一层键，不会递归合并嵌套字典。凭据、Token 和用户数据应保存在 `data/` 中；发布插件前应通过插件自己的忽略规则或打包清单排除敏感数据。

### 3.2 机器人配置

机器人级配置位于 `config/bot.yaml`，通过 `cfg` 读取：

~~~python
from core.base.config import cfg

use_markdown = cfg.get_bot_setting(
    event.appid,
    'message.use_markdown',
    True,
)
bot_config = cfg.get_bot_config(event.appid)
server_port = cfg.get('settings', 'server.port', 5200)
~~~

未 @ 消息由每个机器人配置中的 `non_at_message` 控制：

~~~yaml
non_at_message:
  enabled: true
  group_whitelist: []
  ignore_at_other_bot: true
  ignore_at_other_user: true
  ignore_bot_sender: true
  quiet_at_self: false
  strip_bot_name_at: false
~~~

当 `enabled: false` 时，只有 `group_whitelist` 中的群和声明 `ignore_at_check=True` 的处理器可以响应未 @ 消息。

### 3.3 生命周期钩子

生命周期钩子支持同步和异步函数：

~~~python
import asyncio

from core.plugin.decorators import on_load, on_unload

worker = None


async def run_worker():
    while True:
        await asyncio.sleep(60)


@on_load
async def start_worker():
    global worker
    worker = asyncio.create_task(run_worker())


@on_unload
async def stop_worker():
    if worker:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
~~~

| 钩子 | 执行时机 | 常见用途 |
| --- | --- | --- |
| `@on_load` | 插件导入并收集注册项后 | 启动任务、创建客户端、准备缓存 |
| `@on_unload` | 卸载或热重载前 | 取消任务、关闭客户端、注销页面 |

热重载会执行 `on_unload`，因此插件必须释放自己创建的任务、连接和其他外部资源。

---

## 4. 事件与处理器

### 4.1 handler 装饰器

所有装饰器均从 `core.plugin.decorators` 导入：

~~~python
from core.plugin.decorators import handler, interceptor, on_load, on_unload
~~~

`handler` 签名：

~~~text
@handler(
    pattern,
    *,
    name='',
    desc='',
    priority=0,
    owner_only=False,
    group_only=False,
    direct_only=False,
    channel_only=False,
    event_types=None,
    cooldown=0,
    ignore_at_check=False,
    block=False,
    fallback=False,
)
~~~

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `pattern` | 必填 | 正则表达式，使用 `re.DOTALL` 编译并通过 `search()` 匹配 |
| `name` | 函数名 | Web 面板和日志中的处理器名称 |
| `desc` | 空字符串 | 功能描述 |
| `priority` | `0` | 数字越大越先匹配、先执行 |
| `owner_only` | `False` | 消息事件仅允许当前机器人配置的主人触发 |
| `group_only` | `False` | 仅群聊场景 |
| `direct_only` | `False` | 仅私聊场景 |
| `channel_only` | `False` | 仅频道场景 |
| `event_types` | `None` | 只接收指定事件类型 |
| `cooldown` | `0` | 兼容保留字段；分发器不执行冷却 |
| `ignore_at_check` | `False` | 未开启全量消息时也允许匹配未 @ 消息 |
| `block` | `False` | 命中后停止收集后续低优先级处理器 |
| `fallback` | `False` | 只在普通处理器未命中后参与匹配；也可传入 `Callable[[Event], bool]` |

`owner_only` 属于消息分发权限。生命周期事件和其他非消息事件走快速分发路径，不应把 `owner_only` 当作这类事件的授权校验。

### 4.2 事件类型

事件常量位于 `core.message.event`：

~~~python
from core.message.event import GROUP_JOIN_REQUEST


@handler(r'.*', event_types=[GROUP_JOIN_REQUEST], group_only=True)
async def on_join_request(event, match):
    ...
~~~

| 分类 | 常量 | 含义 |
| --- | --- | --- |
| 群消息 | `GROUP_AT_MESSAGE_CREATE` | 群聊 @ 机器人消息 |
| 群消息 | `GROUP_MESSAGE_CREATE` | 群聊全量消息 |
| 私聊 | `C2C_MESSAGE_CREATE` | 单聊消息 |
| 频道 | `AT_MESSAGE_CREATE` | 频道 @ 机器人消息 |
| 频道 | `MESSAGE_CREATE` | 频道公开消息 |
| 频道 | `DIRECT_MESSAGE_CREATE` | 频道私信 |
| 交互 | `INTERACTION_CREATE` | 按钮或其他交互回调 |
| 群生命周期 | `GROUP_ADD_ROBOT` / `GROUP_DEL_ROBOT` | 机器人加入或退出群 |
| 群生命周期 | `GROUP_MEMBER_ADD` / `GROUP_MEMBER_REMOVE` | 用户加入或退出群 |
| 群管理 | `GROUP_JOIN_REQUEST` | 用户提交入群申请 |
| 群状态 | `GROUP_MSG_REJECT` / `GROUP_MSG_RECEIVE` | 群拒绝或恢复接收消息 |
| 好友 | `FRIEND_ADD` / `FRIEND_DEL` | 添加或删除机器人好友 |
| 订阅 | `SUBSCRIBE_MESSAGE_STATUS` | 用户开启或关闭消息订阅 |

没有设置 `event_types` 的处理器会参与所有已分发事件的匹配。`MESSAGE_REACTION_ADD`、`MESSAGE_REACTION_REMOVE` 和 `GUILD_UPDATE` 只由框架记录，不会分发给插件处理器。

### 4.3 Event 字段

常用字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `event.appid` | `str` | 当前机器人 AppID |
| `event.event_type` | `str` | 事件类型 |
| `event.event_id` | `str` | 事件 ID |
| `event.message_id` | `str` | 消息 ID |
| `event.message_type` | `int 或 None` | 平台消息内容类型，如复合消息为 `103` |
| `event.user_id` | `str` | 按身份配置转换后的用户 ID |
| `event.raw_user_id` | `str` | 平台原始用户 OpenID |
| `event.union_openid` | `str 或 None` | 跨机器人统一用户 ID，可能为空 |
| `event.username` | `str 或 None` | 用户昵称，可能为空 |
| `event.group_id` | `str 或 None` | 群 OpenID |
| `event.guild_id` | `str 或 None` | 频道服务器 ID |
| `event.channel_id` | `str 或 None` | 频道 ID |
| `event.content` | `str` | 供处理器匹配的文本 |
| `event.raw_content` | `str` | 原始消息文本 |
| `event.attachments` | `list` | 消息顶层附件列表 |
| `event.image_url` | `str 或 None` | 消息中的首个图片地址 |
| `event.msg_elements` | `list` | 平台原始消息元素 |
| `event.parallel_message` | `dict` | 复合消息数据，子消息位于 `msg_nodes`；无数据时为空字典 |
| `event.member_role` | `str` | 发送者群身份，如 `admin`、`owner` |
| `event.bot_member_role` | `str` | mentions 中解析出的机器人群身份 |
| `event.message_reference_id` | `str` | 当前消息可引用的 REFIDX |
| `event.interaction_data` | `dict 或 None` | 交互回调数据 |
| `event.subscribe_results` | `list` | 订阅状态变化结果 |
| `event.raw` | `dict 或 None` | 原始事件载荷；处理链结束后可能被释放 |
| `event.error` | `dict 或 None` | 上一次媒体上传失败响应 |

入群申请事件额外提供：

| 字段 | 说明 |
| --- | --- |
| `event.join_request_id` | 入群申请 ID |
| `event.apply_at` | 申请时间 |
| `event.apply_source` | 申请来源 |
| `event.invited_by` | 邀请人 OpenID，可能为空 |
| `event.verify_info` | 平台验证信息原始对象 |
| `event.verify_method` | 验证方式，例如 `admin_review_qa` |
| `event.review_qa_list` | 管理员审核问答列表，每项包含 `question` 和 `answer` |

场景与 @ 标识：

| 字段 | 说明 |
| --- | --- |
| `event.is_group` / `is_direct` / `is_channel` | 当前会话场景 |
| `event.is_interaction` | 是否为交互回调 |
| `event.is_lifecycle` | 是否为生命周期事件 |
| `event.is_bot` | 发送者是否为机器人 |
| `event.is_at_self` | 是否 @ 当前机器人 |
| `event.is_at_other_bot` | 是否 @ 其他机器人 |
| `event.is_at_other_user` | 是否 @ 其他用户 |
| `event.is_at_all` | 是否 @ 全体成员 |
| `event.mentions` | 原始 mentions 列表 |

派生属性和工具：

~~~python
chat_type = event.chat_type  # group / direct / channel / unknown
chat_id = event.chat_id
author_id = event.get('d/author/id')
sender = event.sender
~~~

不要在后台任务中长期保留完整 `event`。需要延迟处理时，复制必要的 ID 和业务字段即可。

### 4.4 拦截、阻断与兜底

拦截器在消息处理器之前执行，返回 `True` 时终止本次消息分发：

~~~python
from core.plugin.decorators import interceptor


@interceptor(priority=100)
async def filter_keywords(event):
    if '违禁词' in (event.content or ''):
        await event.reply('消息包含不可用内容')
        return True
    return False
~~~

多个处理器命中同一条消息时，默认按优先级依次执行。`block=True` 会停止收集后续低优先级处理器：

~~~python
@handler(r'^状态$', name='系统状态', priority=10, block=True)
async def system_status(event, match):
    await event.reply('系统正常')


@handler(r'^状态$', name='业务状态', priority=0)
async def business_status(event, match):
    # 上一个处理器命中并阻断时不会执行
    await event.reply('业务正常')
~~~

`fallback=True` 适合 AI 对话等兜底处理器。框架会先让普通处理器匹配原文，再匹配自动加上或移除 `/` 的兼容文本；均未命中时才进入兜底阶段：

~~~python
@handler(r'(?s)^(.+)$', name='自然对话', priority=-50, fallback=True)
async def chat(event, match):
    await event.reply(await ask_model(match.group(1)))
~~~

运行时开关可以使用函数形式：

~~~python
@handler(
    r'(?s)^(.+)$',
    fallback=lambda event: bool(ctx.read_config().get('fallback_enabled')),
)
async def conditional_chat(event, match):
    ...
~~~

---

## 5. 消息 API

`event` 会把常用发送方法代理到当前机器人的 `MessageSender`。被动回复优先使用 `event.reply*()`，跨会话或延迟发送使用 `send_to_*()`。

### 5.1 回复文本与媒体

~~~python
await event.reply('Hello!')

await event.reply_image('https://example.com/image.png', '图片说明')
await event.reply_image(image_bytes, '本地图片')
await event.reply_voice('https://example.com/audio.wav')
await event.reply_video('https://example.com/video.mp4')
await event.reply_file('/path/to/report.pdf', '报告', file_name='report.pdf')

# 自动撤回，单位为秒
await event.reply('这条消息将在 5 秒后撤回', auto_delete_time=5)
~~~

`reply()` 的核心参数：

~~~python
await event.reply(
    content=None,
    buttons=None,
    media=None,
    msg_type=None,
    template_name=None,
    template_vars=None,
    prompt_buttons=None,
    auto_delete_time=None,
    skip_suffix=False,
    message_reference_id=None,
    message_reference=None,
    button_font_size=None,
    button_style=None,
    # 其他关键字会合并到平台载荷
)
~~~

媒体方法支持 URL 或 `bytes`；`reply_file()` 还支持本地路径。使用 `target_group_id` 或 `target_user_id` 可将媒体主动发送到其他会话：

~~~python
await event.reply_image(
    image_bytes,
    '日报',
    target_group_id='目标群 OpenID',
)
~~~

媒体上传失败时返回 `None`，原始失败响应会写入 `event.error`。

### 5.2 私聊流式消息

流式消息仅支持 `C2C_MESSAGE_CREATE`。`content_type='text'` 发送普通文本，
`content_type='markdown'` 发送 Markdown；两者都接受文本增量：

~~~python
import asyncio


async def generate_chunks():
    for text in ('正在', '生成', '**Markdown**', ' 回复'):
        await asyncio.sleep(0.3)
        yield text


@handler(
    r'^流式消息$',
    direct_only=True,
    event_types=['C2C_MESSAGE_CREATE'],
)
async def stream_reply(event, match):
    await event.reply_stream(
        generate_chunks(),
        content_type='markdown',  # 普通文本改为 text
    )
~~~

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `chunks` | 必填 | 字符串、同步或异步迭代器；每项可为字符串，或 `{'type': 'delta'/'replace', 'text': '...'}` |
| `content_type` | 机器人配置 | `text` 普通文本；`markdown` Markdown |
| `input_mode` | `replace` | `replace` 每次提交累计正文；`append` 每次只提交新增正文 |
| `min_interval` | `0.25` | 中间分片最小发送间隔，单位秒 |
| `msg_id` / `event_id` | 自动取当前事件 | 显式关联平台消息或事件 |
| `msg_seq` | 自动生成 | 同一条流式消息使用的消息序列号 |
| `is_wakeup` | `False` | 仅主动发送支持，标记召回消息 |

主动发送使用相同参数，但第一个参数为用户 OpenID：

~~~python
await event.send_stream_to_user(
    user_openid,
    generate_chunks(),
    content_type='text',
)
~~~

两个方法都返回结束分片的平台响应；没有正文时返回 `None`。参数错误抛出
`ValueError`，平台发送失败抛出 `RuntimeError`。已发送的正文前缀不能被后续
`replace` 片段修改。

### 5.3 消息类型

文本消息默认根据 `message.use_markdown` 选择 Markdown 或纯文本。单条消息可以强制覆盖：

~~~python
await event.reply('**原样文本**', msg_type=0)
await event.reply('**加粗文本**', msg_type=2)
await event.reply('不附加全局 Markdown 后缀', msg_type=2, skip_suffix=True)
await event.reply(
    '![示例 #208px #320px](https://example.com/image.png)',
    msg_type=2,
    force_verify_image_resource=True,
)
~~~

`force_verify_image_resource=True` 会要求平台在发送前确认 Markdown 图片资源转存成功；任一图片转存失败时，整条消息发送失败。默认关闭，保持原有行为。该参数也适用于 `send_to_group()` 和 `send_to_user()`。

| `msg_type` | 说明 |
| --- | --- |
| `0` | 纯文本 |
| `2` | 原生 Markdown |
| `3` | Ark，由 `reply_ark()` 构建 |
| `7` | 富媒体，由媒体方法构建 |
| `8` | 卡片，由 `reply_card()` 构建 |


### 5.4 按钮与交互回调

按钮使用二维数组表示行和列：

~~~python
buttons = [
    [
        {'text': '打开官网', 'link': 'https://example.com'},
        {'text': '查看状态', 'type': 1, 'data': 'query_status'},
        {'text': '/帮助', 'type': 2, 'data': '/帮助'},
    ],
]

await event.reply('请选择操作', buttons=buttons)
~~~

按钮字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `id` | 自动编号 | 按整组键盘从 `0` 开始递增；也可显式指定稳定 ID |
| `text` | 空字符串 | 显示文字 |
| `type` | `2` | `0` 跳转、`1` 回调、`2` 输入、`4` 订阅 |
| `data` | 空字符串 | 链接、回调数据或输入内容 |
| `link` | 无 | 链接简写，会覆盖 `type` 和 `data` |
| `show` | `text` | 点击后的显示文字 |
| `style` | `1` | `0` 灰框、`1` 蓝框、`2` 黑框、`3` 红字、`4` 蓝底 |
| `enter` | `False` | 点击后直接发送；开启 `button_enter_to_send` 时，type 2 会转换为回调 |
| `reply` | `False` | 点击后作为引用回复发送 |
| `limit` | 无 | 点击次数限制 |
| `tips` | 无 | 客户端不支持时的提示 |
| `modal` | 无 | 二次确认内容，可传字符串或字典 |
| `subscribe` | 无 | 订阅模板 ID、ID 列表或原生订阅字典 |

权限字段按以下优先级取第一个：`permission` > `role` > `list` > `admin` > 所有人。平台原生 `action`、`render_data`、`subscribe_data`、`click_limit`、`unsupport_tips` 和 `anchor` 也可直接传入。

整组按钮字号：

~~~python
await event.reply(
    '小按钮',
    buttons=buttons,
    button_font_size='small',  # small / middle / large
)

await event.reply(
    '自定义小按钮样式',
    buttons={'rows': buttons, 'style': {'font_size': 'small'}},
)
~~~

扩展 prompt 按钮最多三个：

~~~python
await event.reply('请选择', prompt_buttons=['选项 A', '选项 B'])
await event.reply('请选择', prompt_buttons=[('确认', 1), ('取消', 0)])
~~~

用户点击 `type=1` 的回调按钮后会产生 `INTERACTION_CREATE` 事件，`event.content` 为按钮的 `data`：

~~~python
from core.message.event import INTERACTION_CREATE


@handler(r'^query_status$', event_types=[INTERACTION_CREATE])
async def on_query_status(event, match):
    event.set_callback_code(0)
    await event.reply('状态正常')
~~~

框架默认在 2 秒或分发结束时返回回调码 `0`。确需等待较长处理时，可先调用 `event.set_ack_timeout(seconds)`；通常不需要旧式 `await event.ack_interaction(...)` REST 应答。

### 5.5 Ark 与卡片消息

Ark 简写：

~~~python
await event.reply_ark(23, (
    '列表标题',
    '提示文本',
    [['项目 1'], ['项目 2', 'https://example.com']],
))
~~~

`reply_ark()` 支持模板 `23`、`24` 和 `37`，元组字段顺序由对应模板定义。

卡片消息 `msg_type=8`：

~~~python
await event.reply_card('tuwen', (
    '标题',
    '描述',
    'https://example.com/image.png',
    'https://example.com',
))
~~~

传入字典时，数据会写入 `card.content`。

### 5.6 主动消息

~~~python
ok, data, payload = await event.send_to_group(group_id, '群通知')
ok, data, payload = await event.send_to_user(user_id, '私聊通知')
ok, data = await event.send_to_channel(channel_id, '频道通知')

ok, data = await event.send_image('group', group_id, image_bytes, '图片通知')
~~~

`send_to_group()` 和 `send_to_user()` 支持 `buttons`、`media`、`msg_type`、`skip_suffix`、`message_reference_id` 等消息载荷参数。传入 `msg_id` 或 `event_id` 时，会关联已有消息或事件。

没有 `event` 的定时任务应通过公开应用对象获取 Sender：

~~~python
from core.application import get_app

app = get_app()
bot = app.get_bot(appid) if app else None
if bot:
    await bot.sender.send_to_group(group_id, '定时通知')
~~~

不要依赖 `_bot_manager_ref`、`_bots` 等私有属性。

### 5.7 引用、撤回与返回值

引用消息需要 REFIDX，而不是普通平台消息 ID：

~~~python
# 引用用户当前消息
await event.reply(
    '引用回复',
    message_reference_id=event.message_reference_id,
)

# 引用机器人刚发送的消息
data = await event.reply('第一条')
ref_id = (data or {}).get('ext_info', {}).get('ref_idx', '')
if ref_id:
    await event.reply('第二条', message_reference_id=ref_id)
~~~

需要完全控制引用对象时，可传 `message_reference={...}`，其优先级高于 `message_reference_id`。

撤回消息：

~~~python
await event.recall()
await event.recall(message_id='平台消息 ID')
~~~

| 方法 | 返回值 |
| --- | --- |
| `reply()`、`reply_image()`、`reply_ark()` 等 | 平台响应字典；失败为 `None` |
| `reply_stream()` / `send_stream_to_user()` | 最终分片的平台响应字典；无正文时为 `None` |
| `send_to_group()` / `send_to_user()` | `(ok, data, payload)` |
| `send_to_channel()` | `(ok, data)` |
| `send_wakeup()` | `(ok, 消息 ID 或失败原因)` |

平台响应中的常用字段为 `data['id']` 和 `data['ext_info']['ref_idx']`。

### 5.8 订阅消息

订阅按钮必须附加在 Markdown 消息上，并使用真实有效的模板 ID：

~~~python
template_id = '102134274_1749040268'
buttons = [[{
    'id': 'subscribe_report',
    'text': '订阅日报',
    'show': '已订阅',
    'subscribe': template_id,
    'modal': {
        'content': '确认订阅日报？',
        'confirm_text': '确认',
        'cancel_text': '取消',
    },
}]]

await event.reply('日报订阅', buttons=buttons, msg_type=2)
~~~

状态变化会产生 `SUBSCRIBE_MESSAGE_STATUS` 事件，结果位于 `event.subscribe_results`。框架会自动记录目标、模板和 `subscribe_id`。

发送订阅消息时必须带上对应 `subscribe_id`，否则会按普通主动消息处理：

~~~python
records = bot.log_service.subscribe_get_by_target(group_id)
record = next(
    (item for item in records if item['template_id'] == template_id),
    None,
)

if record:
    ok, data, payload = await event.send_to_group(
        group_id,
        '日报已生成',
        subscribe_id=record['subscribe_id'],
    )
    if ok and record['sub_type'] == 'once':
        await bot.log_service.subscribe_consume(template_id, group_id)
~~~

### 5.9 Sender 工具方法

不属于普通回复流程的能力通过 `event.sender` 调用：

~~~python
url = await event.sender.get_share_link(callback_data='source_plugin')

size = await event.sender.get_image_size(image_bytes)
# {'width': 1920, 'height': 1080, 'px': '#1920px #1080px'}

file_info = await event.sender.upload_media(
    event,
    file_bytes,
    file_type=1,  # 1 图片、2 视频、3 语音、4 文件
    file_name='image.png',
)

member = await event.sender.get_group_member(group_id, member_openid)
bot_member = await event.sender.get_bot_member(group_id)

ok, result = await event.send_wakeup(user_id, '召回提示')
ok, result = await event.sender.force_wakeup(user_id, '强制召回')
~~~

### 5.10 自定义菜单与指令面板

自定义菜单仅在单聊窗口生效，更新会覆盖完整配置：

~~~python
menu, error = await event.sender.get_global_menu(return_error=True)

ok, response = await event.sender.update_global_menu({
    'items': [
        {'type': 'send_message', 'name': '帮助', 'send_message': '/help'},
        {'type': 'link', 'name': '官网', 'link': 'https://example.com'},
    ],
})
~~~

菜单字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `items` | `list[MenuItem]` | 一级菜单，最多 10 项 |
| `MenuItem.name` | `str` | 按钮名称，最多 10 个字符 |
| `MenuItem.type` | `str` | `send_message`、`link`、`switch` 或 `menu` |
| `send_message` | `str` | `send_message` 类型填入聊天框的内容 |
| `link` | `str` | `link` 类型的 HTTPS 地址 |
| `switch` | `dict` | `{'switch_id': str, 'default': bool}` |
| `sub_menu_items` | `list[SubMenuItem]` | `menu` 类型的子菜单，最多 5 项 |
| `SubMenuItem` | `dict` | 支持 `name`、`type`、`send_message`、`link`；不可继续嵌套 |

菜单方法：

| 方法 | 参数 | 返回值 |
| --- | --- | --- |
| `get_global_menu(return_error=False)` | `return_error=True` 可取得错误详情 | 数据或 `None`；详细模式为 `(data, error)` |
| `update_global_menu(menu=None)` | `menu` 为完整菜单；`None` 清空菜单 | `(ok, response)` |

指令面板支持 `c2c`、`group`、`channel`、`dm`：

~~~python
page = await event.sender.get_panels('group', limit=20)
panel = {
    'items': [
        {'type': 'command', 'name': '/签到', 'desc': '每日签到'},
    ],
    'remark': '群聊常用指令',
}

ok, response = await event.sender.create_panel(
    'group',
    panel,
    target_type='specific',
    group_openids=[event.group_id],
)
~~~

面板方法：

| 方法 | 参数 | 返回值 |
| --- | --- | --- |
| `get_panels(scope, cursor='', limit=20, return_error=False)` | `scope` 必填；`limit` 最大 50 | 分页数据或错误元组 |
| `create_panel(scope, panel, target_type='all', ...)` | 指定范围时传 `user_openids` 或 `group_openids` | `(ok, response)` |
| `get_panel(panel_id, return_error=False)` | `panel_id` 必填 | 详情或错误元组 |
| `update_panel(panel_id, panel)` | 覆盖 `items` 和 `remark` | `(ok, response)` |
| `delete_panel(panel_id)` | `panel_id` 必填 | `(ok, response)` |
| `update_panel_targets(panel_id, op, ...)` | `op` 为 `add` 或 `del` | `(ok, response)` |

`panel` 与指令项字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `panel.items` | `list[PanelItem]` | 面板元素，最多 20 项 |
| `panel.remark` | `str` | 开发者备注，最多 255 个字符，不展示给用户 |
| `PanelItem.type` | `str` | `command` 指令或 `link` 链接 |
| `PanelItem.name` | `str` | 名称；指令类型点击后会填入聊天框 |
| `PanelItem.desc` | `str` | 展示说明，最多 30 个字符 |
| `PanelItem.only_admin` | `bool` | 是否仅群或频道管理员可用 |
| `PanelItem.link` | `str` | 仅 `link` 类型使用 |

范围参数：

| 参数 | 说明 |
| --- | --- |
| `scope` | `c2c` 单聊、`group` 群聊、`channel` 文字子频道、`dm` 频道私信 |
| `target_type` | `all` 全局；`specific` 指定对象，仅支持 `c2c` 和 `group` |
| `user_openids` | 单聊指定用户列表，单次最多 20 个 |
| `group_openids` | 群聊指定群列表，单次最多 20 个 |

修改关联对象：

~~~python
ok, response = await event.sender.update_panel_targets(
    panel_id,
    'add',  # add 添加，del 移除
    group_openids=['group_openid'],
)
~~~

查询方法默认失败返回 `None`；需要错误详情时传 `return_error=True`。写操作
统一返回 `(ok, response)`。

---

## 6. 群管理 API

### 6.1 权限与接口概览

以下接口用于读取群资料、处理入群申请和设置群禁言。平台会校验机器人权限。

| 方法 | 用途 | 返回值 |
| --- | --- | --- |
| `get_group_record(group_id)` | 读取本地完整群记录，不调用平台接口 | 字典或 `None` |
| `get_group_info(group_id, return_error=False)` | 获取群名称和人数并保存 | 数据、`None` 或错误元组 |
| `get_group_bot_state(group_id, return_error=False)` | 获取机器人群身份与消息权限并保存 | 数据、`None` 或错误元组 |
| `refresh_group_info(group_id)` | 并发刷新群资料和机器人状态 | 汇总字典 |
| `get_group_join_requests(group_id, cursor='', limit=20, return_error=False)` | 分页查询入群申请 | 分页数据、`None` 或错误元组 |
| `review_group_join_request(...)` | 通过或拒绝入群申请 | `(ok, response)` |
| `get_group_restrict_chat_setting(group_id, return_error=False)` | 查询全员和成员禁言状态 | 数据、`None` 或错误元组 |
| `set_group_member_mute(group_id, members)` | 批量设置或解除成员禁言 | `(ok, response)` |

平台接口有调用频率限制。

### 6.2 群资料与本地记录

读取 `data.db` 中已有群记录，不产生平台请求：

~~~python
group = await event.get_group_record(event.group_id)
if group:
    print(group['group_name'], group['group_member_num'], group['is_admin'])
~~~

返回字段：

| 字段 | 说明 |
| --- | --- |
| `group_id` / `group_name` | 群 OpenID 和群名称 |
| `users` / `group_member_num` | 已保存的成员列表和群人数 |
| `is_admin` | 机器人是否为群主或管理员 |
| `is_full_access` | 是否接收群全量消息 |
| `allow_proactive_msg` | 是否允许主动推送 |
| `in_group` | 机器人是否仍在群内 |

刷新平台数据：

~~~python
result = await event.sender.refresh_group_info(event.group_id)
print(result['group_info'], result['bot_state'], result['errors'])
~~~

接口识别到群无效或机器人已退群时，会同步修正本地群记录。

### 6.3 入群申请事件

监听入群申请事件：

~~~python
from core.message.event import GROUP_JOIN_REQUEST
from core.plugin.decorators import handler


@handler(r'.*', event_types=[GROUP_JOIN_REQUEST], group_only=True)
async def on_join_request(event, match):
    print(event.group_id, event.raw_user_id, event.join_request_id)
    for item in event.review_qa_list:
        print(item.get('question', ''), item.get('answer', ''))
~~~

### 6.4 查询与审批入群申请

分页查询申请列表：

~~~python
page, error = await event.sender.get_group_join_requests(
    event.group_id,
    limit=20,
    return_error=True,
)
if page:
    print(page['list'], page['next_cursor'])
~~~

`limit` 会被限制在 1 到 100 之间。`next_cursor` 为空表示已到最后一页。

通过申请：

~~~python
ok, response = await event.sender.review_group_join_request(
    event.group_id,
    member_openid,
    'approve',
    join_request_id=join_request_id,
)
~~~

拒绝申请，可选填写理由并加入群成员黑名单：

~~~python
ok, response = await event.sender.review_group_join_request(
    event.group_id,
    member_openid,
    'decline',
    join_request_id=join_request_id,
    reject_reason='不符合入群要求',
    add_to_member_blacklist=True,
)
~~~

`op` 仅支持 `approve` 和 `decline`。`reject_reason` 与 `add_to_member_blacklist` 只在拒绝时写入请求。

### 6.5 群禁言

查询全员禁言规则和成员禁言列表：

~~~python
setting, error = await event.sender.get_group_restrict_chat_setting(
    event.group_id,
    return_error=True,
)
if setting:
    print(setting.get('global_rule'), setting.get('members'))
~~~

成员禁言通过 `members` 列表批量提交，单次最多 10 人：

| 字段 | 说明 |
| --- | --- |
| `op` | `add` 添加、`update` 更新、`del` 解除 |
| `member_openid` | 目标成员 OpenID |
| `mute_expire_at` | 带时区的 ISO 8601 到期时间；解除禁言时不需要 |

设置禁言：

~~~python
from datetime import datetime, timedelta

minutes = 30
expire_at = (
    datetime.now().astimezone() + timedelta(minutes=minutes)
).isoformat(timespec='seconds')

members = [{
    'op': 'add',
    'member_openid': member_openid,
    'mute_expire_at': expire_at,
}]
ok, response = await event.sender.set_group_member_mute(event.group_id, members)
~~~

解除禁言：

~~~python
members = [{'op': 'del', 'member_openid': member_openid}]
ok, response = await event.sender.set_group_member_mute(event.group_id, members)
~~~

### 6.6 返回值与错误处理

查询方法默认失败返回 `None`。需要展示或记录具体原因时，传入 `return_error=True`：

~~~python
data, error = await event.sender.get_group_info(
    event.group_id,
    return_error=True,
)
if error:
    ctx.log.warning('获取群资料失败：%s', error)
~~~

写操作返回 `(ok, response)`：

~~~python
ok, response = await event.sender.set_group_member_mute(event.group_id, members)
if not ok:
    ctx.log.warning('设置禁言失败：%s', response)
~~~

---

## 7. Web 面板扩展

插件可以注册自定义页面：

~~~python
from core.plugin.decorators import on_unload
from core.plugin.web_pages import register_page, unregister_page


register_page(
    key='my-plugin',
    label='我的插件',
    source='plugin',
    source_name='my_plugin',
    html_file=ctx.get_resource_path('assets/panel.html'),
    icon='settings',
)


@on_unload
def cleanup_page():
    unregister_page('my-plugin')
~~~

`key` 必须全局唯一。页面不会按插件所有者自动清理，因此必须在 `on_unload` 中调用 `unregister_page()`。

插件 HTTP 路由必须以 `/api/ext/` 开头：

~~~python
from aiohttp import web
from core.plugin.web_pages import register_route


@register_route('GET', '/api/ext/my-plugin/status')
async def status(request):
    return web.json_response({'ok': True})


@register_route('POST', '/api/ext/my-plugin/callback', auth=False)
async def callback(request):
    body = await request.json()
    return web.json_response({'received': bool(body)})
~~~

| 参数 | 说明 |
| --- | --- |
| `method` | HTTP 方法，如 `GET`、`POST` |
| `path` | 精确路径，必须以 `/api/ext/` 开头，不支持路径参数 |
| `handler` | 接收 `aiohttp.web.Request` 的异步处理函数 |
| `auth` | 默认 `True`，复用 Web 面板登录认证 |

路由应在插件导入期间注册，以便框架记录插件所有者并在卸载时自动清理。仅对确实需要公开访问的回调使用 `auth=False`，并自行实现签名校验、防重放和限流。

---

## 8. Image Hosting 模块

插件通过 `Application.module_manager` 获取 Image Hosting 模块：

~~~python
from core.application import get_app

app = get_app()
hosting = app.module_manager.get('image_hosting') if app else None

if hosting:
    url = await hosting.upload_any(image_bytes, 'report.png')
else:
    url = None
~~~

模块未启用或初始化失败时，`get()` 返回 `None`。单个图床可能因依赖、凭据或网络不可用，上传前可检查状态：

~~~python
status = hosting.status()
if status.get('cos'):
    result = await hosting.upload_cos_url(
        image_bytes,
        'report.png',
        user_id=event.user_id,
    )
else:
    result = await hosting.upload_any(image_bytes, 'report.png')
~~~

统一入口 `upload_any()` 会按优先级尝试可用图床，返回第一个 HTTP URL，全部失败时返回 `None`。指定图床的返回结构可能不同，需要按对应图床 API 判断。

配置、图床列表、动态方法和扩展规范见 [Image Hosting 模块接入文档](modules/image_hosting/README.md)。模块实例由框架管理生命周期。

---

## 9. 调试与运行限制

### 日志与异常

通过插件上下文记录日志：

~~~python
ctx.log.info('任务开始')

try:
    await risky_operation()
except Exception:
    ctx.log.exception('任务执行失败')
    await event.reply('操作失败，请稍后重试')
~~~

需要写入框架错误中心时：

~~~python
from core.base.logger import PLUGIN, report_error

try:
    await risky_operation()
except Exception as error:
    report_error(PLUGIN, '我的插件', error)
~~~

处理器总执行时间上限为 300 秒。网络请求、渲染和外部命令应设置更短的业务超时。

同步 handler 在线程池中运行，不能直接使用 `await`。`async def` 中调用同步 IO 会阻塞事件循环，可通过 `asyncio.to_thread()` 或 executor 执行。由 `on_load` 创建的后台任务和客户端，应在 `on_unload` 中关闭。

---

## 10. 参考实现

| 路径 | 内容 |
| --- | --- |
| [plugins/alone/示例插件.py](plugins/alone/示例插件.py) | 媒体、卡片、按钮、交互、群资料、入群审批、禁言、引用、主动消息和 Web 扩展示例 |
| [plugins/system/main.py](plugins/system/main.py) | 内置系统插件的组织方式和管理命令 |
| [modules/image_hosting/README.md](modules/image_hosting/README.md) | 统一图床完整 API 与配置 |

插件可以在 Web 面板的“插件”页面启用、禁用或重载；禁用状态保存在 `data/plugins_disabled.json`。
