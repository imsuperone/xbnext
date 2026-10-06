# -*- coding: utf-8 -*-
"""插件 KV 存储封装。

AstrBot 的插件 KV 是**插件维度独立空间**（>= 4.9.2），由 Star 自身代理::

    await star.put_kv_data(key, value)
    value = await star.get_kv_data(key, default)
    await star.delete_kv_data(key)

本封装加三层保护（aidoc/02-架构设计.md §5）：

1. **统一前缀**：所有键都加 ``xbnext:``，避免与其他功能串号；
2. **内存缓存 + 写穿**：读过就缓存，写直接更新缓存并落库；
3. **异常降级**：KV 不可用 / 读写失败一律当成"没数据"，绝不裸抛。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from . import KV_PREFIX

_MISSING = object()


class KV:
    """带前缀与缓存的插件 KV 视图。"""

    __slots__ = ("_owner", "_prefix", "_logger", "_cache", "_loaded", "_broken")

    def __init__(self, owner: Any = None, prefix: str = KV_PREFIX, logger: Any = None):
        #: owner = Star 实例（提供 put_kv_data / get_kv_data / delete_kv_data）
        self._owner = owner
        self._prefix = prefix
        self._logger = logger
        self._cache: Dict[str, Any] = {}
        self._loaded: Set[str] = set()
        #: 首次发现 KV 完全不可用后置位，后续直接走缓存，不再反复报错
        self._broken = False

    # ------------------------------------------------------------------
    # 键名
    # ------------------------------------------------------------------
    def key(self, *parts: Any) -> str:
        """拼出带前缀的完整键，如 ``xbnext:profile:aiocqhttp:123``。"""
        tail = ":".join(str(p) for p in parts if p != "" and p is not None)
        return f"{self._prefix}:{tail}" if tail else self._prefix

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def get(self, key: str, default: Any = None) -> Any:
        """读一个键；未命中 / 异常都返回 ``default``。"""
        full = self.key(key)
        if full in self._cache:
            return self._cache[full]
        if self._broken or self._owner is None:
            return default
        getter = getattr(self._owner, "get_kv_data", None)
        if not callable(getter):
            self._broken = True
            return default
        try:
            value = await getter(full, _MISSING)
        except Exception as exc:  # noqa: BLE001
            self._log("debug", f"读取 KV 失败 {full}: {exc}")
            return default
        if value is _MISSING:
            self._cache[full] = default
            self._loaded.add(full)
            return default
        self._cache[full] = value
        self._loaded.add(full)
        return value

    async def get_dict(self, key: str) -> dict:
        """读一个字典值；类型不对时返回 ``{}``。"""
        value = await self.get(key, {})
        return value if isinstance(value, dict) else {}

    async def get_list(self, key: str) -> list:
        """读一个列表值；类型不对时返回 ``[]``。"""
        value = await self.get(key, [])
        return value if isinstance(value, list) else []

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def set(self, key: str, value: Any) -> bool:
        """写一个键（写穿）。失败返回 ``False``，缓存仍会更新以便本次会话可用。"""
        full = self.key(key)
        self._cache[full] = value
        self._loaded.add(full)
        if self._broken or self._owner is None:
            return False
        putter = getattr(self._owner, "put_kv_data", None)
        if not callable(putter):
            self._broken = True
            return False
        try:
            await putter(full, value)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"写入 KV 失败 {full}: {exc}")
            return False

    async def delete(self, key: str) -> bool:
        """删一个键；同步清缓存。"""
        full = self.key(key)
        self._cache.pop(full, None)
        self._loaded.discard(full)
        if self._broken or self._owner is None:
            return False
        deleter = getattr(self._owner, "delete_kv_data", None)
        if not callable(deleter):
            return False
        try:
            await deleter(full)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"删除 KV 失败 {full}: {exc}")
            return False

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------
    def invalidate(self, key: str = "") -> None:
        """清缓存：给 ``key`` 清单个，给空串清全部（配置热改后用）。"""
        if not key:
            self._cache.clear()
            self._loaded.clear()
            return
        full = self.key(key)
        self._cache.pop(full, None)
        self._loaded.discard(full)

    @property
    def usable(self) -> bool:
        """KV 后端当前是否可用（WebUI 状态面板展示）。"""
        return self._owner is not None and not self._broken

    def _log(self, level: str, message: str) -> None:
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["KV"]
