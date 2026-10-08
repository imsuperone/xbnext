# -*- coding: utf-8 -*-
"""消息段读取的唯一实现（attribution 与 face 共用）。

消息段有两种形态：适配器给的 ``dict``（``{"type": ..., "data": ...}``）
和核心消息链里的段对象（用类名当类型、``.data`` 当数据）。以前
attribution 和 face 各抄了一份同款 helper（``_seg_data`` 逐字相同），
收口到本模块（aidoc/02 §10.2-2）。

- :func:`seg_type` —— 段类型名（小写）；
- :func:`seg_data` —— 段的 ``data`` 字典；
- :func:`field` —— 段里按顺序取第一个非空字段（先 ``data`` 后对象属性）；
- :func:`first` —— 字典里按顺序取第一个非空键值。
"""

from __future__ import annotations

from typing import Any

__all__ = ["seg_type", "seg_data", "field", "first"]

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
