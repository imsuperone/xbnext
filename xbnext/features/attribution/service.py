# -*- coding: utf-8 -*-
"""R1 · 回复指向说明 —— 纯逻辑部分。

本文件**不 import astrbot**：输入是普通字符串 / 字典 / 列表，输出是字符串。

三方角色混淆是 R1 的核心（aidoc/01 §1.1）：

- **当前发言人**（本轮说话的人）
- **被引用消息的发送者**（被回复的人，可能根本没在说话）
- **bot 最近若干次回复的对象**（历史话题发起人）

三者常常不是同一个人。本模块把它们拼成一段**显式的中文说明**注入给模型，
并明说"这是提示、不是用户发言"，避免把提示本身当成某人说的话。

**同人 / 异人要分清**（真机第二轮反馈）：所有已知身份都指向当前发言人时
不能再写"以上是不同的人" —— 那会让模型把用户自己的历史发言推给"别人"。
分支规则见 :func:`build_hint` 的 docstring，**身份对不上/未知一律回落
:data:`TAIL`**，绝不为凑话而猜。

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

#: 异人尾注（默认 / 身份存疑时的保守回落）
TAIL = (
    "注意：以上是不同的人。你之前的回复是说给上面列表里的人的，"
    "那些话不是当前发言人说的；当前发言人只说了本轮消息里的内容。"
    "不要把任何一方的立场、问题或结论安到另一方头上。"
)

#: 同人尾注：上面列出的所有身份都指向当前发言人本人
TAIL_SAME = (
    "注意：以上都是同一个人 —— 被引用者、@ 对象与历史回应对象都是"
    "当前发言人本人，不存在不同的人。上面那些是他更早的发言（不是本轮"
    "新说的话），你之前的回复也都是说给他的；"
    "不要把他更早的发言当成别人说的，也不要当成他本轮新说的。"
)

#: 自引尾注：被引用的那条是当前发言人自己发的，但列表里还有别人
TAIL_SELF_QUOTED = (
    "注意：被引用的那条消息是当前发言人本人发的，属于同一个人；"
    "其余列出的对象才是不同的人 —— 你之前的回复是说给他们的，"
    "那些话不是当前发言人说的。不要把任何一方的立场、问题或结论安到另一方头上。"
)

#: 被引用者是 bot 自己时挂在行尾的标注（真机第八轮：引用 bot 旧回复的场景）
BOT_MARK = "（即你自己的旧回复）"

#: 引用的是 bot 自己的旧回复（真机第八轮：原来没有这个分支，bot 自己的
#: 旧消息被归进「以上是不同的人」，模型把"自己发过的内容"当成别人的话
#: 认真回应 —— 评图、聊旧话题，冷落当前发言人的新消息）
TAIL_BOT_QUOTED = (
    "注意：被引用的那条是你自己之前的回复 —— 那是你发过的内容，不是别人的话；"
    "内容你已经知道，不要再重复描述或评价它。"
    "本轮重心是当前发言人这条新消息，先回应他说的内容。"
)

#: 自引用时挂在被引用者行尾的标注
SELF_MARK = "（即当前发言人本人）"


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


def key_of(person: Any) -> str:
    """身份主键，用来判断「是不是同一个人」。

    ID 优先、退化到昵称，两者都取不到返回 ``""``（**未知，绝不猜**）。
    加 ``id:`` / ``name:`` 前缀做命名空间隔离 —— 避免某人的 QQ 号
    恰好等于另一人的昵称时被误判成同一个人（aidoc/01 §R1 保守原则）。
    """
    if not isinstance(person, (list, tuple)) or not person:
        return ""
    raw_id = person[0] if len(person) > 0 else ""
    raw_name = person[1] if len(person) > 1 else ""
    pid = sanitize("" if raw_id is None else str(raw_id), MAX_NAME)
    name = sanitize(raw_name, MAX_NAME)
    if pid:
        return f"id:{pid}"
    if name:
        return f"name:{name}"
    return ""


def history_items(history: Iterable[Any], depth: int) -> List[Dict[str, Any]]:
    """取回溯窗口内的合法记录（``dict``），新的在前；越界/坏数据直接丢。"""
    try:
        depth = int(depth or 0)
    except Exception:  # noqa: BLE001
        depth = 0
    if depth <= 0:
        return []
    return [it for it in list(history or [])[:depth] if isinstance(it, dict)]


def history_lines(
    history: Iterable[Any], depth: int, now: Any
) -> List[str]:
    """把 store 里的记录渲染成编号列表（新的在前）。"""
    out: List[str] = []
    for item in history_items(history, depth):
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
    self_id: Any = "",
) -> str:
    """拼出注入用的三方说明；**没有需要区分的信息时返回 ``""``**。

    :param current: 当前发言人 ``(id, 昵称)``
    :param quoted: 本轮引用消息的发送者；``None`` 表示拿不到（不猜）
    :param ats: 本轮被 @ 的对象列表
    :param history: store 里"bot 最近回复给谁"的记录（新的在前）
    :param depth: 最多回溯几条
    :param now: 当前时间戳，用于「N 分钟前」
    :param matched: 当前引用的消息恰好命中 store 记录时的那条
    :param self_id: bot 自己的 ID —— 用于识别「被引用的是 bot 自己的旧回复」
        （只比对 ID；拿不到就不判，绝不靠昵称猜）

    **同人 / 异人 / 自引 bot 分支**（真机第二轮 + 第八轮反馈）：

    - 被引用者是 bot 自己 ⇒ :data:`TAIL_BOT_QUOTED` + :data:`BOT_MARK`
      （优先级最高 —— 那是 bot 发过的内容，不是"别人的话"）；
    - 所有已知身份都等于当前发言人 ⇒ :data:`TAIL_SAME`；
    - 被引用者是本人、但列表里还有别人 ⇒ :data:`TAIL_SELF_QUOTED`，
      并在被引用者行尾挂 :data:`SELF_MARK`；
    - 任何一方身份未知/对不上 ⇒ 一律回落 :data:`TAIL`（保守，不猜）。
    """
    at_list = [a for a in (ats or []) if isinstance(a, (list, tuple))]
    hist_list = history_items(history, depth)
    hist_lines = history_lines(hist_list, depth, now)
    quoted_known = isinstance(quoted, (list, tuple)) and bool(
        quoted and (quoted[0] or quoted[1])
    )
    # 只有"当前发言人"时没必要注入 —— AstrBot 自带 identifier 已经写了这一行
    if not (quoted_known or at_list or hist_list or matched):
        return ""

    current_known = isinstance(current, (list, tuple)) and bool(
        current and (current[0] or current[1])
    )
    current_key = key_of(current) if current_known else ""

    # 需要跟"当前发言人"比对的其余身份（顺序即输出顺序）
    others: List[Any] = []
    if quoted_known:
        others.append(quoted)
    others.extend(at_list)
    if matched:
        others.append((matched.get("target_id"), matched.get("target_name")))
    others.extend((it.get("target_id"), it.get("target_name")) for it in hist_list)

    other_keys = [key_of(p) for p in others]
    #: 全同：当前发言人已知，且列表里每个人的身份键都等于它
    same_all = bool(current_key) and bool(other_keys) and all(
        k == current_key for k in other_keys
    )
    #: 自引：被引用者就是当前发言人（无论列表里还有没有别人）
    self_quoted = bool(current_key) and quoted_known and key_of(quoted) == current_key
    #: 引用 bot 自己的旧回复：只比 ID，取不到 self_id 就不判
    quoted_is_bot = bool(self_id) and quoted_known and str(
        quoted[0] or ""
    ) == str(self_id)

    lines: List[str] = [TITLE]
    if current_known:
        lines.append(f"- 当前发言人：{who(current)}")
    if quoted_known:
        if quoted_is_bot:
            mark = BOT_MARK
        elif self_quoted:
            mark = SELF_MARK
        else:
            mark = ""
        lines.append(f"- 本轮被引用消息的发送者：{who(quoted)}{mark}")
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
    if quoted_is_bot:
        lines.append(TAIL_BOT_QUOTED)
    elif same_all:
        lines.append(TAIL_SAME)
    elif self_quoted:
        lines.append(TAIL_SELF_QUOTED)
    else:
        lines.append(TAIL)
    return "\n".join(lines)


__all__ = [
    "Person",
    "MAX_NAME",
    "TITLE",
    "TAIL",
    "TAIL_SAME",
    "TAIL_SELF_QUOTED",
    "TAIL_BOT_QUOTED",
    "SELF_MARK",
    "BOT_MARK",
    "rel_time",
    "who",
    "key_of",
    "history_items",
    "history_lines",
    "build_hint",
]
