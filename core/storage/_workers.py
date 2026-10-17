"""SQLite 工作线程与网络 DNS 隔离；每日大扫描共用单线程，限制磁盘并发。"""

from concurrent.futures import ThreadPoolExecutor

SQLITE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix='sqlite-io')
DAILY_SCAN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='daily-scan')
