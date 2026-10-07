# -*- coding: utf-8 -*-
"""撤回取消请求（P16 一期掐请求 · P17 二期连带撤回复 · P19 三期确认询问）。

用户撤回**触发 LLM 的那条消息** ⇒ 取消这条消息的在飞请求，省一次白烧
的 token，也避免 bot 对着已经撤回的消息回一整段；若 bot 已经把回复
发出去了，**连带把回复也撤掉**（P17 二期，方案②接管本条发送）。

实现（纯插件侧，不改核心源码）：

- **登记**：早期钩子 ``event_message_type(ALL)`` 对每条进来的消息记
  ``message_id -> asyncio.current_task()``（钩子与整条管线同一个任务），
  任务完成时经 ``add_done_callback`` 自动摘表，登记表天然有界；
- **命中**：撤回 notice 走同一条早期钩子（aiocqhttp 把它转成
  ``message_str=""`` 的事件，core 不会拿它唤醒 LLM），查表
  ``task.cancel()``。撤回的是老消息 / 不存在的消息 ⇒ 查表落空，静默
  no-op；
- **取消落点**：``CancelledError`` 是 ``BaseException`` —— 沿途
  ``except Exception`` 全拦不住，直穿 ``scheduler.execute`` 到
  EventBus；总线 ``_on_task_done`` 对 ``task.cancelled()`` 直接
  return，**不弹报错、不发错误回执**。落在钩子窗口里会被 core 的
  ``call_event_hook`` 吞成一条 ERROR 日志（极小概率的噪音，接受）。

**二期 · 连带撤回复（P17）**：核心把 ``send_group_msg`` 返回的
``message_id`` 在最内层就丢掉，没有任何「发送后白拿 id」的钩子 ——
所以对**这一条事件实例**做发送接管（不改核心源码、不碰全局共享对象，
实例属性遮蔽类方法，事件生命周期一过自动失效）：

1. 早期钩子给本条事件挂 ``event.send`` 包装；
2. 包装复用核心 ``AiocqhttpMessageEvent._parse_onebot_json`` 解析段，
   自己调 ``send_group_msg / send_private_msg`` 拿到 ``message_id``
   记进 :mod:`.replies` 登记表（``trigger -> [reply_ids]``，有界）；
3. **解析不了 / 发送异常一律退回原方法发送** —— 宁可丢 id 也绝不丢
   回复；``CancelledError`` 摘表后原样上抛，不重发；
4. 撤回命中时从登记表取 id 调 ``delete_msg``（str→int 双试）；撤回时
   回复还在飞 ⇒ 标记补删，发送完成后由包装自己取全量 id 删掉
   （分段回复的多条一起删）。
   非 aiocqhttp 事件（没有 ``bot`` 实例）不装包装，行为与一期完全一致。

**三期 · 确认询问（P19，``enable_recall_confirm`` 默认关）**：开关
打开后撤回不再立刻动手 —— 先在原会话问一句（群里 @ 执行撤回的人），
**只有撤回者本人的回答算数**（会话 + ``operator_id`` 双匹配，别人的
回复不消费、照常走管线）；答「是」才掐请求 + 连带撤回复，答「否」
保留，30 秒不回答按老规矩自动取消。回答命中即 ``stop_event()`` 吞掉
（不唤醒 LLM、不进历史）；询问发不出去 ⇒ 回退老行为立刻取消。

范围：请求在飞 ⇒ 掐请求；回复已发 ⇒ 连带撤回；回复在途 ⇒ 补删；
确认开关打开时以上动作都要先过「撤回者本人回答 / 30 秒超时」这道闸。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from . import replies, service
from ..base import Feature


class _Awaiting:
    """一次等待确认的撤回：被撤消息 id → 撤回者 / 会话 / 挂起的定时器。"""

    __slots__ = ("trigger_id", "group_id", "operator_id", "event", "timer")

    def __init__(self, trigger_id: str, group_id: str, operator_id: str, event: Any) -> None:
        self.trigger_id = trigger_id
        #: 群撤回 = 群号；好友撤回 = 空串
        self.group_id = group_id
        #: 执行撤回的人（群管理员可撤别人的 message，operator ≠ user）
        self.operator_id = operator_id
        #: 撤回 notice 事件（超时 / 确认时用它的 bot 补删回复）
        self.event = event
        self.timer: Optional[asyncio.Task] = None


class RecallFeature(Feature):
    """见模块 docstring；适配器早期钩子 + 本条事件的发送接管。"""

    key = "enable_recall_cancel"
    name = "撤回取消请求"
    description = "撤回触发消息后，取消在飞请求并连带撤回 bot 已发出的回复"
    #: 守候类功能，排在清洗 / 注入之后（order 无强依赖，登记要趁早）
    order = 90
    uses_adapter_hook = True
    #: 确认询问的等待秒数（类属性，测试调小即可不真等 30 秒）
    confirm_timeout: float = service.CONFIRM_TIMEOUT

    def __init__(self) -> None:
        #: 在飞任务登记表：触发消息 message_id -> pipeline 任务
        self._inflight: Dict[str, asyncio.Task] = {}
        #: 等待确认的撤回：触发消息 message_id -> _Awaiting
        self._awaiting: Dict[str, _Awaiting] = {}
        self._log: Optional[Any] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        self._log = getattr(runtime, "log", None)

    def on_unload(self) -> None:
        self._inflight.clear()
        for item in list(self._awaiting.values()):
            if item.timer is not None:
                try:
                    item.timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
        self._awaiting.clear()
        replies.clear()
        self._log = None

    # ------------------------------------------------------------------
    # 适配器早期钩子
    # ------------------------------------------------------------------
    async def on_adapter_message(self, ctx: Any) -> None:
        event = ctx.event

        # ① 撤回 notice？确认开关开着 ⇒ 先问不急着动；否则照老规矩立刻取消
        for raw in service.raw_sources(event):
            mid = service.parse_recall(raw)
            if mid is None:
                continue
            if self._confirm_on(ctx):
                await self._begin_confirm(ctx, event, raw, mid)
            else:
                await self._cancel_now(ctx, event, mid, via="撤回命中")
            return

        # ② 确认询问挂着时：撤回者本人的回答先于一切处理（命中即吞掉）
        if self._awaiting and await self._consume_answer(ctx, event):
            return

        # ③ 普通消息：登记 message_id → 本管线任务 + 接管本条 send
        mid = service.trigger_id(event)
        task = asyncio.current_task()
        if mid and task is not None:
            self._track(mid, task)
        if mid:
            self._install_capture(event, mid)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _confirm_on(self, ctx: Any) -> bool:
        """确认询问开关（``ctx.conf`` 缺失 / 读失败一律当关，绝不冒泡）。"""
        conf = getattr(ctx, "conf", None)
        getter = getattr(conf, "bool", None)
        if not callable(getter):
            return False
        try:
            return bool(getter("enable_recall_confirm", False))
        except Exception:  # noqa: BLE001
            return False

    async def _cancel_now(self, ctx: Any, event: Any, mid: str, via: str) -> None:
        """立刻执行取消：掐在飞请求 + 连带撤回复（老行为，也是确认后的落点）。

        ``ctx`` 可为 ``None``（超时定时器里没有 ctx，只写日志不记备注）。
        """
        task = self._inflight.pop(mid, None)
        if task is not None and not task.done():
            try:
                task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._info(f"{via}：已取消在飞请求（消息 {mid}）")
            note = getattr(ctx, "note", None)
            if callable(note):
                try:
                    note(f"{via}：已取消消息 {mid} 的在飞请求")
                except Exception:  # noqa: BLE001
                    pass
        await self._recall_replies(ctx, event, mid)

    def _track(self, mid: str, task: asyncio.Task) -> None:
        """登记在飞任务；完成时自动摘表（``is`` 比对防误删新任务）。"""
        self._inflight[mid] = task

        def _done(t: asyncio.Task, key: str = mid) -> None:
            if self._inflight.get(key) is t:
                self._inflight.pop(key, None)

        try:
            task.add_done_callback(_done)
        except Exception:  # noqa: BLE001
            pass

    def _info(self, message: str) -> None:
        """带 ``[XBNEXT] `` 前缀写日志（照抄 face 的安全写法）。"""
        method = getattr(self._log, "info", None) if self._log is not None else None
        if not callable(method):
            return
        try:
            method(f"[XBNEXT] {message}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 三期 · 确认询问（P19）
    # ------------------------------------------------------------------
    async def _begin_confirm(
        self, ctx: Any, event: Any, raw: Any, mid: str
    ) -> None:
        """撤回确认：发询问 → 登记等待 → 启动 30 秒超时。

        询问发不出去（非 aiocqhttp / 发送异常）⇒ 不留悬空等待，
        回退老行为立刻取消。
        """
        meta = service.recall_meta(raw) or {}
        group_id = meta.get("group_id", "")
        operator = meta.get("operator_id", "")
        sent = await self._send_ask(event, group_id, operator, raw)
        if not sent:
            self._info("撤回确认：询问发送失败，回退为立刻自动取消")
            await self._cancel_now(ctx, event, mid, via="撤回命中")
            return

        self._awaiting[mid] = _Awaiting(mid, group_id, operator, event)
        self._spawn_deadline(mid)
        self._info(
            f"撤回确认：已发出询问，等 {self.confirm_timeout:.0f} 秒回答（消息 {mid}）"
        )
        note = getattr(ctx, "note", None)
        if callable(note):
            try:
                note(f"撤回命中：已发出取消询问，等待撤回者确认（消息 {mid}）")
            except Exception:  # noqa: BLE001
                pass

    async def _send_ask(
        self, event: Any, group_id: str, operator: str, raw: Any
    ) -> bool:
        """把询问发到原会话（群里 @ 撤回者）；发不出去返回 ``False``。"""
        bot = getattr(event, "bot", None)
        if bot is None or not operator:
            return False
        text = service.ask_text()
        routing = self._routing(raw)
        try:
            if group_id:
                if not str(group_id).isdigit():
                    return False
                segs: List[Any] = [
                    {"type": "at", "data": {"qq": str(operator)}},
                    {"type": "text", "data": {"text": f" {text}"}},
                ]
                await bot.send_group_msg(group_id=int(group_id), message=segs, **routing)
            else:
                if not str(operator).isdigit():
                    return False
                segs = [{"type": "text", "data": {"text": text}}]
                await bot.send_private_msg(user_id=int(operator), message=segs, **routing)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._info(f"撤回确认：询问发送失败 {exc!r}")
            return False

    def _spawn_deadline(self, mid: str) -> None:
        """给这次询问挂一个超时任务（无事件循环时静默不挂）。"""
        try:
            task = asyncio.get_running_loop().create_task(self._deadline(mid))
        except Exception:  # noqa: BLE001
            return
        item = self._awaiting.get(mid)
        if item is not None:
            item.timer = task

    async def _deadline(self, mid: str) -> None:
        """超时没人答 ⇒ 按老规矩自动取消（省 token 的兜底不丢）。"""
        try:
            await asyncio.sleep(self.confirm_timeout)
        except asyncio.CancelledError:
            raise  # 已被回答处理 / 卸载：正常取消
        item = self._awaiting.pop(mid, None)
        if item is None:
            return  # 回答先到，已处理过
        self._info(
            f"撤回确认：{self.confirm_timeout:.0f} 秒无人回答，自动取消（消息 {mid}）"
        )
        try:
            await self._cancel_now(None, item.event, mid, via="超时未确认")
        except Exception:  # noqa: BLE001
            pass

    async def _consume_answer(self, ctx: Any, event: Any) -> bool:
        """撤回者本人的回答 ⇒ 处理并吞掉本条消息；其它情况 ``False``。

        三道闸都过才算命中：词认得（``match_answer``）、会话对得上、
        发言人就是执行撤回的人 —— 群里其他人回「是」不消费、照常走管线。
        """
        verdict = service.match_answer(getattr(event, "message_str", ""))
        if verdict is None:
            return False
        try:
            group = str(event.get_group_id() or "")
            sender = str(event.get_sender_id() or "")
        except Exception:  # noqa: BLE001
            return False
        hits = [
            mid
            for mid, item in self._awaiting.items()
            if item.operator_id
            and item.operator_id == sender
            and (item.group_id == group if item.group_id else not group)
        ]
        if not hits:
            return False  # 不是撤回者本人 → 不消费
        try:
            event.stop_event()  # 先吞：不唤醒 LLM、不进历史
        except Exception:  # noqa: BLE001
            pass
        for mid in hits:
            item = self._awaiting.pop(mid, None)
            if item is None:
                continue
            if item.timer is not None:
                try:
                    item.timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
            if verdict:
                self._info(f"撤回确认：撤回者答「是」，执行取消（消息 {mid}）")
                await self._cancel_now(ctx, item.event, mid, via="确认取消")
            else:
                self._info(f"撤回确认：撤回者答「否」，保留不动（消息 {mid}）")
                note = getattr(ctx, "note", None)
                if callable(note):
                    try:
                        note(f"撤回确认：撤回者选择保留（消息 {mid}）")
                    except Exception:  # noqa: BLE001
                        pass
        return True

    # ------------------------------------------------------------------
    # 二期 · 连带撤回复（P17）
    # ------------------------------------------------------------------
    def _install_capture(self, event: Any, trigger_id: str) -> None:
        """接管本条事件的 ``send``（方案②）：自己发并记下回复 id。

        只在有 ``bot`` 实例（aiocqhttp 事件）且 ``message_str`` 非空的
        消息上装；装不上就当没这回事（退化成一期）。同一条事件只装一次。
        """
        try:
            if not str(getattr(event, "message_str", "") or "").strip():
                return
            if getattr(event, "bot", None) is None:
                return
            gpn = getattr(event, "get_platform_name", None)
            if callable(gpn):
                try:
                    if gpn() != "aiocqhttp":
                        return
                except Exception:  # noqa: BLE001
                    return
            if getattr(event, "_xbnext_send_capture", False):
                return
            original = event.send
            if not callable(original):
                return

            async def _wrapped(chain: Any) -> None:
                try:
                    handled = await self._send_captured(event, trigger_id, chain)
                except Exception:  # noqa: BLE001 意外兜底：退回原发送
                    handled = False
                if not handled:
                    await original(chain)

            event.send = _wrapped
            event._xbnext_send_capture = True
        except Exception:  # noqa: BLE001 装不上就不装，不影响回复
            pass

    async def _send_captured(
        self, event: Any, trigger_id: str, chain: Any
    ) -> bool:
        """接管发送：复刻核心 aiocqhttp 发送分支，拿到 id 记账后补指标。

        返回 ``True`` = 本方法已把消息发出去（或有意不发），包装不再
        触碰 ``original``；返回 ``False`` = 交给 ``original`` 走核心路径。
        ``CancelledError`` 摘登记表（有补删标记则交出旧 id 让包装收尾）
        后原样上抛，绝不重发。
        """
        segs = await self._parse_segments(chain)
        if not segs:
            return False
        if getattr(event, "bot", None) is None:
            return False
        # Node / Nodes / File 走核心的逐条/合并转发路径，形态特殊 —— 放弃接管
        raw_chain = getattr(chain, "chain", None)
        if raw_chain is not None and any(
            type(seg).__name__ in ("Node", "Nodes", "File") for seg in raw_chain
        ):
            return False

        replies.begin(trigger_id)
        try:
            reply_ids = await self._dispatch(event, event.bot, segs)
        except asyncio.CancelledError:
            leftover = replies.abandon(trigger_id)
            if leftover:
                self._spawn_delete(event, leftover, trigger_id)
            raise
        except Exception as exc:  # noqa: BLE001
            replies.abandon(trigger_id)
            self._info(f"接管发送失败，退回原发送：{exc!r}")
            return False

        try:
            pending = replies.record(trigger_id, reply_ids)
        except Exception:  # noqa: BLE001
            pending = False
        await self._after_sent(event, chain)
        if pending:
            all_ids, _ = replies.take(trigger_id)  # 摘表并取全量（含分段旧 id）
            if all_ids:
                await self._delete_replies(event, all_ids, trigger_id)
        return True

    def _spawn_delete(self, event: Any, ids: List[str], trigger_id: str) -> None:
        """取消语境下不能 await —— 另起任务补删（失败只记日志）。"""
        try:
            asyncio.get_running_loop().create_task(
                self._delete_replies(event, ids, trigger_id)
            )
        except Exception:  # noqa: BLE001
            pass

    async def _parse_segments(self, chain: Any) -> Optional[List[Any]]:
        """复用核心的 OneBot 段解析（lazy import，单测环境没有 astrbot）。"""
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            segs = await AiocqhttpMessageEvent._parse_onebot_json(chain)
            return segs or None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _routing(raw: Any) -> Dict[str, Any]:
        """OneBot 多连接路由参数：raw dict 里有 ``self_id`` 就带上。"""
        try:
            if raw is not None and hasattr(raw, "get") and raw.get("self_id"):
                return {"self_id": raw["self_id"]}
        except Exception:  # noqa: BLE001
            pass
        return {}

    async def _dispatch(self, event: Any, bot: Any, segs: List[Any]) -> List[str]:
        """按核心 ``_dispatch_send`` 的分支发出去，返回拿到的回复 id 列表。"""
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        routing = self._routing(raw)

        is_group = bool(event.get_group_id())
        session_id = event.get_group_id() if is_group else event.get_sender_id()
        sid = str(session_id or "")
        sid_int = int(sid) if sid.isdigit() else None

        ret: Any = None
        if is_group and sid_int is not None:
            ret = await bot.send_group_msg(group_id=sid_int, message=segs, **routing)
        elif not is_group and sid_int is not None:
            ret = await bot.send_private_msg(user_id=sid_int, message=segs, **routing)
        elif raw is not None and hasattr(raw, "get"):
            ret = await bot.send(event=raw, message=segs)
        else:
            raise ValueError(f"无法发送：session 非数字 {session_id!r} 且无 raw event")

        if isinstance(ret, dict):
            rid = ret.get("message_id")
            if rid is not None and str(rid).strip():
                return [str(rid)]
        return []

    async def _after_sent(self, event: Any, chain: Any) -> None:
        """补上核心基类 ``send`` 的副作用（Metric 上报 + ``_has_send_oper``）。

        ``_has_send_oper = False`` 会让 core 的 process 阶段多跑一轮
        空结果处理，接管路径必须等价补位。
        """
        try:
            from astrbot.core.platform.astr_message_event import AstrMessageEvent

            await AstrMessageEvent.send(event, chain)
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            event._has_send_oper = True
        except Exception:  # noqa: BLE001
            pass

    async def _recall_replies(self, ctx: Any, event: Any, trigger_id: str) -> None:
        """撤回命中后：能立刻删的删掉；回复在飞的标记补删。"""
        ids, sending = replies.take(trigger_id)
        if sending:
            self._info(f"撤回连带：回复在途，完成后自动补删（消息 {trigger_id}）")
            return
        if not ids:
            return
        done = await self._delete_replies(event, ids, trigger_id)
        note = getattr(ctx, "note", None)
        if callable(note):
            try:
                note(f"撤回命中：连带撤回 {done}/{len(ids)} 条回复")
            except Exception:  # noqa: BLE001
                pass

    async def _delete_replies(
        self, event: Any, reply_ids: List[str], trigger_id: str
    ) -> int:
        """OneBot ``delete_msg``（str→int 双试），返回成功条数。"""
        bot = getattr(event, "bot", None)
        call = getattr(bot, "call_action", None)
        if not callable(call):
            call = getattr(getattr(bot, "api", None), "call_action", None)
        if not callable(call):
            return 0

        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        routing = self._routing(raw)

        done = 0
        for rid in reply_ids:
            candidates: List[Any] = [rid]
            if str(rid).isdigit():
                candidates.append(int(rid))
            for cand in candidates:
                try:
                    await call("delete_msg", message_id=cand, **routing)
                    done += 1
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    continue
        if reply_ids:
            self._info(
                f"撤回连带：删除 bot 回复 {done}/{len(reply_ids)} 条（消息 {trigger_id}）"
            )
        return done
