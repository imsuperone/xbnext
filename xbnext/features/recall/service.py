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

from typing import Any, Dict, List, Optional

__all__ = [
    "CONFIRM_TIMEOUT",
    "RECALL_NOTICE_TYPES",
    "ask_text",
    "match_answer",
    "parse_recall",
    "raw_sources",
    "recall_meta",
    "trigger_id",
]

#: 认得的撤回 notice 类型（群 + 好友）
RECALL_NOTICE_TYPES = ("group_recall", "friend_recall")

#: 撤回确认询问的等待时长（秒）；超时按老规矩自动取消
CONFIRM_TIMEOUT = 30.0

#: 认「是」（取消）的回答词 —— 精确匹配，不搞包含判断防误伤
_YES_WORDS = frozenset({"是", "对", "要", "确认", "取消", "y", "yes", "ok", "1"})

#: 认「否」（保留）的回答词
_NO_WORDS = frozenset(
    {"否", "不", "不要", "不取消", "不要取消", "保留", "继续", "不用", "n", "no", "0"}
)

#: 归一化时剥掉的首尾字符（标点 + 引导符，用户照抄提问里的「是」也算）
_EDGE_CHARS = "。！？!?.．,，~～…、;；:：\"'`“”‘’「」『』（）() \t\n"


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


def recall_meta(raw: Any) -> Optional[Dict[str, str]]:
    """撤回 notice → ``{message_id, operator_id, group_id}``；非撤回 ``None``。

    ``operator_id`` 是**执行撤回的人**（群管理员可以撤别人的 message，
    此时 operator ≠ user）；群撤回带 ``group_id``，好友撤回没有 ⇒
    ``group_id`` 为空串、``operator_id`` 取 ``user_id``。
    """
    mid = parse_recall(raw)
    if mid is None:
        return None
    operator = raw.get("operator_id")
    if operator in (None, ""):
        operator = raw.get("user_id")
    group = raw.get("group_id")
    return {
        "message_id": mid,
        "operator_id": str(operator) if operator not in (None, "") else "",
        "group_id": str(group) if group not in (None, "") else "",
    }


def match_answer(text: Any) -> Optional[bool]:
    """回答文本 → ``True``（取消）/ ``False``（保留）/ ``None``（不认识）。

    只做**精确匹配**（先剥首尾空白与标点、转小写）：群里聊着聊着出现
    「取消」两个字太常见，包含判断会误伤 —— 认不出的词一律 ``None``，
    询问继续等到超时。
    """
    if not isinstance(text, str):
        return None
    norm = text.strip(_EDGE_CHARS).lower()
    if not norm:
        return None
    if norm in _NO_WORDS:
        return False
    if norm in _YES_WORDS:
        return True
    return None


def ask_text() -> str:
    """撤回确认询问的正文（群私通用；群里前面会再 @ 撤回者）。"""
    return (
        "你撤回了一条消息，要取消这次回复吗？"
        "回复「是」：不再往下生成，已经发出来的回复也一并撤回；"
        "回复「否」：保留。30 秒内不回复，就自动取消。"
    )


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
