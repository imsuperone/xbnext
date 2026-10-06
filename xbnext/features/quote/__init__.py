# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗（enable_quote_clean，默认开）。

根因：core 往引用链塞 ``[Empty Text]`` / ``[Image unavailable]`` 占位符，
失败图片还残留在 ``req.image_urls``，模型据此臆想出空白图片。

本功能**只处理可证实的失败**，不做像素级空白检测。
"""

from __future__ import annotations

from ..base import Feature
from . import service


class QuoteFeature(Feature):
    """清洗引用链里 core 留下的占位噪声与死路径。"""

    key = "enable_quote_clean"
    name = "引用占位清洗"
    description = "识别并标注引用链里失效的占位内容，杜绝模型臆想出空白图片。"
    order = 20

    def on_llm_request(self, ctx) -> None:
        """改写 ``req.prompt`` 中的占位符（``quote_placeholder_action`` 控制策略）。"""
        prompt = ctx.prompt()
        if not prompt:
            return
        action = ctx.conf.text("quote_placeholder_action", "label").strip().lower()
        if action not in ("label", "strip", "keep"):
            action = "label"
        try:
            cleaned, stats = service.clean_prompt(prompt, action=action)
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"quote_clean 失败：{exc!r}")
            return
        if any(stats.values()):
            ctx.note(f"quote_clean {service.report(stats)} action={action}")
        if cleaned != prompt:
            ctx.set_prompt(cleaned)

        # 死路径：非 URL 的残留项只记日志，不擅自删（可能是本地可读路径，
        # 真正的删除策略在 P3 结合 os.path.exists 判定后落地）
        dead = service.dead_image_urls(getattr(ctx.req, "image_urls", None))
        if dead:
            ctx.note(f"quote_clean 发现 {len(dead)} 个非 URL 图片路径")


__all__ = ["QuoteFeature"]
