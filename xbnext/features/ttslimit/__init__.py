# -*- coding: utf-8 -*-
"""TTS 字数上限控制（防止超长回复触发 FishAudio 等 TTS 接口超时报错）。

当机器人的单次回复纯文本总字数超过 ``tts_max_chars``（默认 100 字）时，
自动跳过 AstrBot 核心 ``result_decorate/stage.py`` 的 ``tts_provider.get_audio``
语音合成，直接以文本（或交由下游 xbimg 转图）发送。
"""

from __future__ import annotations

import functools
from typing import Any, Optional

from ... import log
from ..base import Feature


class TtsLimitFeature(Feature):
    """控制 AstrBot TTS 触发的最大字数上限。"""

    key = "enable_tts_limit"
    name = "TTS 字数上限"
    description = "回复文本超过设定字数时自动跳过 TTS 语音合成直接发文字，避免长文卡死语音接口"
    order = 98
    uses_decorating_hook = True

    def __init__(self) -> None:
        self._runtime: Optional[Any] = None
        self._log: Optional[Any] = None

    def on_load(self, runtime: Any) -> None:
        self._runtime = runtime
        self._log = getattr(runtime, "log", None)
        self._patch_tts_providers()

    def on_unload(self) -> None:
        self._unpatch_tts_providers()
        self._runtime = None
        self._log = None

    async def on_llm_request(self, ctx: Any) -> None:
        """满足 Feature 接口约定；顺手确保新挂载的 TTS Provider 已打补丁。"""
        self._patch_tts_providers()

    async def on_decorating_result(self, ctx: Any) -> None:
        """在 result_decorate 阶段统计文本字数，超限时标记本条事件跳过 TTS。"""
        self._patch_tts_providers()
        event = getattr(ctx, "event", None)
        if event is None:
            return
        max_chars = ctx.conf.int("tts_max_chars", 100)
        if max_chars <= 0:
            return
        try:
            result = event.get_result()
        except Exception:  # noqa: BLE001
            result = None
        chain = getattr(result, "chain", None) if result is not None else None
        if not chain:
            return
        total_chars = 0
        for comp in chain:
            if type(comp).__name__ == "Plain":
                txt = getattr(comp, "text", "") or ""
                if isinstance(txt, str):
                    total_chars += len(txt.strip())
        if total_chars > max_chars:
            try:
                setattr(event, "_xbnext_skip_tts", True)
            except Exception:  # noqa: BLE001
                pass
            try:
                if hasattr(result, "use_tts"):
                    result.use_tts = False
            except Exception:  # noqa: BLE001
                pass
            ctx.note(f"TTS 字数超限（{total_chars} > {max_chars}），本轮跳过语音转换")
            log.emit(
                self._log,
                "info",
                f"TTS 字数超限（{total_chars} 字 > 上限 {max_chars} 字），已跳过语音转换直接发送文本",
            )

    def _get_provider_manager(self) -> Any:
        rt = self._runtime
        if rt is None:
            return None
        star = getattr(rt, "_star", None) or getattr(getattr(rt, "kv", None), "_owner", None)
        context = getattr(star, "context", None) if star is not None else None
        return getattr(context, "provider_manager", None) if context is not None else None

    def _iter_tts_providers(self):
        pm = self._get_provider_manager()
        if pm is None:
            return
        seen = set()
        curr = getattr(pm, "curr_tts_inst", None)
        if curr is not None:
            seen.add(id(curr))
            yield curr
        for inst in list(getattr(pm, "tts_provider_insts", None) or []):
            if inst is not None and id(inst) not in seen:
                seen.add(id(inst))
                yield inst

    def _patch_tts_providers(self) -> None:
        try:
            for inst in self._iter_tts_providers() or []:
                self._patch_one(inst)
        except Exception:  # noqa: BLE001
            pass

    def _patch_one(self, inst: Any) -> None:
        if getattr(inst, "_xbnext_tts_patched", False) is True:
            return
        orig = getattr(inst, "get_audio", None)
        if not callable(orig):
            return
        feat_self = self

        @functools.wraps(orig)
        async def _guarded_get_audio(text: str, *args: Any, **kwargs: Any) -> Any:
            rt = feat_self._runtime
            if rt is not None and rt.conf.enabled("enable_tts_limit"):
                limit = rt.conf.int("tts_max_chars", 100)
                clean_len = len(str(text or "").strip())
                if limit > 0 and clean_len > limit:
                    log.emit(
                        feat_self._log,
                        "info",
                        f"TTS 字数超限（{clean_len} 字 > 上限 {limit} 字），已拦截 get_audio 语音合成",
                    )
                    return None
            return await orig(text, *args, **kwargs)

        try:
            inst._xbnext_orig_get_audio = orig
            inst.get_audio = _guarded_get_audio
            inst._xbnext_tts_patched = True
        except Exception:  # noqa: BLE001
            pass

    def _unpatch_tts_providers(self) -> None:
        try:
            for inst in self._iter_tts_providers() or []:
                if getattr(inst, "_xbnext_tts_patched", False) is not True:
                    continue
                orig = getattr(inst, "_xbnext_orig_get_audio", None)
                if callable(orig):
                    try:
                        inst.get_audio = orig
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    delattr(inst, "_xbnext_tts_patched")
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass


__all__ = ["TtsLimitFeature"]
