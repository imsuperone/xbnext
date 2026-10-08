# -*- coding: utf-8 -*-
"""Token 用量展示（纯逻辑）。

三件事：

1. **取数** :func:`pick_usage` —— 优先读核心 agent runner 的整轮累计
   （``_ACTIVE_AGENT_RUNNERS``，含工具循环的多次调用；私有表，惰性
   import、拿不到就回落），再回落到钩子实参 ``resp.usage``；
   两条路都要求 provider 真回报了 usage，**全 0 不显示**；
2. **暂存** —— 直接用核心原生的 ``event.get_extra`` /
   ``event.set_extra``（``on_llm_response`` 写入，``on_decorating_result``
   读走即焚）；
3. **格式与名单** :func:`format_line` / :func:`parse_umos` /
   :func:`in_whitelist`。

除惰性 import 外不碰 astrbot —— 单测直接跑。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ...eventids import umo_of

#: event extras 里暂存本轮用量的键（发完即焚）
EXTRA_KEY = "_xb_token_usage"

#: UMO 名单分隔符：中文/英文逗号、顿号、分号（换行走 str.splitlines）
_SPLIT_MARKERS = ("，", ",", "、", ";", "；")

#: 黏连切开：单行输入框赋值会剥掉换行，名单黏成
#: ``...GroupMessage:753700701default:GroupMessage:...`` 一坨。在「**数字
#: 结尾 + 新 UMO 开头**」处补回换行。锚定前面的数字是关键——光用
#: lookahead 会在 ``efault:`` 这类平台名中间撕开。
_GLUE_RE = re.compile(r"(\d)(?=[A-Za-z_][A-Za-z0-9_\-]*:[A-Za-z0-9_\-]*Message:)")


def parse_umos(text: Any) -> List[str]:
    """把配置里的会话名单文本拆成列表（逗号 / 顿号 / 分号 / 换行）。

    自救两步（D4 根因：单行 input 赋值剥换行 → 名单黏成一坨）：

    1. 按 :data:`_GLUE_RE` 把黏连处补回换行，切开成一条条 UMO；
    2. **保序去重** —— 群里存过一次、WebUI 又存过一次的重复只留第一条。
    """
    if isinstance(text, (list, tuple)):
        raw = "\n".join(str(item) for item in text)
    elif isinstance(text, str) and text.strip():
        raw = text
    else:
        return []
    for marker in _SPLIT_MARKERS:
        raw = raw.replace(marker, "\n")
    raw = _GLUE_RE.sub(r"\1\n", raw)
    seen = set()
    out: List[str] = []
    for chunk in raw.splitlines():
        chunk = chunk.strip()
        if chunk and chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return out


def normalize_umos(text: Any) -> str:
    """写回配置前洗一遍：切开黏连 + 保序去重，换行拼接（存侧归一化）。"""
    return "\n".join(parse_umos(text))


def in_whitelist(text: Any, umo: Any) -> bool:
    """``umo`` 是否在名单里（名单空 / umo 空 ⇒ ``False``，纯白名单语义）。"""
    if not umo:
        return False
    return str(umo) in parse_umos(text)


def extract_usage(obj: Any) -> Optional[Dict[str, int]]:
    """从 ``TokenUsage`` 形状的对象取三列；空对象 / 全 0 ⇒ ``None``。"""
    if obj is None:
        return None

    def _num(name: str) -> int:
        try:
            return int(getattr(obj, name, 0) or 0)
        except Exception:  # noqa: BLE001
            return 0

    try:  # ``.input`` 是属性（= input_other + input_cached）
        total_input = int(obj.input or 0)
    except Exception:  # noqa: BLE001
        total_input = _num("input_other") + _num("input_cached")
    vals = {
        "input": total_input,
        "cached": _num("input_cached"),
        "output": _num("output"),
    }
    if vals["input"] or vals["cached"] or vals["output"]:
        return vals
    return None


def _runner_usage(event: Any) -> Optional[Dict[str, int]]:
    """核心 agent runner 的整轮累计用量；私有表，任何失败返回 ``None``。"""
    try:
        from astrbot.core.pipeline.process_stage.follow_up import (  # noqa: PLC0415
            _ACTIVE_AGENT_RUNNERS,
        )

        umo = umo_of(event)
        if not umo:
            return None
        runner = _ACTIVE_AGENT_RUNNERS.get(umo)
        stats = getattr(runner, "stats", None)
        return extract_usage(getattr(stats, "token_usage", None))
    except Exception:  # noqa: BLE001  单测环境没有 astrbot，走回落
        return None


def pick_usage(event: Any, resp: Any) -> Optional[Tuple[str, Dict[str, int]]]:
    """选本轮展示用的用量：``(kind, vals)``；``kind`` 决定叠加策略。

    - ``"stats"`` —— runner 整轮累计（已含所有工具循环调用），直接覆盖；
    - ``"sum"``   —— 单次调用 ``resp.usage``，同轮多次到达时累加。

    provider 没回报 usage / 三列全 0 ⇒ ``None``（不显示 0/0/0 误导人）。
    """
    vals = _runner_usage(event)
    if vals:
        return "stats", vals
    vals = extract_usage(getattr(resp, "usage", None))
    if vals:
        return "sum", vals
    return None


def merge_extra(prev: Any, kind: str, vals: Dict[str, int]) -> Dict[str, int]:
    """把新用量并进已暂存的：``stats`` 覆盖，``sum`` 累加。"""
    if isinstance(prev, dict) and prev.get("kind") == kind == "sum":
        merged: Dict[str, int] = {"kind": "sum"}
        for name in ("input", "cached", "output"):
            try:
                merged[name] = int(prev.get(name, 0) or 0) + int(vals.get(name, 0) or 0)
            except Exception:  # noqa: BLE001
                merged[name] = int(vals.get(name, 0) or 0)
        return merged
    return {"kind": kind, **vals}


def format_line(vals: Dict[str, int]) -> str:
    """拼展示行：``📊 本轮 token：输入 1,234 · 输出 567 · 缓存 800``。"""
    try:
        inp = int(vals.get("input", 0) or 0)
        out = int(vals.get("output", 0) or 0)
        cached = int(vals.get("cached", 0) or 0)
    except Exception:  # noqa: BLE001
        return ""
    if not (inp or out or cached):
        return ""
    return f"📊 本轮 token：输入 {inp:,} · 输出 {out:,} · 缓存 {cached:,}"


__all__ = [
    "EXTRA_KEY",
    "parse_umos",
    "normalize_umos",
    "in_whitelist",
    "extract_usage",
    "pick_usage",
    "merge_extra",
    "format_line",
]
