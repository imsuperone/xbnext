# -*- coding: utf-8 -*-
"""R1 · 回复指向说明 —— 纯逻辑部分。

本文件**不 import astrbot**：输入是普通字符串 / 字典 / 列表，输出是字符串。

三方角色混淆是 R1 的核心（aidoc/01 §1.1）：

- **当前发言人**（本轮说话的人）
- **被引用消息的发送者**（被回复的人，可能根本没在说话）
- **bot 最近若干次回复的对象**（历史话题发起人）

三者常常不是同一个人。本模块把它们拼成一段**显式的中文说明**注入给模型，
并明说"这是提示、不是用户发言"，避免把提示本身当成某人说的话。

**红线**：身份字段一律过 :func:`xbnext.injector.sanitize`（aidoc/03）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...injector import sanitize

#: 一个人 = ``(id, 昵称)``，两者都可能为空，至少要有一个
Person = Tuple[Any, Any]

#: 昵称 / ID 的截断长度
MAX_NAME = 32

#: 说明开头：明确告诉模型这不是用户说的话
TITLE = "【回复指向说明 —— 这是给模型的提示，不是任何人的发言】"

#: 结尾的区分规则
TAIL = (
    "注意：以上是不同的人。你之前的回复是说给上面列表里的人的，"
    "那些话不是当前发言人说的；当前发言人只说了本轮消息里的内容。"
    "不要把任何一方的立场、问题或结论安到另一方头上。"
)


def rel_time(ts: Any, now: Any) -> str:
    """把时间戳压成「刚刚 / N 分钟前 / N 小时前 / N 天前」。"""
    try:
        ts = int(ts or 0)
        now = int(now or 0)
    except Exception:  # noqa: BLE001
        return ""
    if ts <= 0 or now <= 0:
        return ""
    delta = max(0, now - ts)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    if delta < 86400:
        return f"{delta // 3600} 小时前"
    return f"{delta // 86400} 天前"


def who(person: Any) -> str:
    """把 ``(id, 昵称)`` 渲染成「昵称（ID 123）」；都取不到就返回「未知」。"""
    if not isinstance(person, (list, tuple)) or not person:
        return "未知"
    raw_id = person[0] if len(person) > 0 else ""
    raw_name = person[1] if len(person) > 1 else ""
    name = sanitize(raw_name, MAX_NAME)
    pid = sanitize("" if raw_id is None else str(raw_id), MAX_NAME)
    if name and pid:
        return f"{name}（ID {pid}）"
    if name:
        return name
    if pid:
        return f"ID {pid}"
    return "未知"


def history_lines(
    history: Iterable[Any], depth: int, now: Any
) -> List[str]:
    """把 store 里的记录渲染成编号列表（新的在前）。"""
    try:
        depth = int(depth or 0)
    except Exception:  # noqa: BLE001
        depth = 0
    if depth <= 0:
        return []
    out: List[str] = []
    for item in list(history or [])[:depth]:
        if not isinstance(item, dict):
            continue
        stamp = rel_time(item.get("ts"), now)
        suffix = f" · {stamp}" if stamp else ""
        out.append(f"  {len(out) + 1}. {who((item.get('target_id'), item.get('target_name')))}{suffix}")
    return out


def build_hint(
    current: Any = None,
    quoted: Any = None,
    ats: Optional[Sequence[Any]] = None,
    history: Iterable[Any] = (),
    depth: int = 3,
    now: Any = 0,
    matched: Optional[Dict[str, Any]] = None,
) -> str:
    """拼出注入用的三方说明；**没有需要区分的信息时返回 ``""``**。

    :param current: 当前发言人 ``(id, 昵称)``
    :param quoted: 本轮引用消息的发送者；``None`` 表示拿不到（不猜）
    :param ats: 本轮被 @ 的对象列表
    :param history: store 里"bot 最近回复给谁"的记录（新的在前）
    :param depth: 最多回溯几条
    :param now: 当前时间戳，用于「N 分钟前」
    :param matched: 当前引用的消息恰好命中 store 记录时的那条
    """
    at_list = [a for a in (ats or []) if isinstance(a, (list, tuple))]
    hist_lines = history_lines(history, depth, now)
    quoted_known = isinstance(quoted, (list, tuple)) and bool(
        quoted and (quoted[0] or quoted[1])
    )
    # 只有"当前发言人"时没必要注入 —— AstrBot 自带 identifier 已经写了这一行
    if not (quoted_known or at_list or hist_lines or matched):
        return ""

    lines: List[str] = [TITLE]
    current_known = isinstance(current, (list, tuple)) and bool(
        current and (current[0] or current[1])
    )
    if current_known:
        lines.append(f"- 当前发言人：{who(current)}")
    if quoted_known:
        lines.append(f"- 本轮被引用消息的发送者：{who(quoted)}")
    if at_list:
        lines.append(f"- 本轮被 @ 的对象：{'、'.join(who(a) for a in at_list)}")
    if matched:
        lines.append(
            f"- 你引用的这条消息，你当时回应过它 → 当时的回应对象："
            f"{who((matched.get('target_id'), matched.get('target_name')))}"
        )
    if hist_lines:
        lines.append(f"- 你最近 {len(hist_lines)} 次回复的对象（新的在前）：")
        lines.extend(hist_lines)
    lines.append(TAIL)
    return "\n".join(lines)


__all__ = [
    "Person",
    "MAX_NAME",
    "TITLE",
    "TAIL",
    "rel_time",
    "who",
    "history_lines",
    "build_hint",
]
