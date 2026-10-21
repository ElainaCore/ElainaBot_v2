"""小时分布直接读取增量汇总；按机器人隔离，包含迟到消息。"""

from web.tools._bots import iter_bots
from web.tools._stats import context


def _aggregate_hourly(appid_filter, date):
    hourly = {}
    for _, inst in iter_bots(context.bot_manager, appid_filter):
        stats = inst.log_service.message_statistics(date)
        for hour, count in enumerate(stats['hourly_distribution']):
            key = f'{hour:02d}'
            hourly[key] = hourly.get(key, 0) + count
    return hourly
