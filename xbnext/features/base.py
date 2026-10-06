# -*- coding: utf-8 -*-
"""功能基类。

新增一个功能的完整步骤（**这就是"以后加很多功能"的扩展点**）：

1. 在本目录下建一个子包，例如 ``features/my_thing/``；
2. 里头写 ``class MyThingFeature(Feature)``，填好 ``key`` / ``name`` /
   ``description`` / ``order``，实现 ``on_llm_request``（可选
   ``on_message_sent`` / ``on_load`` / ``on_unload``）；
3. 在 ``_conf_schema.json`` 里加一个 ``enable_my_thing`` 开关；
4. 在 ``features/__init__.py`` 的 ``FEATURES`` 里加一行实例化；
5. 在 ``tests/`` 加一个 ``test_my_thing.py``。

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

    def on_message_sent(self, ctx: Any) -> Any:
        """bot 回复发出后调用（仅 ``uses_sent_hook=True`` 的功能）。"""

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
