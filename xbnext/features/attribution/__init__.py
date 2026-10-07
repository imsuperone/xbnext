# -*- coding: utf-8 -*-
"""R1 · 回复指向与身份归属（enable_reply_attribution，默认**关**）。

问题：A 先问 bot，之后 bot 把 A 的话套到 B 头上。
根因：会话历史里 user 消息不带发言人标记，群聊上下文块也只按
``[昵称/时间]`` 平铺；bot 回复"回给谁"没有落库索引。

两条腿（详见 ``aidoc/01-需求与根因.md`` §R1）：

1. **落库**：``after_message_sent`` 记下"这条回复是回给谁的"——
   目标取**当前发言人**（bot 的回复就是在回应触发它的那条消息），
   另存触发消息的 ``message_id`` 与文本 hash，供后续按引用精确匹配；
2. **注入**：下一轮 ``on_llm_request`` 生成三方区分说明
   （当前发言人 / 被引用消息发送者 / bot 最近回复对象），
   由 :mod:`xbnext.features.attribution.service` 拼装，``ctx.inject()`` 走 temp part。

**红线**：不发明标记协议、身份字段过 ``sanitize``、歧义即放弃（aidoc/01 §R1）。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, List, Optional, Tuple

from ..base import Feature
from . import service
from .store import DEFAULT_LIMIT, ReplyTargetStore

#: 回溯轮数上限（配置越界时收敛到这里）
MAX_DEPTH = 10


def _seg_type(seg: Any) -> str:
    """段类型名（小写）；dict 用 ``type`` 字段，对象用类名。"""
    if isinstance(seg, dict):
        return str(seg.get("type") or "").lower()
    return type(seg).__name__.lower()


def _seg_data(seg: Any) -> Any:
    if isinstance(seg, dict):
        return seg.get("data") or {}
    data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


def _field(seg: Any, *names: str) -> Any:
    """从段里按顺序取第一个非空字段（先看 ``data``，再看对象属性）。"""
    data = _seg_data(seg)
    for n in names:
        if isinstance(data, dict) and data.get(n) not in (None, ""):
            return data.get(n)
    for n in names:
        v = getattr(seg, n, None)
        if v not in (None, ""):
            return v
    return None


def chain_of(event: Any) -> List[Any]:
    """列出事件里的消息段（多个可能的来源，顺序即优先级，可能重复）。"""
    out: List[Any] = []
    for attr in ("message", "message_obj"):
        v = getattr(event, attr, None)
        if v is None:
            continue
        if attr == "message_obj":
            v = getattr(v, "message", None)
            if v is None:
                continue
        out.append(v)
    getter = getattr(event, "get_messages", None)
    if callable(getter):
        try:
            v = getter()
        except Exception:  # noqa: BLE001
            v = None
        if v is not None:
            out.append(v)
    return out


def sender_of(event: Any) -> Tuple[str, str]:
    """当前发言人 ``(id, 昵称)``；取不到的位置留空串，**不猜**。"""
    uid = ""
    name = ""
    try:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        uid = str(getattr(sender, "user_id", "") or "")
        name = str(getattr(sender, "nickname", "") or "")
    except Exception:  # noqa: BLE001
        uid, name = "", ""
    if not uid:
        fn = getattr(event, "get_sender_id", None)
        if callable(fn):
            try:
                uid = str(fn() or "")
            except Exception:  # noqa: BLE001
                uid = ""
    if not name:
        fn = getattr(event, "get_sender_name", None)
        if callable(fn):
            try:
                name = str(fn() or "")
            except Exception:  # noqa: BLE001
                name = ""
    return uid, name


def self_id_of(event: Any) -> str:
    """bot 自己的 ID；取不到返回 ``""``（后续就不做自我过滤）。"""
    for owner in (getattr(event, "message_obj", None), event):
        if owner is None:
            continue
        for attr in ("self_id", "bot_id"):
            v = getattr(owner, attr, None)
            if v not in (None, ""):
                return str(v)
    fn = getattr(event, "get_self_id", None) or getattr(event, "get_bot_id", None)
    if callable(fn):
        try:
            v = fn()
            if v not in (None, ""):
                return str(v)
        except Exception:  # noqa: BLE001
            pass
    return ""


def message_id_of(event: Any) -> str:
    """触发消息的 ``message_id``（Reply 段匹配的主键）；没有就空串。"""
    for owner in (getattr(event, "message_obj", None), event):
        if owner is None:
            continue
        for attr in ("message_id", "msg_id", "id"):
            v = getattr(owner, attr, None)
            if isinstance(v, (str, int)) and str(v) != "":
                return str(v)
    return ""


def message_text(event: Any) -> str:
    """触发消息的纯文本（用于 hash 兜底）。"""
    for attr in ("message_str", "raw_message"):
        v = getattr(event, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    try:
        obj = getattr(event, "message_obj", None)
        v = getattr(obj, "message_str", None)
        if isinstance(v, str) and v.strip():
            return v
    except Exception:  # noqa: BLE001
        pass
    return ""


def session_scope(event: Any) -> str:
    """会话类型：``group`` / ``channel`` / ``private`` / ``""``。"""
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    parts = [p for p in umo.split("/") if p]
    if len(parts) >= 2:
        kind = parts[1].lower()
        if "group" in kind:
            return "group"
        if "channel" in kind or "guild" in kind:
            return "channel"
        if kind in ("friend", "c2c", "private", "single"):
            return "private"
    sid = ""
    fn = getattr(event, "get_session_id", None)
    if callable(fn):
        try:
            sid = str(fn() or "")
        except Exception:  # noqa: BLE001
            sid = ""
    low = sid.lower()
    if "groupmessage" in low or "/group/" in low:
        return "group"
    if "channelmessage" in low or "/channel/" in low:
        return "channel"
    if "c2c" in low or "friend" in low:
        return "private"
    return ""


def quoted_sender(event: Any) -> Tuple[str, str]:
    """本轮**被引用消息**的发送者 ``(id, 昵称)``；拿不到就返回空对。

    aiocqhttp 会把 Reply 构造成带 ``sender_id`` / ``sender_nickname`` 的组件；
    QQ 官方 Bot 的引用**缺被引用者信息**（aidoc/01 §R1），此时返回空对 ——
    **绝不猜**，猜错正是本需求要修的 bug。
    """
    for container in chain_of(event):
        try:
            iterator = container if hasattr(container, "__iter__") else [container]
            for seg in iterator:
                if _seg_type(seg) not in ("reply", "quote"):
                    continue
                sid = _field(seg, "sender_id", "sender_user_id", "qq")
                name = _field(seg, "sender_nickname", "sender_name", "nickname")
                if sid or name:
                    return str(sid or ""), str(name or "")
                return "", ""  # 有引用但拿不到被引用者 → 放弃，不猜
        except Exception:  # noqa: BLE001
            continue
    return "", ""


def quoted_message_id(event: Any) -> str:
    """本轮被引用消息的 ``message_id``（用于精确命中 store）。"""
    for container in chain_of(event):
        try:
            iterator = container if hasattr(container, "__iter__") else [container]
            for seg in iterator:
                if _seg_type(seg) not in ("reply", "quote"):
                    continue
                v = _field(seg, "id", "message_id", "msg_id")
                return str(v) if v not in (None, "") else ""
        except Exception:  # noqa: BLE001
            continue
    return ""


def at_targets(event: Any, self_id: str = "") -> List[Tuple[str, str]]:
    """本轮 @ 到的对象（排除 bot 自己与 ``@全体成员``）。"""
    out: List[Tuple[str, str]] = []
    seen = set()
    for container in chain_of(event):
        try:
            iterator = container if hasattr(container, "__iter__") else [container]
            for seg in iterator:
                if _seg_type(seg) != "at":
                    continue
                raw = _field(seg, "qq", "user_id", "target", "id")
                if raw in (None, ""):
                    continue
                sid = str(raw)
                if sid == "all" or sid.lower() == "atall":
                    continue
                if self_id and sid == str(self_id):
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                name = _field(seg, "name", "nickname", "display_name")
                out.append((sid, str(name or "")))
        except Exception:  # noqa: BLE001
            continue
    return out


class AttributionFeature(Feature):
    """给 bot 的回复建立"回给谁"索引并在下一轮说明。"""

    key = "enable_reply_attribution"
    name = "回复指向索引"
    description = "记录 bot 每条回复回给谁，避免把 A 问过的话套到 B 头上。"
    order = 40
    uses_sent_hook = True

    def __init__(self) -> None:
        self._store: Optional[ReplyTargetStore] = None

    # -- 生命周期 -----------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        limit = runtime.conf.int("reply_history_limit", DEFAULT_LIMIT)
        self._store = ReplyTargetStore(runtime.kv, limit=limit, logger=runtime.log)

    def on_unload(self) -> None:
        self._store = None

    # -- 钩子 ---------------------------------------------------------
    async def on_message_sent(self, ctx) -> None:
        """bot 回复已发出 → 记下"这条回复是回给谁的"。"""
        if self._store is None:
            return
        item = self._build_item(ctx)
        if not item:
            return
        ok = await self._store.push(self._umo(ctx), item)
        if ok:
            ctx.note(
                "reply_attribution 记录 "
                f"target={item.get('target_name') or item.get('target_id')} "
                f"scope={item.get('scope')}"
            )

    async def on_llm_request(self, ctx) -> None:
        """注入三方区分说明（当前发言人 / 被引用者 / bot 最近回复对象）。"""
        if self._store is None:
            return
        depth = ctx.conf.int("reply_scope_depth", 3)
        if depth <= 0:
            return
        depth = min(depth, MAX_DEPTH)
        event = ctx.event
        umo = self._umo(ctx)
        try:
            history = await self._store.load(umo)
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"reply_attribution 读取失败：{exc!r}")
            history = []

        matched = None
        qmid = quoted_message_id(event)
        if qmid:
            try:
                matched = await self._store.find(umo, message_id=qmid)
            except Exception as exc:  # noqa: BLE001
                ctx.note(f"reply_attribution 引用匹配失败：{exc!r}")

        sid = self_id_of(event)
        note = service.build_hint(
            current=sender_of(event),
            quoted=quoted_sender(event),
            ats=at_targets(event, sid),
            history=history,
            depth=depth,
            now=int(time.time()),
            matched=matched,
            self_id=sid,
        )
        if not note:
            ctx.note("reply_attribution 无需说明（没有可区分的三方信息）")
            return
        if ctx.inject(note):
            ctx.note(f"reply_attribution 注入 {len(note)} 字")

    # -- 工具 ---------------------------------------------------------
    @staticmethod
    def _umo(ctx) -> str:
        try:
            umo = getattr(ctx.event, "get_session_id", None)
            value = umo() if callable(umo) else ""
            return str(value or "default")
        except Exception:  # noqa: BLE001
            return "default"

    @staticmethod
    def _build_item(ctx) -> Optional[dict]:
        """从事件里压出最小记录；拿不到就返回 ``None``（不猜）。

        **目标 = 当前发言人**：bot 的这条回复就是在回应触发它的那条消息，
        所以"bot 回给谁"= 谁发的那条消息。被引用者 / 被 @ 者不作为目标 ——
        引用一个人不代表 bot 在跟他说话（aidoc/01 §1.4）。
        """
        event = ctx.event
        if event is None:
            return None
        uid, name = sender_of(event)
        self_id = self_id_of(event)
        if self_id and uid and str(uid) == str(self_id):
            return None  # bot 自己的消息不入库
        if not uid and not name:
            return None
        mid = message_id_of(event)
        text = message_text(event)
        try:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""
        except Exception:  # noqa: BLE001
            digest = ""
        if not uid and not mid and not digest:
            return None
        return {
            "target_id": uid,
            "target_name": name,
            "scope": session_scope(event),
            "message_id": mid,
            "hash": digest,
            "ts": int(time.time()),
        }


__all__ = [
    "AttributionFeature",
    "sender_of",
    "self_id_of",
    "message_id_of",
    "message_text",
    "session_scope",
    "quoted_sender",
    "quoted_message_id",
    "at_targets",
    "chain_of",
]
