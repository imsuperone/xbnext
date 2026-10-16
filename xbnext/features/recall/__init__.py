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
回复不消费、照常走管线）；等回答期间这条消息的回复**扣在发送口不发**
（先拦后放）：答「否」放行发出，答「是」掐请求 + 连带撤回复、回复
根本不出门，超时按老规矩自动取消（秒数见
``service.CONFIRM_TIMEOUT``）。回答命中即 ``stop_event()``
吞掉（不唤醒 LLM、不进历史）；**流程收尾（答是 / 答否 / 超时）时把
bot 自己发的那句询问一并撤回**，不给会话留残句；询问发不出去 ⇒
回退老行为立刻取消；bot 自己删消息的撤回回执不问也不动（防自问自答）。

**排队疏通（真机回归 #6）**：核心把同会话连发的消息排成严格按号
叫序的队，只有「run 正常完成 / 出错 / 主动中断」三条路会把手里代管
的排队票结清 —— 硬杀 run 任务（撤回触发消息后答「是」）不走这三条，
票永远悬着，**整个会话的排队从此永远堵死**（单发消息不受影响，连发
的 follow-up 全部石沉大海，直到重启机器人）。两道保险（纯插件侧）：

1. **杀主 run 前先还票**：``_cancel_now`` 里确认被撤的就是当前 run
   的触发消息 ⇒ 先替核心把 ``_pending_follow_ups`` 全部 resolve，
   再 ``task.cancel()`` —— 排队的前后消息照常醒来回复，不留死票；
2. **看门狗疏通旧伤**：每条新消息到达先扫一眼本会话的登记表，发现
   「没有 run 在跑 + 年龄超 ``stuck_timeout`` + 隔 ``stuck_grace``
   复看仍挂着」的任务就 cancel —— 核心管线的 ``finally`` 会给它补标
   翻号，堵死的队自动疏通，不用重启机器人。核心模块拿不到（无
   astrbot 环境 / 核心改名）时看门狗整段跳过，宁不动不误杀。

范围：请求在飞 ⇒ 掐请求；回复已发 ⇒ 连带撤回；回复在途 ⇒ 补删；
确认开关打开时以上动作都要先过「撤回者本人回答 / 超时」这道闸，
闸未落定前回复一律扣在发送口。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from ... import log
from ...eventids import gid_of, umo_of, uid_of
from . import replies, service
from ..base import Feature

#: ``_active_runner`` 的「核心拿不到」哨兵：有别于「没有活跃 run」，
#: 看门狗见到它就整段跳过（宁不动不误杀）
_RUNNER_UNKNOWN = object()


def _msg_id_of(ret: Any) -> str:
    """发送回执里的 ``message_id`` → 字符串；拿不到返回空串。

    OneBot 的 ``send_group_msg / send_private_msg`` 回执是
    ``{"message_id": 123, ...}``；个别实现返回对象形态也认。
    """
    try:
        value = ret.get("message_id") if isinstance(ret, dict) else getattr(
            ret, "message_id", None
        )
        return str(value) if value is not None and str(value).strip() else ""
    except Exception:  # noqa: BLE001
        return ""


class _Awaiting:
    """一次等待确认的撤回：被撤消息 id → 撤回者 / 会话 / 挂起的定时器。"""

    __slots__ = (
        "trigger_id",
        "group_id",
        "operator_id",
        "event",
        "timer",
        "gate",
        "verdict",
        "ask_id",
    )

    def __init__(self, trigger_id: str, group_id: str, operator_id: str, event: Any) -> None:
        self.trigger_id = trigger_id
        #: 群撤回 = 群号；好友撤回 = 空串
        self.group_id = group_id
        #: 执行撤回的人（群管理员可撤别人的 message，operator ≠ user）
        self.operator_id = operator_id
        #: 撤回 notice 事件（超时 / 确认时用它的 bot 补删回复）
        self.event = event
        self.timer: Optional[asyncio.Task] = None
        #: 发送闸门：确认挂着时回复在 ``gate.wait()`` 上扣着（先拦后放）
        self.gate = asyncio.Event()
        #: ``None`` 等回答；``True`` 放行（答「否」/ 卸载）；``False`` 拦截不发
        self.verdict: Optional[bool] = None
        #: bot 发出的那句询问的 message_id（流程收尾时撤回；拿不到为空串）
        self.ask_id: str = ""


