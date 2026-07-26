"""图床实现子包: 每个图床一个子文件, 定义 Bed 类即可被自动发现"""

import importlib
import pkgutil

from ._common import log


def discover_beds():
    """扫描本包下的所有图床实现 (文件需定义 Bed 类), 按 priority 排序返回类列表"""
    beds = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith('_'):
            continue
        try:
            mod = importlib.import_module(f'{__name__}.{info.name}')
        except Exception as e:
            log.error(f'图床 {info.name} 加载失败: {e}')
            continue
        bed_cls = getattr(mod, 'Bed', None)
        if bed_cls is None or not getattr(bed_cls, 'name', ''):
            log.warning(f'图床文件 {info.name} 未定义有效的 Bed 类, 已跳过')
            continue
        beds.append(bed_cls)
    beds.sort(key=lambda c: (c.priority, c.name))
    return beds
