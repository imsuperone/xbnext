# -*- coding: utf-8 -*-
"""XBNEXT 插件一键检查与更新功能入口。"""

from __future__ import annotations

import asyncio
from typing import Any, List

from ..base import Feature
from .service import UpdaterService, DEFAULT_TARGET_PLUGINS


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


class UpdaterFeature(Feature):
    """插件一键检查与更新功能。"""

    key: str = "enable_updater"
    name: str = "插件一键更新"
    description: str = "通过 AstrBot OpenAPI 接口检查并一键更新选定插件"
    order: int = 99
    command: str = "updater"

    async def handle_command(self, event: Any, args: List[str], conf: Any) -> str:
        """处理 /xbnext 检查更新 与 /xbnext 一键更新 指令。"""
        # 仅限 AstrBot 管理员使用，普通用户拦截并阻止刷屏
        if not await is_admin_event(event):
            return "⚠️ 权限不足：插件检查与更新指令仅限 AstrBot 管理员使用。"

        sub_arg = (args[0] if args else "").strip().lower()
        updater = UpdaterService(conf)

        if not updater.enabled:
            return (
                "⚠️ 插件一键更新功能未开启。\n"
                "请前往 XBNEXT WebUI 控制台「功能开关」中启用【插件一键更新】，并填入 AstrBot OpenAPI 密钥。"
            )

        if not updater.api_key:
            return (
                "⚠️ 未检测到有效的 OpenAPI 密钥。\n"
                "请在 XBNEXT WebUI 控制台「功能开关」中填入 OpenAPI 密钥（需包含 plugin 权限）。"
            )

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
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            for p in plugins:
                nick = p.get("nickname", p.get("name"))
                if p.get("has_update"):
                    lines.append(f"• {nick}: 🚀 {p.get('status_text')}")
                else:
                    lines.append(f"• {nick}: 🟢 {p.get('status_text')}")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            if res.get("has_new_version"):
                lines.append("💡 发送【/xbnext 一键更新】即可完成一键升级与热重载。")
            else:
                lines.append("✨ 所有选定插件均已处于最新状态！")
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
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            all_ok = True
            for r in results:
                nick = r.get("nickname", r.get("name"))
                if r.get("ok"):
                    lines.append(f"• {nick}: ✅ {r.get('message')}")
                else:
                    all_ok = False
                    lines.append(f"• {nick}: ❌ {r.get('message')}")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            if all_ok:
                lines.append("✨ 选定插件已全部更新并热重载生效！")
            else:
                lines.append("⚠️ 部分插件更新遇到异常，详情请查看上方日志或后台日志。")
            return "\n".join(lines)

        # 强制更新分支
        if sub_arg in ("force", "force_update", "强制更新", "强制升级"):
            res = await updater.apply_updates(force=True)
            if not res.get("ok"):
                return f"❌ 执行强制更新失败：{res.get('error')}"

            results = res.get("results") or []
            if not results:
                return "ℹ️ 当前未匹配到任何需要更新的插件。"

            lines = [
                "⚡【插件强制更新与重载】",
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            all_ok = True
            for r in results:
                nick = r.get("nickname", r.get("name"))
                if r.get("ok"):
                    lines.append(f"• {nick}: ✅ {r.get('message')}")
                else:
                    all_ok = False
                    lines.append(f"• {nick}: ❌ {r.get('message')}")

            lines.append("━━━━━━━━━━━━━━━━━━━━")
            if all_ok:
                lines.append("✨ 选定插件已全部强制重新拉取并重载完成！")
            else:
                lines.append("⚠️ 部分插件重拉遇到异常，详情请查看上方日志或后台日志。")
            return "\n".join(lines)

        return (
            "❓ 未知操作，可用指令：\n"
            "• /xbnext 检查更新 — 检查插件是否有新版本\n"
            "• /xbnext 一键更新 — 一键更新有新版本的插件\n"
            "• /xbnext 强制更新 — 强制从 GitHub 重新拉取并热重载"
        )


__all__ = ["UpdaterFeature", "UpdaterService", "DEFAULT_TARGET_PLUGINS"]