class RecallFeature(Feature):
    """见模块 docstring；适配器早期钩子 + 本条事件的发送接管。"""

    key = "enable_recall_cancel"
    name = "撤回取消请求"
    description = "撤回触发消息后，取消在飞请求并连带撤回 bot 已发出的回复"
    #: 守候类功能，排在清洗 / 注入之后（order 无强依赖，登记要趁早）
    order = 90
    uses_adapter_hook = True
    uses_decorating_hook = True
    #: 确认询问的等待秒数（类属性，测试调小即可不真等完整秒数）
    confirm_timeout: float = service.CONFIRM_TIMEOUT
    #: 判「挂死」的年龄门槛（秒）：长文生成可能耗时数分钟，设 600s 防止长文流式输出中途被误杀触发 499
    stuck_timeout: float = 600.0
    #: 首次疑似后的复看间隔（秒）：两次都挂着才动手，避开「正在收尾 /
    #: 正要叫号」的毫秒窗口，防误杀
    stuck_grace: float = 30.0

    def __init__(self) -> None:
        #: 在飞任务登记表：触发消息 message_id -> pipeline 任务
        self._inflight: Dict[str, asyncio.Task] = {}
        #: 辅助元数据：message_id -> (unified_msg_origin, 登记时刻 monotonic)
        self._meta: Dict[str, Tuple[str, float]] = {}
        #: 看门狗疑似表：message_id -> 首次发现挂死的时刻（复看用）
        self._suspect: Dict[str, float] = {}
        #: 等待确认的撤回：触发消息 message_id -> _Awaiting
        self._awaiting: Dict[str, _Awaiting] = {}
        #: 已确认取消的触发消息 message_id -> 取消时刻 monotonic（防超时/回答收尾窗口期漏发）
        self._cancelled: Dict[str, float] = {}
        self._log: Optional[Any] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        self._log = getattr(runtime, "log", None)

    def on_unload(self) -> None:
        self._inflight.clear()
        self._meta.clear()
        self._suspect.clear()
        self._cancelled.clear()
        for item in list(self._awaiting.values()):
            # 插件卸载 ⇒ 按「没有确认功能」的老行为放行，别让扣在闸上的
            # 回复（或它的管线任务）卡死在 gate.wait() 上
            item.verdict = True
            item.gate.set()
            if item.timer is not None:
                try:
                    item.timer.cancel()
                except Exception:  # noqa: BLE001
                    pass
        self._awaiting.clear()
        replies.clear()
        self._log = None

    def _mark_cancelled(self, mid: str) -> None:
        if not mid:
            return
        now = time.monotonic()
        dead = [k for k, ts in self._cancelled.items() if now - ts > 300.0]
        for k in dead:
            self._cancelled.pop(k, None)
        self._cancelled[str(mid)] = now

    def _is_cancelled(self, mid: str) -> bool:
        if not mid:
            return False
        ts = self._cancelled.get(str(mid))
        if ts is None:
            return False
        if time.monotonic() - ts > 300.0:
            self._cancelled.pop(str(mid), None)
            return False
        return True

    # ------------------------------------------------------------------
    # 适配器早期钩子 & 装饰阶段拦截
    # ------------------------------------------------------------------
    async def on_decorating_result(self, ctx: Any) -> None:
        """若本条触发消息已被撤回取消，在装饰阶段（TTS / 转图之前）直接清空结果链并终止发送。"""
        event = getattr(ctx, "event", None)
        if event is None:
            return
        mid = service.trigger_id(event)
        if not mid or not self._is_cancelled(mid):
            return
        try:
            result = event.get_result()
            if result is not None and hasattr(result, "chain") and isinstance(result.chain, list):
                result.chain.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            event._has_send_oper = True
            event.stop_event()
        except Exception:  # noqa: BLE001
            pass
        self._info(f"撤回拦截：装饰阶段已清空待发结果链（消息 {mid}）")

    async def on_adapter_message(self, ctx: Any) -> None:
        event = ctx.event
        umo = umo_of(event)
        if umo and ("webchat" in umo.lower()):
            return

        # ⓪ 看门狗：顺手疏通本会话被死票堵死的排队（#6 旧伤，见 docstring）
        self._sweep_stuck(umo)

        # ① 撤回 notice？确认开关开着 ⇒ 先问不急着动；否则照老规矩立刻取消
        for raw in service.raw_sources(event):
            mid = service.parse_recall(raw)
            if mid is None:
                continue
            # bot 自己删自己消息的回执（连带撤回复的 delete_msg 产生的
            # notice）⇒ 不是用户撤回：不问也不动，防自问自答
            if self._is_self_recall(raw):
                self._info(f"撤回者是 bot 自己，跳过（消息 {mid}）")
                return
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
            self._track(mid, task, umo)
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

    @staticmethod
    def _is_self_recall(raw: Any) -> bool:
        """撤回执行者就是 bot 自己（OneBot ``self_id``）⇒ 自己删自己消息。

        连带撤回复用 ``delete_msg`` 删 bot 自己的消息时，QQ 会推一条
        ``group_recall`` 回执回来 —— operator == self_id 即为此情形，
        当用户撤回处理会再问一轮（真机回归「自问自答」）。
        """
        try:
            if not hasattr(raw, "get"):
                return False
            self_id = raw.get("self_id")
            if self_id in (None, ""):
                return False
            meta = service.recall_meta(raw) or {}
            operator = meta.get("operator_id", "")
            return bool(operator) and str(operator) == str(self_id)
        except Exception:  # noqa: BLE001
            return False

    async def _cancel_now(self, ctx: Any, event: Any, mid: str, via: str) -> None:
        """立刻执行取消：立取消标记 + 掐在飞请求 + 连带撤回复（老行为，也是确认后的落点）。

        杀主 run 之前先把核心手里代管的排队票还清（``_settle_followups``），
        否则排队的前后消息会被死票堵死（#6）。``ctx`` 可为 ``None``
        （超时定时器里没有 ctx，只写日志不记备注）。
        """
        self._mark_cancelled(mid)
        task = self._inflight.pop(mid, None)
        self._meta.pop(mid, None)
        self._suspect.pop(mid, None)
        if task is not None and not task.done():
            self._settle_followups(event, mid, via)
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

    def _track(self, mid: str, task: asyncio.Task, umo: str = "") -> None:
        """登记在飞任务；完成时自动摘表（``is`` 比对防误删新任务）。

        ``umo`` / 登记时刻进辅助元数据表，供看门狗判「这个会话还有没有
        run 在跑、这条挂了多久」；老测试两参调用不带 ``umo`` ⇒ 看门狗
        对它天然跳过（无元数据不动手）。
        """
        self._inflight[mid] = task
        self._suspect.pop(mid, None)
        if umo:
            self._meta[mid] = (umo, time.monotonic())
        else:
            self._meta.pop(mid, None)

        def _done(t: asyncio.Task, key: str = mid) -> None:
            if self._inflight.get(key) is t:
                self._inflight.pop(key, None)
                self._meta.pop(key, None)
                self._suspect.pop(key, None)

        try:
            task.add_done_callback(_done)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 排队疏通（#6）：还票 + 看门狗
    # ------------------------------------------------------------------
    def _active_runner(self, umo: str) -> Any:
        """核心该会话的活跃 runner；核心拿不到 ⇒ ``_RUNNER_UNKNOWN`` 哨兵。

        与「没有活跃 run（``None``）」严格区分：哨兵出现时看门狗整段
        跳过、还票也跳过 —— 环境不对时宁不动，不误杀。
        """
        try:
            from astrbot.core.pipeline.process_stage.follow_up import (
                _ACTIVE_AGENT_RUNNERS,
            )

            return _ACTIVE_AGENT_RUNNERS.get(umo)
        except Exception:  # noqa: BLE001
            return _RUNNER_UNKNOWN

    def _settle_followups(self, event: Any, mid: str, via: str) -> None:
        """杀主 run 前，先把核心代管的排队票全部还清（#6 堵源头）。

        核心只在 run 正常完成 / 出错 / 主动中断三条路调
        ``_resolve_unconsumed_follow_ups()``——任务被 ``cancel()`` 不走
        这三条，票会永远悬着、整个会话的排队堵死。只在「被撤的正是这次
        run 的触发消息」时动手：撤排队中的消息不该动主 run 的票。
        """
        umo = umo_of(event)
        if not umo:
            return
        runner = self._active_runner(umo)
        if runner is _RUNNER_UNKNOWN or runner is None:
            return
        try:
            runner_event = getattr(
                getattr(getattr(runner, "run_context", None), "context", None),
                "event",
                None,
            )
            if service.trigger_id(runner_event) != str(mid):
                return  # 撤的是排队消息 / 别的消息，主 run 的票不动
            pending = getattr(runner, "_pending_follow_ups", None)
            count = len(pending) if isinstance(pending, list) else 0
            if not count:
                return  # 手里没票，没什么可还
            runner._resolve_unconsumed_follow_ups()
        except Exception as exc:  # noqa: BLE001
            self._info(f"{via}：归还排队票失败 {exc!r}")
            return
        self._info(f"{via}：已让排队的 {count} 条消息照常继续（不留堵点）")

    def _sweep_stuck(self, umo: str) -> None:
        """看门狗：疏通被死票堵死的排队（#6 旧伤，不用重启机器人）。

        只在「该会话没有任何 run 在跑」时动手 —— 有 run 在跑，排队等
        叫号是正常的。先标记、隔 ``stuck_grace`` 复看仍挂着才 cancel：
        取消后核心管线的 ``finally`` 会补标翻号，队自动通；两次确认则
        避开「任务正在收尾 / 正要叫号」的毫秒窗口，防误杀。
        """
        if not umo or not self._inflight:
            return
        runner = self._active_runner(umo)
        if runner is _RUNNER_UNKNOWN or runner is not None:
            return
        now = time.monotonic()
        for mid, task in list(self._inflight.items()):
            meta = self._meta.get(mid)
            if meta is None or meta[0] != umo:
                continue  # 没元数据 / 属于别的会话：不动手
            if task.done() or now - meta[1] < self.stuck_timeout:
                self._suspect.pop(mid, None)
                continue
            first = self._suspect.get(mid)
            if first is None:
                self._suspect[mid] = now
                continue  # 首次发现：先记下，等复看
            if now - first < self.stuck_grace:
                continue  # 复看还没到点
            self._suspect.pop(mid, None)
            try:
                task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._info(
                f"疏通排队：任务挂死超 {self.stuck_timeout:.0f} 秒，"
                f"已取消解卡（消息 {mid}）"
            )

    def _info(self, message: str) -> None:
        """写日志（前缀与安全跳过统一走 :mod:`xbnext.log`）。"""
        log.emit(self._log, "info", message)

    # ------------------------------------------------------------------
    # 三期 · 确认询问（P19）
    # ------------------------------------------------------------------
    async def _begin_confirm(
        self, ctx: Any, event: Any, raw: Any, mid: str
    ) -> None:
        """撤回确认：**先立发送闸** → 发询问 → 登记等待 → 启动超时。

        闸一立，这条消息的回复就被扣在发送口等回答（先拦后放，不再
        「问归问、回复照发」）。没有在飞请求也没有已发回复 ⇒ 无可取消
        对象，不发多余的询问；重复撤回通知 ⇒ 询问已在进行中，不叠加。
        询问发不出去 ⇒ 收闸拦截并回退老行为立刻取消。
        """
        if mid in self._awaiting:
            return  # 同一条消息的重复撤回通知，询问已在进行中
        if mid not in self._inflight and not replies.has(mid):
            self._info(f"撤回确认：无可取消的对象，跳过询问（消息 {mid}）")
            return
        meta = service.recall_meta(raw) or {}
        group_id = meta.get("group_id", "")
        operator = meta.get("operator_id", "")
        # 先登记（闸生效）再发问：发问 await 期间回复也要扣住
        self._awaiting[mid] = _Awaiting(mid, group_id, operator, event)
        ask_id = await self._send_ask(event, group_id, operator, raw)
        if ask_id is None:
            item = self._awaiting.pop(mid, None)
            if item is not None:
                item.verdict = False  # 问不出去 ⇒ 拦截不发，随后立刻取消
                self._mark_cancelled(mid)
                item.gate.set()
            self._info("撤回确认：询问发送失败，回退为立刻自动取消")
            await self._cancel_now(ctx, event, mid, via="撤回命中")
            return
        item = self._awaiting.get(mid)
        if item is not None:
            item.ask_id = ask_id  # 流程收尾时把这句询问撤回
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
    ) -> Optional[str]:
        """把询问发到原会话（群里 @ 撤回者）；发不出去返回 ``None``。

        成功返回询问消息的 ``message_id``（bot 没回报 id 时为空串），
        流程收尾要把这条询问撤回。
        """
        bot = getattr(event, "bot", None)
        if bot is None or not operator:
            return None
        text = service.ask_text()
        routing = self._routing(raw)
        try:
            if group_id:
                if not str(group_id).isdigit():
                    return None
                segs: List[Any] = [
                    {"type": "at", "data": {"qq": str(operator)}},
                    {"type": "text", "data": {"text": f" {text}"}},
                ]
                ret = await bot.send_group_msg(
                    group_id=int(group_id), message=segs, **routing
                )
            else:
                if not str(operator).isdigit():
                    return None
                segs = [{"type": "text", "data": {"text": text}}]
                ret = await bot.send_private_msg(
                    user_id=int(operator), message=segs, **routing
                )
            return _msg_id_of(ret)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._info(f"撤回确认：询问发送失败 {exc!r}")
            return None

    def _spawn_deadline(self, mid: str) -> None:
        """给这次询问挂一个超时任务（无事件循环时静默不挂）。"""
        try:
            task = asyncio.get_running_loop().create_task(self._deadline(mid))
        except Exception:  # noqa: BLE001
            return
        item = self._awaiting.get(mid)
        if item is not None:
            item.timer = task

    def _cancel_inflight_sync(self, ctx: Any, event: Any, mid: str, via: str) -> None:
        """同步立取消标记并立刻 cancel 在飞任务（零 await 窗口，杜绝 _delete_ask 期间 LLM 偷跑发送）。"""
        self._mark_cancelled(mid)
        task = self._inflight.pop(mid, None)
        self._meta.pop(mid, None)
        self._suspect.pop(mid, None)
        if task is not None and not task.done():
            self._settle_followups(event, mid, via)
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

    async def _deadline(self, mid: str) -> None:
        """超时没人答 ⇒ 按老规矩自动取消（省 token 的兜底不丢）。"""
        try:
            await asyncio.sleep(self.confirm_timeout)
        except asyncio.CancelledError:
            raise  # 已被回答处理 / 卸载：正常取消
        item = self._awaiting.pop(mid, None)
        if item is None:
            return  # 回答先到，已处理过
        item.verdict = False  # 超时 ⇒ 拦截不发，按老规矩自动取消
        self._cancel_inflight_sync(None, item.event, mid, via="超时未确认")
        item.gate.set()
        self._info(
            f"撤回确认：{self.confirm_timeout:.0f} 秒无人回答，自动取消（消息 {mid}）"
        )
        try:
            await self._delete_ask(item)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._cancel_now(None, item.event, mid, via="超时未确认")
        except Exception:  # noqa: BLE001
            pass

    async def _delete_ask(self, item: Any) -> None:
        """流程收尾：撤回 bot 自己发的那句询问（答是 / 答否 / 超时都撤）。"""
        ask_id = getattr(item, "ask_id", "")
        if not ask_id:
            return
        done = await self._delete_ids(item.event, [ask_id])
        if done:
            self._info(f"撤回确认：询问消息已撤回（消息 {item.trigger_id}）")

    async def _consume_answer(self, ctx: Any, event: Any) -> bool:
        """撤回者本人的回答 ⇒ 处理并吞掉本条消息；其它情况 ``False``。

        三道闸都过才算命中：词认得（``match_answer``）、会话对得上、
        发言人就是执行撤回的人 —— 群里其他人回「是」不消费、照常走管线。
        """
        verdict = service.match_answer(getattr(event, "message_str", ""))
        if verdict is None:
            return False
        group = gid_of(event)
        sender = uid_of(event)
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
            # 是 ⇒ 拦截不发（verdict False）；否 ⇒ 放行（True）。先同步掐任务立拦截标记再落定闸门
            item.verdict = not bool(verdict)
            if verdict:
                self._cancel_inflight_sync(ctx, item.event, mid, via="确认取消")
            item.gate.set()
            try:
                await self._delete_ask(item)  # 流程收尾：询问撤回，不留残句
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
                if self._is_cancelled(trigger_id):
                    self._info(
                        f"撤回确认：回复被拦截，未发送（消息 {trigger_id}）"
                    )
                    try:
                        event._has_send_oper = True
                    except Exception:  # noqa: BLE001
                        pass
                    return
                # 撤回确认挂着 ⇒ 回复先扣在闸上等回答（先拦后放）。
                # 答「是」/超时判定 False ⇒ 有意不发；CancelledError 在
                # gate.wait() 上抛出时还没登记，无需清理
                gate_item = self._awaiting.get(trigger_id)
                if gate_item is not None:
                    await gate_item.gate.wait()
                    if gate_item.verdict is not True or self._is_cancelled(trigger_id):
                        self._info(
                            f"撤回确认：回复被拦截，未发送（消息 {trigger_id}）"
                        )
                        try:
                            event._has_send_oper = True  # 有意不发也算已处理
                        except Exception:  # noqa: BLE001
                            pass
                        return
                try:
                    handled = await self._send_captured(event, trigger_id, chain)
                except Exception:  # noqa: BLE001 意外兜底：退回原发送
                    handled = False
                if not handled and not self._is_cancelled(trigger_id):
                    await original(chain)

            event.send = _wrapped
            event._xbnext_send_capture = True
        except Exception:  # noqa: BLE001 装不上就不装，不影响回复
            pass

    @staticmethod
    def _is_send_timeout(exc: BaseException) -> bool:
        """NapCat / OneBot 发送超时（retcode=1200 / Timeout）通常已在后台发出去，
        退回原发送会触发二次发送（如 xbimg 双发两张图）。"""
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return True
        if getattr(exc, "retcode", None) == 1200:
            return True
        msg = f"{exc} {repr(exc)} {getattr(exc, 'message', '')} {getattr(exc, 'wording', '')}".lower()
        return "retcode=1200" in msg or "retcode': 1200" in msg or "timeout" in msg

    async def _send_captured(
        self, event: Any, trigger_id: str, chain: Any
    ) -> bool:
        """接管发送：复刻核心 aiocqhttp 发送分支，拿到 id 记账后补指标。

        返回 ``True`` = 本方法已把消息发出去（或有意不发），包装不再
        触碰 ``original``；返回 ``False`` = 交给 ``original`` 走核心路径。
        ``CancelledError`` 摘登记表（若底层发送请求已发出则用 shield 等待其返回 message_id 后补删）
        后原样上抛，绝不重发。
        """
        if self._is_cancelled(trigger_id):
            try:
                event._has_send_oper = True
            except Exception:  # noqa: BLE001
                pass
            return True
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
        send_coro = self._dispatch(event, event.bot, segs)
        send_task: Optional[asyncio.Task] = None
        try:
            try:
                loop = asyncio.get_running_loop()
                send_task = loop.create_task(send_coro)
            except RuntimeError:
                send_task = None

            if send_task is not None:
                reply_ids = await asyncio.shield(send_task)
            else:
                reply_ids = await send_coro
        except asyncio.CancelledError:
            leftover = replies.abandon(trigger_id)
            if send_task is not None and not send_task.done():
                self._spawn_await_and_delete(event, send_task, leftover, trigger_id)
            elif leftover:
                self._spawn_delete(event, leftover, trigger_id)
            raise
        except Exception as exc:  # noqa: BLE001
            replies.abandon(trigger_id)
            if self._is_send_timeout(exc):
                self._info(f"接管发送超时（不再退回原发送以免重复）：{exc!r}")
                await self._after_sent(event, chain)
                return True
            self._info(f"接管发送失败，退回原发送：{exc!r}")
            return False

        try:
            pending = replies.record(trigger_id, reply_ids)
        except Exception:  # noqa: BLE001
            pending = False
        await self._after_sent(event, chain)
        if pending or self._is_cancelled(trigger_id):
            all_ids, _ = replies.take(trigger_id)  # 摘表并取全量（含分段旧 id）
            if not all_ids and reply_ids:
                all_ids = list(reply_ids)
            if all_ids:
                await self._delete_replies(event, all_ids, trigger_id)
        return True

    def _spawn_await_and_delete(
        self,
        event: Any,
        send_task: asyncio.Task,
        leftover: List[str],
        trigger_id: str,
    ) -> None:
        """当发送中途任务被 cancel 时，底层 OneBot 请求已经发出，需后台等它返回 message_id 后立刻撤回。"""
        async def _wait_and_del() -> None:
            ids: List[str] = list(leftover or [])
            try:
                sent_ids = await send_task
                if isinstance(sent_ids, list):
                    for sid in sent_ids:
                        if sid and sid not in ids:
                            ids.append(sid)
            except Exception:  # noqa: BLE001
                pass
            if ids:
                await self._delete_replies(event, ids, trigger_id)

        try:
            asyncio.get_running_loop().create_task(_wait_and_del())
        except Exception:  # noqa: BLE001
            pass

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

        gid = gid_of(event)
        is_group = bool(gid)
        sid = gid if is_group else uid_of(event)
        sid_int = int(sid) if sid.isdigit() else None

        ret: Any = None
        if is_group and sid_int is not None:
            ret = await bot.send_group_msg(group_id=sid_int, message=segs, **routing)
        elif not is_group and sid_int is not None:
            ret = await bot.send_private_msg(user_id=sid_int, message=segs, **routing)
        elif raw is not None and hasattr(raw, "get"):
            ret = await bot.send(event=raw, message=segs)
        else:
            raise ValueError(f"无法发送：session 非数字 {sid!r} 且无 raw event")

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
        """删除 bot 回复（``delete_msg``，str→int 双试），返回成功条数。"""
        done = await self._delete_ids(event, reply_ids)
        if reply_ids:
            self._info(
                f"撤回连带：删除 bot 回复 {done}/{len(reply_ids)} 条（消息 {trigger_id}）"
            )
        return done

    async def _delete_ids(self, event: Any, ids: List[str]) -> int:
        """OneBot ``delete_msg``（str→int 双试），返回成功条数。

        连带撤回复与「撤回询问消息」共用的底层；不写日志（各自的
        调用方按自己的措辞记）。
        """
        bot = getattr(event, "bot", None)
        call = getattr(bot, "call_action", None)
        if not callable(call):
            call = getattr(getattr(bot, "api", None), "call_action", None)
        if not callable(call):
            return 0

        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        routing = self._routing(raw)

        done = 0
        for rid in ids:
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
        return done
