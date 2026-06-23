#!/usr/bin/env python
"""身份标识辅助 — union_openid / openid 交换"""


class IdentityHelper:
    """身份标识辅助 — union_openid / openid 交换"""

    @staticmethod
    def swap_ids(uid, union_id, should_swap):
        """union_openid 与 openid 交换: 返回 (user_id, union_openid, raw_user_id)"""
        if should_swap and union_id:
            return union_id, uid, uid
        return uid, union_id or uid, uid
