"""EventFactory — generate realistic Event objects for stress testing.

Creates valid Event instances matching the production __slots__ schema.
Supports all event types: group messages, direct messages, channel messages,
interactions, lifecycle events, and audit events.
"""

import time
import uuid

from core.message.event import (
    AT_MESSAGE_CREATE,
    C2C_MESSAGE_CREATE,
    DIRECT_MESSAGE_CREATE,
    FRIEND_ADD,
    GROUP_ADD_ROBOT,
    GROUP_AT_MESSAGE_CREATE,
    GROUP_DEL_ROBOT,
    GROUP_MESSAGE_CREATE,
    INTERACTION_CREATE,
    MESSAGE_AUDIT_PASS,
    MESSAGE_AUDIT_REJECT,
    MESSAGE_REACTION_ADD,
    Event,
)


class EventFactory:
    """Generate realistic Event objects for stress testing.

    Events are fully populated with required fields so they can flow through
    the entire pipeline: dedup → union_id → lifecycle → log → track → dispatch.
    """

    _seq = 0

    @classmethod
    def _next_seq(cls):
        cls._seq += 1
        return cls._seq

    @classmethod
    def _make_raw(cls, event_type, d_data=None):
        return {
            "op": 0,
            "s": cls._next_seq(),
            "t": event_type,
            "id": f"evt_{uuid.uuid4().hex[:16]}",
            "d": d_data or {},
        }

    # ---- Group Messages ----

    @classmethod
    def group_at_message(cls, content, user_id="user_001", group_id="group_001",
                         appid="102000001", is_at_self=True, is_at_all=False, is_bot=False):
        """GROUP_AT_MESSAGE_CREATE with @self."""
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        d = {
            "id": msg_id,
            "author": {
                "id": user_id,
                "member_openid": f"mem_{user_id}",
                "union_openid": f"uni_{user_id}",
            },
            "group_id": group_id,
            "group_openid": group_id,
            "content": content,
            "timestamp": str(time.time()),
            "mentions": [{"id": appid}] if is_at_self else [],
        }
        if is_bot:
            d["author"]["bot"] = True
        raw = cls._make_raw(GROUP_AT_MESSAGE_CREATE, d)
        return Event.from_websocket(appid, raw)

    @classmethod
    def group_message(cls, content, user_id="user_001", group_id="group_001",
                      appid="102000001", is_at_self=False, is_at_all=False, is_bot=False):
        """GROUP_MESSAGE_CREATE (full group message without @)."""
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        d = {
            "id": msg_id,
            "author": {
                "id": user_id,
                "member_openid": f"mem_{user_id}",
                "union_openid": f"uni_{user_id}",
            },
            "group_id": group_id,
            "group_openid": group_id,
            "content": content,
            "timestamp": str(time.time()),
            "mentions": [{"id": appid}] if is_at_self else [],
        }
        if is_bot:
            d["author"]["bot"] = True
        raw = cls._make_raw(GROUP_MESSAGE_CREATE, d)
        event = Event.from_websocket(appid, raw)
        # Fix is_at_self (parser sets it based on mentions)
        if is_at_all:
            event.is_at_all = True
        return event

    @classmethod
    def group_at_all(cls, content, user_id="user_001", group_id="group_001",
                     appid="102000001"):
        """GROUP_MESSAGE_CREATE with @all."""
        event = cls.group_message(content, user_id, group_id, appid, is_at_all=True)
        event.is_at_all = True
        return event

    # ---- Direct Messages ----

    @classmethod
    def direct_message(cls, content, user_id="user_001", appid="102000001"):
        """C2C_MESSAGE_CREATE (private chat)."""
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        d = {
            "id": msg_id,
            "author": {
                "id": user_id,
                "union_openid": f"uni_{user_id}",
            },
            "content": content,
            "timestamp": str(time.time()),
        }
        raw = cls._make_raw(C2C_MESSAGE_CREATE, d)
        return Event.from_websocket(appid, raw)

    # ---- Channel Messages ----

    @classmethod
    def channel_message(cls, content, user_id="user_001", channel_id="chan_001",
                        guild_id="guild_001", appid="102000001"):
        """AT_MESSAGE_CREATE (channel/freq)."""
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        d = {
            "id": msg_id,
            "author": {"id": user_id},
            "channel_id": channel_id,
            "guild_id": guild_id,
            "content": content,
            "timestamp": str(time.time()),
        }
        raw = cls._make_raw(AT_MESSAGE_CREATE, d)
        return Event.from_websocket(appid, raw)

    @classmethod
    def channel_direct_message(cls, content, user_id="user_001", guild_id="guild_001",
                               appid="102000001"):
        """DIRECT_MESSAGE_CREATE (channel DM)."""
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        d = {
            "id": msg_id,
            "author": {"id": user_id},
            "guild_id": guild_id,
            "content": content,
            "timestamp": str(time.time()),
        }
        raw = cls._make_raw(DIRECT_MESSAGE_CREATE, d)
        return Event.from_websocket(appid, raw)

    # ---- Interactions ----

    @classmethod
    def interaction(cls, interaction_id="inter_001", appid="102000001",
                    user_id="user_001", data=None):
        """INTERACTION_CREATE (button click)."""
        raw = cls._make_raw(INTERACTION_CREATE, {
            "id": interaction_id,
            "user_id": user_id,
            "data": data or {"type": 1},
            "chat_type": 1,
        })
        return Event.from_websocket(appid, raw)

    # ---- Lifecycle ----

    @classmethod
    def lifecycle(cls, event_type, appid="102000001", user_id="user_001",
                  group_id="group_001"):
        """FRIEND_ADD, FRIEND_DEL, GROUP_ADD_ROBOT, GROUP_DEL_ROBOT."""
        d = {
            "user_id": user_id,
            "openid": user_id,
        }
        if event_type in (GROUP_ADD_ROBOT, GROUP_DEL_ROBOT):
            d["group_id"] = group_id
            d["group_openid"] = group_id
        raw = cls._make_raw(event_type, d)
        return Event.from_websocket(appid, raw)

    @classmethod
    def group_member(cls, event_type, appid="102000001", user_id="user_001",
                     group_id="group_001"):
        """GROUP_MEMBER_ADD, GROUP_MEMBER_REMOVE."""
        d = {
            "group_openid": group_id,
            "member_openid": user_id,
            "op_member_openid": user_id,
            "timestamp": int(time.time()),
        }
        raw = cls._make_raw(event_type, d)
        return Event.from_websocket(appid, raw)

    # ---- Audit ----

    @classmethod
    def audit_pass(cls, audit_id="audit_001", real_msg_id="real_001", appid="102000001"):
        """MESSAGE_AUDIT_PASS."""
        raw = cls._make_raw(MESSAGE_AUDIT_PASS, {
            "audit_id": audit_id,
            "message_id": real_msg_id,
        })
        return Event.from_websocket(appid, raw)

    @classmethod
    def audit_reject(cls, audit_id="audit_001", appid="102000001"):
        """MESSAGE_AUDIT_REJECT."""
        raw = cls._make_raw(MESSAGE_AUDIT_REJECT, {
            "audit_id": audit_id,
        })
        return Event.from_websocket(appid, raw)

    # ---- Silent ----

    @classmethod
    def reaction_add(cls, user_id="user_001", group_id="group_001", appid="102000001"):
        """MESSAGE_REACTION_ADD."""
        raw = cls._make_raw(MESSAGE_REACTION_ADD, {
            "user_id": user_id,
            "group_id": group_id,
            "target": {"id": "msg_001", "type": 0},
            "emoji": {"id": "1", "type": 1},
        })
        return Event.from_websocket(appid, raw)

    # ---- Batch Generators ----

    @classmethod
    def batch_group_messages(cls, count, content_pattern="/ping {i}", group_ids=None,
                             user_ids=None, appid="102000001"):
        """Generate N GROUP_AT_MESSAGE_CREATE events."""
        group_ids = group_ids or ["group_001"]
        user_ids = user_ids or [f"user_{i:04d}" for i in range(min(count, 1000))]
        events = []
        for i in range(count):
            gid = group_ids[i % len(group_ids)]
            uid = user_ids[i % len(user_ids)]
            content = content_pattern.format(i=i)
            events.append(cls.group_at_message(content, uid, gid, appid))
        return events

    @classmethod
    def batch_mixed_events(cls, count, appid="102000001"):
        """Generate a mix of all event types."""
        events = []
        for i in range(count):
            r = i % 20
            if r < 8:  # 40% GROUP_AT
                events.append(cls.group_at_message(
                    f"/ping_{i}", f"user_{i:04d}", "group_001", appid))
            elif r < 12:  # 20% GROUP (non-@)
                events.append(cls.group_message(
                    f"hello_{i}", f"user_{i:04d}", "group_001", appid, is_at_self=False))
            elif r < 15:  # 15% C2C
                events.append(cls.direct_message(
                    f"/help_{i}", f"user_{i:04d}", appid))
            elif r < 17:  # 10% INTERACTION
                events.append(cls.interaction(
                    f"inter_{i}", appid, f"user_{i:04d}"))
            elif r < 18:  # 5% REACTION (silent)
                events.append(cls.reaction_add(
                    f"user_{i:04d}", "group_001", appid))
            elif r < 19:  # 5% FRIEND_ADD (lifecycle)
                events.append(cls.lifecycle(
                    FRIEND_ADD, appid, f"user_{i:04d}"))
            else:  # 5% AUDIT
                events.append(cls.audit_pass(
                    f"audit_{i}", f"real_{i}", appid))
        return events
