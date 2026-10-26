# -*- coding: utf-8 -*-
"""机器人与黑名单拦截功能（BotGuard Feature）。

在适配器早期钩子（event_message_type(ALL)）拦截指定机器人或黑名单账号的发言，
彻底杜绝多机器人同群互相接话、刷屏对轰与死循环消耗 token。
order = 5（最前执行优先级），在 core 判定空消息与大模型管线介入前第一时间掐断。

默认黑名单为空，遵循严格隐私规范，绝不硬编码任何 QQ 号。
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Set

from ... import log, switches
from ..base import Feature
from . import service

__all__ = ["BotGuardFeature"]


async def is_admin_event(event: Any) -> bool:
    """判定当前事件发送者是否具备管理员权限（超管/群主/管理员）。"""
    if event is None:
        return False

    # 1. 尝试 event.is_admin() (AstrBot 官方接口，支持 sync / async)
    try:
        fn = getattr(event, "is_admin", None)
        if callable(fn):
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, bool):
                return res
    except Exception:  # noqa: BLE001
        pass

    # 2. 检查 event.role (group admin / owner / master)
    try:
        role = str(getattr(event, "role", "") or "").lower()
        if role in ("admin", "owner", "master"):
            return True
        if role in ("member", "user"):
            return False
    except Exception:  # noqa: BLE001
        pass

    # 3. 检查 event.sender.role
    try:
        sender = getattr(event, "sender", None)
        if sender:
            s_role = str(getattr(sender, "role", "") or "").lower()
            if s_role in ("admin", "owner", "master"):
                return True
            if s_role in ("member", "user"):
                return False
    except Exception:  # noqa: BLE001
        pass

    # 4. 检查 sender ID 是否在 AstrBot 全局 admins_id 白名单中
    try:
        user_id = service.extract_sender_id(event)
        if user_id:
            ctx = getattr(event, "context", None)
            if ctx and hasattr(ctx, "get_config"):
                g_cfg = ctx.get_config()
                if isinstance(g_cfg, dict):
                    admins = g_cfg.get("admins_id") or []
                    if isinstance(admins, list) and user_id in [str(x).strip() for x in admins]:
                        return True
    except Exception:  # noqa: BLE001
        pass

    return False


class BotGuardFeature(Feature):
    """拦截其他机器人与黑名单账号消息，杜绝多机器人互相接话与死循环。"""

    key = "enable_bot_guard"
    name = "机器人拦截"
    description = "拦截指定机器人或黑名单账号的消息，防止多机器人互相接话与死循环大乱斗"
    #: 最前优先级：5 < 15(image_slim) < 20(quote_clean)
    order = 5
    #: 需要在消息进入核心管线前掐断
    uses_adapter_hook = True
    #: /xbnext botguard 指令
    command = "botguard"

    def __init__(self) -> None:
        self._runtime: Optional[Any] = None
        self._log: Optional[Any] = None

    def on_load(self, runtime: Any) -> None:
        self._runtime = runtime
        self._log = getattr(runtime, "log", None)

    def on_unload(self) -> None:
        self._runtime = None
        self._log = None

    async def on_adapter_message(self, ctx: Any) -> None:
        """适配器早期钩子：发现黑名单或机器人消息即刻拦截。

        红线 14：早期钩子整体异常自吞，绝不冒泡给用户。
        """
        event = getattr(ctx, "event", None)
        if event is None:
            return

        try:
            # 1. 破坏性指令防多 Bot 串台保护（群聊中的 /new, /reset 等必须 @ 当前机器人）
            if ctx.conf.bool("bot_guard_require_at_for_reset", True):
                if service.is_group_event(event):
                    msg_text = getattr(event, "message_str", "") or ""
                    if not msg_text:
                        msg_obj = getattr(event, "message_obj", None)
                        if msg_obj is not None:
                            msg_text = getattr(msg_obj, "raw_message", "") or ""
                    if service.is_destructive_reset_command(msg_text):
                        self_id = service.extract_self_id(event)
                        if self_id and not service.is_at_self(event, self_id):
                            self._stop_event_safely(event)
                            log.emit(
                                self._log,
                                "info",
                                f"破坏性指令保护：群聊收到重置指令 '{msg_text.strip()}' 但未 @ 本机器人(self_id={self_id})，已安全忽略防止多Bot冲突",
                            )
                            if ctx.conf.bool("debug_log"):
                                ctx.note(f"BotGuard 拦截未@自身的破坏性指令：{msg_text.strip()}")
                            return

            # 2. 黑名单账号拦截
            blacklist_raw = ctx.conf.text("bot_guard_blacklist", "")
            blacklist = service.parse_blacklist(blacklist_raw)
            if not blacklist:
                return  # 未配置黑名单，正常放行

            sender_id = service.extract_sender_id(event)
            if not sender_id:
                return

            self_id = service.extract_self_id(event)
            if not service.is_sender_blacklisted(sender_id, blacklist, self_id=self_id):
                return

            mode = str(ctx.conf.text("bot_guard_mode", "drop") or "drop").strip().lower()

            if mode == "no_active":
                # no_active 模式：拦截主动艾特/唤醒，防止机器人间互相触发
                is_wake = getattr(event, "is_at_or_wake_command", False)
                if is_wake:
                    self._stop_event_safely(event)
                    log.emit(
                        self._log,
                        "info",
                        f"机器人拦截（防唤醒）：已拦截来自账号 {sender_id} 的唤醒尝试",
                    )
                    if ctx.conf.bool("debug_log"):
                        ctx.note(f"BotGuard 拦截黑名单唤醒：sender={sender_id}")
                else:
                    # 普通背景消息：禁止调用大模型回复，但允许事件静默流过（如供群聊历史记录）
                    try:
                        event.call_llm = False
                    except Exception:  # noqa: BLE001
                        pass
                    if ctx.conf.bool("debug_log"):
                        ctx.note(f"BotGuard 禁用黑名单被动响应：sender={sender_id}")
            else:
                # drop 模式（默认推荐）：彻底静默丢弃，零消耗、绝不死循环
                self._stop_event_safely(event)
                log.emit(
                    self._log,
                    "info",
                    f"机器人拦截：已屏蔽账号 {sender_id} 的消息，防止死循环",
                )
                if ctx.conf.bool("debug_log"):
                    ctx.note(f"BotGuard 彻底静默拦截：sender={sender_id}")

        except Exception as exc:  # noqa: BLE001
            # 异常自吞保底
            if ctx.conf.bool("debug_log"):
                ctx.note(f"BotGuard 拦截判定异常：{exc!r}")

    async def on_llm_request(self, ctx: Any) -> None:
        """LLM 请求阶段防御性兜底：若早期钩子未能拦截，此处阻止 LLM 调用。"""
        event = getattr(ctx, "event", None)
        if event is None:
            return

        try:
            blacklist_raw = ctx.conf.text("bot_guard_blacklist", "")
            blacklist = service.parse_blacklist(blacklist_raw)
            if not blacklist:
                return

            sender_id = service.extract_sender_id(event)
            self_id = service.extract_self_id(event)
            if service.is_sender_blacklisted(sender_id, blacklist, self_id=self_id):
                # 命中黑名单，截断请求
                self._stop_event_safely(event)
                req = getattr(ctx, "req", None)
                if req is not None and hasattr(req, "prompt"):
                    req.prompt = ""
                ctx.note(f"BotGuard LLM兜底拦截：sender={sender_id}")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _stop_event_safely(event: Any) -> None:
        """彻底安全截断 AstrBot 事件流。"""
        try:
            event.stop_event()
        except Exception:  # noqa: BLE001
            pass
        try:
            event.call_llm = False
        except Exception:  # noqa: BLE001
            pass
        try:
            event._has_send_oper = True
        except Exception:  # noqa: BLE001
            pass
        try:
            result = getattr(event, "get_result", None)
            if callable(result):
                res = result()
                if res is not None and hasattr(res, "chain") and isinstance(res.chain, list):
                    res.chain.clear()
        except Exception:  # noqa: BLE001
            pass

    async def handle_command(self, event: Any, args: List[str], conf: Any = None) -> str:
        """处理 /xbnext botguard 子指令。

        非管理员执行保持静默（红线 5），管理员可查看、添加、删除拦截账号及切换模式。
        """
        # 管理员权限门禁：非超管保持静默
        if not await is_admin_event(event):
            return ""

        if conf is None:
            return "配置句柄未就绪，请稍后再试。"

        action = args[0].lower() if args else "status"

        blacklist_raw = conf.text("bot_guard_blacklist", "")
        current_set = service.parse_blacklist(blacklist_raw)
        current_mode = str(conf.text("bot_guard_mode", "drop") or "drop").strip().lower()
        is_enabled = conf.enabled("enable_bot_guard")

        # 1. 状态查看
        if action in ("status", "list", "show", "查", "状态", "名单"):
            masked_list = [service.mask_id(x) for x in sorted(current_set)]
            display_list = ", ".join(masked_list) if masked_list else "（当前无拦截账号）"
            mode_desc = "彻底静默拦截 (drop，推荐)" if current_mode == "drop" else "仅防主动接话 (no_active)"
            return (
                "🛡️【XBNEXT · 机器人拦截】\n"
                "━━━━━━━━━━━━━━\n"
                f"• 功能状态：{'✅ 已开启' if is_enabled else '❌ 已关闭'}\n"
                f"• 拦截模式：{mode_desc}\n"
                f"• 拦截名单：共 {len(current_set)} 个账号\n"
                f"• 当前名单：{display_list}\n"
                "━━━━━━━━━━━━━━\n"
                "👉 快捷操作：\n"
                "• /xbnext botguard add <QQ> — 添加拦截账号\n"
                "• /xbnext botguard del <QQ> — 移除拦截账号\n"
                "• /xbnext botguard mode drop|no_active — 切换模式\n"
                "• /xbnext botguard clear — 清空黑名单"
            )

        # 2. 添加黑名单
        if action in ("add", "添加", "+"):
            to_add = args[1:]
            if not to_add:
                return "请指定要添加的账号 QQ，例如：/xbnext botguard add 12345678"
            new_ids = service.parse_blacklist(" ".join(to_add))
            if not new_ids:
                return "未解析到有效的账号 ID。"

            # 守卫：不可添加 bot 自身
            self_id = service.extract_self_id(event)
            if self_id and self_id in new_ids:
                new_ids.remove(self_id)
                if not new_ids:
                    return "不可将机器人自身账号加入拦截黑名单。"

            added = new_ids - current_set
            if not added:
                return "所指定的账号已在拦截名单中，无需重复添加。"

            updated_set = current_set | added
            res = await switches.apply_conf(
                conf, "bot_guard_blacklist", service.format_blacklist(updated_set)
            )
            if res.get("error"):
                return f"保存配置失败：{res['error']}"

            return (
                f"✅ 已成功添加 {len(added)} 个拦截账号："
                f"{', '.join(service.mask_id(x) for x in sorted(added))}。\n"
                f"当前拦截黑名单共 {len(updated_set)} 个账号。"
            )

        # 3. 移除黑名单
        if action in ("del", "delete", "rm", "remove", "删除", "移除", "-"):
            to_del = args[1:]
            if not to_del:
                return "请指定要移除的账号 QQ，例如：/xbnext botguard del 12345678"
            del_ids = service.parse_blacklist(" ".join(to_del))
            if not del_ids:
                return "未解析到有效的账号 ID。"

            removed = current_set & del_ids
            if not removed:
                return "指定的账号不在当前拦截名单中。"

            updated_set = current_set - removed
            res = await switches.apply_conf(
                conf, "bot_guard_blacklist", service.format_blacklist(updated_set)
            )
            if res.get("error"):
                return f"保存配置失败：{res['error']}"

            return (
                f"✅ 已从拦截名单中移除 {len(removed)} 个账号："
                f"{', '.join(service.mask_id(x) for x in sorted(removed))}。\n"
                f"当前拦截名单剩余 {len(updated_set)} 个账号。"
            )

        # 4. 模式切换
        if action in ("mode", "模式"):
            if len(args) < 2:
                return f"当前拦截模式为：{current_mode}。可选模式：drop（彻底静默）或 no_active（仅防接话）。"
            target_mode = args[1].lower().strip()
            if target_mode not in ("drop", "no_active"):
                return "模式参数无效，仅支持 drop 或 no_active。"
            res = await switches.apply_conf(conf, "bot_guard_mode", target_mode)
            if res.get("error"):
                return f"切换模式失败：{res['error']}"
            desc = "彻底静默拦截 (drop)" if target_mode == "drop" else "仅防主动接话 (no_active)"
            return f"✅ 已成功将机器人拦截模式切换为：{desc}。"

        # 5. 清空名单
        if action in ("clear", "empty", "清空", "重置"):
            if not current_set:
                return "当前拦截名单本就为空，无需清空。"
            res = await switches.apply_conf(conf, "bot_guard_blacklist", "")
            if res.get("error"):
                return f"清空失败：{res['error']}"
            return f"✅ 已成功清空机器人拦截黑名单（已清除 {len(current_set)} 个账号）。"

        return f"❓ 未知操作「{action}」，发送 /xbnext botguard 查看可用操作。"
