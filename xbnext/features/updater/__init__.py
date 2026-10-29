# -*- coding: utf-8 -*-
"""XBNEXT 插件一键检查与更新功能入口。"""

from __future__ import annotations

import asyncio
from typing import Any, List

from ... import PLUGIN_NAME
from ..base import Feature
from .service import (
    DEFAULT_TARGET_PLUGINS,
    UpdaterService,
    format_version,
)


async def is_admin_event(event: Any) -> bool:
    """判定当前事件的发送者是否为 AstrBot 管理员/主号。

    层级判定机制：
    1. 优先调用 event.is_admin()（兼容同步与协程）
    2. 检查 event.role 或 event.sender.role 是否为 'admin', 'owner', 'master'
    3. 检查 AstrBot 内核的管理员/主号 ID 列表
    4. 平台不支持或判定异常时拒绝（fail-closed，严格阻止普通用户刷屏/操作更新）
    """
    if event is None:
        return False

    # 1. 尝试 event.is_admin() (AstrBot 官方事件权限接口，支持 sync / async)
    try:
        fn = getattr(event, "is_admin", None)
        if callable(fn):
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, bool):
                return res
    except Exception:
        pass

    # 2. 尝试 event.role 判定 (group admin / owner / master)
    try:
        role = str(getattr(event, "role", "") or "").lower()
        if role in ("admin", "owner", "master"):
            return True
        if role in ("member", "user"):
            return False
    except Exception:
        pass

    # 3. 尝试 event.sender.role
    try:
        sender = getattr(event, "sender", None)
        if sender:
            s_role = str(getattr(sender, "role", "") or "").lower()
            if s_role in ("admin", "owner", "master"):
                return True
            if s_role in ("member", "user"):
                return False
    except Exception:
        pass

    # 4. 检查 sender ID 是否在 AstrBot 全局 admins_id 白名单中
    try:
        user_id = ""
        for attr in ("user_id", "sender_id", "author_id"):
            val = getattr(event, attr, None)
            if val:
                user_id = str(val).strip()
                break
        if not user_id:
            sender = getattr(event, "sender", None)
            if sender:
                user_id = str(getattr(sender, "user_id", "") or getattr(sender, "id", "") or "").strip()

        if user_id:
            ctx = getattr(event, "context", None)
            if ctx and hasattr(ctx, "get_config"):
                g_cfg = ctx.get_config()
                if isinstance(g_cfg, dict):
                    admins = g_cfg.get("admins_id") or []
                    if isinstance(admins, list) and user_id in [str(x).strip() for x in admins]:
                        return True
    except Exception:
        pass

    return False


import re
from datetime import datetime, timedelta
from typing import Any, List, Optional


