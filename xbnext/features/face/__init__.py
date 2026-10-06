# -*- coding: utf-8 -*-
"""R3 · QQ 表情翻译（enable_face_translate，默认开）。

根因：aiocqhttp 适配器把 ``face`` 排除在 ``message_str`` 外、``mface``
直接丢弃，而 ``req.prompt = event.message_str`` ⇒ 模型收不到任何表情。

落地（P2）：

1. 从消息链（``event.message`` / ``event.message_obj.message`` /
   ``event.get_messages()``）抽 ``Face`` / ``Mface`` / ``Rps`` / ``Dice``；
2. 从 ``event.message_obj.raw_message``（OneBot 原始段）补捞 ``mface`` ——
   它根本没进消息链，只能从原始载荷拿 ``summary``（表情中文名）；
3. 结果通过 ``ctx.inject()`` 以 temp part 追加（不写历史）；
4. ``data.py`` 的权威表由 ``aidoc/tools/gen_face_table.py`` 从上游抓取生成。

**取名优先级**：段自带 ``summary`` > 内置 ID 表 > ``[表情:ID123]`` /
``[表情:key...]`` —— 永远兜底，**绝不静默丢弃**（静默丢弃就是现状 bug）。
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..base import Feature
from . import service

#: 非消息段对象（例如 dict 的类名）走 dict 分支
_DICT_TYPES = ("dict", "dictproxy", "MappingProxyType", "mappingproxy")
_MFACE_TYPES = ("mface", "market_face", "marketface")


def _seg_type_name(seg: Any) -> str:
    """取段的类型名（小写；对象用类名，dict 用 ``type`` 字段）。"""
    if isinstance(seg, dict) or type(seg).__name__ in _DICT_TYPES:
        return str(seg.get("type") or "").lower()
    return type(seg).__name__.lower()


def _seg_data(seg: Any) -> Any:
    """取段的 ``data`` 字典；对象没有 ``data`` 时返回 ``{}``。"""
    if isinstance(seg, dict):
        return seg.get("data") or {}
    data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


def _first(mapping: Any, *keys: str) -> Any:
    """按顺序取第一个非空字段。"""
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        v = mapping.get(k)
        if v not in (None, ""):
            return v
    return None


def _token_from_obj(seg: Any) -> Optional[Tuple[str, Any]]:
    """把一个消息段对象压成 ``(kind, code)``；不相关就返回 ``None``。"""
    t = _seg_type_name(seg)
    if t == "face":
        code = _first(_seg_data(seg), "id", "face_id", "faceId")
        if code is None:
            code = getattr(seg, "id", None)
        return (service.KIND_FACE, code) if code not in (None, "") else None
    if t in _MFACE_TYPES:
        # summary（表情中文名）优先 —— mface 没有权威 key 表
        summary = _first(_seg_data(seg), "summary", "name", "emoji_name", "face_name")
        if summary is None:
            summary = _first(seg, "summary") if isinstance(seg, dict) else None
        if summary is None:
            summary = getattr(seg, "summary", None) or getattr(seg, "name", None)
        if summary and service.clean_summary(summary):
            return (service.KIND_SUMMARY, summary)
        key = _first(_seg_data(seg), "emoji_id", "key", "id")
        if key is None:
            key = getattr(seg, "emoji_id", None) or getattr(seg, "key", None)
        if key in (None, ""):
            key = getattr(seg, "id", None)
        return (service.KIND_MFACE, key) if key not in (None, "") else None
    if t in ("rps", "dice"):
        data = _seg_data(seg)
        result = _first(data, "result", "resultId", "result_id")
        if result is None:
            result = getattr(seg, "result", None)
        if result in (None, ""):
            return None
        return (service.KIND_SUMMARY, f"{t} {result}")
    return None


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
        parts = service.translate_all(tokens, fmt=fmt)
        if not parts:
            return
        # 正文里已经写出来的片段就不重复注入（例如有人把 [表情:得意] 打成文字）
        prompt = ctx.prompt()
        if prompt:
            parts = [p for p in parts if p not in prompt]
        note = service.note_from_parts(parts)
        if not note:
            return
        if ctx.inject(note):
            ctx.note(f"face_translate 注入 {len(parts)} 个表情片段")

    # -- 抽取 ---------------------------------------------------------
    def _extract_tokens(self, ctx) -> List[Tuple[str, Any]]:
        """从事件里抽出表情片段（消息链 + OneBot 原始载荷）。"""
        event = ctx.event
        if event is None:
            return []
        try:
            tokens: List[Tuple[str, Any]] = []
            seen_sources: List[int] = []
            for source in self._chain_sources(event):
                if id(source) in seen_sources:
                    continue
                seen_sources.append(id(source))
                tokens.extend(self._walk_chain(source))
            for source in self._raw_sources(event):
                if id(source) in seen_sources:
                    continue
                seen_sources.append(id(source))
                tokens.extend(service.tokens_from_raw(source))
            return tokens
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"face_translate 抽取失败：{exc!r}")
            return []

    @staticmethod
    def _chain_sources(event: Any) -> List[Any]:
        """列出可能装着消息链的容器（顺序即优先级）。"""
        out: List[Any] = []
        for attr in ("message", "message_obj"):
            try:
                v = getattr(event, attr, None)
            except Exception:  # noqa: BLE001
                v = None
            if v is None:
                continue
            if attr == "message_obj":
                inner = getattr(v, "message", None)
                if inner is not None:
                    out.append(inner)
            else:
                out.append(v)
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                v = getter()
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                out.append(v)
        return out

    @staticmethod
    def _raw_sources(event: Any) -> List[Any]:
        """列出可能装着 OneBot 原始载荷的字段。"""
        out: List[Any] = []
        msg_obj = getattr(event, "message_obj", None)
        for owner in (msg_obj, event):
            if owner is None:
                continue
            for attr in ("raw_message", "raw_msg", "origin_message"):
                try:
                    v = getattr(owner, attr, None)
                except Exception:  # noqa: BLE001
                    v = None
                if v not in (None, ""):
                    out.append(v)
        return out

    @staticmethod
    def _walk_chain(chain: Any) -> List[Tuple[str, Any]]:
        """遍历消息链，抽出 ``(kind, code)``；识别不了就跳过。"""
        if chain is None:
            return []
        tokens: List[Tuple[str, Any]] = []
        try:
            iterator = chain if hasattr(chain, "__iter__") else [chain]
            for seg in iterator:
                tok = _token_from_obj(seg)
                if tok is not None:
                    tokens.append(tok)
        except Exception:  # noqa: BLE001
            return tokens
        return tokens


__all__ = ["FaceFeature"]
