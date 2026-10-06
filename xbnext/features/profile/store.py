# -*- coding: utf-8 -*-
"""R4 · 用户档案存储。

键：``xbnext:profile:<platform>:<uid>``（前缀由 :class:`~xbnext.storage.KV` 统一加）。

值::

    {
      "name":   "用户自己起的称呼",
      "facts":  "自述信息（身份、偏好、忌讳）",
      "style":  "希望 bot 用什么口吻对待自己",
      "updated": 1728000000
    }

**档案是不可信输入**（AstrBot 原则 4 / aidoc 03 红线）：

- 每个字段过 :func:`xbnext.injector.sanitize`；
- 超长截断；
- 禁止存放 ``<xbnext>`` 之类标记（写入前先 :func:`~xbnext.injector.strip_xbnext`）；
- 注入时再截断一次到 ``user_profile_max_chars``。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ...injector import sanitize, strip_xbnext

#: 允许的档案字段（多余字段一律丢弃，避免用户塞结构化指令进去）
FIELDS = ("name", "facts", "style")
DEFAULT_MAX_LEN = {"name": 32, "facts": 480, "style": 120}


def normalize(raw: Any, max_chars: int = 0) -> Dict[str, Any]:
    """把任意输入压成合法档案；非法输入返回 ``{}``。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    try:
        for field in FIELDS:
            value = raw.get(field)
            if value in (None, ""):
                continue
            limit = DEFAULT_MAX_LEN.get(field, 120)
            if max_chars and field == "facts":
                limit = min(limit, max_chars)
            text = sanitize(strip_xbnext(value), max_len=limit)
            if text:
                out[field] = text
        if out:
            out["updated"] = int(time.time())
    except Exception:  # noqa: BLE001
        return {}
    return out


def is_empty(profile: Any) -> bool:
    """档案是否为空（除时间戳外没有任何字段）。"""
    if not isinstance(profile, dict):
        return True
    return not any(f in profile and profile[f] for f in FIELDS)


def render(profile: Dict[str, Any], max_chars: int = 0) -> str:
    """把档案渲染成给模型看的一段中文；空档案返回 ``""``。

    刻意写成**平铺中文**而不是 JSON —— JSON 会被模型当成数据而非描述，
    而且更容易被用户内容里的引号/花括号打断。
    """
    if is_empty(profile):
        return ""
    lines: List[str] = []
    try:
        if profile.get("name"):
            lines.append(f"称呼：{profile['name']}")
        if profile.get("facts"):
            lines.append(f"自述：{profile['facts']}")
        if profile.get("style"):
            lines.append(f"希望的相处方式：{profile['style']}")
        text = "\n".join(lines)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text
    except Exception:  # noqa: BLE001
        return ""


def key_of(platform: str, uid: Any) -> str:
    """拼出 KV 键（不含 ``xbnext:`` 前缀）。"""
    return f"profile:{platform or 'unknown'}:{uid or 'unknown'}"


class ProfileStore:
    """用户档案的 KV 读写封装。"""

    def __init__(self, kv: Any, logger: Any = None):
        self._kv = kv
        self._log = logger

    async def get(self, platform: str, uid: Any) -> Dict[str, Any]:
        """读一份档案；异常返回 ``{}``。"""
        try:
            data = await self._kv.get_dict(key_of(platform, uid))
            return data if not is_empty(data) else {}
        except Exception as exc:  # noqa: BLE001
            self._warn(f"读取档案失败 {platform}/{uid}: {exc}")
            return {}

    async def set(self, platform: str, uid: Any, raw: Dict[str, Any]) -> Dict[str, Any]:
        """写一份档案（先 sanitize）；返回实际写入内容。"""
        try:
            profile = normalize(raw)
            if not profile:
                return {}
            await self._kv.set(key_of(platform, uid), profile)
            return profile
        except Exception as exc:  # noqa: BLE001
            self._warn(f"写入档案失败 {platform}/{uid}: {exc}")
            return {}

    async def delete(self, platform: str, uid: Any) -> bool:
        """删除一份档案。"""
        try:
            return await self._kv.delete(key_of(platform, uid))
        except Exception:  # noqa: BLE001
            return False

    def _warn(self, message: str) -> None:
        method = getattr(self._log, "warning", None)
        if callable(method):
            try:
                method(message)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "FIELDS",
    "DEFAULT_MAX_LEN",
    "normalize",
    "is_empty",
    "render",
    "key_of",
    "ProfileStore",
]
