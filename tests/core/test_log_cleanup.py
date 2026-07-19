"""_cleanup_expired 单元测试 — 验证日期目录校验和保留天数逻辑"""

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from core.storage._base import _BaseLogService


def _make_service(base_dir, retention_days=5):
    """创建最小 _BaseLogService 实例"""
    return _BaseLogService(
        base_dir=base_dir,
        wal_mode=False,
        insert_interval=2,
        batch_size=0,
        retention_days=retention_days,
        queue_types=(),
    )


def _mkdir(base, name):
    """在 base 下创建子目录并放入一个占位文件"""
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'placeholder.db'), 'w') as f:
        f.write('')
    return d


class TestCleanupExpired:
    """_cleanup_expired 核心逻辑测试"""

    @pytest.mark.asyncio
    async def test_skips_non_date_directories(self):
        """10 位 appid 目录不应被当作过期日期目录删除"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=5)

            # 创建 10 位 appid 目录 (不是日期格式)
            appid_dir = _mkdir(tmpdir, '1020123456')
            # 创建 6 位 appid 目录
            appid_dir2 = _mkdir(tmpdir, '123456')

            await svc._cleanup_expired()

            assert os.path.isdir(appid_dir), '10 位 appid 目录被误删'
            assert os.path.isdir(appid_dir2), '6 位 appid 目录被误删'

    @pytest.mark.asyncio
    async def test_deletes_expired_date_dirs(self):
        """超过保留天数的日期目录应被删除"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=5)

            today = datetime.now()
            # 7 天前 (应被删除)
            old_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')
            old_dir = _mkdir(tmpdir, old_date)
            # 昨天 (应保留)
            yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
            yesterday_dir = _mkdir(tmpdir, yesterday)
            # 今天 (应保留)
            today_str = today.strftime('%Y-%m-%d')
            today_dir = _mkdir(tmpdir, today_str)

            await svc._cleanup_expired()

            assert not os.path.exists(old_dir), '过期目录未被清理'
            assert os.path.isdir(yesterday_dir), '昨天的目录被误删'
            assert os.path.isdir(today_dir), '今天的目录被误删'

    @pytest.mark.asyncio
    async def test_boundary_day_not_deleted(self):
        """恰好等于 cutoff 日期的目录不应被删除 (严格小于)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=5)

            cutoff_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
            cutoff_dir = _mkdir(tmpdir, cutoff_date)

            await svc._cleanup_expired()

            assert os.path.isdir(cutoff_dir), 'cutoff 边界日期的目录不应被删除'

    @pytest.mark.asyncio
    async def test_retention_zero_skips_cleanup(self):
        """retention_days=0 时不执行任何清理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=0)

            old_date = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
            old_dir = _mkdir(tmpdir, old_date)

            await svc._cleanup_expired()

            assert os.path.isdir(old_dir), 'retention_days=0 不应删除任何目录'

    @pytest.mark.asyncio
    async def test_mixed_dirs_only_expired_dates_removed(self):
        """混合目录场景: appid 目录、非日期目录、有效日期目录都应保留"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=3)

            today = datetime.now()
            keep = []
            # 应保留的目录
            keep.append(_mkdir(tmpdir, '1020123456'))       # 10 位 appid
            keep.append(_mkdir(tmpdir, '102012345'))        # 9 位 appid
            keep.append(_mkdir(tmpdir, 'some_other'))       # 非数字目录
            keep.append(_mkdir(tmpdir, (today - timedelta(days=1)).strftime('%Y-%m-%d')))  # 昨天
            keep.append(_mkdir(tmpdir, (today - timedelta(days=3)).strftime('%Y-%m-%d')))  # 边界

            # 应被删除的目录
            expired = _mkdir(tmpdir, (today - timedelta(days=10)).strftime('%Y-%m-%d'))

            await svc._cleanup_expired()

            for d in keep:
                assert os.path.isdir(d), f'{os.path.basename(d)} 不应被删除'
            assert not os.path.exists(expired), '过期日期目录未被清理'

    @pytest.mark.asyncio
    async def test_conn_locks_cleaned_up(self):
        """清理过期目录时应同步清理 _conn_locks"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = _make_service(tmpdir, retention_days=5)

            old_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            old_dir = _mkdir(tmpdir, old_date)
            db_path = os.path.join(old_dir, 'placeholder.db')

            # 模拟已有连接锁
            import threading
            svc._conn_locks[db_path] = threading.Lock()

            await svc._cleanup_expired()

            assert db_path not in svc._conn_locks, '_conn_locks 未被清理'
