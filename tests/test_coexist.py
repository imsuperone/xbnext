# -*- coding: utf-8 -*-
"""与 xbdoc 共存的回归测试（真机第二轮反馈：「注入记得不要和 xbdoc 搞出冲突」）。

两个插件同时开是常见配置。结论**已从双方源码查实**：

============ ================================ =============================
维度          xbdoc（astrbot_plugin_xbdoc）   xbnext（本插件）
============ ================================ =============================
事件钩子      ``priority=100``                ``priority=1000``
``on_llm_request`` 默认 ``priority=0``        ``priority=1000``
注入通道      ``extra_user_content_parts`` append（缺 TextPart 时回退拼 prompt）
``system_prompt`` 会替换（``apply_system_prompt``） **绝不碰**
``contexts``  不碰                            **绝不碰**
============ ================================ =============================

core 按 priority **降序**执行
（``astrbot/core/star/star_handler.py``：``sort(key=lambda h: -priority)``）
⇒ 1000 先跑 ⇒ **本插件的占位清洗一定排在 xbdoc 注入之前**，
xbdoc 后追加的 ``【参考资料】`` 绝不会被我们洗掉；我们注入的 temp part
也只会 append、不会清掉 xbdoc 已经放进去的内容。
"""

from __future__ import annotations

import asyncio
import unittest

from conftest import FakeEvent, FakeReq, FakeTextPart, _ROOT  # noqa: F401

from xbnext import HOOK_PRIORITY, injector
from xbnext.features.quote import service as quote_service
from xbnext.runtime import XbnextRuntime

#: xbdoc 的 ``@filter.event_message_type(ALL, priority=100)``
#: 见 ``astrbot_plugin_xbdoc/main.py`` L347
XBDOC_EVENT_PRIORITY = 100

#: xbdoc 的 ``@filter.on_llm_request()`` —— 未传 priority ⇒ core 默认 0
XBDOC_LLM_PRIORITY = 0

#: xbimg 的 ``@filter.event_message_type(ALL, priority=100)``（同款旁证）
XBIMG_EVENT_PRIORITY = 100

#: xbdoc 注入到 ``req.prompt`` / TextPart 的分节标题
REFERENCE_BLOCK = "【参考资料】"


class _Log:
    """只收集日志，不打印（单测环境没有 astrbot logger）。"""

    def __init__(self):
        self.records = []

    def info(self, msg):
        self.records.append(("info", msg))

    def warning(self, msg):
        self.records.append(("warning", msg))

    def debug(self, msg):
        self.records.append(("debug", msg))

    def error(self, msg):
        self.records.append(("error", msg))

    def of(self, level):
        return [m for lv, m in self.records if lv == level]


