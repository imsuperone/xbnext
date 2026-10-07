# -*- coding: utf-8 -*-
"""bot 回复登记表（P17 · 撤回二期：连带撤回已发回复）。

发送接管（``__init__._install_capture``）在发送开始时 :func:`begin`
占位、发送成功后 :func:`record` 记账；撤回 notice 到达时 :func:`take`
按状态取 id：

- 发送已完成 → 交出 id 列表，调用方立即 ``delete_msg``；
- 发送还在飞 → 打补删标记（返回 ``sending=True``），发送完成后由
  :func:`record` 的返回值驱动接管方自己删（连同此前分段回复的 id
  一并取走）；
- 发送被取消 → :func:`abandon` 摘位；若已被标记补删则交出已记的 id。

**纯 dict 操作、不 import astrbot**：全部在事件循环里同步完成
（单键读写无 ``await``，天然原子，无需加锁）。登记表有界：
``MAX_ENTRIES`` 挤最老 + ``TTL_SECONDS`` 兜底，防长跑膨胀。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

__all__ = [
    "MAX_ENTRIES",
    "TTL_SECONDS",
    "begin",
    "record",
    "take",
    "abandon",
    "clear",
]

#: 登记表上限（超出挤最老的一条）
MAX_ENTRIES = 200

#: 条目最长存活（秒）—— 正常几分钟内就消费，6 小时只是防泄漏兜底
TTL_SECONDS = 6 * 3600

#: ``trigger_id -> {ids, sending, pending, ts}``
_entries: Dict[str, Dict[str, Any]] = {}


def _prune(now: float) -> None:
    """按 TTL 清死条目；仍超上限则挤最老的。"""
    dead = [key for key, item in _entries.items() if now - item["ts"] > TTL_SECONDS]
    for key in dead:
        _entries.pop(key, None)
    while len(_entries) > MAX_ENTRIES:
        oldest = min(_entries, key=lambda key: _entries[key]["ts"])
        _entries.pop(oldest, None)


def begin(trigger_id: str) -> None:
    """发送开始占位（``sending=True``）。

    同 id 已有条目时**保留已记的 id**（分段回复会连续 begin/record），
    只翻 sending 位与时间戳。
    """
    if not trigger_id:
        return
    now = time.time()
    entry = _entries.get(trigger_id)
    if entry is None:
        _entries[trigger_id] = {"ids": [], "sending": True, "pending": False, "ts": now}
    else:
        entry["sending"] = True
        entry["ts"] = now
    _prune(now)


def record(trigger_id: str, reply_ids: List[str]) -> bool:
    """发送完成记账，返回 ``True`` = 已被标记补删（调用方应立即取走删除）。

    条目若已被挤掉，补建一条普通条目，保证后续 :func:`take` 仍拿得到。
    """
    if not trigger_id:
        return False
    now = time.time()
    ids = [str(item) for item in reply_ids if str(item).strip()]
    entry = _entries.get(trigger_id)
    if entry is None:
        _entries[trigger_id] = {"ids": ids, "sending": False, "pending": False, "ts": now}
        _prune(now)
        return False
    entry["sending"] = False
    entry["ids"].extend(ids)
    return bool(entry["pending"])


def take(trigger_id: str) -> Tuple[List[str], bool]:
    """撤回调用：返回 ``(可立即删的 id 列表, 是否仍有发送在飞)``。

    在飞时打补删标记、不摘表；不在飞则摘表交出全部 id（含分段回复）。
    """
    if not trigger_id:
        return [], False
    entry = _entries.get(trigger_id)
    if entry is None:
        return [], False
    if entry["sending"]:
        entry["pending"] = True
        return [], True
    _entries.pop(trigger_id, None)
    return list(entry["ids"]), False


def abandon(trigger_id: str) -> List[str]:
    """发送被取消 / 拿不到 id：摘掉 sending 位。

    返回**需要补删的 id**（仅当已被标记补删，且有已记的 id 时）；
    其余情况按需摘表 —— 有 id 留着等撤回，无 id 且无等待方直接清掉。
    """
    if not trigger_id:
        return []
    entry = _entries.get(trigger_id)
    if entry is None:
        return []
    entry["sending"] = False
    ids = list(entry["ids"])
    pending = bool(entry["pending"])
    if pending or not ids:
        _entries.pop(trigger_id, None)
    return ids if pending else []


def clear() -> None:
    """整表清空（功能卸载 / 测试隔离）。"""
    _entries.clear()
