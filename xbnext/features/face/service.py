# -*- coding: utf-8 -*-
"""R3 · QQ 表情翻译 —— 纯逻辑部分。

本文件**不 import astrbot**：输入是 ``(kind, code)`` 二元组或普通字符串，
输出是字符串，可直接 unittest。

三种片段种类（:data:`KIND_*`）：

====== ======================== ==================================
种类    code 含义                 来源
====== ======================== ==================================
face    标准表情数字 ID           消息链 ``Face(id=)`` / OneBot ``face``
mface   商城表情 key              消息链 / OneBot ``mface``（查表通常失败）
summary 段自带的显示名            ``mface.summary`` / ``rps``、``dice`` 的 result
====== ======================== ==================================

设计要点：

- 翻译结果用**方括号短语**（``[表情:得意]``）而不是 emoji 字面量 ——
  emoji 会被模型当成普通符号，方括号短语明确表示"这是一条消息里的表情"；
- 取不到名称时回退成 ``[表情:ID12345]`` / ``[表情:key]``，
  **宁可泄露 ID 也不要瞎猜语义**；
- 格式串从配置 ``face_format`` 读，``{name}`` 是占位符。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .data import face_name, mface_name

DEFAULT_FORMAT = "[表情:{name}]"

#: 我们识别的片段种类
KIND_FACE = "face"  # 标准小黄脸，参数是数字 ID
KIND_MFACE = "mface"  # 商城表情，参数是字符串 key
KIND_SUMMARY = "summary"  # 段自带的显示名（mface.summary / rps、dice 结果）

#: 一条说明里最多塞多少个表情片段（防止刷一屏表情撑爆请求）
MAX_PARTS = 40

#: 显示名允许的最大长度（summary 理论上是短名，超长说明不是表情名）
MAX_NAME_LEN = 24

#: 说明文案：前缀 + 中缀 + 后缀（分开常量便于测试断言与后续换文案）
NOTE_PREFIX = "本轮消息中出现了 QQ 表情："
NOTE_MID = "。这些表情在转成纯文本时已被丢弃，未出现在上方正文里，"
NOTE_SUFFIX = "请按其含义理解用户当时的情绪与语气。"

#: OneBot CQ 码转义还原
_CQ_UNESCAPE = (
    ("&#44;", ","),
    ("&#91;", "["),
    ("&#93;", "]"),
    ("&amp;", "&"),
)
_CQ_RE = re.compile(r"\[CQ:([A-Za-z0-9_\-]+)((?:,[^\[\]]*)?)\]")
#: 包裹在显示名外层的括号
_WRAP_RE = re.compile(r"^[（(\[【]+|[）)\]】]+$")
#: 显示名里的装饰前缀（不同实现写法不一）
_NAME_PFX = ("表情：", "表情:", "商城表情：", "商城表情:", "Sticker:", "Sticker：", "Sticker ")


def render(name: str, fmt: str = DEFAULT_FORMAT) -> str:
    """按格式串渲染一个表情片段；格式串非法时回退默认格式。"""
    if not isinstance(fmt, str) or not fmt.strip() or "{name}" not in fmt:
        fmt = DEFAULT_FORMAT
    try:
        return fmt.replace("{name}", name)
    except Exception:  # noqa: BLE001
        return DEFAULT_FORMAT.replace("{name}", name)


def clean_summary(value: object) -> str:
    """把段自带的显示名规整成纯名称；**不像名称就返回 ``""``**。

    调用方拿到 ``""`` 应改走 ``KIND_MFACE`` / ``KIND_FACE`` 兜底，
    而不是把空串渲染成 ``[表情:]``。
    """
    if not isinstance(value, str):
        return ""
    s = value.strip()
    if not s:
        return ""
    for _ in range(3):
        stripped = _WRAP_RE.sub("", s).strip()
        if stripped == s:
            break
        s = stripped
    for pfx in _NAME_PFX:
        if s.startswith(pfx):
            s = s[len(pfx):].strip()
            break
    s = _WRAP_RE.sub("", s).strip()
    if not s or len(s) > MAX_NAME_LEN:
        return ""
    return s


def translate_token(kind: str, code: object, fmt: str = DEFAULT_FORMAT) -> str:
    """把一个 ``(kind, code)`` 翻译成文本片段。

    - ``face``：查表得中文名 → ``[表情:得意]``；查不到 → ``[表情:ID5]``
    - ``mface``：查表得名 → ``[表情:XXX]``；查不到 → ``[表情:key270_abc]``
    - ``summary``：直接用段自带显示名 → ``[表情:得意]``；不像名称 → ``[表情:?]``
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
        if kind == KIND_SUMMARY:
            name = clean_summary(code)
            if name:
                return render(name, fmt)
            return render("?", fmt)
        return render("?", fmt)
    except Exception:  # noqa: BLE001
        return render("?", fmt)


