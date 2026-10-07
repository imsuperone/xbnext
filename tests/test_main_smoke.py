# -*- coding: utf-8 -*-
"""入口冒烟测试：main.py 能否被"像 AstrBot 那样"装载并跑通一轮请求。

本机没装 AstrBot，这里用**最小 stub** 顶替 ``astrbot.api`` 的几个符号，
目的不是测 AstrBot，而是抓住纯本项目侧的接线错误：

- 相对导入层级写错（compileall 查不出来）；
- 装饰器用法与官方插件不一致（``@filter.command`` 单指令入口必须能转发）；
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


class Face:
    """假 QQ 表情段（类名即类型名，与 aiocqhttp 适配器对齐）。"""

    def __init__(self, id=None):
        self.id = id


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
            # AstrBot 也提供 command_group；本插件已改单指令（xbdoc/xbimg 同款），
            # stub 保留它只为模拟完整 API 集合。
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
        on_decorating_result=_decorator,
        event_message_type=_decorator,
        EventMessageType=types.SimpleNamespace(
            ALL="all", GROUP_MESSAGE="group", PRIVATE_MESSAGE="private"
        ),
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
        for name in (
            "on_adapter_message",
            "on_llm_request",
            "on_astrbot_loaded",
            "after_message_sent",
            "on_decorating_result",
            "xbnext",
        ):
            self.assertTrue(hasattr(cls, name), f"缺少钩子 {name}")

    def test_all_hooks_share_priority(self):
        """四个钩子必须都用 ``priority=HOOK_PRIORITY``，否则会被别人插队。

        core 按 priority **降序**执行（``sort(key=lambda h: -priority)``）
        ⇒ 数值越大越靠前；xbdoc 的事件钩子是 100、``on_llm_request`` 是 0。
        """
        source = (_ROOT / "main.py").read_text(encoding="utf-8")
        for deco in (
            "@filter.event_message_type(",
            "@filter.on_llm_request(",
            "@filter.after_message_sent(",
            "@filter.on_astrbot_loaded(",
        ):
            idx = source.index(deco)
            tail = source[idx : idx + len(deco) + 120]
            self.assertIn("priority=HOOK_PRIORITY", tail, f"{deco} 未使用统一 priority")

    def test_decorating_hook_beats_xbimg(self):
        """输出面清洗**故意**不跟其余钩子共用优先级 —— 必须抢在 xbimg 之前。

        消息转图插件 xbimg 挂 ``priority=99999``，跑完会把整段文本渲染成
        图片并丢弃所有 ``Plain``；我们排它后面清洗就是空做（标记会被画进
        卡片图）。core 降序执行 ⇒ 我们用 999999 稳压它一头。
        """
        source = (_ROOT / "main.py").read_text(encoding="utf-8")
        idx = source.index("@filter.on_decorating_result(")
        tail = source[idx : idx + 160]
        self.assertIn("priority=999999", tail)

    def test_single_command_entry_registered(self):
        """单指令入口（xbdoc/xbimg 同款）：handler 可调用，且不挂子命令句柄。"""
        cls = self.main.XbnextPlugin
        self.assertTrue(callable(getattr(cls, "xbnext", None)))
        self.assertFalse(
            hasattr(cls.xbnext, "command"),
            "已改单指令分发，不应再是 command_group（不挂 .command 子命令句柄）",
        )

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
            "enable_recall_cancel",
        })
        # AstrNa 共存整套已移除，状态里不该再出现这个键
        self.assertNotIn("astrna", status)
        self.assertNotIn("conflict", status["features"]["enable_quote_clean"])

        lines = plugin.runtime.status_lines()
        self.assertTrue(any("XBNEXT" in x for x in lines))
        self.assertEqual(len(lines), 7)  # 标题 + 5 个功能 + KV
        self.assertFalse(any("AstrNa" in x for x in lines))

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

        # P16 · 注入记录：这轮跑完必须留一条（KV 无后端时也进内存缓存）
        from xbnext import inject_log

        items = asyncio.run(inject_log.load(plugin.runtime.kv))
        self.assertEqual(len(items), 1, "每轮 LLM 请求都该记一条注入记录")
        self.assertIn("已失效", items[0]["prompt"], "记录里应是清洗后的正文")
        self.assertTrue(items[0]["actions"], "本轮 quote 清洗有动作")
        self.assertEqual(items[0]["umo"], "aiocqhttp:GroupMessage:1")

        asyncio.run(plugin.after_message_sent(event))  # 不能抛
        asyncio.run(plugin.terminate())

    def test_lazy_init_without_astrbot_loaded_event(self):
        """热装的插件收不到 ``on_astrbot_loaded`` → 首个入口必须自己补初始化。

        真机症状（未修复时）：WebUI 显示「未加载」、``/xbnext profile`` 报
        「档案存储还没就绪」、日志里一条 ``[XBNEXT]`` 都没有。
        """
        plugin = self.main.XbnextPlugin(context=None, config={})
        self.assertFalse(plugin.runtime._loaded)

        asyncio.run(plugin.on_llm_request(FakeEvent(), FakeReq(prompt="你好")))
        self.assertTrue(plugin.runtime._loaded, "首个 LLM 请求应触发懒初始化")

        status = plugin.runtime.status()
        self.assertTrue(status["loaded"])
        asyncio.run(plugin.terminate())

    def test_command_entry_also_initialises(self):
        plugin = self.main.XbnextPlugin(context=None, config={})
        self.assertFalse(plugin.runtime._loaded)
        # FakeEvent 的 message_str 是空串 → 指令解析为空，但初始化必须先跑
        asyncio.run(plugin.runtime.handle_command("profile", FakeEvent()))
        self.assertTrue(plugin.runtime._loaded)
        asyncio.run(plugin.terminate())

    def test_command_menu_and_dispatch_matrix(self):
        """单指令分发矩阵：裸指令/help 回菜单、status 回状态、未知回提示。"""
        plugin = self.main.XbnextPlugin(context=None, config={})
        sent = []

        class Ev(FakeEvent):
            def plain_result(self, text):
                return text

            async def send(self, result):
                sent.append(result)

        def ask(msg):
            del sent[:]
            asyncio.run(plugin.xbnext(Ev(message_str=msg)))
            self.assertEqual(len(sent), 1, f"{msg!r} 必须恰好回一条")
            return sent[0]

        menu = ask("/xbnext")
        self.assertIn("🧩【XBNEXT · 指令菜单】", menu)
        self.assertIn("• /xbnext status", menu)
        self.assertIn("• /xbnext profile 清空", menu)
        self.assertNotIn("**", menu)
        self.assertEqual(ask("/xbnext help"), menu, "help 应回同一份菜单")

        status = ask("/xbnext status")
        self.assertIn("XBNEXT v", status)
        self.assertIn("KV：", status)

        unknown = ask("/xbnext foo")
        self.assertIn("未知子指令", unknown)
        self.assertIn("「foo」", unknown)
        self.assertNotIn("🧩【", unknown, "未知提示不应整份甩菜单")

        asyncio.run(plugin.terminate())

    # -- 纯表情补写（真机第二轮：「表情是无效的」） --------------------
    def test_pure_face_message_is_rewritten(self):
        """``@bot + 纯表情`` 的 message_str 是空的 → 早期钩子必须补上。

        不补的话 core 会 ``skip llm request: empty message``，LLM 根本不被调用。
        """
        plugin = self.main.XbnextPlugin(context=None, config={})
        asyncio.run(plugin.runtime.on_loaded())

        ev = FakeEvent(message_str="", message=[Face(4)])
        ev.is_at_or_wake_command = True
        asyncio.run(plugin.on_adapter_message(ev))
        self.assertEqual(ev.message_str, "[表情:得意]")

        # 补写之后整轮 on_llm_request 不该再重复注入同一段
        req = FakeReq(prompt=ev.message_str)
        asyncio.run(plugin.on_llm_request(ev, req))
        self.assertEqual(req.extra_user_content_parts, [])

        asyncio.run(plugin.terminate())

    def test_pure_face_without_wake_is_untouched(self):
        """没被 @ / 没命中唤醒前缀的纯表情不能让 bot 开口。"""
        plugin = self.main.XbnextPlugin(context=None, config={})
        asyncio.run(plugin.runtime.on_loaded())

        ev = FakeEvent(message_str="", message=[Face(4)])
        asyncio.run(plugin.on_adapter_message(ev))
        self.assertEqual(ev.message_str, "")

        # 正文非空的消息绝不改写（那是 on_llm_request 的注入路径）
        ev2 = FakeEvent(message_str="普通文字", message=[Face(4)])
        ev2.is_at_or_wake_command = True
        asyncio.run(plugin.on_adapter_message(ev2))
        self.assertEqual(ev2.message_str, "普通文字")

        asyncio.run(plugin.terminate())

    def test_adapter_hook_survives_feature_boom(self):
        """早期钩子单功能炸掉不能冒泡 —— core 会把异常变成发给用户的消息。"""
        from xbnext.features import FEATURES

        plugin = self.main.XbnextPlugin(context=None, config={})
        asyncio.run(plugin.runtime.on_loaded())

        class Exploder:
            key = "enable_face_translate"
            name = "x"
            description = "x"
            order = 30
            uses_sent_hook = False
            uses_adapter_hook = True

            def on_adapter_message(self, ctx):
                raise RuntimeError("boom")

        original = list(FEATURES)
        try:
            import xbnext.features as feats

            feats.FEATURES = (Exploder(),)
            plugin.runtime.features = feats.all_features()
            ev = FakeEvent(message_str="", message=[Face(4)])
            ev.is_at_or_wake_command = True
            asyncio.run(plugin.on_adapter_message(ev))  # 不许抛
            self.assertEqual(ev.message_str, "")
        finally:
            import xbnext.features as feats

            feats.FEATURES = tuple(original)
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
