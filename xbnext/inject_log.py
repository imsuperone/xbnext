# -*- coding: utf-8 -*-
"""提示词注入记录（P16 · 「运行状态最下面加一个入口，
能看到具体提示词注入了些什么」）。

每轮 LLM 请求收尾时把**发给模型的最终态**记一条：

- ``prompt``  —— 清洗后的正文（quote 清洗此时已就地改写完）
- ``parts``   —— ``req.extra_user_content_parts`` 的文本（core 塞的
  引用块 + 各功能注入段都在这里）
- ``actions`` —— 本轮哪些功能动了手（与 INFO 汇总同源）
- ``images``  —— 本轮图片张数
- ``umo`` / ``ts`` —— 会话与时间

存**插件 KV**（键 ``inject_log``，重启不丢），保留最近
:data:`MAX_ITEMS` 轮、新在前；截断上限写死在模块顶，KV 里永远只有
一份小抄。**纯函数 + KV 读写，不 import astrbot。**
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

__all__ = [
    "KEY",
    "MAX_ITEMS",
    "MAX_PROMPT",
    "MAX_PART",
    "MAX_PARTS",
    "build_entry",
    "load",
    "part_text",
    "record",
    "umo_of",
]

#: KV 键（KV 视图会自动带 ``xbnext:`` 前缀）
KEY = "inject_log"
#: 保留的轮数（新在前）
MAX_ITEMS = 10
#: 正文截断上限
MAX_PROMPT = 8000
#: 单个注入段截断上限
MAX_PART = 4000
#: 注入段条数上限
MAX_PARTS = 24


def _clip(text: str, limit: int) -> str:
    """截断到 ``limit``，超了挂省略标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "…（已截断）"


def umo_of(event: Any) -> str:
    """会话标识：优先 ``unified_msg_origin``，兜底 ``get_session_id()``。"""
    umo = getattr(event, "unified_msg_origin", None)
    if isinstance(umo, str) and umo:
        return umo
    getter = getattr(event, "get_session_id", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:  # noqa: BLE001
            return ""
    return ""


def part_text(part: Any) -> str:
    """把 ``extra_user_content_parts`` 里的一段压成可读文本。

    TextPart（含 mark_as_temp 的注入段）取 ``.text``；纯字符串直接用；
    其它组件记类型名占位 —— 至少让人知道这里有个东西。
    """
    text = getattr(part, "text", None)
    if isinstance(text, str) and text:
        return text
    if isinstance(part, str):
        return part
    return f"<{type(part).__name__}>"


def build_entry(event: Any, actions: List[str], req: Any) -> Dict[str, Any]:
    """压一条记录（所有截断都在这里落）；输入异常字段一律降级。"""
    prompt = str(getattr(req, "prompt", None) or "")
    raw_parts = getattr(req, "extra_user_content_parts", None) or []
    try:
        iterator = list(raw_parts)
    except Exception:  # noqa: BLE001
        iterator = []
    parts: List[str] = []
    for part in iterator:
        if len(parts) >= MAX_PARTS:
            break
        text = part_text(part)
        if text:
            parts.append(_clip(text, MAX_PART))
    try:
        images = len(getattr(req, "image_urls", None) or [])
    except Exception:  # noqa: BLE001
        images = 0
    return {
        "ts": int(time.time()),
        "umo": umo_of(event),
        "actions": [str(a) for a in (actions or [])],
        "prompt": _clip(prompt, MAX_PROMPT),
        "parts": parts,
        "images": int(images),
    }


async def load(kv: Any) -> List[Dict[str, Any]]:
    """读记录；任何异常 / 脏数据都降级成 ``[]``。"""
    if kv is None:
        return []
    try:
        data = await kv.get(KEY, None)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)][:MAX_ITEMS]


async def record(kv: Any, entry: Dict[str, Any]) -> bool:
    """插到队首、截断到上限、写 KV；失败返回 ``False`` 不冒泡。"""
    if kv is None:
        return False
    try:
        items = await load(kv)
        items.insert(0, dict(entry))
        del items[MAX_ITEMS:]
        return bool(await kv.set(KEY, {"items": items}))
    except Exception:  # noqa: BLE001
        return False
