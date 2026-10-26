# -*- coding: utf-8 -*-
"""灵性主动交互功能（Proactive）。

让永雏塔菲告别死板的随机概率主动回复，实现：
1. 意图感知智能插话（根据发言关键词、话题倾向与好感度决定是否插话）；
2. 顶掉 AstrBot 自带的死板随机概率（suppress_active）；
3. 提示词动态注入（告知模型是在主动插话，以自然、傲娇生动的语气互动）；
4. 定时冷场破冰与主动提问（群冷清时动态随机冒泡，适宜时段 10:00~23:30）；
5. 记忆与档案挖坟联动（主动关心群友历史话题）；
6. 默认关闭（enable_proactive: false）。
"""

from __future__ import annotations

import asyncio
import datetime
import random
import sys
import time
from typing import Any, List, Optional

from ..base import Feature
from . import service


class ProactiveFeature(Feature):
    """灵性主动交互功能。"""

    key: str = "enable_proactive"
    name: str = "灵性主动交互"
    description: str = "意图感知智能插话、冷场破冰提问、记忆挖坟与好感度联动"
    order: int = 25
    uses_adapter_hook: bool = True
    command: str = "proactive"

    def __init__(self) -> None:
        self.runtime: Any = None
        self._bg_task: Optional[asyncio.Task] = None
        self._total_proactive_count: int = 0
        self._total_icebreak_count: int = 0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def on_load(self, runtime: Any) -> None:
        """插件加载时启动冷场检测后台循环。"""
        self.runtime = runtime
        if self._bg_task is None or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._icebreaker_loop())

    async def on_unload(self) -> None:
        """插件卸载时取消后台任务。"""
        if self._bg_task is not None and not self._bg_task.done():
            self._bg_task.cancel()
            try:
                await self._bg_task
            except (asyncio.CancelledError, Exception):
                pass
            self._bg_task = None

    # ------------------------------------------------------------------
    # 核心钩子
    # ------------------------------------------------------------------
    async def on_adapter_message(self, ctx: Any) -> None:
        """适配器早期钩子：评估意图与智能插话判定。"""
        event = ctx.event
        if event is None:
            return

        # 私聊不执行群主动交互
        is_priv = False
        try:
            fn = getattr(event, "is_private_chat", None)
            if callable(fn):
                is_priv = bool(fn())
        except Exception:
            pass
        if is_priv:
            return

        gid = ""
        try:
            fn = getattr(event, "get_group_id", None)
            if callable(fn):
                gid = str(fn() or "").strip()
        except Exception:
            pass

        # 记录群活跃时间戳（供冷场破冰判定）
        if gid:
            service.record_group_activity(gid)

        # 如果已经判定为唤醒或 @bot，交由正常管线处理
        is_wake = bool(getattr(event, "is_at_or_wake_command", False) or getattr(event, "is_wake", False))
        if is_wake:
            return

        # 若功能未启用，不参与干预
        if not ctx.enabled(self.key):
            return

        # 提取发言内容与发送者
        text = str(getattr(event, "message_str", "") or "").strip()
        uid = ""
        try:
            fn = getattr(event, "get_sender_id", None)
            if callable(fn):
                uid = str(fn() or "").strip()
        except Exception:
            pass

        # 尝试联动 self_learning 获取群友当前好感度
        affection = None
        try:
            sl_mod = sys.modules.get("astrbot_plugin_self_learning")
            if sl_mod and hasattr(sl_mod, "get_affection"):
                affection = sl_mod.get_affection(gid, uid)
        except Exception:
            pass

        # 智能判定是否插话
        should_reply, reason = service.should_proactively_reply(
            msg_str=text,
            sender_id=uid,
            group_id=gid,
            conf=ctx.conf,
            affection=affection,
        )

        if should_reply:
            # 激活唤醒标志，直接放行进入 LLM 阶段
            try:
                event.is_at_or_wake_command = True
                event.is_wake = True
                event.set_extra("_xbnext_proactive", reason)
                self._total_proactive_count += 1
                ctx.note(f"灵性主动插话触发: {reason}")
            except Exception:
                pass
        else:
            # 未命中插话条件：标记压制 AstrBot 核心自带的死板随机概率
            try:
                event.set_extra("_xbnext_suppress_active", True)
            except Exception:
                pass

    async def on_llm_request(self, ctx: Any) -> None:
        """LLM 请求阶段：压制死板概率或注入灵性插话提示词。"""
        event = ctx.event
        if event is None:
            return

        # 1. 顶掉自带主动概率：若被标记为抑制且未命中灵性插话，拦截本次请求
        suppress = False
        proactive_reason = None
        try:
            suppress = bool(event.get_extra("_xbnext_suppress_active"))
            proactive_reason = event.get_extra("_xbnext_proactive")
        except Exception:
            pass

        if suppress and not proactive_reason:
            try:
                fn = getattr(event, "stop_event", None)
                if callable(fn):
                    fn()
                ctx.note("已压制 AstrBot 原生随机主动概率")
                return
            except Exception:
                pass

        # 2. 灵性主动插话：向请求中注入提示词，引导模型以生动、真实的口吻插话
        if proactive_reason:
            hint = service.build_proactive_prompt_hint(str(proactive_reason))
            req = ctx.req
            if req is not None:
                injected = False
                # 尝试 AstrBot TextPart 注入
                try:
                    from astrbot.core.agent.message import TextPart
                    extra_parts = getattr(req, "extra_user_content_parts", None)
                    if isinstance(extra_parts, list):
                        extra_parts.append(TextPart(text=hint))
                        injected = True
                except Exception:
                    pass

                # 兜底：注入系统提示词
                if not injected and hasattr(req, "system_prompt"):
                    try:
                        cur_sys = str(getattr(req, "system_prompt", "") or "")
                        req.system_prompt = (cur_sys + "\n\n" + hint).strip()
                        injected = True
                    except Exception:
                        pass
                if injected:
                    ctx.note(f"主动插话提示已注入（{proactive_reason}）")

    # ------------------------------------------------------------------
    # 冷场破冰后台任务
    # ------------------------------------------------------------------
    async def _icebreaker_loop(self) -> None:
        """定时检测冷场群并适时发起破冰。"""
        while True:
            try:
                await asyncio.sleep(60.0)
                if self.runtime is None or not hasattr(self.runtime, "conf"):
                    continue

                conf = self.runtime.conf
                if not conf.enabled(self.key):
                    continue
                if not conf.bool("proactive_icebreaker", True):
                    continue

                # 检查当前时间（仅在 10:00 ~ 23:30 适宜社交时间段冒泡）
                now_dt = datetime.datetime.now()
                now_min = now_dt.hour * 60 + now_dt.minute
                if now_min < 10 * 60 or now_min > 23 * 60 + 30:
                    continue

                now_ts = time.time()
                base_minutes = conf.int("proactive_idle_minutes", 90)
                base_delay = max(1800.0, float(base_minutes * 60))

                seen_groups = getattr(self.runtime, "_seen_groups", {})
                for gid, info in list(seen_groups.items()):
                    gid_str = str(gid or "").strip()
                    if not gid_str:
                        continue
                    # 检查分群白名单
                    if not conf.is_group_allowed(gid_str):
                        continue

                    # 计算距上次群消息的冷场时间
                    last_seen = service._GROUP_LAST_SEEN_TS.get(gid_str, now_ts)
                    idle_sec = now_ts - last_seen

                    target_delay = service._GROUP_NEXT_ICEBREAK_DELAY.get(gid_str)
                    if target_delay is None:
                        # 动态随机间隔：基准时间的 0.8 ~ 1.5 倍（打破死板规律）
                        target_delay = base_delay * random.uniform(0.8, 1.5)
                        service._GROUP_NEXT_ICEBREAK_DELAY[gid_str] = target_delay

                    last_break = service._GROUP_LAST_ICEBREAK_TS.get(gid_str, 0.0)
                    # 至少冷场达标且距上次破冰超过 2.5 小时
                    if idle_sec >= target_delay and (now_ts - last_break > 9000.0):
                        line = service.pick_icebreaker(gid_str, runtime=self.runtime)
                        umo = str(info.get("umo") or f"aiocqhttp:GroupMessage:{gid_str}")
                        ok, _ = await service.send_group_message(
                            context=self.runtime.context,
                            group_id=gid_str,
                            message=line,
                            umo=umo,
                        )
                        if ok:
                            service._GROUP_LAST_ICEBREAK_TS[gid_str] = now_ts
                            # 重设下次动态间隔
                            service._GROUP_NEXT_ICEBREAK_DELAY[gid_str] = base_delay * random.uniform(0.8, 1.5)
                            # 假装重置活跃，避免连续触发
                            service.record_group_activity(gid_str)
                            self._total_icebreak_count += 1
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10.0)

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------
    async def handle_command(self, event: Any, args: List[str], conf: Any) -> str:
        """处理 ``/xbnext proactive ...``。"""
        sub = str(args[0]).lower().strip() if args else "status"

        if sub in ("status", "状态", "info"):
            is_on = conf.enabled(self.key)
            ice_on = conf.bool("proactive_icebreaker", True)
            idle_min = conf.int("proactive_idle_minutes", 90)
            dig_on = conf.bool("proactive_dig_memory", True)
            return (
                "🎭【XBNEXT 灵性主动交互】\n"
                f"• 总开关状态：{'🟢 已开启' if is_on else '⚪ 已关闭'}\n"
                f"• 冷场破冰冒泡：{'🟢 开启' if ice_on else '⚪ 关闭'}（判定基准: {idle_min} 分钟）\n"
                f"• 记忆挖坟联动：{'🟢 开启' if dig_on else '⚪ 关闭'}\n"
                f"• 累计主动插话：{self._total_proactive_count} 次\n"
                f"• 累计冷场破冰：{self._total_icebreak_count} 次\n"
                "💡 提示：在 WebUI 控制台可随时开启并微调破冰间隔。"
            )

        if sub in ("test", "测试", "破冰"):
            gid = ""
            try:
                fn = getattr(event, "get_group_id", None)
                if callable(fn):
                    gid = str(fn() or "").strip()
            except Exception:
                pass
            line = service.pick_icebreaker(gid, runtime=self.runtime)
            return f"🎭 [灵性主动测试破冰文案]\n{line}"

        return "用法：/xbnext proactive status (查看状态) 或 /xbnext proactive test (测试破冰文案)"
