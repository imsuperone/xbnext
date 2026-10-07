# -*- coding: utf-8 -*-
"""R4 · 档案指令的纯逻辑部分。

**本文件不 import astrbot**：输入是字符串列表与普通字典，输出是字符串/字典。

指令语法（``/xbnext profile ...``；真机反馈「设置逻辑有点乱」后重写成宽容解析）::

    /xbnext profile                    查看自己的档案
    /xbnext profile 清空                删除自己的全部档案
    /xbnext profile 称呼 小明            改称呼（空格分隔）
    /xbnext profile 称呼:小明           冒号分隔也认（全角「：」同）
    /xbnext profile 称呼=小明            等号分隔也认
    /xbnext profile 称呼 小明 自述 学生   一次改多条（字段名自动切分，分隔符可混写）
    /xbnext profile 称呼:               删掉单个字段（分隔符后留空 = 删除）

**宽容规则**：查看词后面带多余内容直接当查看；裸字段名没写值才报错
（防手滑误删，报错里带正确示例）；值里的普通词（含 ``a=b``）不会被误拆。
**红线**：字段名只能是 :data:`~xbnext.features.profile.store.FIELDS` 里的两个
（称呼 / 自述），其余一律报错（不让用户往档案里塞结构化指令，aidoc/03）。
「口吻」字段已随 P16 移除 —— 再发 `口吻 ...` 会走"未知字段"报错。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .store import DEFAULT_MAX_LEN, FIELDS

#: 字段别名 → 正式字段名（小写归一后再查一次原文，兼容中文大小写无关输入）
KEY_ALIASES: Dict[str, str] = {}
for _names, _field in (
    (("称呼", "名字", "昵称", "昵", "name", "nick", "nickname"), "name"),
    (("自述", "信息", "描述", "资料", "简介", "facts", "info", "desc"), "facts"),
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

#: 正式字段名 → 回执/帮助里的中文标签（迭代顺序即展示顺序）
FIELD_LABELS: Dict[str, str] = {"name": "称呼", "facts": "自述"}

#: 值分隔符（等号 + 半角/全角冒号）—— 只有「字段名 + 分隔符」前缀才算字段声明
SEPARATORS = ("=", ":", "：")
#: 单独成 token 的分隔符（``称呼 = 小明`` 里的 ``=``）当噪音忽略
BARE_SEP = frozenset(SEPARATORS)


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
    return "、".join(FIELD_LABELS.values())


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


def _split_field_sep(token: str) -> Optional[Tuple[str, str]]:
    """``称呼:小明`` / ``name=x`` → ``(字段, 值)``；形态不符返回 ``None``。

    分隔符取**第一次出现**的位置（``=`` / ``:`` / ``：`` 都认）；
    字段名不在白名单也返回 ``None`` —— 交给调用方当普通词处理，
    所以 ``自述 a=b`` 的 ``a=b`` 不会被误拆成「字段 = 值」。
    """
    if not isinstance(token, str):
        return None
    positions = [i for i in (token.find(s) for s in SEPARATORS) if i >= 0]
    if not positions:
        return None
    idx = min(positions)
    if idx == 0:
        return None
    field = _alias(token[:idx])
    if not field:
        return None
    return field, token[idx + 1:].strip()


def classify(args: Iterable[Any]) -> Tuple[str, Dict[str, str]]:
    """判定指令动作。

    :returns: ``(action, payload)`` —— payload 是 ``{字段: 值}``（仅 set 时非空）；
        值为空串表示**删掉该字段**（由 :func:`merge` 落实）
    :raises ValueError: 参数看不懂（**把原因直接当回复文案**）

    单遍扫描，规则只有四条（真机反馈「设置逻辑有点乱」后重写）：

    1. ``字段名:值`` / ``字段名=值``（含全角冒号）—— 字段名+分隔符前缀，值可为空；
    2. 裸字段名 —— 开新字段，值取后续的词，直到下一个字段名；
       于是 ``称呼 小明 自述 学生`` 一次改多条、分隔符可混写；
    3. 分隔符后留空（``称呼:``）= 删该字段；裸字段名没值 = 报错
       （防手滑误删，报错带正确示例）；
    4. 查看词后面带多余内容直接当查看（不再报「多余内容」）。
    """
    tokens = parse_args(args)
    first = tokens[0] if tokens else ""
    head = first.lower()

    if head in DELETE_WORDS:
        if len(tokens) > 1:
            raise ValueError("「清空」后面不用带内容，直接发 /xbnext profile 清空。")
        return ACTION_DELETE, {}

    if head in VIEW_WORDS:
        # 查看意图优先：后面带了别的当没看见（宽容，不再报「多余内容」）
        return ACTION_VIEW, {}

    updates: Dict[str, str] = {}
    sep_fields: set = set()  # 用显式分隔符开的字段（留空 = 删除）
    current = ""
    for token in tokens:
        split = _split_field_sep(token)
        if split:
            current, value = split
            updates[current] = value
            sep_fields.add(current)
            continue
        field = _alias(token)
        if field:
            current = field
            updates[field] = ""  # 裸字段名（重）开字段：值随后补
            sep_fields.discard(field)
            continue
        if token in BARE_SEP:
            if not current:
                raise ValueError(
                    f"看不懂参数「{token}」。可用字段：{usable_keys()} —— "
                    "例：/xbnext profile 称呼 小明"
                )
            continue  # 「称呼 = 小明」这类裸分隔符当噪音忽略
        if not current:
            raise ValueError(
                f"看不懂参数「{token}」。可用字段：{usable_keys()} —— "
                "例：/xbnext profile 称呼 小明"
            )
        updates[current] = (
            f"{updates[current]} {token}" if updates[current] else token
        )

    if not updates:
        raise ValueError(
            f"没有要写入的内容。用法：/xbnext profile {usable_keys()} <你的内容>"
        )
    bare_empty = [k for k, v in updates.items() if not v and k not in sep_fields]
    if bare_empty:
        labels = "、".join(FIELD_LABELS[k] for k in bare_empty)
        example = FIELD_LABELS[bare_empty[0]]
        raise ValueError(
            f"「{labels}」还没写内容。例：/xbnext profile {example} 小明；"
            f"想删掉这个字段发「{example}:」。"
        )
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


def change_summary(updates: Dict[str, str], scope: str = "") -> str:
    """设置回执首行：``已更新：称呼=小明；已删除：自述（群 111）``。

    - 值超过 20 字截断成 ``…``（完整值下面的档案卡里有）；
    - ``scope`` 非空才带范围括号 —— 范围标签在首行与卡片**只标一处**，
      不再像旧回执那样 ``· 群 111`` 和 ``你的档案（群 111）：`` 出现两遍。
    """

    def brief(field: str, value: str) -> str:
        label = FIELD_LABELS.get(field, field)
        if not value:
            return label
        text = value if len(value) <= 20 else value[:20] + "…"
        return f"{label}={text}"

    set_parts = [brief(k, v) for k, v in updates.items() if v]
    del_parts = [FIELD_LABELS.get(k, k) for k, v in updates.items() if not v]
    parts: List[str] = []
    if set_parts:
        parts.append("已更新：" + "、".join(set_parts))
    if del_parts:
        parts.append("已删除：" + "、".join(del_parts))
    if not parts:
        return ""
    tag = f"（{scope}）" if scope else ""
    return "；".join(parts) + tag


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
                label = FIELD_LABELS[field]
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
            f"改一个字段：/xbnext profile 称呼 小明（冒号/等号写法也认，字段：{usable_keys()}）",
            "删单个字段：/xbnext profile 称呼:（字段后跟冒号留空 = 删掉它）",
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
    "FIELD_LABELS",
    "usable_keys",
    "parse_args",
    "classify",
    "merge",
    "scope_label",
    "change_summary",
    "describe",
]
