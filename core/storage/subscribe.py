"""订阅消息服务 (SubscribeMixin) — 模板ID ↔ 群/用户 映射 (subscribe.db)"""

import asyncio
from datetime import datetime

SUB_TYPE_ONCE = 'once'
SUB_TYPE_PERMANENT = 'permanent'

OP_SUBSCRIBE = 1  # 开启订阅
OP_UNSUBSCRIBE = 2  # 关闭订阅

_UPSERT_SQL = (
    'INSERT INTO log (template_id, target_id, target_type, sub_type, subscribe_id, '
    'status, subscribe_ts, update_ts, updated_at) VALUES (?,?,?,?,?,?,?,?,?) '
    'ON CONFLICT(template_id, target_id) DO UPDATE SET '
    'sub_type=excluded.sub_type, subscribe_id=excluded.subscribe_id, '
    'status=excluded.status, subscribe_ts=excluded.subscribe_ts, '
    'update_ts=excluded.update_ts, updated_at=excluded.updated_at'
)


class SubscribeMixin:
    """订阅消息映射 (subscribe.db) 方法集"""

    def _subscribe_locked(self):
        """获取 subscribe.db 连接和锁"""
        db_path = self._resolve_db_path('subscribe')
        conn = self._get_conn(db_path, 'subscribe')
        return conn, self._conn_locks.get(db_path)

    async def subscribe_record(self, results, group_id='', user_id='', once_template_ids=()):
        """记录订阅事件 result 列表并返回写入条数: group_id 非空为群聊链路否则单聊, 模板 ID 在 once_template_ids 中为单次订阅否则永久, op 1=开启(status=1) 2=关闭(status=0)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._subscribe_record_sync, results, group_id, user_id, once_template_ids)

    def _subscribe_record_sync(self, results, group_id, user_id, once_template_ids):
        target_id = group_id or user_id
        if not target_id:
            return 0
        target_type = 'group' if group_id else 'user'
        once = {str(t) for t in once_template_ids}
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = []
        for item in results or []:
            if not isinstance(item, dict):
                continue
            template_id = str(item.get('custom_template_id') or item.get('template_id') or '')
            if not template_id:
                continue
            sub_type = SUB_TYPE_ONCE if template_id in once else SUB_TYPE_PERMANENT
            rows.append((
                template_id,
                target_id,
                target_type,
                sub_type,
                str(item.get('subscribe_id') or ''),
                1 if int(item.get('op') or 0) == OP_SUBSCRIBE else 0,
                int(item.get('subscribe_ts') or 0),
                int(item.get('update_ts') or 0),
                now,
            ))
        if not rows:
            return 0
        conn, lock = self._subscribe_locked()
        with lock:
            conn.executemany(_UPSERT_SQL, rows)
            conn.commit()
        return len(rows)

    def subscribe_get_map(self, sub_type=None, target_type='group'):
        """{template_id: [target_id, ...]} — 仅返回允许订阅 (status=1) 的映射"""
        sql = 'SELECT template_id, target_id FROM log WHERE status=1'
        params = []
        if target_type:
            sql += ' AND target_type=?'
            params.append(target_type)
        if sub_type:
            sql += ' AND sub_type=?'
            params.append(sub_type)
        mapping = {}
        for row in self.query('subscribe', sql, tuple(params)):
            mapping.setdefault(row['template_id'], []).append(row['target_id'])
        return mapping

    def subscribe_get_targets(self, template_id, sub_type=None, target_type='group'):
        """查询某模板允许订阅的目标列表 [{target_id, sub_type, subscribe_id}, ...]"""
        sql = 'SELECT target_id, sub_type, subscribe_id FROM log WHERE status=1 AND template_id=?'
        params = [str(template_id)]
        if target_type:
            sql += ' AND target_type=?'
            params.append(target_type)
        if sub_type:
            sql += ' AND sub_type=?'
            params.append(sub_type)
        return self.query('subscribe', sql, tuple(params))

    def subscribe_get_by_target(self, target_id, target_type='group'):
        """查询某群/用户已订阅的模板列表 [{template_id, sub_type, subscribe_id}, ...]"""
        sql = 'SELECT template_id, sub_type, subscribe_id FROM log WHERE status=1 AND target_id=?'
        params = [str(target_id)]
        if target_type:
            sql += ' AND target_type=?'
            params.append(target_type)
        return self.query('subscribe', sql, tuple(params))

    async def subscribe_consume(self, template_id, target_id):
        """单次订阅发送后作废 (status=0)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._subscribe_consume_sync, template_id, target_id)

    def _subscribe_consume_sync(self, template_id, target_id):
        conn, lock = self._subscribe_locked()
        with lock:
            conn.execute(
                'UPDATE log SET status=0, updated_at=? WHERE template_id=? AND target_id=?',
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), str(template_id), str(target_id)),
            )
            conn.commit()
