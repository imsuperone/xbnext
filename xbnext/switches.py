# -*- coding: utf-8 -*-
"""WebUI 配置写入。

两条职责：

1. :func:`coerce_value` / :func:`save_config` / :func:`apply_conf` ——
   WebUI 写配置的类型转换、落盘与失败回滚。
2. :data:`WRITABLE_KEYS` 键白名单 —— 防止 WebUI 往配置里塞任意键。

（功能开关不存在本模块：runtime 每功能进门直接
``ctx.enabled(key)`` 读配置，热改即生效。）
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .config import SCHEMA, Config

# ==================================================================
# 配置写入（WebUI 用）
# ==================================================================
#: 串行化配置写 + 落盘，避免并发把配置写花
_CONF_LOCK = asyncio.Lock()

#: 可写入的键白名单（等于 schema 全部键）——防止 WebUI 写进任意键
WRITABLE_KEYS = tuple(sorted(SCHEMA.keys()))


def coerce_value(key: str, value: Any) -> Any:
    """按 schema 声明的类型把传入值转成目标类型；无法转换时抛 ``ValueError``。"""
    item = SCHEMA.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"未知配置项: {key}")
    kind = str(item.get("type") or "string")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "yes", "on"):
                return True
            if v in ("0", "false", "no", "off"):
                return False
        raise ValueError(f"{key} 需要布尔值")
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 需要整数") from None
    if kind == "list":
        if isinstance(value, list):
            return value
        raise ValueError(f"{key} 需要列表")
    if kind == "object":
        if isinstance(value, dict):
            return value
        raise ValueError(f"{key} 需要对象")
    return str(value)


async def save_config(config: Any) -> bool:
    """把配置对象落盘；不支持持久化的对象抛 ``RuntimeError``。

    对齐 AstrNa 的 ``_save_shared_config``：优先异步保存，其次线程池同步保存。
    """
    if config is None:
        raise RuntimeError("配置对象不可用")
    save_async = getattr(config, "save_config_async", None)
    if callable(save_async):
        result = await save_async()
        return result is not False
    save_sync = getattr(config, "save_config", None)
    if callable(save_sync):
        result = await asyncio.to_thread(save_sync)
        return result is not False
    raise RuntimeError("当前配置对象不支持持久化保存")


#: 判定"键原本不存在"用的哨兵（不能用 None：None 也是合法配置值）
_MISSING = object()

async def apply_conf(conf: Config, key: str, value: Any) -> Dict[str, Any]:
    """写入一个配置项并尝试落盘；失败则回滚到旧值。

    返回固定结构（调用方不需要判空）::

        {"key": str, "value": 任意, "persisted": bool, "error": str|None}

    **永远不抛异常**。落盘失败时内存里的新值会回滚，保证"看到的就是存下来的"，
    并把原因写进 ``error``，前端据此提示用户去插件配置页手动保存。
    """
    out: Dict[str, Any] = {"key": key, "value": None, "persisted": False, "error": None}
    try:
        coerced = coerce_value(key, value)
    except ValueError as exc:
        out["error"] = str(exc)
        return out

    raw = conf.raw
    if raw is None:
        out["error"] = "配置对象不可用"
        return out

    async with _CONF_LOCK:
        old = _MISSING
        try:
            if hasattr(raw, "get"):
                old = raw.get(key, _MISSING)
            else:
                old = raw[key]
        except Exception:  # noqa: BLE001
            old = _MISSING
        try:
            raw[key] = coerced
        except Exception as exc:  # noqa: BLE001  某些只读配置对象
            out["error"] = f"配置对象拒绝写入: {exc}"
            return out
        try:
            await save_config(raw)
        except Exception as exc:  # noqa: BLE001  不支持落盘 / 落盘报错
            # 回滚到写入前的状态：原本没有的键要删掉，不能留个 None 在里面
            try:
                if old is _MISSING:
                    del raw[key]
                else:
                    raw[key] = old
            except Exception:  # noqa: BLE001
                pass
            out["value"] = None if old is _MISSING else old
            out["error"] = f"未保存（已回滚）: {exc}"
            return out
        out["value"] = coerced
        out["persisted"] = True
    return out


__all__ = [
    "WRITABLE_KEYS",
    "coerce_value",
    "save_config",
    "apply_conf",
]
