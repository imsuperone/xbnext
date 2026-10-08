# -*- coding: utf-8 -*-
"""统一日志出口（全仓唯一实现）。

打日志只有一种写法：``log.emit(logger, level, message)`` ——
自动补 ``[XBNEXT] `` 前缀；logger 缺失 / 方法缺失 / 写失败一律
安全跳过（AstrBot 原则 4，绝不冒泡）。

以前 runtime / face / recall / 两个 store / storage 各写了一份
「getattr + callable + try」的同款壳（6 个实现点、7 种写法），
统一收口到本模块（aidoc/02 §10.2-1）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["emit"]


def emit(logger: Any, level: str, message: str) -> None:
    """按 ``level``（``debug`` / ``info`` / ``warning``）写一条日志。"""
    method = getattr(logger, level, None) if logger is not None else None
    if not callable(method):
        return
    try:
        method(f"[XBNEXT] {message}")
    except Exception:  # noqa: BLE001
        pass
