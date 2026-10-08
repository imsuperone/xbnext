# -*- coding: utf-8 -*-
"""Token 用量展示（P18 · 指定会话、默认关）。

回复末尾追加一行本轮 token 用量（输入 / 输出 / 缓存），让用户看见
每轮对话的真实成本。**默认关闭**，且只有 ``token_usage_umos`` 名单
里的会话才会显示 —— 总开关 + 白名单双闸。

链路（核心钩子 + extras 中转，照抄 ``astr_agent_hooks`` 的官方姿势）：

1. ``on_llm_response``（``main.py`` 转发）取 usage 暂存进
   ``event.set_extra``（取数、格式在 :mod:`.service`）；
2. ``on_decorating_result``（输出面清洗之后、xbimg 之前）读走即焚：
   - 普通回复：往 ``result.chain`` 末尾追加独立 ``Plain``；
   - 流式收尾（``STREAMING_FINISH``）：改链没人发 ⇒ 单独
     ``event.send`` 一条（核心 respond 对流式直接 return，改链无效）。
"""

from __future__ import annotations

from typing import Any

from ...eventids import umo_of
from . import service
from ..base import Feature


class TokenLineFeature(Feature):
    """见模块 docstring。"""

    key = "enable_token_usage"
    name = "Token 用量展示"
    description = (
        "回复末尾追加本轮 输入/输出/缓存 token 用量；仅对名单内的"
        "会话显示，默认关闭"
    )
    #: 收数在 LLM 响应、出数在结果装饰，都不在 on_llm_request 主链
    order = 95
    uses_llm_response_hook = True
    uses_decorating_hook = True
    #: 单测注入的 Plain 实现；``None`` = 用 astrbot 真身（惰性导入）
    _plain_cls: Any = None

    async def on_llm_response(self, ctx: Any, resp: Any) -> None:
        event = ctx.event
        umo = umo_of(event)
        try:
            whitelist = ctx.conf.text("token_usage_umos")
        except Exception:  # noqa: BLE001
            return
        if not service.in_whitelist(whitelist, umo):
            return
        picked = service.pick_usage(event, resp)
        if picked is None:
            return
        kind, vals = picked
        prev = event.get_extra(service.EXTRA_KEY)
        event.set_extra(service.EXTRA_KEY, service.merge_extra(prev, kind, vals))

    async def on_decorating_result(self, ctx: Any) -> None:
        event = ctx.event
        data = event.get_extra(service.EXTRA_KEY)
        if not isinstance(data, dict):
            return
        # 先读后焚 —— 装饰钩子在洋葱模型里可能被多次触发，防重复追加
        event.set_extra(service.EXTRA_KEY, None)
        line = service.format_line(data)
        if not line:
            return
        try:
            result = event.get_result()
        except Exception:  # noqa: BLE001
            return
        if result is None:
            return
        ctype = getattr(result, "result_content_type", None)
        if getattr(ctype, "name", None) == "STREAMING_FINISH":
            # 流式：正文已发完，改链没人看 —— 单独补发一条
            try:
                await event.send(event.plain_result(line))
            except Exception:  # noqa: BLE001
                pass
            return
        chain = getattr(result, "chain", None)
        if not chain:
            return
        try:
            chain.append(self._make_plain("\n────\n" + line))
        except Exception:  # noqa: BLE001
            pass

    def _make_plain(self, text: str) -> Any:
        """造一个 ``Plain``；单测可直接注入 ``_plain_cls``。"""
        cls = self._plain_cls
        if cls is None:
            from astrbot.core.message.components import Plain  # noqa: PLC0415

            cls = Plain
        return cls(text)


__all__ = ["TokenLineFeature"]
