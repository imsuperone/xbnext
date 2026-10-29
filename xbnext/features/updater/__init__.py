# -*- coding: utf-8 -*-
"""XBNEXT 插件一键检查与更新功能入口。"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ... import PLUGIN_NAME
from ...commands import UPDATER_ACTIONS
from ...permissions import is_admin_event
from ...schedule import next_run_delay
from ..base import Feature
from .service import (
    DEFAULT_TARGET_PLUGINS,
    UpdaterService,
    format_update_report,
    format_version,
    no_update_message,
)


async def send_private_notification(context: Any, qq: Any, message: str) -> Tuple[bool, str]:
    """向指定 QQ 账号发送私聊通知消息（兼容 AstrBot 平台适配器与 aiocqhttp call_action）。"""
    if not qq:
        return False, "QQ 号未填写"
    user_id = str(qq).strip()
    if not user_id.isdigit():
        return False, f"无效的 QQ 号: {qq}（必须为纯数字）"

    sent = False
    last_err = ""

    # 1. 优先从 context.platform_manager / platform_insts 遍历 bot 客户端
    try:
        pm = getattr(context, "platform_manager", None)
        insts = (
            getattr(pm, "platform_insts", None)
            or getattr(pm, "platforms", None)
            or getattr(context, "platforms", None)
            or []
        )
        if isinstance(insts, dict):
            insts = list(insts.values())

        for inst in list(insts):
            getter = getattr(inst, "get_client", None)
            client = getter() if callable(getter) else getattr(inst, "bot", None)
            if not client:
                client = inst

            call = getattr(client, "call_action", None) or getattr(client, "call_api", None)
            if callable(call):
                try:
                    await asyncio.wait_for(call("send_private_msg", user_id=int(user_id), message=message), timeout=6.0)
                    sent = True
                    break
                except Exception as ex1:
                    try:
                        await asyncio.wait_for(call("send_msg", user_id=int(user_id), message_type="private", message=message), timeout=6.0)
                        sent = True
                        break
                    except Exception as ex2:
                        last_err = f"{ex1} / {ex2}"
    except Exception as ex:
        last_err = str(ex)

    # 2. 备选方案：通过 context.send_message
    if not sent and hasattr(context, "send_message"):
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain
            chain = MessageChain([Plain(message)])
            target = f"aiocqhttp:PrivateMessage:{user_id}"
            await context.send_message(target, chain)
            sent = True
        except Exception as ex:
            if not last_err:
                last_err = str(ex)

    if sent:
        return True, "发送成功"
    return False, last_err or "未检测到可用的 OneBot/aiocqhttp 机器人连接"


class UpdaterFeature(Feature):
    """插件一键检查与更新功能。"""

    key: str = "enable_updater"
    name: str = "插件一键更新"
    description: str = "通过 AstrBot OpenAPI 接口检查并一键更新选定插件"
    order: int = 99
    command: str = "updater"

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._runtime: Optional[Any] = None
        self._bg_tasks: set = set()

    async def on_load(self, runtime: Any) -> None:
        """插件加载时启动定时自动检测循环。"""
        self._close_task()
        self._runtime = runtime
        try:
            self._task = asyncio.create_task(self._schedule_loop())
        except Exception:
            self._task = None
        self._warm_market_cache(runtime)

    def _warm_market_cache(self, runtime: Any) -> None:
        """后台预热市场索引：2.5MB 的 JSON 冷启动可能下载一分多钟，
        放到线程里，避免用户第一次「检查更新」时干等。"""
        try:
            warm = UpdaterService(getattr(runtime, "conf", None))
            if not warm.enabled:
                return
            threading.Thread(
                target=warm.fetch_market_registry_sync,
                kwargs={"wait": 120.0},
                name="xbnext-market-warm",
                daemon=True,
            ).start()
        except Exception:
            pass

    def on_unload(self) -> None:
        """卸载时安全取消定时任务。"""
        self._close_task()
        self._runtime = None

    def _close_task(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        try:
            if not task.done():
                task.cancel()
        except Exception:
            pass


    async def _dispatch_update_success_notification(
        self, updater: UpdaterService, updates: List[Dict[str, Any]], apply_res: Dict[str, Any]
    ) -> None:
        """定时自动更新后，向设定的 QQ 发送统一格式的私聊报告。"""
        qq_list = updater.notify_qqs
        if not updates or not qq_list:
            return

        results = [
            r for r in (apply_res.get("results") or []) if not r.get("no_change")
        ]
        if not results:
            return

        msg = format_update_report(
            "🤖【XBNEXT 自动更新报告】",
            results,
            intro=f"定时任务（每日 {updater.schedule_time}）已完成以下更新：",
            show_hint=False,
        )

        ctx = getattr(self._runtime, "context", None)
        log = getattr(self._runtime, "log", None)
        for q in qq_list:
            ok, err = await send_private_notification(ctx, q, msg)
            if not ok and log and hasattr(log, "warning"):
                log.warning(f"[插件定时更新] 向 QQ {q} 推送更新报告失败：{err}")

    async def _schedule_loop(self) -> None:
        """后台每日定时更新循环（默认每天 04:30 自动检测并执行更新，无更新时完全静默）。"""
        try:
            # 启动 30 秒后开始进入定时循环
            await asyncio.sleep(30)
            while True:
                conf = getattr(self._runtime, "conf", None)
                ctx = getattr(self._runtime, "context", None)
                updater = UpdaterService(conf, context=ctx)
                sched_time = updater.schedule_time

                # 计算到下一次 schedule_time（默认 04:30）的剩余秒数
                total_delay = next_run_delay(datetime.now(), sched_time)

                # 分段休眠，以支持热改配置即时生效
                while total_delay > 0:
                    step = min(total_delay, 60.0)
                    await asyncio.sleep(step)
                    total_delay -= step
                    cur_conf = getattr(self._runtime, "conf", None)
                    cur_updater = UpdaterService(cur_conf, context=ctx)
                    if cur_updater.schedule_time != sched_time:
                        sched_time = cur_updater.schedule_time
                        total_delay = next_run_delay(datetime.now(), sched_time)

                conf = getattr(self._runtime, "conf", None)
                updater = UpdaterService(conf, context=ctx)
                if not (updater.enabled and updater.schedule_enabled):
                    await asyncio.sleep(70)
                    continue

                log = getattr(self._runtime, "log", None)
                if log and hasattr(log, "info"):
                    log.info(f"[插件定时更新] 到达每日自动更新时刻 ({updater.schedule_time})，开始检测插件更新...")

                try:
                    res = await updater.check_updates()
                    has_update = res.get("has_new_version", False)
                    plugins = res.get("plugins") or []
                    updates = [p for p in plugins if p.get("has_update")]
                    update_names = [p.get("name") for p in updates if p.get("name")]

                    if not has_update or not updates:
                        # 没有更新则完全静默，绝不发送通知骚扰管理员！
                        if log and hasattr(log, "info"):
                            log.info("[插件定时更新] 选定插件均处于最新状态，无更新，静默跳过。")
                        await asyncio.sleep(70)
                        continue

                    # 发现新版本：自动执行拉取与热重载更新
                    if log and hasattr(log, "info"):
                        log.info(f"[插件定时更新] 发现 {len(updates)} 个插件有新版本，开始自动更新: {', '.join(update_names)}")

                    apply_res = await updater.apply_updates(update_names, force=False)

                    # 更新完成后，若开启了通知推送且配置了 QQ，发送主动推送
                    if updater.notify_enabled and updater.notify_qqs:
                        await self._dispatch_update_success_notification(updater, updates, apply_res)
                except Exception as ex:
                    log = getattr(self._runtime, "log", None)
                    if log and hasattr(log, "warning"):
                        log.warning(f"[插件定时更新] 自动更新异常: {ex!r}")

                # 休眠 70 秒，避免在同一分钟内重复触发
                await asyncio.sleep(70)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def handle_command(self, event: Any, args: List[str], conf: Any) -> str:
        """处理 /xbnext 检查更新 与 /xbnext 一键更新 指令。"""
        # 仅限 AstrBot 管理员使用，普通用户拦截并阻止刷屏
        if not await is_admin_event(event):
            return "⚠️ 权限不足：插件检查与更新指令仅限 AstrBot 管理员使用。"

        sub_arg = (args[0] if args else "").strip().lower()
        # 子指令别名表只有一份（commands.UPDATER_ACTIONS）；裸指令等同于「检查更新」。
        action = UPDATER_ACTIONS.get(sub_arg, "check" if not sub_arg else "")
        ctx = getattr(self._runtime, "context", None)
        updater = UpdaterService(conf, context=ctx)

        if not updater.enabled:
            return (
                "⚠️ 插件一键更新功能未开启。\n"
                "请前往 XBNEXT WebUI 控制台「功能开关」中启用【插件一键更新】。"
            )

        # 权限边界：聊天指令只认管理员身份（is_admin_event）；
        # WebUI 更新入口另行要求填写 OpenAPI Key（见 web/updater_web_key_error）。
        # Key 本身只用于可选 OpenAPI 路径，Git / GitHub ZIP 更新不需要它。

        # 诊断分支：把每个插件的「磁盘版本 / AstrBot 加载版本 / 市场版本」全打出来，
        # 用于排查「市场有更新但提示已是最新」这类问题。
        if action == "detail":
            res = await updater.check_updates()
            if not res.get("ok"):
                return f"❌ 检查失败：{res.get('error')}"
            lines = ["🔎【更新诊断】", "━━━━━━━━━━━━━━"]
            for p in res.get("plugins") or []:
                lines.append(
                    f"• {p.get('name')}"
                    f"：磁盘 {format_version(p.get('local_version'))}"
                    f" / 加载 {format_version(p.get('loaded_version'))}"
                    f" / 市场 {format_version(p.get('remote_version'))}"
                )
            lines.append("━━━━━━━━━━━━━━")
            lines.append(f"市场索引：{res.get('market_count', 0)} 条")
            lines.append(f"OpenAPI：{'已配置' if updater.api_key else '未配置'}")
            return "\n".join(lines)

        # 检查更新分支
        if action == "check":
            res = await updater.check_updates()
            if not res.get("ok"):
                return f"❌ 检查更新失败：{res.get('error')}"

            plugins = res.get("plugins") or []
            if not plugins:
                return "ℹ️ 当前未匹配到任何已安装的目标插件。"

            # 没有更新的插件不占聊天版面：只展示真正有更新的，
            # 全部最新时只回一行结论，避免刷屏。
            updatable = [p for p in plugins if p.get("has_update")]
            if not updatable:
                return no_update_message(res)

            lines = [
                "🔍【插件更新检查】",
                "━━━━━━━━━━━━━━",
            ]
            for p in updatable:
                nick = p.get("nickname") or p.get("name") or "未知插件"
                lines.append(f"• {nick}：{p.get('status_text') or '可更新'}")

            lines.append("━━━━━━━━━━━━━━")
            lines.append("💡 发送【/xbnext 一键更新】即可升级并重载。")
            return "\n".join(lines)

        # 一键更新分支
        if action == "update":
            res = await updater.apply_updates(force=False)
            if not res.get("ok"):
                return f"❌ 执行更新失败：{res.get('error')}"

            if res.get("no_updates"):
                return str(res.get("message") or "")

            results = res.get("results") or []
            if not results:
                return "ℹ️ 当前没有需要更新的插件。"

            return format_update_report("🚀【插件一键更新】", results)

        # 强制更新分支：默认只重拉本插件，避免把同群其他插件一起 reset/重载，
        # 更避免命令执行中把当前插件自己热重载掉导致回复丢失。
        if action == "force":
            res = await updater.apply_updates(
                force=True, selected_names=[PLUGIN_NAME]
            )
            if not res.get("ok"):
                return f"❌ 执行强制更新失败：{res.get('error')}"

            results = res.get("results") or []
            if not results:
                return "ℹ️ 当前没有需要强制更新的插件。"

            return format_update_report("⚡【插件强制更新】", results)

        return (
            "❓ 未知操作，可用指令：\n"
            "• /xbnext 检查更新 — 检查插件是否有新版本\n"
            "• /xbnext 一键更新 — 一键更新有新版本的插件\n"
            "• /xbnext 强制更新 — 强制从 GitHub 重新拉取并热重载\n"
            "• /xbnext 诊断 — 查看每个插件的磁盘/加载/市场版本"
        )


__all__ = ["UpdaterFeature", "UpdaterService", "DEFAULT_TARGET_PLUGINS", "format_version", "send_private_notification"]
