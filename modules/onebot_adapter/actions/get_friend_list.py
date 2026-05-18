"""get_friend_list — 返回已缓存的用户列表"""

from __future__ import annotations

from modules.onebot_adapter.base_action import BaseAction


class GetFriendListAction(BaseAction):
    """get_friend_list — 返回已缓存的用户列表"""

    async def execute(self, params: dict, echo=None) -> dict:
        friends = []
        for (_openid, _type), qq_id in self._ctx.id_mapper._cache_fwd.items():
            if _type == 'user':
                friends.append(
                    {
                        'user_id': qq_id,
                        'nickname': str(qq_id),
                        'remark': '',
                    }
                )
        return self._ok(friends, echo=echo)
