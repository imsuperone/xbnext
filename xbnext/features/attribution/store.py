# -*- coding: utf-8 -*-
"""R1 · 回复指向存储 —— "bot 这条回复是回给谁的"索引。

借鉴 AstrNa ``reply_target_history`` 的路线（KV 索引 + 三方中文说明 +
全链路清标记），差异点见 ``aidoc/01-需求与根因.md`` §R1：

- message_id 优先、hash 兜底（AstrNa 只有 hash）；
- 可选的历史发言人标记；
- **不发明新的标记协议**，说明一律写成人类可读中文；
- 检测到 AstrNa 同功能开着时让路（见 :mod:`xbnext.switches`）。

KV 结构（一个会话一份，前缀由 ``KV`` 统一加）::

    xbnext:reply_targets:<umo>  ->  {"items": [{...}, ...]}   # 新的在前
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

DEFAULT_LIMIT = 200

#: 每条记录保留的字段（**只存元数据，绝不存对话原文**）
FIELDS = ("target_id", "target_name", "scope", "message_id", "hash", "ts")


def normalize_item(raw: Any) -> Optional[Dict[str, Any]]:
    """把任意来源压成一条合法记录；不合法返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    item = {k: raw.get(k) for k in FIELDS}
    if not item.get("target_id") and not item.get("message_id") and not item.get("hash"):
        return None
    try:
        item["ts"] = int(item.get("ts") or int(time.time()))
    except Exception:  # noqa: BLE001
        item["ts"] = 0
    return item


def push_item(items: List[Dict[str, Any]], item: Dict[str, Any], limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """把新记录插到最前面并裁剪到 ``limit``（新的在前）。"""
    if not isinstance(items, list):
        items = []
    if not item:
        return items[:limit]
    out = [item] + [x for x in items if x.get("hash") != item.get("hash") or not item.get("hash")]
    if limit and len(out) > limit:
        out = out[:limit]
    return out


def lookup(items: Any, *, message_id: Any = "", text_hash: str = "") -> Optional[Dict[str, Any]]:
    """按 message_id 优先、hash 兜底查记录；查不到返回 ``None``。

    歧义（同 hash 多条且 message_id 不匹配）时**放弃匹配** ——
    宁可不说，也不能把归属指错人。
    """
    if not isinstance(items, list) or not items:
        return None
    try:
        if message_id:
            exact = [x for x in items if x.get("message_id") == message_id]
            if len(exact) == 1:
                return exact[0]
            if len(exact) > 1:
                return None  # 歧义，放弃
        if text_hash:
            fuzzy = [x for x in items if x.get("hash") == text_hash]
            if len(fuzzy) == 1:
                return fuzzy[0]
            return None  # 0 条或多条歧义，放弃
    except Exception:  # noqa: BLE001
        return None
    return None


class ReplyTargetStore:
    """回复指向索引的 KV 读写封装。"""

    def __init__(self, kv: Any, limit: int = DEFAULT_LIMIT, logger: Any = None):
        self._kv = kv
        self._limit = max(1, int(limit or DEFAULT_LIMIT))
        self._log = logger

    def _key(self, umo: str) -> str:
        return f"reply_targets:{umo or 'default'}"

    async def load(self, umo: str) -> List[Dict[str, Any]]:
        """读一个会话的索引；异常返回 ``[]``。"""
        try:
            data = await self._kv.get_dict(self._key(umo))
            items = data.get("items")
            return items if isinstance(items, list) else []
        except Exception as exc:  # noqa: BLE001
            self._warn(f"读取回复索引失败 {umo}: {exc}")
            return []

    async def push(self, umo: str, item: Dict[str, Any]) -> bool:
        """写入一条记录（写穿）。"""
        try:
            clean = normalize_item(item)
            if not clean:
                return False
            items = await self.load(umo)
            items = push_item(items, clean, limit=self._limit)
            return await self._kv.set(self._key(umo), {"items": items})
        except Exception as exc:  # noqa: BLE001
            self._warn(f"写入回复索引失败 {umo}: {exc}")
            return False

    async def find(
        self, umo: str, *, message_id: Any = "", text_hash: str = ""
    ) -> Optional[Dict[str, Any]]:
        """查询一条记录。"""
        return lookup(await self.load(umo), message_id=message_id, text_hash=text_hash)

    async def clear(self, umo: str) -> bool:
        """清空一个会话的索引。"""
        try:
            return await self._kv.delete(self._key(umo))
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
    "DEFAULT_LIMIT",
    "FIELDS",
    "normalize_item",
    "push_item",
    "lookup",
    "ReplyTargetStore",
]
