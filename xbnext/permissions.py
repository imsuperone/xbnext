# -*- coding: utf-8 -*-
"""事件权限判定 —— 全仓唯一实现。

`botguard` 与 `updater` 都需要判断「这条消息是不是管理员发的」，以前各写一份
（逻辑完全相同、兜底却会漂移）。本模块放在功能层之上、只依赖事件对象，
两边共用同一份实现。
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["extract_sender_id", "is_admin_event"]


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


async def is_admin_event(event: Any) -> bool:
    """判定当前事件发送者是否具备管理员权限（超管/群主/管理员）。

    层级判定机制：
    1. 优先调用 ``event.is_admin()``（兼容同步与协程）
    2. 检查 ``event.role`` 或 ``event.sender.role`` 是否为 admin/owner/master
    3. 检查 AstrBot 内核的管理员/主号 ID 列表
    4. 平台不支持或判定异常时拒绝（fail-closed，严格阻止普通用户操作更新）
    """
    if event is None:
        return False

    # 1. 尝试 event.is_admin()（AstrBot 官方事件权限接口，支持 sync / async）
    try:
        fn = getattr(event, "is_admin", None)
        if callable(fn):
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, bool):
                return res
    except Exception:  # noqa: BLE001
        pass

    # 2. 尝试 event.role 判定（group admin / owner / master）
    try:
        role = str(getattr(event, "role", "") or "").lower()
        if role in ("admin", "owner", "master"):
            return True
        if role in ("member", "user"):
            return False
    except Exception:  # noqa: BLE001
        pass

    # 3. 尝试 event.sender.role
    try:
        sender = getattr(event, "sender", None)
        if sender:
            s_role = str(getattr(sender, "role", "") or "").lower()
            if s_role in ("admin", "owner", "master"):
                return True
            if s_role in ("member", "user"):
                return False
    except Exception:  # noqa: BLE001
        pass

    # 4. 检查 sender ID 是否在 AstrBot 全局 admins_id 白名单中
    try:
        user_id = extract_sender_id(event)
        if user_id:
            ctx = getattr(event, "context", None)
            if ctx and hasattr(ctx, "get_config"):
                g_cfg = ctx.get_config()
                if isinstance(g_cfg, dict):
                    admins = g_cfg.get("admins_id") or []
                    if isinstance(admins, list) and user_id in [str(x).strip() for x in admins]:
                        return True
    except Exception:  # noqa: BLE001
        pass

    return False
