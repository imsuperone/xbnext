# -*- coding: utf-8 -*-
"""历史图片瘦身（P18 · 省 token）。

对话历史里的旧图片每轮都以 base64 原样重发 —— 一张截图动辄几千
token，而且越老的图对模型越没用。本功能在 ``on_llm_request`` 里把
**最近 ``keep`` 条之外**的历史消息中的图片成分换成文字占位：

- 当轮请求立刻变小（输入 token 直降）；
- core 落历史用的是本轮已瘦身的 ``run_context.messages``，下一轮
  读到的历史天然已是占位 —— **不改 core 源码、不打补丁**，
  瘦身结果随核心自身的持久化自动保持；
- 当前用户刚发的图片在 ``req.image_urls``（不在历史里），不受影响；
- 最近 6 条消息的原图保留，模型还能看见刚聊过的图。
"""

from __future__ import annotations

from typing import Any

from . import service
from ..base import Feature


class ImageSlimFeature(Feature):
    """见模块 docstring。"""

    key = "enable_image_slim"
    name = "历史图片瘦身"
    description = (
        "把对话历史里较旧消息的图片换成文字占位，大幅减少输入 token；"
        "最近 6 条消息的原图保留，当轮刚发的图不受影响"
    )
    #: 清洗类，排最前（在引用清洗之前动历史，互不干扰）
    order = 15

    async def on_llm_request(self, ctx: Any) -> None:
        req = getattr(ctx, "req", None)
        if req is None:
            return
        replaced = service.slim_contexts(getattr(req, "contexts", None))
        if not replaced:
            return
        try:
            ctx.slimmed_images = getattr(ctx, "slimmed_images", 0) + replaced
        except Exception:  # noqa: BLE001  计数只为日志汇总，失败不影响功能
            pass
        ctx.note(f"历史图片瘦身：{replaced} 张旧图换占位")


__all__ = ["ImageSlimFeature"]
