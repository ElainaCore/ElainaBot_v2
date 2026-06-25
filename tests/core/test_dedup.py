"""_EventDedup 去重逻辑单元测试"""

import time
from unittest.mock import patch

from core.bot.event import _EventDedup


class TestEventDedup:
    """_EventDedup 基本功能"""

    def test_first_id_not_dup(self):
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001') is False

    def test_same_id_is_dup(self):
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001') is False
        assert dedup.is_dup('msg_001') is True

    def test_different_ids_not_dup(self):
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001') is False
        assert dedup.is_dup('msg_002') is False

    def test_empty_id_skipped(self):
        dedup = _EventDedup()
        assert dedup.is_dup('') is False
        assert dedup.is_dup(None) is False
        assert dedup.is_dup('', None) is False

    def test_multi_id_dup_by_any(self):
        """传入多个 ID，任一已见过即判定重复"""
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001', 'evt_001') is False
        # event_id 相同 → 重复
        assert dedup.is_dup('msg_999', 'evt_001') is True

    def test_multi_id_dup_by_msg_id(self):
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001', 'evt_001') is False
        # message_id 相同 → 重复
        assert dedup.is_dup('msg_001', 'evt_999') is True


class TestDedupSelfCollision:
    """message_id == event_id 时不应自碰撞 (修复核心场景)"""

    def test_same_msg_and_event_id_not_self_collision(self):
        """生命周期事件 message_id 回退为 event_id 时，首次不应被判定重复"""
        dedup = _EventDedup()
        # 模拟 lifecycle 事件: message_id == event_id
        assert dedup.is_dup('evt_abc', 'evt_abc') is False

    def test_same_msg_and_event_id_second_time_is_dup(self):
        """相同 message_id == event_id 第二次应正确去重"""
        dedup = _EventDedup()
        assert dedup.is_dup('evt_abc', 'evt_abc') is False
        assert dedup.is_dup('evt_abc', 'evt_abc') is True

    def test_lifecycle_then_different_event(self):
        """lifecycle 事件后，不同事件应正常通过"""
        dedup = _EventDedup()
        assert dedup.is_dup('evt_001', 'evt_001') is False
        assert dedup.is_dup('evt_002', 'evt_002') is False

    def test_no_id_pollution_on_dup(self):
        """重复事件不应将新 ID 写入 _seen"""
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001', 'evt_001') is False
        # msg_new 未见过，但 evt_001 已见过 → 判定重复
        assert dedup.is_dup('msg_new', 'evt_001') is True
        # msg_new 不应被加入 _seen (因为事件整体是重复的)
        assert 'msg_new' not in dedup._seen


class TestDedupTTL:
    """TTL 过期清除"""

    def test_expired_id_not_dup(self):
        dedup = _EventDedup()
        assert dedup.is_dup('msg_001') is False
        # 模拟 TTL 过期
        dedup._seen['msg_001'] = time.time() - 1
        dedup._next_purge = 0  # 强制下次清理
        assert dedup.is_dup('msg_001') is False

    def test_purge_on_size_overflow(self):
        dedup = _EventDedup()
        now = time.time()
        # 填充 5001 个已过期条目
        for i in range(5001):
            dedup._seen[f'old_{i}'] = now - 1
        dedup._next_purge = now + 9999  # 不触发时间清理
        # 下次调用会因 len > 5000 触发清理
        assert dedup.is_dup('new_msg') is False
        assert len(dedup._seen) == 1  # 只剩 new_msg
