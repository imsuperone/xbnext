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
        """清洗 ``req.prompt`` 占位符，并剔除 ``req.image_urls`` 里的死路径。

        顺序刻意如此：**先判定死路径，再改写正文** —— 只有确实发现死路径，
        才把正文里的裸 ``[Image]`` 一并降级成"已失效"说明。
        """
        action = ctx.conf.text("quote_placeholder_action", "label").strip().lower()
        if action not in ("label", "strip", "keep"):
            action = "label"

        # 1) 死路径：只删"能证实不存在"的，http/https/data 一律保留
        dead: list = []
        if ctx.conf.bool("quote_drop_dead_images", True):
            try:
                dead = service.dead_image_urls(getattr(ctx.req, "image_urls", None))
            except Exception as exc:  # noqa: BLE001
                ctx.note(f"quote_clean 死路径判定失败：{exc!r}")
                dead = []
        if dead:
            alive, _ = service.split_image_urls(getattr(ctx.req, "image_urls", None))
            try:
                ctx.req.image_urls = alive
                ctx.note(f"quote_clean 移除 {len(dead)} 个失效图片路径")
            except Exception as exc:  # noqa: BLE001
                ctx.note(f"quote_clean 写回 image_urls 失败：{exc!r}")

        # 2) 正文占位符
        prompt = ctx.prompt()
        if not prompt:
            return
        try:
            cleaned, stats = service.clean_prompt(
                prompt, action=action, degrade_bare=bool(dead)
            )
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"quote_clean 失败：{exc!r}")
            return
        if any(stats.values()):
            extra = "，裸[Image]已降级" if dead and stats.get("bare_image") else ""
            ctx.note(f"quote_clean {service.report(stats)} action={action}{extra}")
        if cleaned != prompt:
            ctx.set_prompt(cleaned)


__all__ = ["QuoteFeature"]
