# -*- coding: utf-8 -*-
"""机器人拦截与黑名单服务（纯函数，不依赖 astrbot 符号）。

本模块仅包含纯逻辑函数，便于独立单测与安全降级。
默认黑名单为空，严格保护隐私，绝不硬编码任何 QQ 号。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Set

__all__ = [
    "parse_blacklist",
    "format_blacklist",
    "extract_sender_id",
    "extract_self_id",
    "is_sender_blacklisted",
    "mask_id",
    "is_destructive_reset_command",
    "is_group_event",
    "is_at_self",
]


def parse_blacklist(raw: Any) -> Set[str]:
    """将配置中的黑名单字符串/列表解析为去重且去空格的 ID 集合。

    支持以逗号、分号、换行、中文逗号、中文分号或空格分隔。
    支持从 [CQ:at,qq=...] 与 [At:...] 码中安全抽取 QQ。
    过滤掉空字符串与非法字符，严格提取数字或标识符。
    """
    if raw is None:
        return set()
    if isinstance(raw, (set, frozenset)):
        return {str(x).strip() for x in raw if str(x).strip()}
    if isinstance(raw, (list, tuple)):
        items: Set[str] = set()
        for x in raw:
            items.update(parse_blacklist(x))
        return items
    if not isinstance(raw, str):
        raw = str(raw)

    raw = raw.strip()
    if not raw:
        return set()

    result: Set[str] = set()

    # 1. 优先提取 CQ 码与 [At:QQ] 码中的纯数字 QQ
    for m in re.finditer(r"(?:\[CQ:at,qq=(\d+)[^\]]*\]|\[At:\s*(\d+)\])", raw, re.I):
        qq = m.group(1) or m.group(2)
        if qq:
            result.add(qq)

    # 2. 移除 CQ 与 At 结构
    stripped = re.sub(r"\[CQ:[^\]]+\]|\[At:[^\]]+\]", " ", raw, flags=re.I)

    # 3. 替换常见分隔符为空格切词
    normalized = re.sub(r"[,;，；\r\n\t]+", " ", stripped)
    tokens = [t.strip() for t in normalized.split(" ") if t.strip()]

    for tok in tokens:
        cleaned = re.sub(r"[^\w\-_]", "", tok)
        if cleaned:
            result.add(cleaned)
    return result


def format_blacklist(ids: Iterable[str]) -> str:
    """格式化黑名单集合为以逗号分隔的标准字符串。"""
    cleaned = sorted({str(x).strip() for x in ids if str(x).strip()})
    return ", ".join(cleaned)


def extract_sender_id(event: Any) -> str:
    """从事件对象中安全提取发送者 ID（数字 QQ 或平台唯一标识）。"""
    if event is None:
        return ""
    # 1. 优先调用 get_sender_id()
    getter = getattr(event, "get_sender_id", None)
    if callable(getter):
        try:
            res = getter()
            if res not in (None, ""):
                return str(res).strip()
        except Exception:  # noqa: BLE001
            pass

    # 2. 从属性取
    for attr in ("sender_id", "user_id", "author_id"):
        val = getattr(event, attr, None)
        if val not in (None, ""):
            return str(val).strip()

    # 3. 从 message_obj.sender / message_obj 取
    msg_obj = getattr(event, "message_obj", None)
    if msg_obj is not None:
        sender = getattr(msg_obj, "sender", None)
        if sender is not None:
            for attr in ("user_id", "id", "sender_id"):
                val = getattr(sender, attr, None)
                if val not in (None, ""):
                    return str(val).strip()
        for attr in ("user_id", "sender_id"):
            val = getattr(msg_obj, attr, None)
            if val not in (None, ""):
                return str(val).strip()

    # 4. 从 raw_message / dict payload 兜底
    raw = getattr(event, "raw_message", None)
    if isinstance(raw, dict):
        user_id = raw.get("user_id") or raw.get("sender", {}).get("user_id")
        if user_id not in (None, ""):
            return str(user_id).strip()

    return ""


def extract_self_id(event: Any) -> str:
    """从事件对象中提取机器人自身的 ID。"""
    if event is None:
        return ""
    getter = getattr(event, "get_self_id", None)
    if callable(getter):
        try:
            res = getter()
            if res not in (None, ""):
                return str(res).strip()
        except Exception:  # noqa: BLE001
            pass

    for attr in ("self_id", "bot_id"):
        val = getattr(event, attr, None)
        if val not in (None, ""):
            return str(val).strip()

    msg_obj = getattr(event, "message_obj", None)
    if msg_obj is not None:
        for attr in ("self_id", "bot_id"):
            val = getattr(msg_obj, attr, None)
            if val not in (None, ""):
                return str(val).strip()

    return ""


def is_sender_blacklisted(sender_id: str, blacklist: Set[str], self_id: str = "") -> bool:
    """判定发送者是否在黑名单中。

    红线自保：无论如何绝不拦截自身（self_id），防止管理员误将自身加入黑名单造成死锁。
    """
    if not sender_id or not blacklist:
        return False
    sender_clean = str(sender_id).strip()
    self_clean = str(self_id).strip()
    if self_clean and sender_clean == self_clean:
        return False
    return sender_clean in blacklist


def mask_id(uid: str) -> str:
    """对账号 ID 进行脱敏展示，防止在聊天消息中泄露隐私。

    示例：12345678 -> 123***78；小于等于 4 位不脱敏。
    """
    uid_str = str(uid).strip()
    if len(uid_str) <= 4:
        return uid_str
    if len(uid_str) <= 6:
        return uid_str[:2] + "***" + uid_str[-1:]
    return uid_str[:3] + "***" + uid_str[-2:]


def is_destructive_reset_command(msg_str: str) -> bool:
    """判定消息是否包含清空会话的破坏性指令（如 /new, /reset, /clear, /重置, /清空）。"""
    if not msg_str:
        return False
    # 移除首尾与常见 At CQ 码后再进行匹配
    stripped = re.sub(r"(?:\[CQ:at,qq=\d+[^\]]*\]|\[At:\s*\d+\])", " ", str(msg_str), flags=re.I).strip()
    pattern = r"^[!/#/]?\s*(?:new|reset|clear|重置|清空)(?:\s|$|@)"
    return bool(re.search(pattern, stripped, re.I))


def is_group_event(event: Any) -> bool:
    """判定当前事件是否发生在群聊场景中。"""
    if event is None:
        return False
    is_priv = getattr(event, "is_private_chat", None)
    if callable(is_priv):
        try:
            return not bool(is_priv())
        except Exception:  # noqa: BLE001
            pass
    msg_obj = getattr(event, "message_obj", None)
    if msg_obj is not None:
        if getattr(msg_obj, "group_id", None):
            return True
        mtype = str(getattr(msg_obj, "message_type", "") or "").lower()
        if "group" in mtype:
            return True
        if "private" in mtype:
            return False
    if getattr(event, "group_id", None):
        return True
    return False


def is_at_self(event: Any, self_id: str) -> bool:
    """判定事件中是否包含明确艾特自身（self_id）的段落。"""
    if not event or not self_id:
        return False
    self_clean = str(self_id).strip()

    # 1. 检查 event.get_messages() 或 message_obj.message 组件
    messages = []
    getter = getattr(event, "get_messages", None)
    if callable(getter):
        try:
            messages = getter() or []
        except Exception:  # noqa: BLE001
            pass
    if not messages:
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None:
            messages = getattr(msg_obj, "message", []) or []

    for comp in messages:
        c_type = type(comp).__name__
        if c_type == "At" or hasattr(comp, "qq") or hasattr(comp, "target"):
            target_qq = str(getattr(comp, "qq", getattr(comp, "target", "")) or "").strip()
            if target_qq == self_clean:
                return True

    # 2. 从 message_str / raw_message 中检查
    for attr in ("message_str", "raw_message"):
        val = getattr(event, attr, None)
        if not val and getattr(event, "message_obj", None) is not None:
            val = getattr(event.message_obj, attr, None)
        if isinstance(val, str) and val:
            if f"qq={self_clean}" in val or f"[At:{self_clean}]" in val or f"[At: {self_clean}]" in val:
                return True

    return False
