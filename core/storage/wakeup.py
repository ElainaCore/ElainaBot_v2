"""唤醒系统服务 (WakeupMixin)"""

from datetime import datetime

# 唤醒阶段阈值: (最大不活跃天数, 目标阶段)
_STAGE_THRESHOLDS = ((0, 1), (3, 2), (7, 3), (30, 4))


def _calc_stage(days):
    """根据不活跃天数计算唤醒阶段, 超出范围返回 0"""
    for max_days, stage in _STAGE_THRESHOLDS:
        if days <= max_days:
            return stage
    return 0


# 可唤醒用户筛选下推 SQL, 阶段判定与 _calc_stage 一致, 不把全表搬回 Python
_GET_USERS_SQL = """
    SELECT openid, days,
           CASE WHEN days <= 0 THEN 1 WHEN days <= 3 THEN 2
                WHEN days <= 7 THEN 3 ELSE 4 END AS stage
    FROM (
        SELECT openid, wakeup_stage,
               CAST(julianday(?1) - julianday(last_msg_date) AS INTEGER) AS days
        FROM log
        WHERE last_wakeup_date IS NULL OR last_wakeup_date != ?1
    )
    WHERE days <= 30
      AND wakeup_stage < CASE WHEN days <= 0 THEN 1 WHEN days <= 3 THEN 2
                              WHEN days <= 7 THEN 3 ELSE 4 END
"""


class WakeupMixin:
    """唤醒系统 (wakeup.db) 方法集"""

    async def wakeup_update(self, openid):
        """用户发消息时更新活跃日期, stage 重置为 0"""
        return await self._run_write(self._resolve_db_path('wakeup'), self._wakeup_update_sync, openid)

    def _wakeup_update_sync(self, openid):
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        with self._transaction(self._resolve_db_path('wakeup'), 'wakeup') as conn:
            row = conn.execute('SELECT last_msg_date FROM log WHERE openid=?', (openid,)).fetchone()
            if row and row[0] == today:
                conn.execute('UPDATE log SET updated_at=? WHERE openid=?', (now_str, openid))
                return
            conn.execute(
                'INSERT INTO log (openid, last_msg_date, wakeup_stage, updated_at) VALUES (?,?,0,?) '
                'ON CONFLICT(openid) DO UPDATE SET last_msg_date=?, wakeup_stage=0, updated_at=?',
                (openid, today, now_str, today, now_str),
            )

    async def wakeup_can_send(self, openid):
        """检查是否可发唤醒, 返回 (can_send, target_stage, days_inactive)"""
        return await self._run_io(self._wakeup_can_send_sync, openid)

    def _wakeup_can_send_sync(self, openid):
        with self._locked_conn(self._resolve_db_path('wakeup'), 'wakeup', read_only=True) as conn:
            row = conn.execute(
                'SELECT last_msg_date, wakeup_stage, last_wakeup_date FROM log WHERE openid=?',
                (openid,),
            ).fetchone()
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
        return await self._run_write(self._resolve_db_path('wakeup'), self._wakeup_mark_sent_sync, openid, stage)

    def _wakeup_mark_sent_sync(self, openid, stage):
        today = datetime.now().strftime('%Y-%m-%d')
        with self._transaction(self._resolve_db_path('wakeup'), 'wakeup') as conn:
            conn.execute(
                'UPDATE log SET wakeup_stage=?, last_wakeup_date=?, updated_at=? WHERE openid=?',
                (stage, today, today, openid),
            )

    async def wakeup_get_users(self, target_stage=None):
        """获取可唤醒用户列表"""
        return await self._run_io(self._wakeup_get_users_sync, target_stage)

    def _wakeup_get_users_sync(self, target_stage=None):
        sql = _GET_USERS_SQL
        params = [datetime.now().strftime('%Y-%m-%d')]
        if target_stage is not None:
            sql += ' AND stage = ?2'
            params.append(target_stage)
        with self._locked_conn(self._resolve_db_path('wakeup'), 'wakeup', read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return rows
