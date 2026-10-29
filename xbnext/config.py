# -*- coding: utf-8 -*-
"""配置读取。

- 运行时读 AstrBot 传进来的 ``config``（``AstrBotConfig``，行为接近 dict）；
- 配置项缺失时回退到 ``xbnext/schema.json`` 的 ``default``；
- schema 再缺失时回退到调用方给的 ``default``。

**这样即使插件配置页还没生成过、或用户用了旧版 schema，行为也不会跑飞。**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"


def _load_schema() -> dict:
    if _SCHEMA_PATH.exists():
        try:
            data = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
    return {}


#: 配置 schema（含每个键的 type / default / condition）
SCHEMA: dict = _load_schema()


def schema_default(key: str) -> Any:
    """取 schema 里声明的默认值；没有则返回 ``None``。"""
    item = SCHEMA.get(key)
    if isinstance(item, dict):
        return item.get("default")
    return None


class LocalAstrBotConfig:
    """当 AstrBot 宿主未提供 AstrBotConfig（如根目录无 xbnext/schema.json 时传入 None）
    时的独立本地持久化配置对象。

    完全对齐 AstrBotConfig 鸭子类型（dict 读写 + save_config / save_config_async），
    确保即使根目录不放 xbnext/schema.json（让原生面板保持『这个插件没有其他配置项』），
    WebUI 写入的配置依然能正确持久化落盘并热生效，不会因 None 报错导致开关自动回滚。
    """

    def __init__(self, file_path: Path, initial: Any = None):
        self._path = Path(file_path)
        self._data: dict = {}
        self._load()
        if isinstance(initial, dict):
            for k, v in initial.items():
                if v is not None and k not in self._data:
                    self._data[k] = v

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._data.update(data)
            except Exception:
                pass

    def save_config(self) -> bool:
        """同步落盘配置（原子写入）。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            import os
            tmp = self._path.with_name(f"config.json.tmp.{os.getpid()}.{os.urandom(4).hex()}")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
            return True
        except Exception:
            try:
                self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
                return True
            except Exception:
                return False

    async def save_config_async(self) -> bool:
        """异步落盘配置。"""
        import asyncio
        return await asyncio.to_thread(self.save_config)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        self._data.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class Config:
    """插件配置的只读视图（直接包在 AstrBot 传入的配置对象上，热改即生效）。"""

    __slots__ = ("_raw",)

    def __init__(self, raw: Any = None):
        self._raw = raw

    # -- 底层 ---------------------------------------------------------
    @property
    def raw(self) -> Any:
        """原始配置对象（可能是 AstrBotConfig），WebUI 需要时直接用。"""
        return self._raw

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

    def list(self, key: str, default: Any = None) -> list:
        """读列表项；非列表做类型兼容转换。"""
        try:
            val = self.get(key, default)
            if isinstance(val, (list, tuple)):
                return list(val)
            if isinstance(val, str):
                import re
                return [x.strip() for x in re.split(r"[,;，；\s]+", val) if x.strip()]
            if default is not None:
                return list(default) if isinstance(default, (list, tuple)) else []
            return []
        except Exception:  # noqa: BLE001
            return []

    def enabled(self, key: str) -> bool:
        """功能总开关的统一读法（``enable_*`` 都走这里）。"""
        return self.bool(key, bool(schema_default(key)))

    def group_features_map(self) -> dict:
        """读取按群独立功能开关配置字典（``group_features``）。"""
        val = self.get("group_features", {})
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:  # noqa: BLE001
                val = {}
        return val if isinstance(val, dict) else {}

    def is_platform_allowed(self, platform: str = "") -> bool:
        """检查指定平台是否允许运行本插件（支持 platform_preset 下拉预设与 allowed_platforms 自定义）。"""
        preset = self.text("platform_preset", "").strip().lower()
        p = (platform or "").strip().lower()
        if preset == "all":
            return True
        if preset in ("onebot", "qq"):
            if not p:
                return True
            return p in ("onebot", "aiocqhttp", "aihttpcq", "cqhttp", "onebot11", "onebot_v11")
        if preset in ("onebot_matrix", "onebot,matrix", "matrix_onebot"):
            if not p:
                return True
            return p in ("onebot", "aiocqhttp", "aihttpcq", "cqhttp", "onebot11", "onebot_v11", "matrix")

        allowed = self.text("allowed_platforms", "onebot, aiocqhttp").strip()
        if not allowed or allowed.lower() in ("all", "*", "any"):
            return True
        import re as _re
        tokens = [t.strip().lower() for t in _re.split(r"[,，、;；\s]+", allowed) if t.strip()]
        if not p:
            return True
        if "all" in tokens or "*" in tokens:
            return True
        if p in tokens:
            return True
        if p in ("aiocqhttp", "aihttpcq", "cqhttp", "onebot11", "onebot_v11") and ("onebot" in tokens or "aiocqhttp" in tokens):
            return True
        if p == "onebot" and "aiocqhttp" in tokens:
            return True
        return False

    def is_group_allowed(self, gid: str) -> bool:
        """检查指定群号/房间是否启用本插件（综合 group_mode / group_list 与 group_features[gid].enabled）。"""
        gid = str(gid or "").strip()
        if not gid:
            val = self.get("enable_private")
            if val is not None:
                return bool(val)
            return True
        gmap = self.group_features_map()
        gconf = gmap.get(gid) or gmap.get(gid.lower())
        if isinstance(gconf, dict) and "enabled" in gconf and gconf["enabled"] is not None:
            return bool(gconf["enabled"])
        mode = self.text("group_mode", "all").strip().lower() or "all"
        if mode == "all":
            return True
        raw_list = self.text("group_list", "")
        import re as _re
        tokens = [t.strip() for t in _re.split(r"[,，、;；\s]+", raw_list) if t.strip()]
        matched = False
        gid_lower = gid.lower()
        for t in tokens:
            t_clean = t.strip()
            # 兼容原生群号/房间号（含 !xxx、纯数字、带符号）、带平台前缀（如 matrix:!xxx）
            if t_clean == gid or t_clean.lower() == gid_lower:
                matched = True
                break
            if t_clean.endswith(":" + gid) or t_clean.lower().endswith(":" + gid_lower):
                matched = True
                break
            if gid.isdigit() and _re.sub(r"\D+", "", t_clean) == gid:
                matched = True
                break
        if mode == "whitelist":
            return matched
        if mode in ("disabled", "off", "none", "closed"):
            return False
        return True

    def enabled_for_group(self, key: str, gid: str = "", platform: str = "") -> bool:
        """结合平台白名单、私聊总开关、群聊总开关与分群单项功能开关判定 ``key`` 是否生效。"""
        if platform and not self.is_platform_allowed(platform):
            return False
        gid = str(gid or "").strip()
        if not gid:
            if not self.is_group_allowed(""):
                return False
            return self.enabled(key)
        if not self.is_group_allowed(gid):
            return False
        gmap = self.group_features_map()
        gconf = gmap.get(gid) or gmap.get(gid.lower())
        if isinstance(gconf, dict) and key in gconf and gconf[key] is not None:
            v = gconf[key]
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)
        return self.enabled(key)

    def as_dict(self) -> dict:
        """导出所有 schema 键的当前值，给 WebUI 状态面板用。"""
        out: dict = {}
        for key in SCHEMA:
            if key == "group_features":
                out[key] = self.group_features_map()
            else:
                out[key] = self.get(key, schema_default(key))
        return out


__all__ = ["Config", "LocalAstrBotConfig", "SCHEMA", "schema_default"]
