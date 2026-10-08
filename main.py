# -*- coding: utf-8 -*-
"""XBNEXT 插件入口。

**薄壳原则**：本文件只负责把 AstrBot 钩子转发给 ``xbnext.runtime.XbnextRuntime``，
不写任何业务逻辑。新增功能请改 ``xbnext/features/``，不要往这里堆代码。

指令（``@filter.command``）保留在这里 —— **单指令入口**（xbdoc / xbimg 同款）：
``main.py::xbnext`` 只转发给 ``runtime.dispatch``，由它分发菜单 / 状态 /
档案 / 未知提示，``main.py`` 不写任何指令分支。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from .xbnext import HOOK_PRIORITY, PLUGIN_NAME, __version__
from .xbnext.runtime import XbnextRuntime
from .xbnext.web import register_web_api


class XbnextPlugin(Star):
    """XBNEXT 插件入口（薄壳）。"""

    def __init__(self, context: Context, config: Optional[Dict[str, Any]] = None):
        super().__init__(context)
        # Star 自身就是插件维度 KV 代理（put_kv_data / get_kv_data）
        self.runtime = XbnextRuntime(config=config, kv_store=self, logger=logger)
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

    @filter.on_llm_response(priority=HOOK_PRIORITY)
    async def on_llm_response(self, event: AstrMessageEvent, resp: Any) -> None:
        """LLM 响应到达 → 把本轮 token 用量暂存进 event extras（R9）。

        只转发；取数与白名单判定在 ``runtime.handle_llm_response``。
        注解用 ``Any``：不同 core 版本导出的 ``LLMResponse`` 位置不一，
        入口不为类型注解赌模块路径。
        """
        await self.runtime.handle_llm_response(event, resp)

    @filter.after_message_sent(priority=HOOK_PRIORITY)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """记录 bot 本轮回复 → 供回复指向功能落库。"""
        await self.runtime.handle_message_sent(event)

    @filter.on_decorating_result(priority=999999)
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """发送前剥掉模型复述的 ``<xbnext>`` 注入体（输出面清洗）。

        优先级必须**高于消息转图插件 xbimg 的 99999** —— core 按
        ``-priority`` 排序（数字越大越先执行），xbimg 跑完会把整段文本
        渲染成图片并丢弃所有 ``Plain``，我们排在它后面就再也够不着文本，
        清洗等于空做。我们只删自己命名空间的标记（纯减法），排最前对
        包括 xbimg 在内的所有下游都无害。
        """
        await self.runtime.handle_decorating_result(event)

    # ------------------------------------------------------------------
    # 指令
    # ------------------------------------------------------------------
    @filter.command("xbnext")
    async def xbnext(self, event: AstrMessageEvent) -> None:
        """XBNEXT 统一指令入口：``/xbnext [status|profile ...]``。

        **单指令分发**（xbdoc / xbimg 同款）：菜单、状态、档案、未知提示
        全部由 ``runtime.dispatch`` 自己回 —— 不走 AstrBot 指令组，
        裸 ``/xbnext`` 不会落到 core 的「参数不足」指令树，打错的子指令
        也不会漏给 LLM 乱答。词表与菜单文案在 ``xbnext/commands.py``。
        """
        reply = await self.runtime.dispatch(event)
        if reply:
            await event.send(event.plain_result(reply))

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<XbnextPlugin v{__version__} plugin={PLUGIN_NAME}>"
