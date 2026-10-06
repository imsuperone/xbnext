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

import sys
from typing import Any, Dict, Optional

from .config import Config

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


__all__ = [
    "FEATURE_KEYS",
    "ASTRNA_CONFLICTS",
    "resolve",
    "detect_astrna",
    "conflict_for",
]
