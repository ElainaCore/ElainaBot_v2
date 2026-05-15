"""唤醒系统服务 (WakeupMixin)"""

import sqlite3
import asyncio
from datetime import datetime


# 唤醒阶段阈值: (最大不活跃天数, 目标阶段)
_STAGE_THRESHOLDS = ((0, 1), (3, 2), (7, 3), (30, 4))


def _calc_stage(days):
    """根据不活跃天数计算唤醒阶段, 超出范围返回 0"""
    for max_days, stage in _STAGE_THRESHOLDS:
        if days <= max_days:
            return stage
    return 0


class WakeupMixin:
    """唤醒系统 (wakeup.db) 方法集"""

    def _wakeup_locked(self):
        """获取 wakeup.db 连接和锁"""
        db_path = self._resolve_db_path('wakeup')
        conn = self._get_conn(db_path, 'wakeup')
        return conn, self._conn_locks.get(db_path)

    async def wakeup_update(self, openid):
        """用户发消息时更新活跃日期, stage 重置为 0"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._wakeup_update_sync, openid)

    def _wakeup_update_sync(self, openid):
        today = datetime.now().strftime('%Y-%m-%d')
        conn, lock = self._wakeup_locked()
        with lock:
            row = conn.execute(
                "SELECT last_msg_date FROM log WHERE openid=?", (openid,)).fetchone()
            if row and row[0] == today:
                return  # 今天已更新过
            conn.execute(
                "INSERT INTO log (openid, last_msg_date, wakeup_stage) VALUES (?,?,0) "
                "ON CONFLICT(openid) DO UPDATE SET last_msg_date=?, wakeup_stage=0",
                (openid, today, today))
            conn.commit()

    async def wakeup_can_send(self, openid):
        """检查是否可发唤醒, 返回 (can_send, target_stage, days_inactive)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._wakeup_can_send_sync, openid)

    def _wakeup_can_send_sync(self, openid):
        conn, lock = self._wakeup_locked()
        with lock:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT last_msg_date, wakeup_stage, last_wakeup_date FROM log WHERE openid=?",
                (openid,)).fetchone()
            conn.row_factory = None
        if not row:
            return (False, 0, -1)
        today = datetime.now().date()
        last_date = datetime.strptime(row['last_msg_date'], '%Y-%m-%d').date()
        days = (today - last_date).days
        stage = row['wakeup_stage']
        if row['last_wakeup_date'] == today.strftime('%Y-%m-%d'):
            return (False, stage, days)
        target = _calc_stage(days)
        if not target:
            return (False, 0, days)
        return (stage < target, target, days)

    async def wakeup_mark_sent(self, openid, stage):
        """标记已发送唤醒消息"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._wakeup_mark_sent_sync, openid, stage)

    def _wakeup_mark_sent_sync(self, openid, stage):
        today = datetime.now().strftime('%Y-%m-%d')
        conn, lock = self._wakeup_locked()
        with lock:
            conn.execute(
                "UPDATE log SET wakeup_stage=?, last_wakeup_date=?, updated_at=? WHERE openid=?",
                (stage, today, today, openid))
            conn.commit()

    async def wakeup_get_users(self, target_stage=None):
        """获取可唤醒用户列表"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._wakeup_get_users_sync, target_stage)

    def _wakeup_get_users_sync(self, target_stage=None):
        conn, lock = self._wakeup_locked()
        with lock:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT openid, last_msg_date, wakeup_stage, last_wakeup_date FROM log").fetchall()
            conn.row_factory = None
        today = datetime.now().date()
        today_str = today.strftime('%Y-%m-%d')
        results = []
        for row in rows:
            if row['last_wakeup_date'] == today_str:
                continue
            days = (today - datetime.strptime(row['last_msg_date'], '%Y-%m-%d').date()).days
            stage = _calc_stage(days)
            if not stage or row['wakeup_stage'] >= stage:
                continue
            if target_stage is not None and stage != target_stage:
                continue
            results.append({'openid': row['openid'], 'days': days, 'stage': stage})
        return results
