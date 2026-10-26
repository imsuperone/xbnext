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
