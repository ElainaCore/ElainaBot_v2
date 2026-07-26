"""可选模块: 统一图床服务 (各图床实现拆分在 beds/ 子文件夹, 每个图床一个子文件)

新增图床: 在 beds/ 下新建 <name>.py 并定义 Bed 类 (继承 beds._common.BaseBed,
声明 name / display_name / priority / defaults / comments 并实现 upload),
即可被自动发现: 自动合并配置、纳入 status(), 并通过 upload_<name>() 调用。

用法 (插件中):
    hosting = bot.module_manager.get("image_hosting")
    if hosting:
        url = await hosting.upload_any(image_bytes, "test.png")  # 自动选首个可用图床
        url = await hosting.upload_cos(image_bytes, "test.png", user_id="abc")
        url = await hosting.upload_bilibili(image_bytes)
        url = await hosting.upload_qq(image_bytes)
        result = await hosting.upload_qq_file(file_bytes, file_type=1)  # {'url', 'ttl', ...}
        url = await hosting.upload_chatglm(image_bytes)
        url = await hosting.upload_xingye(image_bytes)
        url = await hosting.upload_nature(image_bytes)

配置 (modules/image_hosting/data/config.yaml): 各图床一个配置段, 由各自的 Bed.defaults 提供
"""

__module_meta__ = {
    'name': '图床服务',
    'description': '统一图床上传 (COS / B站 / QQ频道 / QQ分片文件 / ChatGLM / 星野 / Nature)',
    'version': '2.0.0',
    'author': 'ElainaBot',
}

import inspect

from core.base.logger import EXTENSION, get_logger

from .beds import discover_beds
from .beds._common import init_executor, parse_dimensions_from_filename, shutdown_executor  # noqa: F401

log = get_logger(EXTENSION, "图床服务")

_instance = None

# 旧方法名 -> (图床名, Bed 方法名) 兼容映射
_LEGACY_METHODS = {
    'upload_qq': ('qq_channel', 'upload'),
    'upload_cos_url': ('cos', 'upload_url'),
    'delete_cos': ('cos', 'delete'),
    'upload_qq_file_url': ('qq_file', 'upload_url'),
    'is_qq_available': ('qq_channel', 'is_available'),
}


# ==================== 模块入口 ====================

async def setup(ctx):
    global _instance
    init_executor()
    bed_classes = discover_beds()
    defaults = {cls.name: dict(cls.defaults) for cls in bed_classes}
    comments = {cls.name: dict(cls.comments) for cls in bed_classes}
    cfg = ctx.ensure_config(defaults, comments=comments)
    beds = {cls.name: cls(cfg.get(cls.name, {})) for cls in bed_classes}
    _instance = ImageHosting(cfg, ctx, beds)
    _instance.initialize()
    return _instance


async def teardown():
    global _instance
    _instance = None
    shutdown_executor()


# ==================== 统一图床服务 ====================

class ImageHosting:
    """统一图床上传门面: 自动发现 beds/ 下的图床实现并按名称分发"""

    __slots__ = ('_cfg', '_ctx', '_beds')

    def __init__(self, cfg, ctx, beds):
        self._cfg = cfg
        self._ctx = ctx
        self._beds = beds

    def initialize(self):
        status = []
        for bed in self._beds.values():
            bed.initialize()
            status.append(f"{bed.display_name or bed.name}={'✅' if bed.is_available() else '❌'}")
        log.info(f"图床状态: {' | '.join(status)}")

    # ==================== 状态查询 ====================

    def get_bed(self, name):
        return self._beds.get(name)

    def status(self):
        """返回各图床状态 dict"""
        return {name: bool(bed.is_available()) for name, bed in self._beds.items()}

    # ==================== 动态分发 ====================

    def __getattr__(self, attr):
        # 旧命名兼容
        legacy = _LEGACY_METHODS.get(attr)
        if legacy:
            bed = self._beds.get(legacy[0])
            if bed:
                return getattr(bed, legacy[1])
        # is_<name>_available
        if attr.startswith('is_') and attr.endswith('_available'):
            bed = self._beds.get(attr[3:-10])
            if bed:
                return bed.is_available
        # upload_<name> / upload_<name>_url
        if attr.startswith('upload_'):
            rest = attr[7:]
            if rest.endswith('_url'):
                bed = self._beds.get(rest[:-4])
                if bed and hasattr(bed, 'upload_url'):
                    return bed.upload_url
            bed = self._beds.get(rest)
            if bed:
                return bed.upload
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{attr}'")

    # ==================== 通用上传 ====================

    async def upload_any(self, image_bytes, filename='image.png', *, token_manager=None, sender=None):
        """按开启状态依次尝试各图床上传, 返回首个成功的 URL; 全部失败返回 None

        token_manager: QQ频道图床需要; sender: QQ分片文件图床可选
        """
        for name, bed in self._beds.items():
            if not bed.is_available():
                continue
            fn = getattr(bed, 'upload_url', None) or bed.upload
            try:
                result = await _call_with_supported_kwargs(
                    fn, image_bytes,
                    filename=filename, file_name=filename,
                    token_manager=token_manager, sender=sender)
            except Exception as e:
                log.debug(f'图床 {name} 上传失败: {e}')
                continue
            if isinstance(result, str) and result.startswith('http'):
                return result
        return None


def _call_with_supported_kwargs(fn, image_bytes, **kwargs):
    """仅传入目标方法签名中声明的关键字参数"""
    try:
        params = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in kwargs.items() if k in params and v is not None}
    except (TypeError, ValueError):
        kwargs = {}
    return fn(image_bytes, **kwargs)
