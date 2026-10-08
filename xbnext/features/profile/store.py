# -*- coding: utf-8 -*-
"""R4 · 用户档案存储。

键（前缀由 :class:`~xbnext.storage.KV` 统一加）::

    xbnext:profile:<platform>:<gid>:<uid>   群聊（档案按群隔离：同一个人
                                            在不同群可以是不同的档案）
    xbnext:profile:<platform>:<uid>         私聊 / 升级前的旧数据（gid 为空
                                            时保持老键形，旧档案零迁移可用）

值::

    {
      "name":   "用户自己起的称呼",
      "facts":  "自述信息（身份、偏好、忌讳）",
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

from ... import log
from ...injector import sanitize, strip_xbnext

#: 允许的档案字段（多余字段一律丢弃，避免用户塞结构化指令进去）
#: 「口吻 / style」已随 P16 移除 —— 旧数据里的 style 在下次写入时被
#: ``normalize`` 顺手清掉，读取路径一律按本元组过滤、不会展示。
FIELDS = ("name", "facts")
DEFAULT_MAX_LEN = {"name": 32, "facts": 480}

#: 索引键：AstrBot 的插件 KV **没有"遍历所有键"的能力**（只有按键 get/put/delete），
#: WebUI 要列出全部档案就必须自己维护一份成员表。
INDEX_KEY = "profile:index"


def clean_gid(gid: Any) -> str:
    """归一化群号：去空白、去掉会破坏索引记号的 ``|``；取不到返回 ``""``。"""
    return str(gid or "").strip().replace("|", "")


def _token(platform: str, uid: Any, gid: Any = "") -> str:
    """索引里的成员记号：``<platform>|<gid>|<uid>``；gid 为空保持老格式
    ``<platform>|<uid>``（私聊 / 升级前的旧数据，两种都能被 ``list_all`` 解析）。"""
    gid = clean_gid(gid)
    if gid:
        return f"{platform or 'unknown'}|{gid}|{uid or 'unknown'}"
    return f"{platform or 'unknown'}|{uid or 'unknown'}"


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
        text = "\n".join(lines)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text
    except Exception:  # noqa: BLE001
        return ""


def key_of(platform: str, uid: Any, gid: Any = "") -> str:
    """拼出 KV 键（不含 ``xbnext:`` 前缀）；gid 为空保持老键形（私聊）。"""
    gid = clean_gid(gid)
    if gid:
        return f"profile:{platform or 'unknown'}:{gid}:{uid or 'unknown'}"
    return f"profile:{platform or 'unknown'}:{uid or 'unknown'}"


class ProfileStore:
    """用户档案的 KV 读写封装。"""

    def __init__(self, kv: Any, logger: Any = None):
        self._kv = kv
        self._log = logger

    async def get(self, platform: str, uid: Any, gid: Any = "") -> Dict[str, Any]:
        """读一份档案（按 scope 精确读，**不做跨 scope 回退**）；异常返回 ``{}``。"""
        try:
            data = await self._kv.get_dict(key_of(platform, uid, gid))
            return data if not is_empty(data) else {}
        except Exception as exc:  # noqa: BLE001
            self._warn(f"读取档案失败 {platform}/{gid or 'private'}/{uid}: {exc}")
            return {}

    async def set(
        self, platform: str, uid: Any, raw: Dict[str, Any], gid: Any = ""
    ) -> Dict[str, Any]:
        """写一份档案（先 sanitize）；返回实际写入内容。"""
        try:
            profile = normalize(raw)
            if not profile:
                return {}
            await self._kv.set(key_of(platform, uid, gid), profile)
            await self._index_add(platform, uid, gid)
            return profile
        except Exception as exc:  # noqa: BLE001
            self._warn(f"写入档案失败 {platform}/{gid or 'private'}/{uid}: {exc}")
            return {}

    async def delete(self, platform: str, uid: Any, gid: Any = "") -> bool:
        """删除一份档案（只删这个 scope，不影响其它群 / 私聊的同名档案）。"""
        try:
            ok = await self._kv.delete(key_of(platform, uid, gid))
            await self._index_remove(platform, uid, gid)
            return ok
        except Exception:  # noqa: BLE001
            return False

    async def list_all(self) -> List[Dict[str, Any]]:
        """列出全部档案（WebUI 档案页签用）。

        返回 ``[{"platform", "group", "uid", "profile", "updated"}, ...]``，
        按更新时间倒序。索引记号两种形态都能解析：
        ``platform|gid|uid``（分群）与 ``platform|uid``（私聊 / 旧数据，
        解析出 ``group=""``）。读索引 → 逐条回读 → 顺手把已不存在的成员
        从索引里剔除（自愈）。
        """
        try:
            tokens = await self._kv.get_list(INDEX_KEY)
        except Exception:  # noqa: BLE001
            tokens = []
        rows: List[Dict[str, Any]] = []
        seen = set()
        stale: List[str] = []
        for token in tokens if isinstance(tokens, list) else []:
            if not isinstance(token, str) or "|" not in token:
                continue
            platform, rest = token.split("|", 1)
            if "|" in rest:
                gid, uid = rest.split("|", 1)
            else:
                gid, uid = "", rest  # 老记号：无群维度
            if not uid or token in seen:
                continue
            seen.add(token)
            try:
                data = await self._kv.get_dict(key_of(platform, uid, gid))
            except Exception:  # noqa: BLE001
                continue
            if is_empty(data):
                stale.append(token)
                continue
            rows.append(
                {
                    "platform": platform,
                    "group": gid,
                    "uid": uid,
                    "profile": {f: data.get(f, "") for f in FIELDS},
                    "updated": int(data.get("updated") or 0),
                }
            )
        if stale:
            await self._index_write([t for t in tokens if t not in stale])
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    # -- 索引维护 -----------------------------------------------------
    async def _index_add(self, platform: str, uid: Any, gid: Any = "") -> None:
        token = _token(platform, uid, gid)
        tokens = await self._kv.get_list(INDEX_KEY)
        if token in tokens:
            return
        tokens.append(token)
        await self._kv.set(INDEX_KEY, tokens)

    async def _index_remove(self, platform: str, uid: Any, gid: Any = "") -> None:
        token = _token(platform, uid, gid)
        tokens = await self._kv.get_list(INDEX_KEY)
        if token not in tokens:
            return
        await self._kv.set(INDEX_KEY, [t for t in tokens if t != token])

    async def _index_write(self, tokens: List[str]) -> None:
        await self._kv.set(INDEX_KEY, [t for t in tokens if isinstance(t, str)])

    def _warn(self, message: str) -> None:
        """写日志（前缀与安全跳过统一走 :mod:`xbnext.log`）。"""
        log.emit(self._log, "warning", message)


__all__ = [
    "FIELDS",
    "DEFAULT_MAX_LEN",
    "normalize",
    "is_empty",
    "render",
    "clean_gid",
    "key_of",
    "ProfileStore",
]
