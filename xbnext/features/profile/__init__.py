# -*- coding: utf-8 -*-
"""R4 · 用户档案注入（enable_user_profile，默认**关**）。

问题：用户主动自定义对自己的设定，bot 却读不到，只能乱猜。
根因：AstrBot 内置 ``requirements`` / ``profile`` 字段**不进 LLM**；
唯一生效的 ``persona`` 全局唯一、每次群聊都注入所有人。

本功能给用户一条自助链路：

- 存：插件 KV ``xbnext:profile:<platform>:<uid>``（用户自己写）；
- 读：每轮按当前发言人取出，sanitize 后以 **temp part** 注入（不写历史）；
- 入口：``/xbnext profile``（P5）+ WebUI（P6）。

注入永远排在最后（order=50），保证不会被本插件自己的清洗删掉。
"""

from __future__ import annotations

from typing import Any, Optional

from ..base import Feature
from .store import ProfileStore, render


class ProfileFeature(Feature):
    """按当前发言人注入其自助档案。"""

    key = "enable_user_profile"
    name = "用户档案"
    description = "读取用户自助维护的档案并注入，让 bot 按档案理解人。"
    order = 50

    def __init__(self) -> None:
        self._store: Optional[ProfileStore] = None

    # -- 生命周期 -----------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        self._store = ProfileStore(runtime.kv, logger=runtime.log)

    def on_unload(self) -> None:
        self._store = None

    @property
    def store(self) -> Optional[ProfileStore]:
        """暴露存储给指令层（P5 的 ``/xbnext profile`` 用）。"""
        return self._store

    # -- 钩子 ---------------------------------------------------------
    def on_llm_request(self, ctx) -> None:
        """取出当前发言人的档案，清洗后注入。"""
        if self._store is None:
            return
        platform, uid = self._speaker(ctx)
        if not uid:
            return
        # KV 读是协程，这里走同步调度由 runtime 统一 await ——
        # 骨架阶段用惰性注入，P5 改成 async on_llm_request 直读。
        ctx.note(f"user_profile 待读取 {platform}/{uid}")

    # -- 工具 ---------------------------------------------------------
    @staticmethod
    def _speaker(ctx) -> tuple:
        """返回 ``(platform, uid)``；取不到返回 ``("", "")``（不猜）。"""
        try:
            event = ctx.event
            if event is None:
                return "", ""
            sender = getattr(event, "get_sender_id", None)
            uid = str(sender()) if callable(sender) else ""
            get_platform = getattr(event, "get_platform_name", None)
            platform = str(get_platform()) if callable(get_platform) else ""
            if not uid:
                get_uid = getattr(event, "get_user_id", None)
                uid = str(get_uid()) if callable(get_uid) else ""
            return platform, uid
        except Exception:  # noqa: BLE001
            return "", ""


__all__ = ["ProfileFeature"]
