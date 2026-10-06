# -*- coding: utf-8 -*-
"""配置读取。

- 运行时读 AstrBot 传进来的 ``config``（``AstrBotConfig``，行为接近 dict）；
- 配置项缺失时回退到 ``_conf_schema.json`` 的 ``default``；
- schema 再缺失时回退到调用方给的 ``default``。

**这样即使插件配置页还没生成过、或用户用了旧版 schema，行为也不会跑飞。**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"


def _load_schema() -> dict:
    try:
        data = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001  schema 读不到不能让插件起不来
        return {}


#: 配置 schema（含每个键的 type / default / condition）
SCHEMA: dict = _load_schema()


def schema_default(key: str) -> Any:
    """取 schema 里声明的默认值；没有则返回 ``None``。"""
    item = SCHEMA.get(key)
    if isinstance(item, dict):
        return item.get("default")
    return None


class Config:
    """插件配置的只读视图（热更新时调用 :meth:`reload` 换底）。"""

    __slots__ = ("_raw",)

    def __init__(self, raw: Any = None):
        self._raw = raw

    # -- 底层 ---------------------------------------------------------
    @property
    def raw(self) -> Any:
        """原始配置对象（可能是 AstrBotConfig），WebUI 需要时直接用。"""
        return self._raw

    def reload(self, raw: Any) -> None:
        """热替换配置对象。AstrBot 配置页保存后调用。"""
        self._raw = raw

    # -- 读取 ---------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """按优先级取值：配置对象 → schema default → 调用方 default。"""
        raw = self._raw
        if raw is not None:
            try:
                getter = getattr(raw, "get", None)
                if callable(getter):
                    value = getter(key, None)
                else:  # 兜底：某些实现只支持下标
                    value = raw[key]
            except Exception:  # noqa: BLE001
                value = None
            if value is not None:
                return value
        sd = schema_default(key)
        if sd is not None:
            return sd
        return default

    def bool(self, key: str, default: bool = False) -> bool:
        """读布尔项；非布尔值按 ``bool()`` 归一，异常回退。"""
        try:
            value = self.get(key, default)
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        except Exception:  # noqa: BLE001
            return bool(default)

    def int(self, key: str, default: int = 0) -> int:
        """读整数项；无法转换时回退。"""
        try:
            return int(self.get(key, default))
        except Exception:  # noqa: BLE001
            try:
                return int(default)
            except Exception:  # noqa: BLE001
                return 0

    def text(self, key: str, default: str = "") -> str:
        """读字符串项；非字符串转 str，``None`` 回退。"""
        try:
            value = self.get(key, default)
            if value is None:
                return str(default)
            return str(value)
        except Exception:  # noqa: BLE001
            return str(default)

    def enabled(self, key: str) -> bool:
        """功能总开关的统一读法（``enable_*`` 都走这里）。"""
        return self.bool(key, bool(schema_default(key)))

    def as_dict(self) -> dict:
        """导出所有 schema 键的当前值，给 WebUI 状态面板用。"""
        out: dict = {}
        for key in SCHEMA:
            out[key] = self.get(key, schema_default(key))
        return out

    def condition_ok(self, key: str) -> bool:
        """判断 schema 里声明的 ``condition`` 是否满足（子项是否该显示）。"""
        item = SCHEMA.get(key)
        if not isinstance(item, dict):
            return True
        cond = item.get("condition")
        if not isinstance(cond, Mapping):
            return True
        try:
            return all(self.get(k) == v for k, v in cond.items())
        except Exception:  # noqa: BLE001
            return True


__all__ = ["Config", "SCHEMA", "schema_default"]
