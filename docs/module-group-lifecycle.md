# 模块群生命周期订阅（API v1）

框架原有 `Event` 解析器和插件 `event_types` 继续工作。模块可额外订阅
`GROUP_MEMBER_ADD`、`GROUP_MEMBER_REMOVE`、`GROUP_ADD_ROBOT`、`GROUP_DEL_ROBOT`，
无须启用任何插件或匹配命令。连接及会话状态机保持现有行为。

```python
async def setup(ctx):
    if ctx.group_lifecycle_api_version != 1:
        raise RuntimeError('unsupported group lifecycle API')

    async def on_change(change):
        # 使用 appid、group_openid 和 member_openid 定位原生主体。
        # 先同步更新内存版本/使旧状态失效；需要纳入完成保证的工作必须 await。
        # 缺身份、缺顺序或处理失败时的业务策略由模块决定。
        await invalidate(change)

    subscription = ctx.subscribe_group_lifecycle(
        on_change, appid=None, timeout=5.0,
    )
    return subscription
```

`subscribe_group_lifecycle` 只接受 async 函数，`appid=None` 订阅所有 Bot，字符串值
限定一个 Bot；`timeout` 必须是有限正秒数。返回 `GroupLifecycleSubscription`。
`subscription.cancel()` 幂等取消订阅。每个 ModuleContext 独立拥有订阅，模块禁用、
重载、卸载和 setup 异常/取消均注销该上下文订阅；旧上下文不能重新订阅。
同名显示名称的模块不共享订阅所有权。新订阅不回放登记前的事件。

## 事件与身份

回调接收 `core.module.group_lifecycle.GroupLifecycleEvent`，它是不可变快照：

| 字段 | 含义 |
| --- | --- |
| `delivery_id` | 每次本地接收的 UUID；仅在本进程关联事件/结果，不能作平台去重键 |
| `appid`、`event_type` | Bot 应用原始标识、四种 QQ 事件类型之一 |
| `group_openid`、`member_openid`、`op_member_openid` | 原载荷 `d` 中的原生身份；缺失/无效为 `None` |
| `event_id` | 实际顶层 `id` 或 `event_id`；缺失为 `None`，不借用消息 ID |
| `timestamp` | 原始 `d.timestamp` 字符串/数字，缺失/无效为 `None`，不补当前时间 |
| `sequence` | 实际顶层整数 `s`，缺失/无效为 `None` |
| `transport` | `websocket`、`webhook`；绕过传输入口的自定义适配器回退为 `internal` |
| `session_id` | WS 当时已有的会话标识；Webhook 或未知时为 `None` |
| `received_at` | 本地 `time.monotonic()` 值，仅作本进程接收时间 |

Bot 进退群的 `op_member_openid` 是操作人，不能据此推断被变更的普通成员。
不会用 union 显示身份、`user_id`/`group_id` 别名补造原生身份。
通知在现有插件去重前进行，重复/迟到/同秒投递仍会通知，且有不同 `delivery_id`。
消费方必须结合真实元数据判断可用性；`s` 不能跨会话比较，Webhook 接收次序不代表
平台因果顺序，时间戳不能作为全序。框架不持久化或保证恰好一次投递。

## 顺序与隔离

应用为 WS（包括初次启动和配置更新创建的客户端）和 Webhook 安装同步接收回调，
在入队/启动后台分发前登记屏障。每个同 Bot/群的生命周期通知等待前一通知的全部
结果；后续接收的同群业务事件等待这些通知的全部结果，才进入原有日志、欢迎回复、
插件调度。缺少原生群身份的生命周期通知采用该 Bot 的屏障，其他 Bot 继续运行。
不同群互不等待；同一个订阅也可并发处理不同 Bot/群。

所有订阅者在一个通知中并发执行，各自有独立超时。一个失败不会注销或暂停其他
订阅者，也不暂停全局插件。框架在得到全部终态结果后继续原有处理；**结果失败不
代表业务获得了安全许可**。需要失败关闭的模块应使自己的状态保持无效。
不等待生命周期历史的后台持久化。不对已开始的查询、插件后台任务或框架外调度
提供追溯屏障；模块须自行对查询版本重新核验。

自定义适配器若只调用异步 `_on_event`，仅从该协程开始执行时登记，不具备传输
入口的接收顺序保证。传输实现可传 `on_event_received` 同步回调；框架内置两种
传输已完整接线。此回调只登记，不等待业务处理，不修改 ACK/READY/RESUMED。

## 结果与完成边界

原有插件接收的 `Event.group_lifecycle_delivery` 在四种事件上是
`GroupLifecycleDelivery`，其他事件为 `None`。`delivery.event` 是同一个通知快照；
`await delivery.wait()` 返回该次登记时匹配的所有订阅者结果元组，调用者取消等待
不会取消通知。`delivery.results` 在处理中为 `None`，完成后为结果元组。
没有订阅者时结果是空元组，不表示某个业务模块已处理事件。

`GroupLifecycleResult` 含 `event`、`subscription_id`、`owner`、`status`、`error_type`、
`started_at`/`finished_at`（本地单调时间）。`result.event.delivery_id` 可关联后续进度
机制。`subscription.results` 是最近 100 个已结束结果的只读元组快照，旧结果会淘汰；
delivery 保留完整的本次结果。订阅历史不是持久审计日志。

| `status` | 含义 |
| --- | --- |
| `success` | async 回调正常返回；仅回调自身及其 await 的工作完成 |
| `error` | 回调抛出异常，`error_type` 为异常类名；不收集消息或敏感载荷 |
| `timeout` | 超时后请求取消；不声称回调实际停止或副作用已回滚 |
| `cancelled` | 回调本身被取消，与取消订阅不同 |
| `unsubscribed` | 订阅被显式取消，涵盖在途和已登记待执行调用 |
| `unloaded` | 模块上下文关闭（含 setup 失败/取消），涵盖在途和待执行调用 |
| `busy` | 前次同订阅/Bot 的超时调用仍未退出，本次未调用，不能当成功 |

普通回调 `asyncio.create_task(...)` 后立即返回时，`success` **不能证明该后台任务完成**。
消费者应 await 必须纳入完成保证的处理；此 API 不追踪回调自行派生的任务。
超时/取消使用 Python 协作式取消，吞取消的回调可继续产生副作用；同一订阅/Bot
在旧超时调用真正退出前报告 `busy`，避免继续重叠调用。卸载也无法强停任意 Python
代码，新上下文是新订阅，模块必须自行防止旧代后台任务写回。阻塞事件循环的代码
无法被异步超时强制中断。不要在回调中等待依赖同一屏障的后续群事件，避免循环等待。

框架只提供通知、结果与上述调度顺序。成员授权、恢复核验、群发策略、失败后的
持久化/告警与暂停范围由业务模块负责。本能力不提供连接连续性、补发完成或恢复
完成信号，可独立使用。

QQ 官方通用 Payload/WS/Webhook 定义：
[事件订阅与通知](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)。
平台权限、订阅设置及字段可用性决定框架能实际收到哪些事件；本地成功不等于
平台保证了完整历史。