class TestPriorityOrder(unittest.TestCase):
    """优先级必须保证「xbnext 先清洗、xbdoc 后注入」。"""

    def test_beats_xbdoc_event_hook(self):
        self.assertGreater(HOOK_PRIORITY, XBDOC_EVENT_PRIORITY)

    def test_beats_xbdoc_llm_hook(self):
        self.assertGreater(HOOK_PRIORITY, XBDOC_LLM_PRIORITY)

    def test_beats_xbimg_event_hook(self):
        self.assertGreater(HOOK_PRIORITY, XBIMG_EVENT_PRIORITY)

    def test_main_registers_shared_priority(self):
        source = (_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("@filter.on_llm_request(priority=HOOK_PRIORITY)", source)
        self.assertIn("@filter.event_message_type(", source)


class TestPromptCleaningKeepsForeignBlocks(unittest.TestCase):
    """清洗只动占位符，绝不动别人拼进正文的结构块。"""

    def test_reference_block_untouched_without_placeholders(self):
        prompt = (
            "帮我查一下\n\n"
            f"{REFERENCE_BLOCK}\n"
            "- 苹果是蔷薇科植物\n"
            "- 苹果富含果胶"
        )
        out, stats = quote_service.clean_prompt(prompt)
        self.assertEqual(out, prompt, "没有占位符就必须原样返回")
        self.assertFalse(any(stats.values()))

    def test_reference_block_survives_placeholder_cleaning(self):
        """即使正文里有占位噪声，xbdoc 的参考资料块也必须完整留下。"""
        prompt = (
            "帮我查一下 [Empty Text]\n\n"
            f"{REFERENCE_BLOCK}\n"
            "- 苹果是蔷薇科植物"
        )
        out, _ = quote_service.clean_prompt(prompt)
        self.assertNotIn("[Empty Text]", out)
        self.assertIn(REFERENCE_BLOCK, out)
        self.assertIn("- 苹果是蔷薇科植物", out)
        self.assertIn("帮我查一下", out)

    def test_reference_block_survives_strip_mode(self):
        prompt = f"问题 [Empty Text]\n\n{REFERENCE_BLOCK}\n- 苹果"
        out, _ = quote_service.clean_prompt(prompt, action="strip")
        self.assertIn(REFERENCE_BLOCK, out)
        self.assertIn("- 苹果", out)

    def test_never_clears_meaningful_prompt(self):
        """红线：清洗绝不能把有内容的 prompt 变成空串。"""
        for text in (
            f"{REFERENCE_BLOCK}\n[Image]",
            "   问题   ",
            "看这个 [Image]",
            f"{REFERENCE_BLOCK}\n- 苹果 [Image unavailable]",
        ):
            out, _ = quote_service.clean_prompt(text)
            self.assertTrue(out.strip(), f"清洗把 prompt 清空了: {text!r}")

    def test_strip_xbnext_keeps_reference_block(self):
        text = f"{REFERENCE_BLOCK}\n- 苹果\n<xbnext>mine</xbnext>"
        out = injector.strip_xbnext(text)
        self.assertIn(REFERENCE_BLOCK, out)
        self.assertIn("- 苹果", out)
        self.assertNotIn("<xbnext>", out)
        self.assertIn("mine", out)


class TestInjectionIsAppendOnly(unittest.TestCase):
    """注入通道必须只 append —— 不能清掉别人已经放进来的 part。"""

    def test_foreign_parts_survive_our_injection(self):
        req = FakeReq(prompt="x")
        foreign = FakeTextPart(text=f"{REFERENCE_BLOCK}\n- 苹果是蔷薇科植物")
        req.extra_user_content_parts.append(foreign)

        ok = injector.inject_text(req, "<xbnext>本插件的内容</xbnext>",
                                  text_part_cls=FakeTextPart)

        self.assertTrue(ok)
        self.assertEqual(len(req.extra_user_content_parts), 2)
        self.assertIs(req.extra_user_content_parts[0], foreign)
        self.assertEqual(req.extra_user_content_parts[0].temp, False,
                         "他人的 part 不该被我们改成 temp")
        self.assertTrue(req.extra_user_content_parts[1].temp)

    def test_missing_parts_list_is_created_not_replaced(self):
        req = FakeReq(prompt="x")
        del req.extra_user_content_parts
        self.assertTrue(injector.inject_text(req, "hi", text_part_cls=FakeTextPart))
        self.assertEqual(len(req.extra_user_content_parts), 1)


class TestFullRoundCoexist(unittest.TestCase):
    """整轮跑完之后，xbdoc 视角下的东西必须原封不动。"""

    @staticmethod
    def _round(prompt: str, extra_parts=None, config=None):
        req = FakeReq(prompt=prompt)
        req.system_prompt = "SYSTEM_PROMPT 不许动"
        req.contexts = [{"role": "user", "content": "历史"}]
        for part in extra_parts or []:
            req.extra_user_content_parts.append(part)
        log = _Log()
        rt = XbnextRuntime(config=config or {}, kv_store=None, logger=log)
        asyncio.run(rt.ensure_loaded())
        try:
            asyncio.run(rt.handle_llm_request(FakeEvent(), req))
        finally:
            asyncio.run(rt.terminate())
        return req, log

    def test_round_cleans_prompt_but_keeps_everything_else(self):
        foreign = FakeTextPart(text=f"{REFERENCE_BLOCK}\n- 苹果是蔷薇科植物")
        req, _ = self._round(
            "引用内容：[Empty Text] 他说 [Image unavailable] 好的",
            extra_parts=[foreign],
        )
        # 我们的职责：占位符被清掉
        self.assertNotIn("[Empty Text]", req.prompt)
        self.assertIn("已失效", req.prompt)
        # xbdoc 的职责：一样都不能少
        self.assertEqual(req.system_prompt, "SYSTEM_PROMPT 不许动")
        self.assertEqual(req.contexts, [{"role": "user", "content": "历史"}])
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertIs(req.extra_user_content_parts[0], foreign)

    def test_round_with_all_features_on_keeps_foreign_parts(self):
        """R1/R4 也打开时仍只 append，不会吞掉 xbdoc 的 part。"""
        foreign = FakeTextPart(text=f"{REFERENCE_BLOCK}\n- 苹果")
        req, _ = self._round(
            "你好 [Empty Text]",
            extra_parts=[foreign],
            config={
                "enable_quote_clean": True,
                "enable_face_translate": True,
                "enable_reply_attribution": True,
                "enable_user_profile": True,
            },
        )
        self.assertIs(req.extra_user_content_parts[0], foreign)
        self.assertEqual(req.system_prompt, "SYSTEM_PROMPT 不许动")

    def test_round_logs_single_summary_line(self):
        """有动作时只打**一条** INFO 汇总，不再逐条刷屏。"""
        req, log = self._round("问题 [Empty Text] 他说 [Image unavailable]")
        infos = log.of("info")
        summaries = [m for m in infos if m.startswith("[XBNEXT] 本轮 ")]
        self.assertEqual(len(summaries), 1, summaries)
        self.assertIn("引用占位清洗", summaries[0])
        self.assertIn("改写正文", summaries[0])

    def test_round_silent_when_nothing_happened(self):
        """没动作就一声不吭 —— 正常请求不该被日志刷屏。"""
        req, log = self._round("普通的一句话")
        self.assertFalse([m for m in log.of("info") if "本轮" in m])

    def test_no_debug_notes_by_default(self):
        """``debug_log`` 关着时一行 DEBUG 都不该有（日志不刷屏）。"""
        _, log = self._round("普通的一句话")
        self.assertEqual(log.of("debug"), [])

    def test_debug_notes_emitted_when_enabled(self):
        """``debug_log`` 打开时逐条备注落 DEBUG（详细版，与 INFO 汇总并存）。"""
        _, log = self._round("普通的一句话", config={"debug_log": True})
        self.assertTrue(log.of("debug"), "debug_log 打开时应有逐条备注")


if __name__ == "__main__":
    unittest.main()
