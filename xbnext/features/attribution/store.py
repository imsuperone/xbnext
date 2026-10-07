# -*- coding: utf-8 -*-
"""R1 · 回复指向存储 —— "bot 这条回复是回给谁的"索引。

借鉴 AstrNa ``reply_target_history`` 的路线（KV 索引 + 三方中文说明 +
全链路清标记），差异点见 ``aidoc/01-需求与根因.md`` §R1：

- message_id 优先、hash 兜底（AstrNa 只有 hash）；
- 可选的历史发言人标记；
- **不发明新的标记协议**，说明一律写成人类可读中文。

KV 结构（一个会话一份 + 一个全局会话索引，前缀由 ``KV`` 统一加）::

    xbnext:reply_targets:<umo>  ->  {"items": [{...}, ...]}   # 新的在前
    xbnext:reply_targets:index  ->  [umo, ...]                # 活跃序（新回复在前）

全局索引给"会话数"封顶（AstrNa 是 300 会话 LRU，我们取 200）：只有 bot
**真回复过**的会话才进索引，超限时从尾部把最久没活跃的会话**连数据带索引
一起删** —— AstrBot 插件 KV 没有按键遍历，不建索引就没法给上限。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

#: 每会话保留的回复记录数（P16 起默认 50，用户拍板；超出先进先出淘汰）
DEFAULT_LIMIT = 50
#: 全局会话数上限（对标 AstrNa 的 300，我们取 200）
DEFAULT_SESSION_LIMIT = 200
#: 全局会话索引键（活跃序，新回复在前）
INDEX_KEY = "reply_targets:index"

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


def touch_session(sessions: Any, umo: str, limit: int = DEFAULT_SESSION_LIMIT) -> Tuple[List[str], List[str]]:
    """把 ``umo`` 提到索引最前（活跃序）；超限从尾部挤出待删除的会话。

    返回 ``(新索引列表, 被挤出待删除的 umo 列表)``。坏数据（非列表 /
    非字符串项 / 空串）顺手清掉 —— 索引被写坏过就当没有，自愈优先。
    """
    if not isinstance(sessions, list):
        sessions = []
    out = [s for s in sessions if isinstance(s, str) and s and s != umo]
    out.insert(0, umo)
    try:
        cap = max(1, int(limit or DEFAULT_SESSION_LIMIT))
    except Exception:  # noqa: BLE001
        cap = DEFAULT_SESSION_LIMIT
    if len(out) > cap:
        return out[:cap], out[cap:]
    return out, []


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
    """回复指向索引的 KV 读写封装（每会话条数 + 全局会话数双上限）。"""

    def __init__(
        self,
        kv: Any,
        limit: int = DEFAULT_LIMIT,
        logger: Any = None,
        session_limit: int = DEFAULT_SESSION_LIMIT,
    ):
        self._kv = kv
        self._limit = max(1, int(limit or DEFAULT_LIMIT))
        self._log = logger
        try:
            self._session_limit = max(1, int(session_limit or DEFAULT_SESSION_LIMIT))
        except Exception:  # noqa: BLE001
            self._session_limit = DEFAULT_SESSION_LIMIT

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
        """写入一条记录（写穿），并顺手维护全局会话索引（超限淘汰）。"""
        try:
            clean = normalize_item(item)
            if not clean:
                return False
            items = await self.load(umo)
            items = push_item(items, clean, limit=self._limit)
            if not await self._kv.set(self._key(umo), {"items": items}):
                return False
        except Exception as exc:  # noqa: BLE001
            self._warn(f"写入回复索引失败 {umo}: {exc}")
            return False
        # 索引维护是 best-effort：主数据已落盘，索引炸了不能回头报写入失败
        try:
            await self._touch_session(umo)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"维护回复会话索引失败 {umo}: {exc}")
        return True

    async def find(
        self, umo: str, *, message_id: Any = "", text_hash: str = ""
    ) -> Optional[Dict[str, Any]]:
        """查询一条记录。"""
        return lookup(await self.load(umo), message_id=message_id, text_hash=text_hash)

    async def clear(self, umo: str) -> bool:
        """清空一个会话的索引，并把它从全局会话索引里一并摘掉。"""
        try:
            ok = await self._kv.delete(self._key(umo))
        except Exception as exc:  # noqa: BLE001
            self._warn(f"删除回复索引失败 {umo}: {exc}")
            return False
        try:
            sessions = [s for s in await self._load_sessions() if s != umo]
            await self._save_sessions(sessions)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"同步回复会话索引失败 {umo}: {exc}")
        return ok

    # ------------------------------------------------------------------
    # 全局会话索引（活跃序）
    # ------------------------------------------------------------------
    async def _touch_session(self, umo: str) -> None:
        """把会话提到索引最前；超限时删掉最久没活跃会话的整份数据。"""
        sessions, evicted = touch_session(
            await self._load_sessions(), umo, self._session_limit
        )
        await self._save_sessions(sessions)
        for old in evicted:
            try:
                await self._kv.delete(self._key(old))
            except Exception as exc:  # noqa: BLE001
                self._warn(f"淘汰过期回复会话失败 {old}: {exc}")

    async def _load_sessions(self) -> Any:
        try:
            return await self._kv.get_list(INDEX_KEY)
        except Exception:  # noqa: BLE001
            return []

    async def _save_sessions(self, sessions: List[str]) -> None:
        await self._kv.set(INDEX_KEY, sessions)

    def _warn(self, message: str) -> None:
        method = getattr(self._log, "warning", None)
        if callable(method):
            try:
                method(message)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_SESSION_LIMIT",
    "INDEX_KEY",
    "FIELDS",
    "normalize_item",
    "push_item",
    "touch_session",
    "lookup",
    "ReplyTargetStore",
]
