# -*- coding: utf-8 -*-
"""R1 · 回复指向与身份归属（enable_reply_attribution，默认**关**）。

问题：A 先问 bot，之后 bot 把 A 的话套到 B 头上。
根因：会话历史里 user 消息不带发言人标记，群聊上下文块也只按
``[昵称/时间]`` 平铺；bot 回复"回给谁"没有落库索引。

两条腿（详见 ``aidoc/01-需求与根因.md`` §R1）：

1. **落库**：``after_message_sent`` 记下"这条回复回给谁"；
2. **注入**：下一轮 ``on_llm_request`` 给出三方区分说明
   （由 AI 补充、可靠性低于原消息的显式中文标注）。

当前骨架只完成存储与开关接线，注入文案在 P4 落地。
"""

from __future__ import annotations

from typing import Any, Optional

from ..base import Feature
from .store import DEFAULT_LIMIT, ReplyTargetStore


class AttributionFeature(Feature):
    """给 bot 的回复建立"回给谁"索引并在下一轮说明。"""

    key = "enable_reply_attribution"
    name = "回复指向索引"
    description = "记录 bot 每条回复回给谁，避免把 A 问过的话套到 B 头上。"
    order = 40
    uses_sent_hook = True

    def __init__(self) -> None:
        self._store: Optional[ReplyTargetStore] = None

    # -- 生命周期 -----------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        limit = runtime.conf.int("reply_history_limit", DEFAULT_LIMIT)
        self._store = ReplyTargetStore(runtime.kv, limit=limit, logger=runtime.log)

    def on_unload(self) -> None:
        self._store = None

    # -- 钩子 ---------------------------------------------------------
    async def on_message_sent(self, ctx) -> None:
        """bot 回复已发出 → 落库。P4 接上真实的"回给谁"判定。"""
        if self._store is None:
            return
        item = self._build_item(ctx)
        if not item:
            return
        umo = self._umo(ctx)
        await self._store.push(umo, item)
        ctx.note(f"reply_attribution 记录 {item}")

    def on_llm_request(self, ctx) -> None:
        """注入三方区分说明。P4 落地（需先确定文案与回溯轮数）。"""
        if self._store is None:
            return
        # TODO(P4): 读取本轮上下文里被引用/被 @ 的目标，
        #  用 store.find() 命中后生成中文说明并 ctx.inject()。
        depth = ctx.conf.int("reply_scope_depth", 3)
        if depth <= 0:
            return
        ctx.note("reply_attribution 注入待 P4 落地")

    # -- 工具 ---------------------------------------------------------
    @staticmethod
    def _umo(ctx) -> str:
        try:
            umo = getattr(ctx.event, "get_session_id", None)
            value = umo() if callable(umo) else ""
            return str(value or "default")
        except Exception:  # noqa: BLE001
            return "default"

    @staticmethod
    def _build_item(ctx) -> Optional[dict]:
        """从事件里压出最小记录；拿不到就返回 ``None``（不猜）。"""
        # TODO(P4): 解析 event.unified_msg_origin / 被回复者 / 消息 id
        return None


__all__ = ["AttributionFeature"]
