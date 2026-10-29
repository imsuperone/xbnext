# -*- coding: utf-8 -*-
"""从 ``event`` 取「会话 / 群 / 人 / 平台」的唯一实现。

以前这件事在 inject_log / attribution / tokenline / profile / recall
各写各的、兜底样式互不相同（10 个散点、4 种写法）—— 统一收口到本模块
（aidoc/02 §10.2-3）。约定：

- 每个函数都是**全函数**：任何异常返回空串，绝不冒泡，调用方不再套 try；
- ``umo_of`` 优先 ``unified_msg_origin``，拿不到兜底 ``get_session_id()``；
- ``gid_of`` 沿用真机跑过的 xbimg 同款三级顺序（原 profile._speaker）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["umo_of", "uid_of", "name_of", "gid_of", "platform_of"]


def umo_of(event: Any) -> str:
    """会话标识：优先 ``unified_msg_origin``，兜底 ``get_session_id()``。"""
    try:
        umo = getattr(event, "unified_msg_origin", None)
        if isinstance(umo, str) and umo:
            return umo
        getter = getattr(event, "get_session_id", None)
        if callable(getter):
            return str(getter() or "")
        return ""
    except Exception:  # noqa: BLE001
        return ""


def uid_of(event: Any) -> str:
    """发言人 ID；拿不到返回 ``""``（不猜）。

    顺序：``message_obj.sender.user_id`` → ``get_sender_id()`` →
    ``get_user_id()``；``None`` / ``0`` 视为没有。
    """
    if event is None:
        return ""
    try:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        uid = str(getattr(sender, "user_id", "") or "")
        if uid:
            return uid
        for attr in ("get_sender_id", "get_user_id"):
            fn = getattr(event, attr, None)
            if callable(fn):
                uid = str(fn() or "")
                if uid:
                    return uid
    except Exception:  # noqa: BLE001
        return ""
    return ""


def name_of(event: Any) -> str:
    """提取发送人昵称。"""
    if event is None:
        return ""
    try:
        mo = getattr(event, "message_obj", None)
        sender = getattr(mo, "sender", None) if mo is not None else None
        if sender is not None:
            for attr in ("nickname", "card", "name", "display_name"):
                val = str(getattr(sender, attr, "") or "").strip()
                if val:
                    return val
        for attr in ("get_sender_name", "get_user_name"):
            fn = getattr(event, attr, None)
            if callable(fn):
                name = str(fn() or "").strip()
                if name:
                    return name
    except Exception:  # noqa: BLE001
        return ""
    return ""


def gid_of(event: Any) -> str:
    """所在群号（分群维度）；私聊 / 拿不到返回 ``""``。

    三级顺序（真机跑过的 xbimg 同款）：``event.get_group_id()`` →
    ``message_obj.group_id`` → ``unified_msg_origin`` 的群段；
    ``"None"`` / ``"0"`` 视为没有。
    """
    if event is None:
        return ""

    def _clean(value: Any) -> str:
        text = str(value or "").strip()
        return "" if text in ("None", "none", "0") else text

    try:
        fn = getattr(event, "get_group_id", None)
        gid = _clean(fn()) if callable(fn) else ""
        if not gid:
            mo = getattr(event, "message_obj", None)
            gid = _clean(getattr(mo, "group_id", "") if mo else "")
        if not gid:
            umo = umo_of(event)
            parts = [p for p in umo.split("/") if p]
            if len(parts) >= 3 and "group" in parts[1].lower():
                gid = _clean(parts[2])
            elif "!" in umo:
                # 兼容 Matrix 房间 ID 格式（以 ! 开头的 room id）
                for tok in umo.replace(":", "/").split("/"):
                    tok_s = _clean(tok)
                    if tok_s.startswith("!"):
                        gid = tok_s
                        break
        return gid
    except Exception:  # noqa: BLE001
        return ""


def platform_of(event: Any) -> str:
    """平台名；``get_platform_name()`` 取不到就从会话来源推。"""
    if event is None:
        return ""
    try:
        raw_p = ""
        fn = getattr(event, "get_platform_name", None)
        if callable(fn):
            raw_p = str(fn() or "").strip()
        if not raw_p:
            for attr in ("platform_id", "platform"):
                raw_p = str(getattr(event, attr, "") or "").strip()
                if raw_p:
                    break
        if not raw_p:
            umo = umo_of(event)
            raw_p = umo.split("/")[0].split(":")[0].strip()
        return raw_p
    except Exception:  # noqa: BLE001
        return ""
