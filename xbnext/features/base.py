# -*- coding: utf-8 -*-
"""功能基类。

新增一个功能的完整步骤（**这就是"以后加很多功能"的扩展点**）：

1. 在本目录下建一个子包，例如 ``features/my_thing/``；
2. 里头写 ``class MyThingFeature(Feature)``，填好 ``key`` / ``name`` /
   ``description`` / ``order``，实现 ``on_llm_request``（可选
   ``on_adapter_message`` / ``on_message_sent`` / ``on_load`` / ``on_unload``）；
3. 在 ``_conf_schema.json`` 里加一个 ``enable_my_thing`` 开关；
4. 在 ``features/__init__.py`` 的 ``FEATURES`` 里加一行实例化；
5. 在 ``tests/`` 加一个 ``test_my_thing.py``。

**可选 · 暴露子指令**（像 ``/xbnext profile`` 那样）：

- 功能里填 ``command = "mything"`` 并实现
  ``async def handle_command(self, event, args, conf) -> str``；
- 在 ``main.py`` 加一个 ``@xbnext.command("mything")`` 转发到
  ``runtime.handle_command(...)`` —— AstrBot 的指令扫描发生在类加载期，
  子命令必须静态写在那里。

约定：

- **顺序即 ``order``**，小的先跑（清洗类 10~30，注入类 40~90）；
- 功能只通过 :class:`~xbnext.context.RequestContext` 干活，不摸全局；
- 纯逻辑拆成模块级函数，输入输出用普通 ``str`` / ``dict`` / ``list``，
  这样不装 astrbot 也能 pytest；
- 任何异常自己吞掉并记日志，不许冒泡（AstrBot 原则 4）。
"""

from __future__ import annotations

from typing import Any


class Feature:
    """功能基类。子类覆写类属性即可，无需调用 ``super().__init__``。"""

    #: 配置开关键，必须与 ``_conf_schema.json`` 里的 ``enable_*`` 对应
    key: str = ""
    #: 中文名，用于状态面板与 /xbnext status
    name: str = ""
    #: 一句话说明，用于 WebUI 卡片
    description: str = ""
    #: 执行顺序，小的先跑
    order: int = 100
    #: 是否关心 ``after_message_sent``（只有 R1 需要）
    uses_sent_hook: bool = False
    #: 是否关心**适配器早期钩子** ``event_message_type(ALL)``；
    #: 只有需要在 core 判定「空消息」之前改写 ``event.message_str`` 的
    #: 功能才打开（目前只有 R3 纯表情补写）
    uses_adapter_hook: bool = False
    #: 是否关心 ``on_llm_response``（收本轮 token 用量，R9）
    uses_llm_response_hook: bool = False
    #: 是否关心 ``on_decorating_result``（发消息前往结果链补内容，R9）
    uses_decorating_hook: bool = False
    #: 可选：对外暴露的子指令名（``/xbnext <command>``）；空串表示没有指令
    command: str = ""

    # ------------------------------------------------------------------
    # 生命周期（全部可选）
    # ------------------------------------------------------------------
    def on_load(self, runtime: Any) -> None:
        """AstrBot 加载完成后调用；用来读 KV、建缓存。"""

    def on_unload(self) -> None:
        """插件卸载时调用；用来落盘、释放资源。"""

    def bind(self, runtime: Any, star: Any = None) -> None:
        """装配期调用；需要持有 runtime / star 引用时覆写。

        :param runtime: :class:`~xbnext.runtime.XbnextRuntime`
        :param star: 插件入口 Star 实例（可读写 KV、发消息）
        """

    # ------------------------------------------------------------------
    # 钩子
    # ------------------------------------------------------------------
    def on_llm_request(self, ctx: Any) -> Any:
        """每轮 LLM 请求调用。可以是普通函数，也可以是 ``async def``。

        :param ctx: :class:`~xbnext.context.RequestContext`
        """

    def on_adapter_message(self, ctx: Any) -> Any:
        """适配器早期钩子（``event_message_type(ALL)``，仅 ``uses_adapter_hook``）。

        在 core 计算 ``has_valid_message`` 之前运行 —— 此时 ``req`` 还不存在，
        ``ctx.req`` 为 ``None``（注入会安全地返回 ``False``）。
        典型用途：把纯表情补写进 ``event.message_str``，否则 core 会以
        ``skip llm request: empty message`` 直接跳过 LLM。
        """

    def on_message_sent(self, ctx: Any) -> Any:
        """bot 回复发出后调用（仅 ``uses_sent_hook=True`` 的功能）。"""

    def on_llm_response(self, ctx: Any, resp: Any) -> Any:
        """LLM 响应到达（仅 ``uses_llm_response_hook=True`` 的功能）。

        :param ctx: 上下文（此阶段 ``req`` 为 ``None``）
        :param resp: 核心 ``LLMResponse``；``resp.usage`` 是本轮用量
        """

    def on_decorating_result(self, ctx: Any) -> Any:
        """结果装饰阶段（仅 ``uses_decorating_hook=True`` 的功能）。

        发送前最后的改写窗口：普通回复追加进 ``result.chain``；流式
        收尾时改链没人发，功能应自行 ``event.send``。
        """

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------
    def describe(self) -> dict:
        """导出功能元信息，给 WebUI / 状态面板。"""
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "order": self.order,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Feature {self.key or type(self).__name__} order={self.order}>"


__all__ = ["Feature"]
