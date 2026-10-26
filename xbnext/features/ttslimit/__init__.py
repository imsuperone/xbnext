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

import os
import re
import wave
import struct


def smooth_tts_text(text: str) -> str:
    """平滑优化 TTS 输入文本，防止尾音强感叹号导致 FishAudio 喷气/爆破音。"""
    if not text or not isinstance(text, str):
        return text
    s = str(text).strip()
    # 1. 语气词（如 喵、啦、呀、哇、捏、呢、哦、啊等）后紧跟的 ！替换为柔和的 ~
    # 例如：啦喵！ -> 啦喵~ ； 大啦喵！ -> 大啦喵~
    s = re.sub(r"([喵啦呀哇捏呢哦啊吖咯滴吧~～])([！!]+)", r"\1~", s)
    # 2. 连续多个感叹号结尾收敛为 ~
    s = re.sub(r"([！!]{2,})$", "~", s)
    # 3. 句末单独的感叹号，若前面是软萌语气词，替换为 ~
    s = re.sub(r"([喵啦呀哇捏呢哦啊吖咯滴吧])[！!]$", r"\1~", s)
    return s


def apply_wav_fade_out(file_path: str, fade_ms: int = 35) -> bool:
    """对 wav 文件末尾施加线性淡出（Fade-out），消除硬切断时的电平爆音。"""
    try:
        if not file_path or not os.path.isfile(file_path):
            return False
        with wave.open(file_path, "rb") as wf:
            params = wf.getparams()
            n_channels = params.nchannels
            sampwidth = params.sampwidth
            framerate = params.framerate
            n_frames = params.nframes
            if n_frames <= 0 or sampwidth != 2:  # 标准 16-bit PCM
                return False
            frames_data = bytearray(wf.readframes(n_frames))

        fade_frames = min(n_frames, int(framerate * fade_ms / 1000.0))
        if fade_frames <= 0:
            return False

        bytes_per_frame = n_channels * 2
        total_fade_bytes = fade_frames * bytes_per_frame
        start_offset = len(frames_data) - total_fade_bytes

        for i in range(fade_frames):
            factor = 1.0 - (i / float(fade_frames))
            frame_idx = start_offset + i * bytes_per_frame
            for ch in range(n_channels):
                offset = frame_idx + ch * 2
                val = struct.unpack_from("<h", frames_data, offset)[0]
                new_val = max(-32768, min(32767, int(val * factor)))
                struct.pack_into("<h", frames_data, offset, new_val)

        with wave.open(file_path, "wb") as wf:
            wf.setparams(params)
            wf.writeframes(frames_data)
        return True
    except Exception:
        return False


def smooth_audio_tail(res: Any) -> Any:
    """对生成的 wav 音频在末尾施加 35ms 线性淡出。"""
    try:
        path = str(res) if res is not None else ""
        if path and path.lower().endswith(".wav") and os.path.isfile(path):
            apply_wav_fade_out(path, fade_ms=35)
    except Exception:
        pass
    return res



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
            # 1. 软化尾部强感叹号，避免 FishAudio 喷气爆音
            target_text = smooth_tts_text(text)
            res = await orig(target_text, *args, **kwargs)
            # 2. 尾音 35ms 平滑淡出，消除直流跳变噗噗声
            smooth_audio_tail(res)
            return res

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