def translate_all(
    tokens: Iterable[Sequence], fmt: str = DEFAULT_FORMAT, dedupe: bool = True
) -> List[str]:
    """批量翻译，返回片段列表。

    :param tokens: ``[(kind, code), ...]``，元素也允许是单元素 ``(code,)``
    :param dedupe: 重复片段只留一个（刷一屏同款表情时避免刷屏）
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
            if len(out) >= MAX_PARTS:
                break
    except Exception:  # noqa: BLE001
        return out
    return out


def note_from_parts(parts: Optional[Iterable[str]]) -> str:
    """把已翻译好的片段合成一段说明文本；没有片段时返回 ``""``。"""
    try:
        items = [p for p in (parts or []) if isinstance(p, str) and p]
    except Exception:  # noqa: BLE001
        return ""
    if not items:
        return ""
    return f"{NOTE_PREFIX}{' '.join(items)}{NOTE_MID}{NOTE_SUFFIX}"


def build_note(tokens: Iterable[Sequence], fmt: str = DEFAULT_FORMAT) -> str:
    """把表情片段合成一段可注入的说明文本；没有片段时返回 ``""``。"""
    return note_from_parts(translate_all(tokens, fmt=fmt))


# ---------------------------------------------------------------------------
# OneBot 原始载荷解析（mface 不进消息链，只能从这里捞）
# ---------------------------------------------------------------------------

def _token_from_segment(seg_type: str, data: Dict[str, Any]) -> Optional[Tuple[str, Any]]:
    """把 ``({"type": ..., "data": ...})`` 压成一个 ``(kind, code)``。"""
    if not isinstance(seg_type, str) or not isinstance(data, dict):
        return None
    t = seg_type.lower()
    if t == "face":
        for key in ("id", "face_id", "faceId"):
            if data.get(key) not in (None, ""):
                return (KIND_FACE, data.get(key))
        return None
    if t in ("mface", "market_face", "marketface"):
        for key in ("summary", "name", "emoji_name", "face_name"):
            if clean_summary(data.get(key)):
                return (KIND_SUMMARY, data.get(key))
        for key in ("emoji_id", "key", "id"):
            if data.get(key) not in (None, ""):
                return (KIND_MFACE, data.get(key))
        return None
    if t in ("rps", "dice"):
        for key in ("result", "resultId", "result_id"):
            if data.get(key) not in (None, ""):
                return (KIND_SUMMARY, f"{t} {data.get(key)}")
        return None
    return None


def _parse_cq(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """解析 CQ 码字符串 → ``[(type, data), ...]``（只认我们关心的段）。"""
    out: List[Tuple[str, Dict[str, Any]]] = []
    for m in _CQ_RE.finditer(text):
        seg_type = m.group(1)
        if seg_type not in ("face", "mface", "market_face", "rps", "dice"):
            continue
        data: Dict[str, Any] = {}
        raw = m.group(2) or ""
        for pair in raw.split(","):
            if not pair or "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            for a, b in _CQ_UNESCAPE:
                v = v.replace(a, b)
            data[k] = v
        out.append((seg_type, data))
    return out


def tokens_from_raw(raw: object) -> List[Tuple[str, Any]]:
    """从 OneBot 原始载荷里捞表情 token。

    ``raw`` 允许是：

    - ``[{"type": "mface", "data": {...}}, ...]``（OneBot v11 JSON 消息）
    - ``{"type": ..., "data": {...}}``（单段）
    - ``"[CQ:mface,summary=得意,...]"``（CQ 码字符串）
    - JSON 字符串（先 ``json.loads`` 再按上面处理）

    解析不了返回空列表，**不抛异常**。
    """
    try:
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            out: List[Tuple[str, Any]] = []
            for item in raw:
                out.extend(tokens_from_raw(item))
            return out
        if isinstance(raw, dict):
            if isinstance(raw.get("message"), (list, tuple, dict, str)):
                nested = tokens_from_raw(raw.get("message"))
                if nested:
                    return nested
            tok = _token_from_segment(
                str(raw.get("type", "")), raw.get("data") or {}
            )
            return [tok] if tok else []
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                return []
            if s[0] in "[{":
                try:
                    return tokens_from_raw(json.loads(s))
                except Exception:  # noqa: BLE001  不是 JSON 就按 CQ 码继续
                    pass
            out = []
            for seg_type, data in _parse_cq(s):
                tok = _token_from_segment(seg_type, data)
                if tok:
                    out.append(tok)
            return out
        return []
    except Exception:  # noqa: BLE001
        return []


__all__ = [
    "DEFAULT_FORMAT",
    "KIND_FACE",
    "KIND_MFACE",
    "KIND_SUMMARY",
    "MAX_PARTS",
    "MAX_NAME_LEN",
    "NOTE_PREFIX",
    "NOTE_MID",
    "NOTE_SUFFIX",
    "render",
    "clean_summary",
    "translate_token",
    "translate_all",
    "note_from_parts",
    "build_note",
    "tokens_from_raw",
]
