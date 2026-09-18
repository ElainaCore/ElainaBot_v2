"""可选模块: 统一图床服务 (各图床实现拆分在 beds/ 子文件夹, 每个图床一个子文件)

新增图床: 在 beds/ 下新建 <name>.py 并定义 Bed 类 (继承 beds._common.BaseBed,
声明 name / display_name / priority / defaults / comments 并实现 upload),
即可被自动发现: 自动合并配置、纳入 status(), 并通过 upload_<name>() 调用。

用法 (插件中):
    hosting = bot.module_manager.get("image_hosting")
    if hosting:
        url = await hosting.upload_any(image_bytes, "test.png")  # 按 priority 顺序选首个可用图床
        print(hosting.bed_order())  # 当前优先级顺序: [{'name', 'display_name', 'priority', 'enabled'}, ...]
        url = await hosting.upload_cos(image_bytes, "test.png", user_id="abc")
        url = await hosting.upload_bilibili(image_bytes)
        url = await hosting.upload_qq(image_bytes)
        result = await hosting.upload_qq_file(file_bytes, file_type=1)  # {'url', 'ttl', ...}
        url = await hosting.upload_chatglm(image_bytes)
        url = await hosting.upload_xingye(image_bytes)
        url = await hosting.upload_nature(image_bytes)
        url = await hosting.upload_self_hosted(image_bytes, "test.png")
        url = await hosting.upload_cnb(image_bytes, "test.png")
        records = await hosting.list_cnb_assets(limit=10)
        await hosting.delete_cnb(records[0])

配置 (modules/image_hosting/data/config.yaml): 各图床一个配置段, 由各自的 Bed.defaults 提供。
每段都有 priority (整数, 越小越先尝试): 默认写入 Bed 类内置的 priority, 改成任意整数即可调整
优先级, upload_any() 与 status() 都按这个顺序走; 不想参与上传的图床用 enabled: false 关掉。
"""

__module_meta__ = {
    'name': '图床服务',
    'description': '统一图床上传 (CNB / ChatGLM / 星野 / Nature / QQ分片 / COS / B站 / QQ频道 / 自身图床)',
    'version': '2.3.0',
    'author': 'ElainaBot',
}

import inspect

from core.base.logger import EXTENSION, get_logger

from . import public_server
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
    defaults = {cls.name: {**cls.defaults, 'priority': cls.priority} for cls in bed_classes}
    comments = {
        cls.name: {**cls.comments,
                   'priority': f'上传优先级, 越小越先尝试 (内置默认 {cls.priority})'}
        for cls in bed_classes
    }
    cfg = ctx.ensure_config(defaults, comments=comments)
    retired_changed = _remove_retired_cnb_config(cfg)
    # 老配置里没有 priority 键: 补上内置值, 让优先级可以直接改
    priority_filled = False
    for cls in bed_classes:
        section = cfg.setdefault(cls.name, {})
        if isinstance(section, dict) and 'priority' not in section:
            section['priority'] = cls.priority
            priority_filled = True
    ordered_cfg = _order_bed_config(cfg, bed_classes)
    if retired_changed or priority_filled or list(ordered_cfg) != list(cfg):
        ctx.save_config(ordered_cfg, comments=comments)
        cfg = ordered_cfg
        log.info('图床配置已更新')
    beds = {cls.name: cls(cfg.get(cls.name, {})) for cls in bed_classes}
    # 按配置优先级排序: dict 保持插入序, upload_any()/status() 都依赖这个顺序
    beds = dict(sorted(beds.items(), key=lambda kv: (_bed_priority(kv[1]), kv[0])))
    _instance = ImageHosting(cfg, ctx, beds)
    _instance.initialize()
    public_server.attach(_instance)
    return _instance


async def teardown():
    global _instance
    if _instance is not None:
        await _instance.aclose()
        public_server.detach(_instance)
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

    async def aclose(self):
        """关闭各图床持有的异步客户端。"""
        for bed in self._beds.values():
            close = getattr(bed, 'close', None)
            if close is None:
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception('图床客户端关闭失败: %s', getattr(bed, 'name', type(bed).__name__))

    def get_bed(self, name):
        return self._beds.get(name)

    def status(self):
        """返回各图床状态 dict (按优先级顺序)"""
        return {name: bool(bed.is_available()) for name, bed in self._beds.items()}

    def bed_order(self):
        """按当前优先级返回图床顺序: [{'name', 'display_name', 'priority', 'enabled'}, ...]"""
        return [
            {
                'name': name,
                'display_name': bed.display_name or name,
                'priority': _bed_priority(bed),
                'enabled': bool(bed.is_available()),
            }
            for name, bed in self._beds.items()
        ]

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
        # list_<name>_assets / delete_<name>
        if attr.startswith('list_') and attr.endswith('_assets'):
            bed = self._beds.get(attr[5:-7])
            if bed and hasattr(bed, 'list_assets'):
                return bed.list_assets
        if attr.startswith('delete_'):
            bed = self._beds.get(attr[7:])
            if bed and hasattr(bed, 'delete'):
                return bed.delete
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


def _section_priority(section, fallback: int) -> int:
    """图床配置段里的 priority 优先, 缺失或非法时退回 Bed 内置值"""
    try:
        return int(section.get('priority'))
    except (AttributeError, TypeError, ValueError):
        return fallback


def _bed_priority(bed) -> int:
    """某个已实例化图床的当前优先级"""
    return _section_priority(getattr(bed, '_cfg', None) or {}, type(bed).priority)


def _order_bed_config(cfg, bed_classes):
    """按当前优先级重排图床配置段，并保留第三方扩展配置段"""
    ordered = {
        cls.name: cfg.get(cls.name, {})
        for cls in sorted(bed_classes,
                          key=lambda c: (_section_priority(cfg.get(c.name) or {}, c.priority), c.name))
    }
    retired = {'qiniu', 'xinyew'}
    ordered.update((name, value) for name, value in cfg.items() if name not in ordered and name not in retired)
    return ordered


def _remove_retired_cnb_config(cfg):
    """Remove CNB endpoint overrides; the service endpoints are fixed constants."""
    cnb_cfg = cfg.get('cnb') if isinstance(cfg, dict) else None
    if not isinstance(cnb_cfg, dict):
        return False
    changed = False
    for key in ('api_base', 'asset_base'):
        if key in cnb_cfg:
            del cnb_cfg[key]
            changed = True
    return changed
