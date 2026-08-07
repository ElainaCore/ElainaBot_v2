# Image Hosting 模块接入文档

Image Hosting 提供统一的图片/文件上传门面，自动发现 `beds/` 下的图床实现，并按图床优先级尝试上传。插件只需依赖统一门面，不必绑定具体第三方 SDK。

## 获取模块

```python
from core.application import get_app

app = get_app()
hosting = app.module_manager.get("image_hosting") if app else None
if hosting:
    url = await hosting.upload_any(image_bytes, "report.png")
```

模块未启用时返回 `None`。启用后可通过 `hosting.status()` 查看每个图床是否可用；配置正确但依赖、凭据或网络不满足时，单个图床仍可能不可用。

配置自动写入 `modules/image_hosting/data/config.yaml`，每个图床对应一个配置段。凭据（Token、Secret 等）只应写入本地配置，不要提交到插件仓库或日志。

## 统一上传

```python
url = await hosting.upload_any(
    image_bytes,
    filename="report.png",
    token_manager=bot.token_manager,  # QQ 频道图床可选
    sender=bot.sender,                 # QQ 分片文件图床可选
)
```

`upload_any(image_bytes, filename='image.png', *, token_manager=None, sender=None)` 会按优先级跳过不可用图床并依次尝试，返回第一个以 `http` 开头的 URL；全部失败返回 `None`。该方法适合插件的降级链路。

## 动态图床 API

门面通过属性名动态分发，名称来自图床的 `name`：

| 属性模式 | 实际调用 | 返回值 |
| --- | --- | --- |
| `hosting.upload_<name>(data, ...)` | 该图床的 `upload()` | 由图床决定，通常为 URL、dict 或 `(False, reason)` |
| `hosting.upload_<name>_url(data, ...)` | 该图床的 `upload_url()`（若实现） | URL 字符串或 `(False, reason)` |
| `hosting.is_<name>_available()` | `is_available()` | `bool` |
| `hosting.list_<name>_assets(...)` | `list_assets()`（若实现） | 图床定义的列表结果 |
| `hosting.delete_<name>(resource)` | `delete()`（若实现） | 图床定义的结果 |

当前内置名称和常用调用如下：

| 图床名 | 调用示例 | 额外参数/限制 |
| --- | --- | --- |
| `chatglm` | `await hosting.upload_chatglm(data)` | 开启即可，单文件最大 20 MB |
| `xingye` | `await hosting.upload_xingye(data)` | 开启即可，单文件最大 20 MB |
| `nature` | `await hosting.upload_nature(data)` | 默认开启，单文件最大 100 MB |
| `qq_file` | `await hosting.upload_qq_file(data, file_type=1, file_name='a.png', sender=bot.sender, target_id=..., target_type='group')` | 返回包含 `url`、`ttl` 等字段的 dict；`upload_qq_file_url()` 只取直链 |
| `cnb` | `await hosting.upload_cnb(data, 'a.png')` | 需配置公开仓库和 Token；另有 `upload_cnb_url()`、`list_cnb_assets(limit=10)`、`delete_cnb(resource)` |
| `cos` | `await hosting.upload_cos(data, 'a.png', user_id='u1', custom_path='reports')` | 返回包含 `file_url` 的 dict；`upload_cos_url()` 只取 URL |
| `bilibili` | `await hosting.upload_bilibili(data)` | 需 `csrf_token`、`sessdata`，单文件最大 20 MB |
| `qq_channel` | `await hosting.upload_qq(data, token_manager=bot.token_manager)` | 需 `channel_id` 和 TokenManager |
| `self_hosted` | `await hosting.upload_self_hosted(data, 'a.png')` | 本地保存并返回公开 URL，仅支持图片格式 |

不同图床的失败结果不完全相同：公共上传接口可能返回 URL 字符串，也可能返回 `(False, reason)` 或包含 URL 的 dict。插件直接调用指定图床时应检查结果；需要统一行为时优先使用 `upload_any()`。

## 状态、图床对象与示例

```python
available = hosting.status()
if not available.get("cos"):
    # 选择其他图床或回退为文本消息
    url = await hosting.upload_any(image_bytes)

bed = hosting.get_bed("self_hosted")
if bed and bed.is_available():
    url = await bed.upload(image_bytes, "photo.png")
```

`get_bed(name)` 返回内部图床对象，适合需要使用该图床额外能力的插件；优先使用门面上的动态方法，以减少对内部类结构的依赖。

## 扩展新图床

模块会自动扫描 `modules/image_hosting/beds/`，但插件不应直接写入框架模块目录。若要贡献新图床，实现 `BaseBed` 的 `Bed` 类并声明 `name`、`display_name`、`priority`、`defaults`、`comments` 和异步 `upload()`，再通过模块发行包安装。模块重载后会自动发现新实现。

## 注意事项

- 上传数据应为 `bytes`；不要把未受信任的文件名拼接为本地路径。
- 图床调用包含网络或文件 IO，必须 `await`，不要阻塞事件循环。
- `upload_any()` 会吞掉单个图床异常并继续尝试；需要诊断时查看模块日志。
- 插件卸载时无需关闭图床线程池，由模块生命周期负责清理。
