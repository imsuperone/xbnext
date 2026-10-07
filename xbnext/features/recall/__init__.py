# -*- coding: utf-8 -*-
"""撤回取消请求（P16 · 用户拍板「撤回可以加上了」）。

用户撤回**触发 LLM 的那条消息** ⇒ 取消这条消息的在飞请求，省一次白烧
的 token，也避免 bot 对着已经撤回的消息回一整段。

实现（纯插件侧，不 patch core）：

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

范围：只掐**请求本身**；bot 已经发出去的回复不跟着撤回（二期看真机
反馈再说）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from . import service
from ..base import Feature


class RecallFeature(Feature):
    """见模块 docstring；只关心适配器早期钩子。"""

    key = "enable_recall_cancel"
    name = "撤回取消请求"
    description = "撤回触发消息后，取消这条消息的在飞 LLM 请求"
    #: 守候类功能，排在清洗 / 注入之后（order 无强依赖，登记要趁早）
    order = 90
    uses_adapter_hook = True

    def __init__(self) -> None:
        #: 在飞任务登记表：触发消息 message_id -> pipeline 任务
        self._inflight: Dict[str, asyncio.Task] = {}
        self._log: Optional[Any] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        self._log = getattr(runtime, "log", None)

    def on_unload(self) -> None:
        self._inflight.clear()
        self._log = None

    # ------------------------------------------------------------------
    # 适配器早期钩子
    # ------------------------------------------------------------------
    async def on_adapter_message(self, ctx: Any) -> None:
        event = ctx.event

        # ① 撤回 notice？命中就掐、掐完就走（撤回事件自己不登记）
        for raw in service.raw_sources(event):
            mid = service.parse_recall(raw)
            if mid is None:
                continue
            task = self._inflight.pop(mid, None)
            if task is not None and not task.done():
                try:
                    task.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._info(f"撤回命中：已取消在飞请求（消息 {mid}）")
                ctx.note(f"撤回命中：已取消消息 {mid} 的在飞请求")
            return

        # ② 普通消息：登记 message_id → 本管线任务
        mid = service.trigger_id(event)
        task = asyncio.current_task()
        if mid and task is not None:
            self._track(mid, task)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
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
