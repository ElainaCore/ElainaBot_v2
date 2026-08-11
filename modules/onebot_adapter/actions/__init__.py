"""Action 命令模块 — Command 模式

每个 OneBot 11 API action 对应一个独立文件中的命令类。

重导出:
  - SendMessageAction: send_msg / send_group_msg / send_private_msg
  - DeleteMessageAction: delete_msg
  - GetLoginInfoAction: get_login_info
  - GetGroupListAction: get_group_list
  - GetFriendListAction: get_friend_list
  - GetStrangerInfoAction: get_stranger_info
  - GetGroupMemberInfoAction: get_group_member_info
  - GetGroupMemberListAction: get_group_member_list
  - GetStatusAction: get_status
  - GetVersionInfoAction: get_version_info
  - CanSendImageAction: can_send_image
  - CanSendRecordAction: can_send_record
"""

from modules.onebot_adapter.actions.can_send_image import CanSendImageAction
from modules.onebot_adapter.actions.can_send_record import CanSendRecordAction
from modules.onebot_adapter.actions.delete_msg import DeleteMessageAction
from modules.onebot_adapter.actions.get_friend_list import GetFriendListAction
from modules.onebot_adapter.actions.get_group_list import GetGroupListAction
from modules.onebot_adapter.actions.get_group_member_info import (
    GetGroupMemberInfoAction,
)
from modules.onebot_adapter.actions.get_group_member_list import (
    GetGroupMemberListAction,
)
from modules.onebot_adapter.actions.get_login_info import GetLoginInfoAction
from modules.onebot_adapter.actions.get_status import GetStatusAction
from modules.onebot_adapter.actions.get_stranger_info import GetStrangerInfoAction
from modules.onebot_adapter.actions.get_version_info import GetVersionInfoAction
from modules.onebot_adapter.actions.send_message import SendMessageAction

__all__ = [
    'CanSendImageAction',
    'CanSendRecordAction',
    'DeleteMessageAction',
    'GetFriendListAction',
    'GetGroupListAction',
    'GetGroupMemberInfoAction',
    'GetGroupMemberListAction',
    'GetLoginInfoAction',
    'GetStatusAction',
    'GetStrangerInfoAction',
    'GetVersionInfoAction',
    'SendMessageAction',
]
