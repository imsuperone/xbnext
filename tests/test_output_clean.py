# -*- coding: utf-8 -*-
"""④ 输出面清洗：``injector.clean_output`` + ``runtime.handle_decorating_result``。

真机第八轮（AstrNa 对照④）：模型偶尔把注入用的 ``<xbnext>`` 注入体原样
复述出来 —— 请求面已有清洗（quote_clean / sanitize），输出面此前没有落点。
发送前必须洗掉，且要排在消息转图插件（xbimg）之前 —— 否则标记会被渲染进
卡片图（见 ``main.py`` 的优先级注释）。
"""

from __future__ import annotations

import asyncio
import unittest

from xbnext import injector
from xbnext.runtime import XbnextRuntime


class _CaptureLog:
    def __init__(self):
        self.records = []

    def info(self, msg):
        self.records.append(("info", msg))

    def warning(self, msg):
        self.records.append(("warning", msg))

    def debug(self, msg):
        self.records.append(("debug", msg))

    def of(self, level):
        return [m for lv, m in self.records if lv == level]


class Plain:
    """按真机 ``Plain`` 的形状造假件 —— runtime 按**类型名**判定，正好看护。"""

    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, url="x.png"):
        self.url = url


class _Result:
    def __init__(self, chain):
        self.chain = chain


class _Event:
    def __init__(self, chain):
        self._result = _Result(chain)

    def get_result(self):
        return self._result


class _BoomEvent:
    def get_result(self):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# injector.clean_output（纯函数）
# ---------------------------------------------------------------------------
class TestCleanOutput(unittest.TestCase):
    def test_whole_block_removed(self):
        text = "好的\n<xbnext>\n[用户档案]\n称呼：小明\n</xbnext>\n明白"
        out = injector.clean_output(text)
        self.assertNotIn("<xbnext>", out)
        self.assertNotIn("[用户档案]", out)
        self.assertNotIn("称呼：小明", out)
        self.assertIn("好的", out)
        self.assertIn("明白", out)

    def test_orphan_tags_removed(self):
        # 孤立闭标记：普通删
        self.assertEqual(injector.clean_output("前</xbnext>后"), "前后")
        # 孤立开标记：与未闭合块同规则吞到结尾 —— 开标记之后的都算注入体
        # （防半截泄漏），这是有意为之的取舍
        self.assertEqual(injector.clean_output("前<xbnext>后"), "前")

    def test_unclosed_block_eats_to_end(self):
        self.assertEqual(injector.clean_output("收到<xbnext>称呼：小明"), "收到")

    def test_plain_text_untouched(self):
        self.assertEqual(injector.clean_output("普通回复"), "普通回复")
        self.assertEqual(injector.clean_output(""), "")

    def test_non_str_returns_empty(self):
        self.assertEqual(injector.clean_output(None), "")
        self.assertEqual(injector.clean_output(123), "")


# ---------------------------------------------------------------------------
# runtime.handle_decorating_result（钩子面）
# ---------------------------------------------------------------------------
class TestRuntimeDecorating(unittest.TestCase):
    def _runtime(self):
        log = _CaptureLog()
        return XbnextRuntime(config={}, kv_store=None, logger=log), log

    def test_strips_block_from_plain_and_logs(self):
        rt, log = self._runtime()
        ev = _Event([Plain("回复<xbnext>\n[用户档案]\n称呼：小明\n</xbnext>尾巴")])
        asyncio.run(rt.handle_decorating_result(ev))

        text = ev._result.chain[0].text
        self.assertNotIn("<xbnext>", text)
        self.assertNotIn("称呼：小明", text)
        self.assertIn("回复", text)
        self.assertIn("尾巴", text)
        self.assertTrue(any("输出面清洗" in m for m in log.of("info")), log.records)

    def test_leaves_other_components_and_clean_text_alone(self):
        rt, log = self._runtime()
        img = Image()
        ev = _Event([img, Plain("干净文本")])
        asyncio.run(rt.handle_decorating_result(ev))

        self.assertIs(ev._result.chain[0], img)
        self.assertEqual(ev._result.chain[1].text, "干净文本")
        self.assertFalse([m for m in log.of("info") if "输出面清洗" in m])

    def test_emptied_component_dropped_from_chain(self):
        """整条回复都是注入体 → 清完变空的组件摘掉，不发空消息。"""
        rt, log = self._runtime()
        ev = _Event([Plain("<xbnext>整块</xbnext>")])
        asyncio.run(rt.handle_decorating_result(ev))

        self.assertEqual(ev._result.chain, [])
        self.assertTrue(any("输出面清洗" in m for m in log.of("info")), log.records)

    def test_empty_chain_noop(self):
        rt, log = self._runtime()
        asyncio.run(rt.handle_decorating_result(_Event([])))
        self.assertEqual(log.records, [])

    def test_get_result_error_swallowed(self):
        rt, log = self._runtime()
        asyncio.run(rt.handle_decorating_result(_BoomEvent()))
        self.assertEqual(log.records, [])
        asyncio.run(rt.terminate())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
