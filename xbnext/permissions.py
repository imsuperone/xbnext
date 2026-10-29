# -*- coding: utf-8 -*-
"""事件权限判定（管理员 / 超管）。

`botguard` 与 `updater` 都需要判断「这条消息是不是管理员发的」，以前各写一份
（逻辑完全相同、兜底却会漂移）。本模块只管权限；发言人 ID 取数统一走
:func:`xbnext.eventids.uid_of`（全仓唯一实现）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .eventids import uid_of

__all__ = ["is_admin_event"]


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
        user_id = uid_of(event)
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
