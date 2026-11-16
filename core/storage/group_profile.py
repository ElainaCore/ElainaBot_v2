"""群资料中来自 QQ 的扩展字段。"""

import json


def group_profile_fields(data):
    """从平台响应或数据库行取得统一的扩展资料字段。"""
    tags = data.get('group_tags')
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = []
    return {
        'group_finger_memo': str(data.get('group_finger_memo') or ''),
        'group_class_text': str(data.get('group_class_text') or ''),
        'group_tags': [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else [],
    }
