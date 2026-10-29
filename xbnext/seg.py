# -*- coding: utf-8 -*-
"""消息段读取的唯一实现（attribution 与 face 共用）。

消息段有两种形态：适配器给的 ``dict``（``{"type": ..., "data": ...}``）
和核心消息链里的段对象（用类名当类型、``.data`` 当数据）。以前
attribution 和 face 各抄了一份同款 helper（``_seg_data`` 逐字相同），
消息链 / 原始载荷的扫描也各有两份 —— 收口到本模块。

- :func:`seg_type` —— 段类型名（小写）；
- :func:`seg_data` —— 段的 ``data`` 字典；
- :func:`field` —— 段里按顺序取第一个非空字段（先 ``data`` 后对象属性）；
- :func:`first` —— 字典里按顺序取第一个非空键值。
"""

from __future__ import annotations

from typing import Any, List

__all__ = ["seg_type", "seg_data", "field", "first", "chain_sources", "raw_sources"]

#: 带 ``.get`` 的 dict 形段（类名走 dict 分支）
_DICT_TYPES = ("dict", "dictproxy", "MappingProxyType", "mappingproxy")


def seg_type(seg: Any) -> str:
    """段类型名（小写；dict 形用 ``type`` 字段，对象用类名）。"""
    if isinstance(seg, dict) or type(seg).__name__ in _DICT_TYPES:
        return str(seg.get("type") or "").lower()
    return type(seg).__name__.lower()


def seg_data(seg: Any) -> Any:
    """取段的 ``data`` 字典；对象没有 ``data`` 时返回 ``{}``。"""
    if isinstance(seg, dict):
        return seg.get("data") or {}
    data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


def field(seg: Any, *names: str) -> Any:
    """从段里按顺序取第一个非空字段（先看 ``data``，再看对象属性）。"""
    data = seg_data(seg)
    for n in names:
        if isinstance(data, dict) and data.get(n) not in (None, ""):
            return data.get(n)
    for n in names:
        v = getattr(seg, n, None)
        if v not in (None, ""):
            return v
    return None


def first(mapping: Any, *keys: str) -> Any:
    """字典里按顺序取第一个非空键值。"""
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        v = mapping.get(k)
        if v not in (None, ""):
            return v
    return None


def chain_sources(event: Any) -> List[Any]:
    """列出可能装着消息链的容器（顺序即优先级，可能重复）。

    覆盖 ``event.message`` / ``event.message_obj.message`` / ``get_messages()``
    三个来源；face 表情抽取与 attribution 引用识别共用。
    """
    out: List[Any] = []
    for v in (getattr(event, "message", None),
              getattr(getattr(event, "message_obj", None), "message", None)):
        if v is not None:
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


def raw_sources(event: Any) -> List[Any]:
    """列出可能装着 OneBot 原始载荷的字段（``message_obj`` 优先于 event）。"""
    out: List[Any] = []
    for owner in (getattr(event, "message_obj", None), event):
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
