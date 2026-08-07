# OneBot Adapter 模块接入文档

OneBot Adapter 将 ElainaBot 的消息事件转换为 OneBot 11 事件，并提供正向/反向 WebSocket、正向 HTTP、反向 HTTP Webhook 四种连接方式。它主要用于让外部 OneBot 框架接入本机器人。

## 模块状态与插件接入

```python
from core.application import get_app

app = get_app()
mm = app.module_manager if app else None
adapter_enabled = bool(mm and mm.is_enabled("onebot_adapter"))
ctx = mm.get_context("onebot_adapter") if mm else None
```

该模块的 `setup()` 不返回业务实例，因此 `mm.get("onebot_adapter")` 在启用时是 `True`。插件需要读取模块状态使用 `is_enabled()`，需要注册 hook 使用 `get_context()`。不要依赖模块内部的 `_adapter`、`_action_registry` 等私有对象。

### 监听转换后的原始事件

```python
from core.application import get_app

app = get_app()
ctx = app.module_manager.get_context("onebot_adapter") if app else None

if ctx:
    @ctx.hook("on_raw_event", priority=100)
    async def observe_onebot_event(event, bot):
        # event 是 ElainaBot Event，bot 是对应 BotInstance
        print(event.type, event.appid)
```

`on_raw_event` 的参数是 `(event, bot)`。适合旁路记录、统计或扩展自己的 OneBot 推送逻辑；不要在此 hook 中修改事件对象，也不要重复发送同一条消息。

## 连接配置

配置文件为 `modules/onebot_adapter/data/config.yaml`。顶层字段：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `connections` | `list[dict]` | 一个 `ws_server` | 连接列表，可同时配置多种连接 |
| `heartbeat_interval` | `int` | `30` | WebSocket 心跳间隔（秒） |
| `debug` | `bool` | `false` | 输出完整收发载荷 |

每个连接至少包含 `type`、`name`、`enable`、`appid`、`access_token`（未配置时为空）。`type` 可选：

| 类型 | 方向 | 关键字段 | 地址/路径 |
| --- | --- | --- | --- |
| `ws_server` | 外部框架连入本机 | `path`、可选独立 `port` | `ws://<框架地址>:<框架端口><path>`；`path: "/"` 时不校验路径 |
| `ws_reverse` | 本机主动连接外部框架 | `url`、`reconnect_interval` | 外部 WebSocket URL |
| `http_server` | 外部框架调用本机 API | `path` | `POST <框架地址><path>/<action>` |
| `http_webhook` | 本机向外部上报事件 | `url`、可选 `secret`、`timeout` | 外部 HTTP URL |

启用 `ws_server` 或 `http_server` 时，路由挂载在框架 HTTP 服务端口；正向 WebSocket 若填写 `port > 0`，则单独监听该端口。

## 支持的 OneBot Action

外部客户端发送 JSON：

```json
{"action":"send_msg","params":{"group_id":123456,"message":"你好"},"echo":"req-1"}
```

响应遵循 OneBot 11：成功为 `{"status":"ok","retcode":0,"data":...}`，失败为 `status: "failed"`，并在存在 `echo` 时原样带回。

| Action | 必要参数 | 结果 |
| --- | --- | --- |
| `send_msg` | `group_id` 或 `user_id`、`message` | `data.message_id` |
| `send_group_msg` | `group_id`、`message` | `data.message_id` |
| `send_private_msg` | `user_id`、`message` | `data.message_id` |
| `delete_msg` | `message_id` | 当前实现依赖消息 ID，失败会返回 `retcode=1` |
| `get_login_info` | 无 | `user_id`、`nickname` |
| `get_group_list` | 无 | 已缓存群列表 |
| `get_friend_list` | 无 | 已缓存用户列表 |
| `get_stranger_info` | `user_id` | 最小用户信息 |
| `get_group_member_info` | `group_id`、`user_id` | 群成员缓存信息；未缓存时返回默认字段 |
| `get_group_member_list` | 无 | 当前实现返回空列表 |
| `get_status` | 无 | `online`、`good` |
| `get_version_info` | 无 | 适配器版本信息 |
| `can_send_image` | 无 | `{"yes": true}` |
| `can_send_record` | 无 | `{"yes": true}` |

### 消息参数

`message` 支持纯文本和 OneBot 消息段数组。常用段包括 `text`、`image`、`record`、`video`、`file`、`markdown`、`reply`、`at`、`face` 和 `keyboard`。图片、语音、视频、文件段可使用 `file`、`url` 或 `data`，具体可用字段取决于下游发送器和平台权限。

```json
{
  "action": "send_group_msg",
  "params": {
    "group_id": 123456,
    "message": [
      {"type": "text", "data": {"text": "今日图片："}},
      {"type": "image", "data": {"file": "https://example.com/a.png"}}
    ]
  }
}
```

群号/用户号会通过适配器的 ID 映射转换为平台 OpenID；如果没有映射，调用会返回“未知群号/用户”。插件可通过正常的 ElainaBot 事件和 `event.reply()` 发送消息，不需要手动调用 OneBot action。

## 外部事件格式

启用 `ws_reverse` 或 `http_webhook` 后，模块会把框架事件转换为 OneBot 11 风格事件并推送。生命周期事件和消息事件均来自 `on_raw_event`，推送目标按连接中的 `appid` 过滤；未填写 `appid` 的连接接收默认机器人事件。

## 安全与兼容性

- 生产环境务必设置 `access_token`；反向 Webhook 可设置 `secret` 做签名校验。
- 不要把 HTTP API 或 WebSocket 直接暴露到不可信网络。
- `debug` 可能记录消息内容和凭据相关载荷，只建议临时排障使用。
- 适配器连接由模块统一启停；插件不要自行创建同路径的 HTTP/WS 路由。
