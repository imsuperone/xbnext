# -*- coding: utf-8 -*-
"""撤回识别的纯逻辑部分。

**本文件不 import astrbot**：输入是适配器的原始载荷与普通对象，
输出是字符串 / 列表。

OneBot v11 的撤回是 notice 事件，aiocqhttp 适配器把整个事件 dict 塞进
``raw_message``（``message_str`` 为空，core 不会拿它唤醒 LLM），形如::

    {"post_type": "notice", "notice_type": "group_recall",
     "group_id": 123, "user_id": 456, "operator_id": 456,
     "message_id": 789, ...}                     # 群撤回
    {"post_type": "notice", "notice_type": "friend_recall",
     "user_id": 456, "message_id": 789, ...}      # 好友撤回

只有 ``notice_type`` 命中且 ``message_id`` 非空才返回被撤回的 id ——
普通消息、poke / honor 之类其它 notice 一概返回 ``None``，绝不误伤。
字符串形态（CQ 码 / JSON 串）不认：撤回在 aiocqhttp 下永远是 dict。
"""

from __future__ import annotations

from typing import Any, List, Optional

__all__ = ["RECALL_NOTICE_TYPES", "parse_recall", "raw_sources", "trigger_id"]

#: 认得的撤回 notice 类型（群 + 好友）
RECALL_NOTICE_TYPES = ("group_recall", "friend_recall")


def parse_recall(raw: Any) -> Optional[str]:
    """撤回 notice → 被撤回消息的 ``message_id``；不是撤回返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    if raw.get("notice_type") not in RECALL_NOTICE_TYPES:
        return None
    mid = raw.get("message_id")
    if mid is None or str(mid).strip() == "":
        return None
    return str(mid)


def raw_sources(event: Any) -> List[Any]:
    """列出可能装着 OneBot 原始载荷的字段（照抄 face 的多字段扫描）。"""
    out: List[Any] = []
    for owner in (getattr(event, "message_obj", None), event):
        if owner is None:
            continue
        for attr in ("raw_message", "raw_msg", "origin_message"):
            try:
                v = getattr(owner, attr, None)
            except Exception:  # noqa: BLE001
                v = None
            if v not in (None, ""):
                out.append(v)
    return out


def trigger_id(event: Any) -> str:
    """触发消息的 ``message_id``（登记在飞任务的键）；取不到空串。

    扫描顺序与 attribution 的 ``message_id_of`` 一致（``message_obj``
    优先），两边独立实现、互不依赖。
    """
    for owner in (getattr(event, "message_obj", None), event):
        if owner is None:
            continue
        for attr in ("message_id", "msg_id", "id"):
            try:
                v = getattr(owner, attr, None)
            except Exception:  # noqa: BLE001
                v = None
            if isinstance(v, (str, int)) and str(v) != "":
                return str(v)
    return ""
