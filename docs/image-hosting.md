# 图床模块接入文档

图床模块（image_hosting）提供统一的图片/文件上传门面。模块启动时会自动扫描 modules/image_hosting/beds/，按每个图床声明的 priority 排序，并为每个实现生成动态方法。插件只需要依赖门面，不必直接导入第三方 SDK。

## 获取模块

~~~python
from core.application import get_app

app = get_app()
hosting = app.module_manager.get("image_hosting") if app else None

if hosting is not None:
    url = await hosting.upload_any(image_bytes, "report.png")
else:
    url = None  # 模块未启用或加载失败时自行降级
~~~

get("image_hosting") 可能返回 None。模块实例由框架管理生命周期，插件不需要手动创建、初始化或关闭图床客户端。

配置文件为 modules/image_hosting/data/config.yaml。模块会根据各图床的 defaults 自动补齐配置段，并按优先级重排内置图床；第三方扩展配置段会保留。Token、Secret、Cookie 等凭据只应写入本地配置，不要提交到仓库或打印到日志。

## 统一上传（推荐）

~~~python
url = await hosting.upload_any(
    image_bytes,
    filename="report.png",
    token_manager=bot.token_manager,  # QQ 频道图床需要
    sender=bot.sender,                 # QQ 分片文件图床可选
)
if url:
    await event.reply_image(url)
~~~

签名：upload_any(image_bytes, filename='image.png', *, token_manager=None, sender=None)。

该方法按优先级只尝试 is_available() 为真的图床；每个图床优先调用 upload_url()（如果实现），否则调用 upload()。只有以 http 开头的字符串才算成功，异常或其他返回结构会触发继续降级，全部失败返回 None。因此返回值始终是 str 或 None。

## 动态 API

门面通过属性名动态分发，名称来自图床的 name：

| 属性模式 | 实际调用 | 典型返回值 |
| --- | --- | --- |
| `hosting.upload_<name>(data, ...)` | 图床的 upload() | URL、结果 dict 或 (False, reason) |
| `hosting.upload_<name>_url(data, ...)` | 图床的 upload_url()（若实现） | URL 或 (False, reason) |
| `hosting.is_<name>_available()` | 图床的 is_available() | bool |
| `hosting.list_<name>_assets(...)` | 图床的 list_assets()（当前为 CNB） | 列表 |
| `hosting.delete_<name>(resource)` | 图床的 delete()（当前为 CNB、COS） | True/False 或 (False, reason) |

当前内置图床按上传优先级排列如下：

| 优先级 | name | 默认启用 | 主要配置 | 上传签名与限制 |
| ---: | --- | :---: | --- | --- |
| 10 | chatglm | 否 | enabled | upload(data) 返回 URL 或失败元组；要求 bytes，最大 20 MB |
| 20 | xingye | 否 | enabled | upload(data) 返回 URL 或失败元组；要求 bytes，最大 20 MB |
| 30 | nature | 是 | enabled | 仅 PNG/JPG/WebP/GIF，最大 100 MB；密钥内置，适合临时图片 |
| 40 | qq_file | 是 | enabled、target_type、target_id | 分片上传；返回结果字典；upload_url() 只取直链 |
| 45 | cnb | 否 | enabled、repo、token、max_file_size、verify_public_url、timeout | 公开仓库资源附件，默认最大 100 MB |
| 50 | cos | 否 | enabled、region、secret_id、secret_key、bucket_name、domain、upload_path_prefix、max_file_size | COS 结果字典，默认最大 100 MB |
| 60 | bilibili | 否 | enabled、csrf_token、sessdata、bucket | 需要 B 站 Cookie，最大 20 MB |
| 70 | qq_channel | 否 | enabled、channel_id | 需要子频道 ID 和 TokenManager |
| 80 | self_hosted | 否 | enabled、public_base_url、storage_dir、max_file_size、permanent_cache | 本地保存并返回公开 URL；默认最大 100 MB |

