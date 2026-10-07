# -*- coding: utf-8 -*-
"""单次请求上下文。

每个 ``on_llm_request`` 造一个 :class:`RequestContext`，按固定顺序传给各
功能。功能只跟它打交道，不直接摸全局状态 —— 这样功能可以脱离 astrbot
单测（把 ctx 换成假对象即可）。
"""

from __future__ import annotations

from typing import Any, List, Optional

from . import injector
from .config import Config


class RequestContext:
    """一次 LLM 请求的执行上下文。"""

    __slots__ = (
        "event",
        "req",
        "conf",
        "runtime",
        "notes",
        "injected",
        "parts_cleaned",
        "slimmed_images",
        "text_part_cls",
    )

    def __init__(
        self,
        event: Any,
        req: Any,
        conf: Config,
        runtime: Any = None,
        text_part_cls: Any = None,
    ):
        self.event = event
        self.req = req
        self.conf = conf
        self.runtime = runtime
        #: 调试备注，``debug_log`` 打开时打进日志
        self.notes: List[str] = []
        #: 本次成功注入的片段数
        self.injected = 0
        #: 本轮被 R2 改写/移除的 ``extra_user_content_parts`` 内容块数
        self.parts_cleaned = 0
        #: 本轮被历史图片瘦身换成占位的旧图片数（日志汇总用）
        self.slimmed_images = 0
        #: 注入用的 ``TextPart`` 实现；``None`` = 走 astrbot 真身，
        #: 单测里传假实现即可在无 astrbot 环境跑通注入链路
        self.text_part_cls = text_part_cls

    # -- 开关 ---------------------------------------------------------
    def enabled(self, key: str) -> bool:
        """某功能当前是否该跑（只看配置开关；功能自身不必再判）。"""
        if not self.conf.enabled(key):
            self.note(f"{key} 已关闭")
            return False
        return True

    # -- 注入 ---------------------------------------------------------
    def inject(self, text: str, text_part_cls: Any = None) -> bool:
        """走统一出口注入一段 temp 文本；返回是否成功。"""
        cls = text_part_cls if text_part_cls is not None else self.text_part_cls
        ok = injector.inject_text(self.req, text, text_part_cls=cls)
        if ok:
            self.injected += 1
        return ok

    def prompt(self) -> str:
        """当前请求的提示词（可能为 ``""``）。"""
        try:
            value = getattr(self.req, "prompt", None)
            return value if isinstance(value, str) else ""
        except Exception:  # noqa: BLE001
            return ""

    def set_prompt(self, text: str) -> bool:
        """改写提示词（清洗类功能用）；失败返回 ``False``。"""
        if not isinstance(text, str):
            return False
        try:
            self.req.prompt = text
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- 调试 ---------------------------------------------------------
    def note(self, message: str) -> None:
        """记一条调试备注（不抛异常）。"""
        try:
            if len(self.notes) < 50:
                self.notes.append(str(message))
        except Exception:  # noqa: BLE001
            pass

    def flush_notes(self, logger: Any = None) -> None:
        """把备注打进日志（``debug_log`` 关闭时不调用即可）。"""
        if not self.notes:
            return
        log = logger or (getattr(self.runtime, "log", None) if self.runtime else None)
        if log is None:
            return
        for line in self.notes:
            try:
                log.debug(f"[XBNEXT] {line}")
            except Exception:  # noqa: BLE001
                pass

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (
            f"<RequestContext injected={self.injected} "
            f"prompt_len={len(self.prompt())}>"
        )


__all__ = ["RequestContext"]
