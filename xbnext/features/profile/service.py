# -*- coding: utf-8 -*-
"""R4 · 档案指令的纯逻辑部分。

**本文件不 import astrbot**：输入是字符串列表与普通字典，输出是字符串/字典。

指令语法（写在 ``main.py`` 的命令组下，形如 ``/xbnext profile ...``）::

    /xbnext profile                 查看自己的档案
    /xbnext profile 清空             删除自己的档案
    /xbnext profile 称呼 小明         改称呼
    /xbnext profile 自述 我是学生      改自述
    /xbnext profile 口吻 毒舌一点      改相处方式
    /xbnext profile 称呼=小明 自述=...  等号形式，可一次改多条

**红线**：字段名只能是 :data:`~xbnext.features.profile.store.FIELDS` 里的三个，
其余一律报错（不让用户往档案里塞结构化指令，aidoc/03）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .store import DEFAULT_MAX_LEN, FIELDS

#: 字段别名 → 正式字段名（小写归一后再查一次原文，兼容中文大小写无关输入）
KEY_ALIASES: Dict[str, str] = {}
for _names, _field in (
    (("称呼", "名字", "昵称", "昵", "name", "nick", "nickname"), "name"),
    (("自述", "信息", "描述", "资料", "简介", "facts", "info", "desc"), "facts"),
    (("口吻", "风格", "相处", "方式", "语气", "style", "tone"), "style"),
):
    for _n in _names:
        KEY_ALIASES[_n] = _field

#: 等价于"查看"的首个参数
VIEW_WORDS = ("", "查看", "看", "list", "view", "show", "info", "help", "?", "？", "说明")
#: 等价于"删除"的首个参数
DELETE_WORDS = ("清空", "删除", "去掉", "重置", "del", "delete", "remove", "reset", "clear")

ACTION_VIEW = "view"
ACTION_DELETE = "delete"
ACTION_SET = "set"


def _alias(raw: str) -> str:
    """把用户输入的字段名归一成正式字段名；认不出返回 ``\"\"``。"""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    return KEY_ALIASES.get(text.lower()) or KEY_ALIASES.get(text) or ""


def usable_keys() -> str:
    """给报错与帮助文案用的可用字段列表。"""
    return "、".join(("称呼", "自述", "口吻"))


def parse_args(args: Iterable[Any]) -> List[str]:
    """把可能很乱的参数压成干净的非空字符串列表。"""
    out: List[str] = []
    for item in args or ():
        if item is None:
            continue
        text = item if isinstance(item, str) else str(item)
        text = text.strip()
        if text:
            out.append(text)
    return out


def classify(args: Iterable[Any]) -> Tuple[str, Dict[str, str]]:
    """判定指令动作。

    :returns: ``(action, payload)`` —— payload 是 ``{字段: 值}``（仅 set 时非空）
    :raises ValueError: 参数看不懂（**把原因直接当回复文案**）
    """
    tokens = parse_args(args)
    first = tokens[0] if tokens else ""
    head = first.lower()

    if head in DELETE_WORDS:
        if len(tokens) > 1:
            raise ValueError(f"清空档案只需要「清空」两个字，多余内容：{' '.join(tokens[1:])}")
        return ACTION_DELETE, {}

    if head in VIEW_WORDS:
        if len(tokens) > 1:
            raise ValueError(
                f"查看档案请只发「/xbnext profile」，多余内容：{' '.join(tokens[1:])}"
            )
        return ACTION_VIEW, {}

    updates: Dict[str, str] = {}
    positional: List[str] = []
    for token in tokens:
        if "=" in token and not token.startswith("="):
            key, value = token.split("=", 1)
            field = _alias(key)
            if not field:
                raise ValueError(f"不认识的档案字段「{key.strip()}」，可用：{usable_keys()}")
            updates[field] = value.strip()
        else:
            positional.append(token)

    if updates and positional:
        raise ValueError(
            "一次只改一种写法：要么「字段 值」，要么「字段=值」，不要混用。"
        )

    if positional:
        field = _alias(positional[0])
        if not field:
            raise ValueError(
                f"看不懂参数「{positional[0]}」。可用字段：{usable_keys()}；"
                f"查看发「/xbnext profile」，删除发「/xbnext profile 清空」。"
            )
        updates[field] = " ".join(positional[1:]).strip()

    if not updates:
        raise ValueError(
            f"没有要写入的内容。用法：/xbnext profile {usable_keys()} <你的内容>"
        )
    empty = [k for k, v in updates.items() if not v]
    if empty:
        raise ValueError(f"字段 {'、'.join(empty)} 的值是空的，没写进去。")
    return ACTION_SET, updates


def merge(existing: Any, updates: Dict[str, str]) -> Dict[str, str]:
    """把新字段并进旧档案，返回**只含 FIELDS** 的干净字典。

    这样改一个字段不会把用户其它字段抹掉（``ProfileStore.set`` 是整值覆盖）。
    """
    merged: Dict[str, str] = {}
    if isinstance(existing, dict):
        for field in FIELDS:
            value = existing.get(field)
            if value:
                merged[field] = str(value)
    for field, value in (updates or {}).items():
        if field in FIELDS:
            if value:
                merged[field] = str(value)
            else:
                merged.pop(field, None)
    return merged


def scope_label(gid: Any) -> str:
    """档案所属范围的中文标签：``群 123456`` / ``私聊``（回复里给用户看）。

    分群之后用户必须一眼看出"我现在改的是哪一份档案"，否则又会乱。
    """
    text = str(gid or "").strip()
    return f"群 {text}" if text else "私聊"


def describe(
    profile: Any,
    extra: Iterable[str] = (),
    scope: str = "",
    usage: bool = True,
) -> str:
    """渲染档案的查看回执（给人看的，不是给模型看的）。

    - ``scope``：范围标签（``群 123456`` / ``私聊``），空串不显示；
    - ``usage``：是否带"维护方式"帮助块（设置/清空回执不再重复帮助，少刷屏）。

    **排版红线**（真机反馈「/xbnext 系列回复都乱」）：
    帮助行不许用空格做列对齐（QQ 是非等宽字体，对齐即乱），
    不许出现 markdown 星号（纯文本消息不渲染 ``**``）。
    """
    lines: List[str] = []
    if isinstance(profile, dict):
        for field in FIELDS:
            value = profile.get(field)
            if value:
                label = {"name": "称呼", "facts": "自述", "style": "口吻"}[field]
                limit = DEFAULT_MAX_LEN.get(field, 120)
                text = str(value)
                if len(text) > limit:
                    text = text[:limit] + "…"
                lines.append(f"  {label}：{text}")
    tag = f"（{scope}）" if scope else ""
    if lines:
        body = "\n".join([f"你的档案{tag}："] + lines)
    else:
        body = f"你还没有填写档案{tag}。"

    out: List[str] = [body]
    if usage:
        out += [
            "",
            "维护方式：",
            f"改一个字段：/xbnext profile 称呼 小明（字段：{usable_keys()}）",
            "删除档案：/xbnext profile 清空",
        ]
    extras = [str(item) for item in extra if item]
    if extras:
        out += [""] + extras
    return "\n".join(out)


__all__ = [
    "KEY_ALIASES",
    "VIEW_WORDS",
    "DELETE_WORDS",
    "ACTION_VIEW",
    "ACTION_DELETE",
    "ACTION_SET",
    "usable_keys",
    "parse_args",
    "classify",
    "merge",
    "scope_label",
    "describe",
]
