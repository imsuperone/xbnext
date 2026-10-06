# -*- coding: utf-8 -*-
"""R3 · QQ 表情翻译（enable_face_translate，默认开）。

根因：aiocqhttp 适配器把 ``face`` 排除在 ``message_str`` 外、``mface``
直接丢弃，而 ``req.prompt = event.message_str`` ⇒ 模型收不到任何表情。
AstrNa 全仓 0 处表情处理，这是 XBNEXT 的独立增量。

**P2 落地清单**（当前骨架只打通链路）：

1. 从 ``event.message_iter()`` / ``event.get_messages()`` 里抽出
   ``Face`` / ``mface`` 片段 → ``[(kind, code), ...]``；
2. 交给 :mod:`xbnext.features.face.service` 翻译；
3. 结果通过 ``ctx.inject()`` 以 temp part 追加（不写历史）；
4. 补全 ``data.py`` 的权威表情表。
"""

from __future__ import annotations

from ..base import Feature
from . import service


class FaceFeature(Feature):
    """把 QQ 自带表情翻译成模型读得懂的方括号短语。"""

    key = "enable_face_translate"
    name = "QQ 表情翻译"
    description = "把 face / mface 翻译成 [表情:得意] 类文字描述追加进本轮请求。"
    order = 30

    def on_llm_request(self, ctx) -> None:
        tokens = self._extract_tokens(ctx)
        if not tokens:
            return
        fmt = ctx.conf.text("face_format", service.DEFAULT_FORMAT)
        note = service.build_note(tokens, fmt=fmt)
        if not note:
            return
        if ctx.inject(note):
            ctx.note(f"face_translate 注入 {len(tokens)} 个表情")

    # ------------------------------------------------------------------
    def _extract_tokens(self, ctx) -> list:
        """从事件消息链里抽出表情片段。

        骨架阶段返回空列表；P2 接上真实的消息链遍历（astrbot 懒导入，
        保证本模块在裸 Python 环境可 import）。
        """
        event = ctx.event
        if event is None:
            return []
        try:
            return self._walk_chain(getattr(event, "message", None))
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"face_translate 抽取失败：{exc!r}")
            return []

    @staticmethod
    def _walk_chain(chain) -> list:
        """遍历消息链，抽出 ``(kind, code)``；识别不了就返回空。"""
        if chain is None:
            return []
        tokens: list = []
        try:
            iterator = chain if hasattr(chain, "__iter__") else [chain]
            for seg in iterator:
                seg_type = type(seg).__name__
                if seg_type == "Face":
                    code = getattr(seg, "id", None)
                    if code is not None:
                        tokens.append((service.KIND_FACE, code))
                elif seg_type in ("Mface", "MarketFace"):
                    key = getattr(seg, "emoji_id", None) or getattr(seg, "id", None)
                    if key is not None:
                        tokens.append((service.KIND_MFACE, key))
        except Exception:  # noqa: BLE001
            return tokens
        return tokens


__all__ = ["FaceFeature"]
