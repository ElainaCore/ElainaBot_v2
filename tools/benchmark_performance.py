"""隔离临时数据库的吞吐和事件循环延迟基准，不连接机器人。"""
import asyncio
import statistics
import tempfile
import time
import tracemalloc
import sys
from pathlib import Path
from types import SimpleNamespace

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bot.event import EventHandlerMixin
from core.storage._base import _BaseLogService


class Sink:
    def db_queue(self, *args):
        pass


def cache_peak(bounded):
    service = object.__new__(EventHandlerMixin)
    service._full_access_cache = {}
    bot = SimpleNamespace(appid='benchmark', log_service=Sink())
    tracemalloc.start()
    for i in range(100000):
        if bounded:
            service._record_full_access_group(bot, str(i))
        else:
            service._full_access_cache[('benchmark', str(i))] = time.time() + 1800
    peak = tracemalloc.get_traced_memory()[1] / 1024**2
    tracemalloc.stop()
    return len(service._full_access_cache), peak


async def benchmark():
    with tempfile.TemporaryDirectory() as directory:
        service = _BaseLogService(directory, True, 1, 1000, 7, [])
        path = str(Path(directory) / 'bench.db')

        def write(i):
            with service._transaction(path, 'benchmark') as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS sample (id INTEGER PRIMARY KEY)')
                conn.execute('INSERT INTO sample VALUES (?)', (i,))

        delays = []
        running = True

        async def heartbeat():
            while running:
                started = time.perf_counter()
                await asyncio.sleep(0.005)
                delays.append(max(0, time.perf_counter() - started - 0.005) * 1000)

        monitor = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        try:
            await asyncio.gather(*(service._run_write(path, write, i) for i in range(3000)))
            elapsed = time.perf_counter() - started
            with service._locked_conn(path, 'benchmark', read_only=True) as conn:
                assert conn.execute('SELECT COUNT(*) AS c FROM sample').fetchone()['c'] == 3000
            running = False
            await monitor
            print(f'SQLite: 3000 committed writes, {elapsed:.3f}s, {3000/elapsed:.0f} ops/s')
            print(f'Event-loop lag: median={statistics.median(delays):.2f}ms, max={max(delays):.2f}ms')
            print(f'Outstanding database queues={len(service._db_queues)}, futures={len(service._io_futures)}')
        finally:
            running = False
            await monitor
            service._pool.close()


if __name__ == '__main__':
    for bounded in (False, True):
        count, peak = cache_peak(bounded)
        print(f'100000 unique groups, bounded={bounded}: entries={count}, Python peak={peak:.2f}MiB')
    asyncio.run(benchmark())
