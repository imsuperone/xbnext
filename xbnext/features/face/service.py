# -*- coding: utf-8 -*-
"""R3 · QQ 表情翻译 —— 纯逻辑部分。

本文件**不 import astrbot**：输入是 ``(kind, code)`` 二元组或普通字符串，
输出是字符串，可直接 pytest。

设计要点：

- 翻译结果用**方括号短语**（``[表情:得意]``）而不是 emoji 字面量 ——
  emoji 会被模型当成普通符号，方括号短语明确表示"这是一条消息里的表情"；
- 取不到名称时回退成 ``[表情:ID12345]`` / ``[表情:key]``，
  **宁可泄露 ID 也不要瞎猜语义**；
- 格式串从配置 ``face_format`` 读，``{name}`` 是占位符。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .data import face_name, mface_name

DEFAULT_FORMAT = "[表情:{name}]"

#: 我们识别的片段种类
KIND_FACE = "face"  # 标准小黄脸，参数是数字 ID
KIND_MFACE = "mface"  # 商城表情，参数是字符串 key


def render(name: str, fmt: str = DEFAULT_FORMAT) -> str:
    """按格式串渲染一个表情片段；格式串非法时回退默认格式。"""
    if not isinstance(fmt, str) or not fmt.strip() or "{name}" not in fmt:
        fmt = DEFAULT_FORMAT
    try:
        return fmt.replace("{name}", name)
    except Exception:  # noqa: BLE001
        return DEFAULT_FORMAT.replace("{name}", name)


def translate_token(kind: str, code: object, fmt: str = DEFAULT_FORMAT) -> str:
    """把一个 ``(kind, code)`` 翻译成文本片段。

    - ``face``：查表得中文名 → ``[表情:得意]``；查不到 → ``[表情:ID5]``
    - ``mface``：查表得名 → ``[表情:XXX]``；查不到 → ``[表情:key270_abc]``
    - 未知种类：原样回退成 ``[表情:?]``（不猜）
    """
    try:
        if kind == KIND_FACE:
            name = face_name(code)
            if name:
                return render(name, fmt)
            return render(f"ID{code}", fmt)
        if kind == KIND_MFACE:
            key = "" if code is None else str(code)
            name = mface_name(key)
            if name:
                return render(name, fmt)
            return render(f"key{key}" if key else "?", fmt)
        return render("?", fmt)
    except Exception:  # noqa: BLE001
        return render("?", fmt)


def translate_all(
    tokens: Iterable[Sequence], fmt: str = DEFAULT_FORMAT, dedupe: bool = True
) -> List[str]:
    """批量翻译，返回片段列表。

    :param tokens: ``[(kind, code), ...]``，元素也允许是单元素 ``(code,)``
    :param dedupe: 相邻重复只留一个（刷一屏同款表情时避免刷屏）
    """
    out: List[str] = []
    seen: List[str] = []
    try:
        for item in tokens or ():
            if isinstance(item, (list, tuple)):
                if len(item) >= 2:
                    kind, code = item[0], item[1]
                elif len(item) == 1:
                    kind, code = KIND_FACE, item[0]
                else:
                    continue
            else:
                kind, code = KIND_FACE, item
            text = translate_token(kind, code, fmt)
            if dedupe and text in seen:
                continue
            seen.append(text)
            out.append(text)
    except Exception:  # noqa: BLE001
        return out
    return out


def build_note(tokens: Iterable[Sequence], fmt: str = DEFAULT_FORMAT) -> str:
    """把表情片段合成一段可注入的说明文本；没有片段时返回 ``""``。"""
    parts = translate_all(tokens, fmt=fmt)
    if not parts:
        return ""
    joined = " ".join(parts)
    return f"（本轮消息里出现的表情：{joined}）"


__all__ = [
    "DEFAULT_FORMAT",
    "KIND_FACE",
    "KIND_MFACE",
    "render",
    "translate_token",
    "translate_all",
    "build_note",
]