def next_schedule_delay(now_or_hhmm: Any = None, hhmm: Optional[str] = None) -> float:
    """计算距离下一个 HH:MM 的秒数，默认 04:30。
    支持两种调用风格：
      - next_schedule_delay(now, "04:30")
      - next_schedule_delay("04:30")
    """
    if isinstance(now_or_hhmm, str):
        target_str = now_or_hhmm
        now = datetime.now()
    elif isinstance(now_or_hhmm, datetime):
        now = now_or_hhmm
        target_str = str(hhmm or "04:30")
    else:
        now = datetime.now()
        target_str = str(hhmm or "04:30")

    text = str(target_str or "").strip()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", text)
    if not m:
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", "04:30")
    hour, minute = int(m.group(1)), int(m.group(2))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


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

    async def on_load(self, runtime: Any) -> None:
        """插件加载时启动定时自动检测循环。"""
        self._close_task()
        self._runtime = runtime
        try:
            self._task = asyncio.create_task(self._schedule_loop())
        except Exception:
            self._task = None

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

    async def _dispatch_update_notification(self, updater: UpdaterService, updates: List[Dict[str, Any]]) -> None:
        """格式化并在有新版本未通知时发送私聊通知。"""
        qq_list = updater.notify_qqs
        if not updates or not qq_list:
            return

        notified_path = getattr(self._runtime, "data_dir", None)
        notified_file = None
        notified_map = {}
        if notified_path:
            notified_file = notified_path / "notified_versions.json"
            if notified_file.is_file():
                try:
                    import json
                    notified_map = json.loads(notified_file.read_text(encoding="utf-8"))
                except Exception:
                    notified_map = {}

        new_updates = []
        for p in updates:
            p_name = p.get("name") or ""
            r_ver = str(p.get("remote_version") or "")
            if notified_map.get(p_name) == r_ver:
                continue
            new_updates.append(p)

        if not new_updates:
            return

        lines = [
            "🔔【XBNEXT 插件更新提醒】",
            "检测到以下插件发布了新版本：",
            "━━━━━━━━━━━━━━",
        ]
        for p in new_updates:
            p_name = p.get("name") or p.get("nickname") or ""
            cur_v = format_version(p.get("local_version") or p.get("version") or "未知")
            new_v = format_version(p.get("remote_version") or "最新")
            lines.append(f"• {p_name} {cur_v} -> {new_v}")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("💡 发送【/xbnext 一键更新】即可完成升级重载。")
        msg = "\n".join(lines)

        ctx = getattr(self._runtime, "context", None)
        log = getattr(self._runtime, "log", None)
        any_ok = False
        for q in qq_list:
            ok, err = await send_private_notification(ctx, q, msg)
            if ok:
                any_ok = True
            elif log and hasattr(log, "warning"):
                log.warning(f"[定时更新通知] 发送给 QQ {q} 失败: {err}")

        if any_ok:
            for p in new_updates:
                notified_map[p.get("name")] = str(p.get("remote_version") or "")
            if notified_file:
                try:
                    import json
                    notified_file.write_text(json.dumps(notified_map, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            if log and hasattr(log, "info"):
                log.info(f"[插件更新推送] 已成功向设定通道 {', '.join(qq_list)} 发送新版本提醒")
        else:
            if log and hasattr(log, "warning"):
                log.warning(f"[插件更新推送] 向设定通道 {', '.join(qq_list)} 推送失败: {err}")

    async def _dispatch_update_success_notification(
        self, updater: UpdaterService, updates: List[Dict[str, Any]], apply_res: Dict[str, Any]
    ) -> None:
        """定时自动更新成功后，按照「插件名 现在版本 -> 更新版本」格式向设定的 QQ 发送私聊报告。"""
        qq_list = updater.notify_qqs
        if not updates or not qq_list:
            return

        lines = [
            "🚀【XBNEXT 插件自动更新报告】",
            f"定时任务已在每日时刻（{updater.schedule_time}）自动完成以下插件的升级：",
            "━━━━━━━━━━━━━━",
        ]
        results = {r.get("name"): r for r in (apply_res.get("results") or [])}
        for p in updates:
            p_name = p.get("name") or ""
            cur_v = format_version(p.get("local_version") or p.get("version") or "旧版")
            new_v = format_version(p.get("remote_version") or "最新")
            r = results.get(p_name)
            if r and not r.get("ok"):
                lines.append(f"• {p_name} {cur_v} -> {new_v} (更新异常: {r.get('message')})")
            else:
                lines.append(f"• {p_name} {cur_v} -> {new_v}")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("💡 插件已热重载生效。")
        msg = "\n".join(lines)

        ctx = getattr(self._runtime, "context", None)
        log = getattr(self._runtime, "log", None)
        for q in qq_list:
            ok, err = await send_private_notification(ctx, q, msg)
            if not ok and log and hasattr(log, "warning"):
                log.warning(f"[插件定时更新] 向 QQ {q} 推送更新成功报告失败: {err}")

    async def _schedule_loop(self) -> None:
        """后台每日定时更新循环（默认每天 04:30 自动检测并执行更新，无更新时完全静默）。"""
        import re
        from datetime import datetime, timedelta

        def _calc_delay(sched_time: str) -> float:
            now = datetime.now()
            m = re.match(r"^(\d{1,2}):(\d{2})$", (sched_time or "").strip())
            h, minute = (int(m.group(1)), int(m.group(2))) if m else (4, 30)
            target = now.replace(hour=h, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return max(1.0, (target - now).total_seconds())

        try:
            # 启动 30 秒后开始进入定时循环
            await asyncio.sleep(30)
            while True:
                conf = getattr(self._runtime, "conf", None)
                ctx = getattr(self._runtime, "context", None)
                updater = UpdaterService(conf, context=ctx)
                sched_time = updater.schedule_time

                # 计算到下一次 schedule_time（默认 04:30）的剩余秒数
                total_delay = _calc_delay(sched_time)

                # 分段休眠，以支持热改配置即时生效
                while total_delay > 0:
                    step = min(total_delay, 60.0)
                    await asyncio.sleep(step)
                    total_delay -= step
                    cur_conf = getattr(self._runtime, "conf", None)
                    cur_updater = UpdaterService(cur_conf, context=ctx)
                    if cur_updater.schedule_time != sched_time:
                        sched_time = cur_updater.schedule_time
                        total_delay = _calc_delay(sched_time)

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

        # 检查更新分支
        if sub_arg in ("check", "检查", "检查更新", "查更新", ""):
            res = await updater.check_updates()
            if not res.get("ok"):
                return f"❌ 检查更新失败：{res.get('error')}"

            plugins = res.get("plugins") or []
            if not plugins:
                return "ℹ️ 当前未匹配到任何已安装的目标插件。"

            lines = [
                "🔍【插件更新检查】",
                "━━━━━━━━━━━━━━",
            ]
            for p in plugins:
                nick = p.get("nickname", p.get("name"))
                if p.get("has_update"):
                    lines.append(f"• {nick}: 🚀 {p.get('status_text')}")
                else:
                    lines.append(f"• {nick}: 🟢 {p.get('status_text')}")

            lines.append("━━━━━━━━━━━━━━")
            if res.get("has_new_version"):
                lines.append("💡 发送【/xbnext 一键更新】即可升级重载。")
            else:
                lines.append("✨ 选定插件均已处于最新状态！")
            return "\n".join(lines)

        # 一键更新分支
        if sub_arg in ("update", "更新", "一键更新", "升级", "do_update"):
            res = await updater.apply_updates(force=False)
            if not res.get("ok"):
                return f"❌ 执行更新失败：{res.get('error')}"

            if res.get("no_updates"):
                return f"✨ {res.get('message')}"

            results = res.get("results") or []
            if not results:
                return "ℹ️ 当前未匹配到任何需要更新的插件。"

            lines = [
                "🚀【插件一键更新】",
                "━━━━━━━━━━━━━━",
            ]
            all_ok = True
            for r in results:
                nick = r.get("nickname", r.get("name"))
                if r.get("ok"):
                    lines.append(f"• {nick}: ✅ {r.get('message')}")
                else:
                    all_ok = False
                    lines.append(f"• {nick}: ❌ {r.get('message')}")

            lines.append("━━━━━━━━━━━━━━")
            if all_ok:
                lines.append("✨ 选定插件已全部更新并热重载！")
            else:
                lines.append("⚠️ 部分插件更新异常，请查看日志。")
            return "\n".join(lines)

        # 强制更新分支：默认只重拉本插件，避免把同群其他插件一起 reset/重载，
        # 更避免命令执行中把当前插件自己热重载掉导致回复丢失。
        if sub_arg in ("force", "force_update", "强制更新", "强制升级"):
            res = await updater.apply_updates(
                force=True, selected_names=[PLUGIN_NAME]
            )
            if not res.get("ok"):
                return f"❌ 执行强制更新失败：{res.get('error')}"

            results = res.get("results") or []
            if not results:
                return "ℹ️ 当前未匹配到任何需要更新的插件。"

            lines = [
                "⚡【插件强制更新与重载】",
                "━━━━━━━━━━━━━━",
            ]
            all_ok = True
            for r in results:
                nick = r.get("nickname", r.get("name"))
                if r.get("ok"):
                    lines.append(f"• {nick}: ✅ {r.get('message')}")
                else:
                    all_ok = False
                    lines.append(f"• {nick}: ❌ {r.get('message')}")

            lines.append("━━━━━━━━━━━━━━")
            if all_ok:
                lines.append("✨ 选定插件已全部强制重载完成！")
            else:
                lines.append("⚠️ 部分插件重拉遇到异常。")
            return "\n".join(lines)

        return (
            "❓ 未知操作，可用指令：\n"
            "• /xbnext 检查更新 — 检查插件是否有新版本\n"
            "• /xbnext 一键更新 — 一键更新有新版本的插件\n"
            "• /xbnext 强制更新 — 强制从 GitHub 重新拉取并热重载"
        )


__all__ = ["UpdaterFeature", "UpdaterService", "DEFAULT_TARGET_PLUGINS", "send_private_notification"]
