# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗（enable_quote_clean，默认开）。

根因：core 往引用链塞 ``[Empty Text]`` / ``[Image unavailable]`` 占位符，
失败图片还残留在 ``req.image_urls``，模型据此臆想出空白图片；引用块与
附件标记（``[File Attachment in quoted message: ...]``）则 append 进
``req.extra_user_content_parts`` —— 单引用+@无正文时那是模型唯一的
"内容"，同样必须清洗（真机第六轮）。

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
        """清洗 ``req.prompt`` 占位符、剔除死路径、清洗 core 的引用内容块。

        顺序刻意如此：**先判定死路径，再改写正文/内容块** —— 只有确实发现
        死路径，才把裸 ``[Image]`` 一并降级成"已失效"说明。
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

        # 2) 正文占位符（prompt 可能为空 —— 单引用+@无正文时必须继续清内容块）
        prompt = ctx.prompt()
        if prompt:
            try:
                cleaned, stats = service.clean_prompt(
                    prompt, action=action, degrade_bare=bool(dead)
                )
            except Exception as exc:  # noqa: BLE001
                ctx.note(f"quote_clean 失败：{exc!r}")
            else:
                if any(stats.values()):
                    extra = (
                        "，裸[Image]已降级" if dead and stats.get("bare_image") else ""
                    )
                    ctx.note(
                        f"quote_clean {service.report(stats)} action={action}{extra}"
                    )
                if cleaned != prompt:
                    ctx.set_prompt(cleaned)

        # 3) core 注入的引用块 / 附件标记住在 extra_user_content_parts，
        #    不在 prompt 里 —— 单引用+@无正文时它们是模型唯一的"内容"，
        #    不清洗模型就盯着图/附件说事（真机第六轮反馈的根因）
        parts = getattr(ctx.req, "extra_user_content_parts", None)
        if isinstance(parts, list) and parts:
            try:
                changed, removed = service.clean_parts(
                    parts, action=action, degrade_bare=bool(dead)
                )
            except Exception as exc:  # noqa: BLE001
                ctx.note(f"quote_clean 内容块清洗失败：{exc!r}")
            else:
                if changed or removed:
                    ctx.parts_cleaned += changed + removed
                    ctx.note(
                        f"quote_clean 内容块 改写{changed}、移除{removed}"
                    )


__all__ = ["QuoteFeature"]
