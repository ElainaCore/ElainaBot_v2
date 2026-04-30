"""分享来源服务 (ShareMixin) — 由 LogService 继承, 提供 share_record / share_get_referrals"""

import json
import asyncio
from datetime import datetime

# 场景值定义
_SCENE_MAP = {
    0: '未知', 1000: '缺省默认值',
    1001: '网络搜索', 1002: '精选推荐', 1003: '分类推荐',
    1004: '排行推荐', 1005: '音乐台推荐', 1006: '好友分享',
    1007: '扫码关注', 1008: '搜索结果',
    1036: 'QQ群-群bot列表',
    1012: 'QQ频道-频道主页', 1019: 'QQ频道-频道消息',
    1043: '添加到群聊-通过分享链接', 1044: '添加到群聊-通过搜索',
    1045: '添加为好友-通过分享链接', 1046: '添加为好友-通过搜索',
}


class ShareMixin:
    """分享来源 (share.db) 方法集"""

    def _share_conn(self):
        """获取 share.db 连接"""
        return self._get_conn(self._resolve_db_path('share'), 'share')

    @staticmethod
    def get_scene_name(scene):
        """场景值 -> 可读名称"""
        try:
            scene = int(scene) if scene else 0
        except (ValueError, TypeError):
            scene = 0
        return _SCENE_MAP.get(scene, f'未知场景({scene})')

    async def share_record(self, sharer_id, referral_id, scene=0):
        """记录分享关系: sharer_id 邀请了 referral_id"""
        if not sharer_id or not referral_id:
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._share_record_sync, sharer_id, referral_id, scene)

    def _share_record_sync(self, sharer_id, referral_id, scene):
        conn = self._share_conn()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        row = conn.execute(
            "SELECT referrals FROM log WHERE openid=?", (sharer_id,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO log (openid, referrals, created_at, updated_at) VALUES (?,?,?,?)",
                (sharer_id, json.dumps({referral_id: scene}, ensure_ascii=False), now, now))
            conn.commit()
            return True
        try:
            referrals = json.loads(row[0]) if row[0] else {}
        except (json.JSONDecodeError, TypeError):
            referrals = {}
        if referral_id in referrals:
            return True
        referrals[referral_id] = scene
        conn.execute("UPDATE log SET referrals=?, updated_at=? WHERE openid=?",
                     (json.dumps(referrals, ensure_ascii=False), now, sharer_id))
        conn.commit()
        return True

    async def share_get_referrals(self, sharer_id):
        """获取分享者邀请的用户 {referral_id: scene}"""
        if not sharer_id:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._share_get_referrals_sync, sharer_id)

    def _share_get_referrals_sync(self, sharer_id):
        conn = self._share_conn()
        row = conn.execute(
            "SELECT referrals FROM log WHERE openid=?", (sharer_id,)).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    async def share_get_count(self, sharer_id):
        """获取某分享者邀请人数"""
        referrals = await self.share_get_referrals(sharer_id)
        return len(referrals)

    async def share_find_sharer(self, referral_id):
        """根据被邀请者查找分享者 openid"""
        if not referral_id:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._share_find_sharer_sync, referral_id)

    def _share_find_sharer_sync(self, referral_id):
        conn = self._share_conn()
        rows = conn.execute(
            "SELECT openid, referrals FROM log WHERE referrals LIKE ?",
            (f'%{referral_id}%',)).fetchall()
        for row in rows:
            try:
                referrals = json.loads(row[1]) if row[1] else {}
                if referral_id in referrals:
                    return row[0]
            except (json.JSONDecodeError, TypeError):
                pass
        return None
