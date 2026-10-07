# -*- coding: utf-8 -*-
"""XBNEXT 插件入口。

**薄壳原则**：本文件只负责把 AstrBot 钩子转发给 ``xbnext.runtime.XbnextRuntime``，
不写任何业务逻辑。新增功能请改 ``xbnext/features/``，不要往这里堆代码。

指令（``@filter.command*``）保留在这里，因为 AstrBot 的钩子扫描发生在类加载阶段，
动态挂载子命令不可靠。**指令只做转发**，真正的逻辑在 ``xbnext/features/`` 或
``xbnext/runtime.py``。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .xbnext import HOOK_PRIORITY, PLUGIN_NAME, __version__
from .xbnext import commands
from .xbnext.runtime import XbnextRuntime
from .xbnext.web import register_web_api

#: 裸发 ``/xbnext``（或带这些词）时回菜单；其余子命令一律静默交给子指令
ROOT_HELP_WORDS = ("", "help", "h", "?", "？", "菜单", "帮助")


class XbnextPlugin(Star):
    """XBNEXT 插件入口（薄壳）。"""

    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        # Star 自身就是插件维度 KV 代理（put_kv_data / get_kv_data）
        self.runtime = XbnextRuntime(config=config, kv_store=self, logger=logger)
        self.runtime.register_commands(self)
        register_web_api(context, self.runtime)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @filter.on_astrbot_loaded(priority=HOOK_PRIORITY)
    async def on_astrbot_loaded(self) -> None:
        """AstrBot 启动完成后初始化存储与各功能。"""
        await self.runtime.on_loaded()

    async def terminate(self) -> None:
        """插件卸载时释放资源。"""
        await self.runtime.terminate()

    # ------------------------------------------------------------------
    # 主战场
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=HOOK_PRIORITY)
    async def on_adapter_message(self, event: AstrMessageEvent) -> None:
        """早期钩子：在 core 判定「空消息」之前补写 ``event.message_str``。

        纯表情（``@bot + 一个 QQ 表情`` / mface）的 ``message_str`` 是空的，
        core 会 ``skip llm request: empty message`` —— LLM 压根不被调用。
        这里只做转发，业务在 ``runtime.handle_adapter_message``。
        """
        await self.runtime.handle_adapter_message(event)

    @filter.on_llm_request(priority=HOOK_PRIORITY)
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """按固定顺序调用各功能：清洗在前、注入在后。"""
        await self.runtime.handle_llm_request(event, req)

    @filter.after_message_sent(priority=HOOK_PRIORITY)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """记录 bot 本轮回复 → 供回复指向功能落库。"""
        await self.runtime.handle_message_sent(event)

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------
    @filter.command_group("xbnext")
    async def xbnext(self, event: AstrMessageEvent) -> None:
        """XBNEXT 指令组根节点（新增子指令写在这里，逻辑放功能里）。

        裸发 ``/xbnext`` / ``/xbnext help`` 时回一份菜单；子指令命中时本函数
        保持静默（不抢子指令的回复）。部分 AstrBot 版本还会自己抛"参数不足"
        指令树，两条通路互不冲突（能走到这里的就回我们的菜单）。
        """
        sub, _ = commands.split(str(getattr(event, "message_str", "") or ""))
        if sub not in ROOT_HELP_WORDS:
            return
        await event.send(
            event.plain_result(
                "XBNEXT 子指令：\n"
                "/xbnext status —— 各功能开关与 KV 状态\n"
                "/xbnext profile —— 查看 / 修改 / 清空你的档案（按群分开存）"
            )
        )

    @xbnext.command("status")
    async def xbnext_status(self, event: AstrMessageEvent) -> None:
        """查看 XBNEXT 各功能开关与 KV 可用性。"""
        await event.send(
            event.plain_result("\n".join(self.runtime.status_lines()))
        )

    @xbnext.command("profile")
    async def xbnext_profile(self, event: AstrMessageEvent) -> None:
        """维护自己的自助档案：``/xbnext profile [查看|清空|字段 内容]``。

        与「用户档案」开关**无关** —— 开关只决定是否喂给模型，
        档案的查看与维护入口始终可用。
        """
        reply = await self.runtime.handle_command("profile", event)
        if reply:
            await event.send(event.plain_result(reply))

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<XbnextPlugin v{__version__} plugin={PLUGIN_NAME}>"