直接调用指定图床时必须检查返回值：成功可能是 URL，也可能是包含 URL 的字典；需要统一 URL 时优先使用 `upload_any()` 或对应的 `upload_<name>_url()`。

## 各图床用法与配置

### QQ 分片文件（qq_file）

该图床走 QQ 官方分片文件上传流程，默认开启，但仅在能取得上传作用域和机器人 sender 时可用。

~~~python
result = await hosting.upload_qq_file(
    file_bytes,
    file_type=1,                # 1 图片 / 2 视频 / 3 语音 / 4 其他文件
    file_name="photo.png",
    sender=bot.sender,
    target_id="群或用户 openid",  # 不传则使用配置或自动从数据库获取
    target_type="group",         # group 或 user
)
if isinstance(result, dict) and result.get("url"):
    url = result["url"]
~~~

成功字典包含 success、url、ttl、file_info、file_uuid、file_size。upload_qq_file_url() 只返回 url；文件类型 4 通常没有直链，会返回 (False, reason)。sender 不传时会尝试使用第一个在线机器人；target_id 不传时先使用配置值，否则从数据库取最近的群/用户 ID。

~~~yaml
qq_file:
  enabled: true
  target_type: group   # group 或 user
  target_id: ""        # 留空自动获取
~~~

### CNB（cnb）

CNB 图床把图片保存为公开仓库的资源附件，不会提交到 Git 分支。repo 必须是 组织名/仓库名，仓库必须公开；上传需要 repo-code:rw，列出资源需要 repo-manage:r，删除需要 repo-manage:rw。API 和公开资源地址使用模块内置固定端点 https://api.cnb.cool 与 https://cnb.cool，不再支持 api_base/asset_base 配置项。

~~~yaml
cnb:
  enabled: true
  repo: "org/public-images"
  token: ""
  max_file_size: 104857600
  verify_public_url: false
  timeout: 30
~~~

~~~python
result = await hosting.upload_cnb(image_bytes, "report.png")
# 返回字典包含 success、url、asset_url、asset_path、asset_id、filename、file_size、content_type、verification
url = await hosting.upload_cnb_url(image_bytes, "report.png")
assets = await hosting.list_cnb_assets(limit=10, page=1)
all_assets = await hosting.get_bed("cnb").list_all_assets(page_size=100)
ok = await hosting.delete_cnb(assets[0])  # 可传 ID、路径或公开 URL
~~~

verify_public_url: true 会在上传后用匿名请求校验公开资源；结果写入 verification。list_cnb_assets() 失败时返回空列表，list_all_assets() 失败时返回 None。

### 腾讯云 COS（cos）

COS 图床依赖 qcloud-cos-v5（模块 requirements 已声明）。必须配置 region、secret_id、secret_key 和 bucket_name；domain 留空时使用 `https://<bucket>.cos.<region>.myqcloud.com`。

~~~yaml
cos:
  enabled: true
  region: ap-guangzhou
  secret_id: ""
  secret_key: ""
  bucket_name: "mybucket-1250000000"
  domain: ""
  upload_path_prefix: "elaina/"
  max_file_size: 104857600
~~~

~~~python
result = await hosting.upload_cos(
    image_bytes, "report.png",
    user_id=str(event.user_id), custom_path="reports",
)
# 返回字典包含 success、cos_key、file_url、filename、file_size、width、height、px
url = await hosting.upload_cos_url(image_bytes, "report.png")
await hosting.delete_cos(result["cos_key"])
~~~

上传键会根据图片尺寸补充 _宽x高（文件名已有该后缀时不重复添加）；未传 custom_path 时按 upload_path_prefix[/user_id]/时间/文件名生成。delete_cos() 接受 COS 对象键，不是公开 URL。

### B站与 QQ 频道

~~~yaml
bilibili:
  enabled: true
  csrf_token: "Cookie 中的 bili_jct"
  sessdata: "Cookie 中的 SESSDATA"
  bucket: openplatform

