# -*- coding: utf-8 -*-
"""历史图片瘦身（R8）：contexts 旧图换占位（纯逻辑 + 功能钩子 + 运行时一轮）。

真机链路：``req.contexts`` 是 OpenAI 格式的历史消息，旧图片以 base64
原样重发 —— 本功能把除最近 ``keep`` 条之外的图片成分换成文字占位；
core 落历史用的是本轮已瘦身的 messages，下一轮天然已是占位。
"""

from __future__ import annotations

import asyncio
import unittest

from conftest import FakeEvent, FakeKV, _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.imageslim import ImageSlimFeature
from xbnext.features.imageslim.service import (
    KEEP_RECENT,
    PLACEHOLDER,
    slim_contexts,
)


def _msg(role="user", parts=None):
    return {"role": role, "content": list(parts or [])}


def _img(url="data:image/png;base64,AAAA"):
    return {"type": "image_url", "image_url": {"url": url}}


def _text(t="hi"):
    return {"type": "text", "text": t}


class TestSlimContexts(unittest.TestCase):
    """slim_contexts：只动「超出保留窗口」的历史消息里的图片成分。"""

    def test_replaces_old_images_keeps_recent(self):
        contexts = [_msg(parts=[_img(), _text()]) for _ in range(10)]
        replaced = slim_contexts(contexts)
        self.assertEqual(replaced, 4, "超出 keep=6 的前 4 条应被替换")
        for msg in contexts[:4]:
            self.assertEqual(
                msg["content"][0], {"type": "text", "text": PLACEHOLDER}
            )
            self.assertEqual(msg["content"][1]["type"], "text", "文本成分不动")
        for msg in contexts[6:]:
            self.assertEqual(msg["content"][0]["type"], "image_url", "最近 6 条保留原图")

    def test_second_pass_is_idempotent(self):
        contexts = [_msg(parts=[_img()]) for _ in range(10)]
        slim_contexts(contexts)
        self.assertEqual(slim_contexts(contexts), 0, "占位已是 text，不该再替换")

    def test_short_history_untouched(self):
        contexts = [_msg(parts=[_img()]) for _ in range(KEEP_RECENT)]
        self.assertEqual(slim_contexts(contexts), 0)

    def test_bad_shapes_are_noop(self):
        self.assertEqual(slim_contexts(None), 0)
        self.assertEqual(slim_contexts("not a list"), 0)
        self.assertEqual(slim_contexts([1, "x", None] * 4), 0)
        self.assertEqual(slim_contexts([_msg(parts="str-content")] * 10), 0)
        self.assertEqual(
            slim_contexts([{"role": "assistant", "content": "plain"}] * 10), 0
        )

    def test_all_image_types_recognized(self):
        variants = (
            [{"type": "image_url", "image_url": {"url": "d"}}],
            [{"type": "image", "source": {"data": "d"}}],
            [{"type": "input_image", "image_url": "d"}],
        )
        contexts = []
        for group in variants:
            contexts.extend(_msg(parts=group) for _ in range(4))
        self.assertEqual(len(contexts), 12)
        self.assertEqual(slim_contexts(contexts), 6, "三种图片类型都应识别")

    def test_custom_keep(self):
        contexts = [_msg(parts=[_img()]) for _ in range(5)]
        self.assertEqual(slim_contexts(contexts, keep=2), 3)


class _Ctx:
    """最小 ctx 假件：req + 计数器 + note。"""

    def __init__(self, contexts=None, with_counter=True):
        self.req = (
            None
            if contexts is None
            else type("Req", (), {"contexts": contexts})()
        )
        if with_counter:
            self.slimmed_images = 0
        self.notes = []

    def note(self, msg):
        self.notes.append(msg)


class TestImageSlimFeature(unittest.TestCase):
    """功能钩子：计数、备注、缺字段容错。"""

    def test_on_llm_request_counts_and_notes(self):
        contexts = [_msg(parts=[_img()]) for _ in range(10)]
        ctx = _Ctx(contexts)
        asyncio.run(ImageSlimFeature().on_llm_request(ctx))
        self.assertEqual(ctx.slimmed_images, 4)
        self.assertTrue(any("瘦身" in n for n in ctx.notes))

    def test_no_req_is_noop(self):
        ctx = _Ctx(None)
        asyncio.run(ImageSlimFeature().on_llm_request(ctx))
        self.assertEqual(ctx.slimmed_images, 0)
        self.assertEqual(ctx.notes, [])

    def test_ctx_without_counter_still_works(self):
        contexts = [_msg(parts=[_img()]) for _ in range(10)]
        ctx = _Ctx(contexts, with_counter=False)
        asyncio.run(ImageSlimFeature().on_llm_request(ctx))
        self.assertEqual(getattr(ctx, "slimmed_images", 0), 4)


class _Log:
    """捕获 INFO 的日志假件（校验运行时汇总行）。"""

    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def debug(self, message):
        pass


class TestRuntimeRound(unittest.TestCase):
    """过一遍 runtime.handle_llm_request：开关、动作汇总、注入记录不炸。"""

    def test_round_slims_and_logs_action(self):
        from xbnext.runtime import XbnextRuntime

        log = _Log()
        rt = XbnextRuntime(
            config={"enable_image_slim": True},
            kv_store=FakeKV(),
            logger=log,
        )
        contexts = [_msg(parts=[_img()]) for _ in range(10)]
        req = type(
            "Req",
            (),
            {
                "prompt": "你好",
                "image_urls": [],
                "extra_user_content_parts": [],
                "contexts": contexts,
            },
        )()
        try:
            asyncio.run(rt.on_loaded())
            asyncio.run(rt.handle_llm_request(FakeEvent(message_str="你好"), req))
            self.assertEqual(contexts[0]["content"][0]["type"], "text")
            self.assertTrue(
                any("瘦历史图4张" in line for line in log.infos),
                f"汇总日志应含瘦图动作: {log.infos}",
            )
        finally:
            asyncio.run(rt.terminate())

    def test_round_skips_when_disabled(self):
        from xbnext.runtime import XbnextRuntime

        log = _Log()
        rt = XbnextRuntime(config={}, kv_store=FakeKV(), logger=log)
        contexts = [_msg(parts=[_img()]) for _ in range(10)]
        req = type(
            "Req",
            (),
            {
                "prompt": "你好",
                "image_urls": [],
                "extra_user_content_parts": [],
                "contexts": contexts,
            },
        )()
        try:
            asyncio.run(rt.on_loaded())
            asyncio.run(rt.handle_llm_request(FakeEvent(message_str="你好"), req))
            self.assertEqual(contexts[0]["content"][0]["type"], "image_url")
            self.assertFalse(any("瘦历史图" in line for line in log.infos))
        finally:
            asyncio.run(rt.terminate())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
