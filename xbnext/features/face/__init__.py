# -*- coding: utf-8 -*-
"""R3 · QQ 表情翻译（enable_face_translate，默认开）。

根因：aiocqhttp 适配器把 ``face`` 排除在 ``message_str`` 外、``mface``
直接丢弃，而 ``req.prompt = event.message_str`` ⇒ 模型收不到任何表情。

落地（P2）：

1. 从消息链（``event.message`` / ``event.message_obj.message`` /
   ``event.get_messages()``）抽 ``Face`` / ``Mface`` / ``Rps`` / ``Dice``；
2. 从 ``event.message_obj.raw_message``（OneBot 原始段）补捞 ``mface`` ——
   它根本没进消息链，只能从原始载荷拿 ``summary``（表情中文名）；
3. 结果通过 ``ctx.inject()`` 以 temp part 追加（不写历史）；
4. ``data.py`` 的权威表由 ``aidoc/tools/gen_face_table.py`` 从上游抓取生成。
5. **纯表情消息**（正文为空）走 :meth:`FaceFeature.on_adapter_message`
   早期钩子直接补写 ``event.message_str`` —— 否则 core 会判定
   ``skip llm request: empty message``，LLM 压根不被调用。
6. **权威表运行时自动更新**（``face_auto_update``，默认开）：
   ``on_load`` 启动后台循环 —— 从未更新过则启动约 1 分钟后先拉一次，
   之后按 ``face_update_time``（默认 ``04:30``）每天从 QFace
   ``_index.json`` 拉缺口补进 overlay（存插件 KV，内置表优先、只补缺）；
   门禁只挡拉取、循环常驻（配置热开即时可生效），失败只打 WARNING，
   **异常一律不上抛**（AstrBot 官方原则 4）。详见 :mod:`.updater`。

**取名优先级**：段自带 ``summary`` > 内置 ID 表 > ``[表情:ID123]`` /
``[表情:key...]`` —— 永远兜底，**绝不静默丢弃**（静默丢弃就是现状 bug）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..base import Feature
from . import service, updater

#: 非消息段对象（例如 dict 的类名）走 dict 分支
_DICT_TYPES = ("dict", "dictproxy", "MappingProxyType", "mappingproxy")
_MFACE_TYPES = ("mface", "market_face", "marketface")


def _seg_type_name(seg: Any) -> str:
    """取段的类型名（小写；对象用类名，dict 用 ``type`` 字段）。"""
    if isinstance(seg, dict) or type(seg).__name__ in _DICT_TYPES:
        return str(seg.get("type") or "").lower()
    return type(seg).__name__.lower()


def _seg_data(seg: Any) -> Any:
    """取段的 ``data`` 字典；对象没有 ``data`` 时返回 ``{}``。"""
    if isinstance(seg, dict):
        return seg.get("data") or {}
    data = getattr(seg, "data", None)
    return data if isinstance(data, dict) else {}


def _first(mapping: Any, *keys: str) -> Any:
    """按顺序取第一个非空字段。"""
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        v = mapping.get(k)
        if v not in (None, ""):
            return v
    return None


def _token_from_obj(seg: Any) -> Optional[Tuple[str, Any]]:
    """把一个消息段对象压成 ``(kind, code)``；不相关就返回 ``None``。"""
    t = _seg_type_name(seg)
    if t == "face":
        code = _first(_seg_data(seg), "id", "face_id", "faceId")
        if code is None:
            code = getattr(seg, "id", None)
        return (service.KIND_FACE, code) if code not in (None, "") else None
    if t in _MFACE_TYPES:
        # summary（表情中文名）优先 —— mface 没有权威 key 表
        summary = _first(_seg_data(seg), "summary", "name", "emoji_name", "face_name")
        if summary is None:
            summary = _first(seg, "summary") if isinstance(seg, dict) else None
        if summary is None:
            summary = getattr(seg, "summary", None) or getattr(seg, "name", None)
        if summary and service.clean_summary(summary):
            return (service.KIND_SUMMARY, summary)
        key = _first(_seg_data(seg), "emoji_id", "key", "id")
        if key is None:
            key = getattr(seg, "emoji_id", None) or getattr(seg, "key", None)
        if key in (None, ""):
            key = getattr(seg, "id", None)
        return (service.KIND_MFACE, key) if key not in (None, "") else None
    if t in ("rps", "dice"):
        data = _seg_data(seg)
        result = _first(data, "result", "resultId", "result_id")
        if result is None:
            result = getattr(seg, "result", None)
        if result in (None, ""):
            return None
        return (service.KIND_SUMMARY, f"{t} {result}")
    return None


class FaceFeature(Feature):
    """把 QQ 自带表情翻译成模型读得懂的方括号短语。"""

    key = "enable_face_translate"
    name = "QQ 表情翻译"
    description = "把 face / mface 翻译成 [表情:得意] 类文字描述追加进本轮请求。"
    order = 30
    #: 需要在 core 判定「空消息」之前改写 ``event.message_str``
    uses_adapter_hook = True

    def __init__(self) -> None:
        #: 后台更新任务（FEATURES 是模块级单例，on_load 前先掐旧任务）
        self._task: Optional[asyncio.Task] = None
        self._runtime: Optional[Any] = None
        self._log: Optional[Any] = None
        #: 上次成功更新时间（ISO 字符串）；``None`` = 从未拉过 → 首拉名额
        self._updated_at: Optional[str] = None

    # -- 生命周期：权威表自动更新 --------------------------------------
    async def on_load(self, runtime: Any) -> None:
        """恢复 KV 里的 overlay，并启动后台更新循环（async：``runtime._call`` 会 await）。"""
        self._close_task()
        self._runtime = runtime
        self._log = getattr(runtime, "log", None)
        stored: Any = None
        try:
            stored = await runtime.kv.get_dict(updater.OVERLAY_KV_KEY)
        except Exception as exc:  # noqa: BLE001
            self._emit("warning", f"读取表情 overlay KV 失败：{exc!r}")
        updater.overlay_from_stored(stored)
        self._updated_at = updater.updated_at_from_stored(stored)
        try:
            self._task = asyncio.create_task(self._update_loop())
        except Exception as exc:  # noqa: BLE001
            self._task = None
            self._emit("warning", f"启动表情更新任务失败：{exc!r}")

    def on_unload(self) -> None:
        """取消更新任务（sync；保留 ``_updated_at``，热重载不丢首拉名额判断）。"""
        self._close_task()
        self._runtime = None

    def _close_task(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        try:
            if not task.done():
                task.cancel()
        except Exception:  # noqa: BLE001  跨 loop / 已关 loop 的任务取消失败不追
            pass
        # 绝不 await：任务可能属于另一个已关闭的事件循环

    def stats(self) -> Dict[str, Any]:
        """状态卡数据源（P17 · D）：内置/补充条数 + 上次更新时间（只读）。"""
        return updater.stats(self._updated_at)

    def _should_pull(self, runtime: Any) -> bool:
        """门禁：R3 开启且 ``face_auto_update`` 开着才允许拉取。"""
        if runtime is None:
            return False
        try:
            conf = runtime.conf
            return conf.enabled("enable_face_translate") and conf.bool(
                "face_auto_update", True
            )
        except Exception:  # noqa: BLE001
            return False

    async def _update_loop(self) -> None:
        """后台更新循环：首拉 60s 后先试一次，之后每天 ``face_update_time`` 拉一次。

        红线：所有睡眠 ≥ 60s（:data:`~.updater.FIRST_RUN_DELAY` /
        :data:`~.updater.SLEEP_SEGMENT` / ``max(delay, 60)``），且**首次动作必是
        sleep** —— 单测里 ``asyncio.run`` 退出时任务被取消，永不触网；
        异常一律不上抛（AstrBot 原则 4），``CancelledError`` 原样抛出。
        """
        runtime = self._runtime
        if runtime is None:
            return
        first = not self._updated_at
        while True:
            try:
                if first:
                    await asyncio.sleep(float(updater.FIRST_RUN_DELAY))
                else:
                    delay = updater.next_run_delay(
                        datetime.now(),
                        runtime.conf.text(
                            "face_update_time", updater.DEFAULT_UPDATE_TIME
                        ),
                    )
                    if delay > updater.SLEEP_SEGMENT:
                        # 分段睡：醒来重读配置，热改最迟 1 小时生效
                        await asyncio.sleep(float(updater.SLEEP_SEGMENT))
                        continue
                    await asyncio.sleep(max(delay, float(updater.FIRST_RUN_DELAY)))
                if not self._should_pull(runtime):
                    # 门禁挡 fetch：first 保留 → 60s 后重查（支持配置热开）
                    continue
                first = False  # 首拉名额：成败皆算，失败等下一排程点
                await self._pull_once(runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._emit("warning", f"表情更新循环异常：{exc!r}")
                await asyncio.sleep(60)

    async def _pull_once(self, runtime: Any) -> bool:
        """拉一次权威源并落 KV；成功 ``True``，失败 WARNING 后 ``False``（不动 ``_updated_at``）。"""
        if runtime is None:
            return False
        prev = set(updater.get_overlay())
        try:
            await updater.pull_overlay()
        except Exception as exc:  # noqa: BLE001
            self._emit("warning", f"拉取表情权威源失败：{exc!r}")
            return False
        try:
            iso = datetime.now().isoformat(timespec="seconds")
            stored = {"ids": updater.overlay_to_stored(), "updated_at": iso}
            if not await runtime.kv.set(updater.OVERLAY_KV_KEY, stored):
                self._emit("warning", "表情 overlay 写 KV 失败，本次会话有效")
            self._updated_at = iso
            added = updater.added_since(prev)
            if added:
                self._emit("info", f"表情表更新完成，新增 {added} 个 ID")
            else:
                self._emit("debug", "表情表更新完成，无变化")
            return True
        except Exception as exc:  # noqa: BLE001
            self._emit("warning", f"保存表情 overlay 失败：{exc!r}")
            return False

    def _emit(self, level: str, message: str) -> None:
        """带 ``[XBNEXT] `` 前缀写日志；``_log`` 缺失 / 方法缺失 / 写失败都安全跳过。"""
        method = getattr(self._log, level, None) if self._log is not None else None
        if callable(method):
            try:
                method(f"[XBNEXT] {message}")
            except Exception:  # noqa: BLE001
                pass

    # -- 早期钩子：纯表情消息的救生索 ------------------------------------
    def on_adapter_message(self, ctx) -> None:
        """**纯表情消息**：把翻译结果补写进 ``event.message_str``。

        根因（aidoc/01 §R3 第二版）：aiocqhttp 适配器把 ``face`` 排除在
        ``message_str`` 外、``mface`` 段直接丢弃、@首个到自己的 ``At``
        也不进正文 —— 于是 ``@bot + 纯表情`` 的 ``message_str`` 是 ``""``，
        core 的 ``internal.py`` 判定 ``has_valid_message=False`` 且表情不算
        媒体内容 ⇒ ``skip llm request: empty message``，**LLM 根本不被调用**。

        因此在 ``event_message_type(ALL)`` 早期钩子里把正文补上，
        让 core 照常走到 ``req.prompt = event.message_str``。

        守卫（缺一不可）：

        - ``event.message_str`` strip 后**为空** —— 非空走
          :meth:`on_llm_request` 的 note 注入路径，绝不改用户正文；
        - ``event.is_at_or_wake_command`` 为真 —— 没被 @/唤醒前缀命中的
          消息本来就不会走默认 LLM，补了也白补（不能让 bot 回每条消息）；
        - 确实抽得出表情 token。
        """
        event = ctx.event
        if event is None:
            return
        try:
            raw = getattr(event, "message_str", None)
            if not isinstance(raw, str) or raw.strip():
                return
            if not getattr(event, "is_at_or_wake_command", False):
                return
            tokens = self._extract_tokens(ctx)
            if not tokens:
                return
            fmt = ctx.conf.text("face_format", service.DEFAULT_FORMAT)
            parts = service.translate_all(tokens, fmt=fmt)
            if not parts:
                return
            text = " ".join(parts)
            event.message_str = text
            msg_obj = getattr(event, "message_obj", None)
            if msg_obj is not None and not str(
                getattr(msg_obj, "message_str", "") or ""
            ).strip():
                msg_obj.message_str = text
            ctx.note(f"face_translate 补写 message_str：{text}")
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"face_translate 补写失败：{exc!r}")

    def on_llm_request(self, ctx) -> None:
        tokens = self._extract_tokens(ctx)
        if not tokens:
            return
        fmt = ctx.conf.text("face_format", service.DEFAULT_FORMAT)
        parts = service.translate_all(tokens, fmt=fmt)
        if not parts:
            return
        # 正文里已经写出来的片段就不重复注入（例如有人把 [表情:得意] 打成文字）
        prompt = ctx.prompt()
        if prompt:
            parts = [p for p in parts if p not in prompt]
        note = service.note_from_parts(parts)
        if not note:
            return
        if ctx.inject(note):
            ctx.note(f"face_translate 注入 {len(parts)} 个表情片段")

    # -- 抽取 ---------------------------------------------------------
    def _extract_tokens(self, ctx) -> List[Tuple[str, Any]]:
        """从事件里抽出表情片段（消息链 + OneBot 原始载荷）。"""
        event = ctx.event
        if event is None:
            return []
        try:
            tokens: List[Tuple[str, Any]] = []
            seen_sources: List[int] = []
            for source in self._chain_sources(event):
                if id(source) in seen_sources:
                    continue
                seen_sources.append(id(source))
                tokens.extend(self._walk_chain(source))
            for source in self._raw_sources(event):
                if id(source) in seen_sources:
                    continue
                seen_sources.append(id(source))
                tokens.extend(service.tokens_from_raw(source))
            return tokens
        except Exception as exc:  # noqa: BLE001
            ctx.note(f"face_translate 抽取失败：{exc!r}")
            return []

    @staticmethod
    def _chain_sources(event: Any) -> List[Any]:
        """列出可能装着消息链的容器（顺序即优先级）。"""
        out: List[Any] = []
        for attr in ("message", "message_obj"):
            try:
                v = getattr(event, attr, None)
            except Exception:  # noqa: BLE001
                v = None
            if v is None:
                continue
            if attr == "message_obj":
                inner = getattr(v, "message", None)
                if inner is not None:
                    out.append(inner)
            else:
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

    @staticmethod
    def _raw_sources(event: Any) -> List[Any]:
        """列出可能装着 OneBot 原始载荷的字段。"""
        out: List[Any] = []
        msg_obj = getattr(event, "message_obj", None)
        for owner in (msg_obj, event):
            if owner is None:
                continue
            for attr in ("raw_message", "raw_msg", "origin_message"):
                try:
                    v = getattr(owner, attr, None)
                except Exception:  # noqa: BLE001
                    v = None
                if v not in (None, ""):
                    out.append(v)
        return out

    @staticmethod
    def _walk_chain(chain: Any) -> List[Tuple[str, Any]]:
        """遍历消息链，抽出 ``(kind, code)``；识别不了就跳过。"""
        if chain is None:
            return []
        tokens: List[Tuple[str, Any]] = []
        try:
            iterator = chain if hasattr(chain, "__iter__") else [chain]
            for seg in iterator:
                tok = _token_from_obj(seg)
                if tok is not None:
                    tokens.append(tok)
        except Exception:  # noqa: BLE001
            return tokens
        return tokens


__all__ = ["FaceFeature"]