qq_channel:
  enabled: true
  channel_id: "子频道 ID"
~~~

~~~python
bili_url = await hosting.upload_bilibili(image_bytes)
qq_url = await hosting.upload_qq_channel(
    image_bytes, token_manager=bot.token_manager,
)
~~~

QQ 频道仍保留兼容别名 hosting.upload_qq(...) 和 hosting.is_qq_available()；新代码建议使用完整的 upload_qq_channel/is_qq_channel_available 名称。

### 自身图床（self_hosted）

自身图床将图片按 SHA-256 内容哈希保存到本机，并通过框架扩展路由公开读取；传入的 filename 仅为统一接口兼容，实际存储名由哈希和真实图片格式决定，可自动去重。

~~~yaml
self_hosted:
  enabled: true
  public_base_url: "127.0.0.1:5200" # IP:端口、域名:端口或域名；留空自动探测公网 IP
  storage_dir: ""                    # 留空为 modules/image_hosting/data/self_hosted
  max_file_size: 104857600
  permanent_cache: true
~~~

public_base_url 只能填写 IP:端口、域名:端口 或不带端口的域名（不带端口时使用主服务端口，默认 5200），不能包含协议头或路径。上传成功后 URL 形如：

~~~text
http://<public_base_url>/api/ext/image-hosting?filename=<sha256>.<ext>
~~~

支持 PNG、JPG、GIF、WebP、BMP、TIFF、AVIF。公开路由 GET /api/ext/image-hosting 免鉴权；模块停用、初始化失败、文件名非法或文件不存在时返回 404。permanent_cache: true 返回一年期 immutable 缓存头；关闭后返回 Cache-Control: no-store。公开地址必须能从需要访问图片的客户端连到机器人主服务。

## 状态与底层对象

~~~python
status = hosting.status()
# {'chatglm': False, 'xingye': False, 'nature': True, ...}

if status.get("cos"):
    result = await hosting.upload_cos_url(image_bytes, "photo.png")

bed = hosting.get_bed("cnb")
if bed and bed.is_available():
    records = await bed.list_assets(limit=10)
~~~

status() 返回各图床当前是否可用的字典；可用不仅取决于 enabled，还可能要求凭据、依赖、初始化结果或必要参数。get_bed(name) 返回内部图床对象，适合使用扩展能力；一般插件优先使用门面动态方法。

## 兼容别名

为兼容旧插件，门面仍提供：

| 旧属性 | 当前目标 |
| --- | --- |
| upload_qq | upload_qq_channel |
| upload_cos_url | COS 图床的 upload_url |
| delete_cos | COS 图床的 delete |
| upload_qq_file_url | QQ 分片文件图床的 upload_url |
| is_qq_available | is_qq_channel_available |

新插件建议直接使用图床完整名称。

## 扩展新图床

模块会自动扫描 modules/image_hosting/beds/ 中的 Python 文件。若要贡献新实现，请定义继承 BaseBed 的 Bed 类，并声明 name、display_name、priority、defaults、comments 和异步 upload()；按需实现 upload_url、list_assets、delete、close 等方法。新图床应随模块发行包安装，模块重载后会自动发现。

## 注意事项

- 上传数据通常必须是 bytes；仅 COS/自身图床额外兼容 BytesIO。所有上传方法都需要 await。
- 直接调用指定图床时必须检查返回值；失败可能是 (False, reason)，成功也可能是包含 URL 的字典。
- upload_any() 会吞掉单个图床异常并继续降级；诊断失败原因请查看模块日志，或直接调用指定图床。
- 凭据和公开 URL 都可能具有安全影响：不要把 Token/Secret/Cookie 写入日志；自身图床公开路由没有鉴权，请确认网络访问范围符合预期。
- 图床客户端和线程池由模块生命周期负责初始化与清理，插件卸载时无需自行关闭。
