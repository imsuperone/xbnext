# -*- coding: utf-8 -*-
"""开关对账与 AstrNa 共存检测。

两条职责：

1. :func:`resolve` —— 把配置摊平成 ``{功能键: 是否启用}``，
   供 runtime 每请求对账（配置热改无需重启）。
2. :func:`detect_astrna` —— 尽力探测同装的 AstrNa 及其开关，
   决定 XBNEXT 是否让路。**探测失败一律当"未安装"处理，绝不抛异常。**

冲突矩阵见 aidoc/02-架构设计.md §6。
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, Optional

from .config import SCHEMA, Config

#: XBNEXT 功能总开关（顺序 = 注入执行顺序，与 features 注册表一致）
FEATURE_KEYS = (
    "enable_quote_clean",  # R2 quote_clean
    "enable_face_translate",  # R3 face_translate
    "enable_reply_attribution",  # R1 reply_attribution
    "enable_user_profile",  # R4 user_profile
)

#: XBNEXT 功能键 → AstrNa 对应开关（值为 True 表示该功能要让路）
ASTRNA_CONFLICTS: Dict[str, str] = {
    "enable_reply_attribution": "optimize_reply_target_history",
}

_ASTRNA_PACKAGE_CANDIDATES = (
    "astrbot_plugin_AstrNa",
    "astrna",
)


def resolve(conf: Config) -> Dict[str, bool]:
    """把配置摊平成功能开关表。"""
    return {key: conf.enabled(key) for key in FEATURE_KEYS}


def detect_astrna() -> Dict[str, Any]:
    """尽力探测 AstrNa 是否安装、其关键开关状态。

    返回结构固定（调用方不需要判空）::

        {"installed": bool, "switches": {..}, "error": str|None}

    任何失败都降级为 ``installed=False`` —— 检测只是优化，不是前置条件。
    """
    info: Dict[str, Any] = {"installed": False, "switches": {}, "error": None}
    pkg = _find_astrna_package()
    if pkg is None:
        return info
    info["installed"] = True
    info["package"] = pkg
    # 从已加载模块里读它的配置；读不到就算了
    try:
        mod = sys.modules.get(pkg)
        cfg = getattr(mod, "config", None) if mod else None
        if isinstance(cfg, dict):
            for key in ASTRNA_CONFLICTS.values():
                if key in cfg:
                    info["switches"][key] = bool(cfg.get(key))
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"read config failed: {exc}"
    return info


def _find_astrna_package() -> Optional[str]:
    """找 AstrNa 的包名；找不到返回 ``None``。"""
    try:
        import importlib.util

        for name in _ASTRNA_PACKAGE_CANDIDATES:
            try:
                if importlib.util.find_spec(name) is not None:
                    return name
            except Exception:  # noqa: BLE001  查找失败继续下一个候选
                continue
    except Exception:  # noqa: BLE001
        pass
    for name in _ASTRNA_PACKAGE_CANDIDATES:
        if name in sys.modules:
            return name
    return None


def conflict_for(conf: Config, key: str, astrna: Any) -> Optional[str]:
    """返回导致 ``key`` 让路的原因；无冲突返回 ``None``。

    :param astrna: :func:`detect_astrna` 的返回值
    """
    try:
        if not isinstance(astrna, dict) or not astrna.get("installed"):
            return None
        astrna_key = ASTRNA_CONFLICTS.get(key)
        if not astrna_key:
            return None
        switches = astrna.get("switches") or {}
        if switches.get(astrna_key):
            return f"AstrNa 已开启 {astrna_key}"
        # 装了但读不到开关状态 → 保守让路，避免重复注入
        if astrna_key not in switches:
            return f"检测到 AstrNa（{astrna.get('package')}）且开关状态未知"
    except Exception:  # noqa: BLE001
        return None
    return None


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
    "FEATURE_KEYS",
    "ASTRNA_CONFLICTS",
    "WRITABLE_KEYS",
    "resolve",
    "detect_astrna",
    "conflict_for",
    "coerce_value",
    "save_config",
    "apply_conf",
]
