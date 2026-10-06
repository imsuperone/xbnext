# -*- coding: utf-8 -*-
"""入口冒烟测试：main.py 能否被"像 AstrBot 那样"装载并跑通一轮请求。

本机没装 AstrBot，这里用**最小 stub** 顶替 ``astrbot.api`` 的几个符号，
目的不是测 AstrBot，而是抓住纯本项目侧的接线错误：

- 相对导入层级写错（compileall 查不出来）；
- 装饰器用法与官方插件不一致（``@command_group`` 后必须能挂 ``.command``）；
- runtime 装配 / 生命周期 / 一轮 ``on_llm_request`` 跑不通；
- 指令与状态输出直接崩。

> stub 只提供**签名**，不提供任何真实行为 —— 真机行为以云端 AstrBot 为准。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path

from conftest import FakeEvent, FakeReq, _ROOT


# ---------------------------------------------------------------------------
# astrbot stub
# ---------------------------------------------------------------------------
def _install_astrbot_stub() -> None:
    if "astrbot" in sys.modules:
        return  # 已装过（真实 astrbot 或前一个测试装的）

    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []  # 当成包，允许 astrbot.api 这种子模块
    api = types.ModuleType("astrbot.api")

    class _Logger:
        def _log(self, *a, **k):
            pass

        info = warning = debug = error = exception = _log

    api.logger = _Logger()

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = type("AstrMessageEvent", (), {})

    def _decorator(*args, **kwargs):
        def wrap(fn):
            return fn

        return wrap

    def _command_group(*args, **kwargs):
        def wrap(fn):
            # 官方用法：@xbnext.command("status") —— 句柄必须能挂子命令
            def sub(*a, **k):
                return _decorator(*a, **k)

            fn.command = sub
            fn.group = sub
            return fn

        return wrap

    event.filter = types.SimpleNamespace(
        on_astrbot_loaded=_decorator,
        on_llm_request=_decorator,
        after_message_sent=_decorator,
        command=_decorator,
        command_group=_command_group,
    )

    provider = types.ModuleType("astrbot.api.provider")
    provider.ProviderRequest = type("ProviderRequest", (), {})

    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context=None):
            self.context = context

    star.Star = Star
    star.Context = type("Context", (), {})

    api.event = event
    api.provider = provider
    api.star = star
    astrbot.api = api

    for name, mod in (
        ("astrbot", astrbot),
        ("astrbot.api", api),
        ("astrbot.api.event", event),
        ("astrbot.api.provider", provider),
        ("astrbot.api.star", star),
    ):
        sys.modules[name] = mod


def _load_main():
    _install_astrbot_stub()
    parent = str(_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    sys.modules.pop("astrbot_plugin_xbnext.main", None)
    return importlib.import_module("astrbot_plugin_xbnext.main")


# ---------------------------------------------------------------------------
class TestMainSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _load_main()

    def test_star_class_exists(self):
        self.assertTrue(hasattr(self.main, "XbnextPlugin"))

    def test_hooks_are_decorated(self):
        cls = self.main.XbnextPlugin
        for name in ("on_llm_request", "on_astrbot_loaded", "after_message_sent", "xbnext"):
            self.assertTrue(hasattr(cls, name), f"缺少钩子 {name}")

    def test_command_group_has_subcommand_api(self):
        """``@filter.command_group`` 返回的句柄必须能挂 ``.command``。"""
        cls = self.main.XbnextPlugin
        self.assertTrue(callable(getattr(cls.xbnext, "command", None)))

    def test_instantiates_and_runs_lifecycle(self):
        plugin = self.main.XbnextPlugin(context=None, config={})
        self.assertIsNotNone(plugin.runtime)

        asyncio.run(plugin.runtime.on_loaded())
        self.assertTrue(plugin.runtime._loaded)

        status = plugin.runtime.status()
        self.assertEqual(set(status["features"]), {
            "enable_quote_clean",
            "enable_face_translate",
            "enable_reply_attribution",
            "enable_user_profile",
        })
        self.assertFalse(status["astrna"]["installed"])

        lines = plugin.runtime.status_lines()
        self.assertTrue(any("XBNEXT" in x for x in lines))
        self.assertEqual(len(lines), 7)  # 标题 + 4 个功能 + AstrNa + KV

        asyncio.run(plugin.terminate())
        self.assertFalse(plugin.runtime._loaded)

    def test_one_llm_request_round(self):
        plugin = self.main.XbnextPlugin(context=None, config={"debug_log": True})
        asyncio.run(plugin.runtime.on_loaded())

        req = FakeReq(prompt="引用内容：[Empty Text] 他说 [Image unavailable] 好的")
        event = FakeEvent()
        asyncio.run(plugin.on_llm_request(event, req))

        self.assertNotIn("[Empty Text]", req.prompt, "占位噪声应被清掉")
        self.assertIn("已失效", req.prompt, "失效图片应改写成中文说明")
        # 默认只开 R2/R3：没有表情片段就不注入，历史保持干净
        self.assertEqual(req.extra_user_content_parts, [])

        asyncio.run(plugin.after_message_sent(event))  # 不能抛
        asyncio.run(plugin.terminate())

    def test_feature_failure_does_not_break_request(self):
        """单功能炸掉不能拖垮整轮请求（AstrBot 原则 4）。"""
        from xbnext.features import FEATURES

        plugin = self.main.XbnextPlugin(context=None, config={})
        asyncio.run(plugin.runtime.on_loaded())

        class Exploder:
            key = "enable_face_translate"
            name = "x"
            description = "x"
            order = 30
            uses_sent_hook = False

            def on_llm_request(self, ctx):
                raise RuntimeError("boom")

        original = list(FEATURES)
        try:
            import xbnext.features as feats

            feats.FEATURES = tuple(original[:1]) + (Exploder(),) + tuple(original[1:])
            plugin.runtime.features = feats.all_features()
            req = FakeReq(prompt="[Empty Text] 正常")
            asyncio.run(plugin.on_llm_request(FakeEvent(), req))
            self.assertNotIn("[Empty Text]", req.prompt, "其余功能仍应正常执行")
        finally:
            import xbnext.features as feats

            feats.FEATURES = tuple(original)
            asyncio.run(plugin.terminate())


if __name__ == "__main__":
    unittest.main()
