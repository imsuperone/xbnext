# -*- coding: utf-8 -*-
"""历史图片瘦身（纯逻辑）。

对话历史 ``req.contexts``（OpenAI 消息格式）里，旧消息携带的图片是
base64 data URL —— 每轮都原样重发，输入 token 极度浪费。本模块把
**除最近 ``keep`` 条之外**的历史消息里的图片成分换成文字占位：
模型知道「这里曾有一张图」，但不再为它付 token。

纯函数、不 import astrbot —— 单测直接跑。
"""

from __future__ import annotations

from typing import Any

#: 保留原图的消息条数（末尾 N 条；最近对话的图片继续让模型看）
KEEP_RECENT = 6

#: 占位文案（发给模型的替身）
PLACEHOLDER = "[历史图片已省略（省 token 瘦身）]"

#: 认得出的图片成分类型（OpenAI / Responses / Anthropic 历史格式）
IMAGE_TYPES = ("image_url", "image", "input_image")


def slim_contexts(contexts: Any, keep: int = KEEP_RECENT) -> int:
    """把 ``contexts`` 里过旧消息的图片成分**原地**换成占位文本。

    返回替换掉的图片成分条数（``0`` = 没动）。结构不认识就跳过，
    **绝不抛异常** —— 这是请求发出前的最后一道改动，炸了会拖垮整轮对话。
    """
    if not isinstance(contexts, list) or len(contexts) <= keep:
        return 0
    replaced = 0
    for msg in contexts[:-keep]:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for idx, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") in IMAGE_TYPES:
                content[idx] = {"type": "text", "text": PLACEHOLDER}
                replaced += 1
    return replaced


__all__ = ["KEEP_RECENT", "PLACEHOLDER", "IMAGE_TYPES", "slim_contexts"]
