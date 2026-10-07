# -*- coding: utf-8 -*-
"""Token 用量展示（R9）：取数、白名单、暂存、结果追加 / 流式补发。

链路：``on_llm_response`` 收 usage 存进 ``event`` extras →
``on_decorating_result`` 读走即焚（普通回复追加 Plain；流式收尾单独发）。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import FakeKV, _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.tokenline import TokenLineFeature
from xbnext.features.tokenline import service


class _Usage:
    """``TokenUsage`` 形状假件（字段与核心 dataclass 对齐）。"""

    def __init__(self, input_other=0, input_cached=0, output=0):
        self.input_other = input_other
        self.input_cached = input_cached
        self.output = output

    @property
    def input(self):
        return self.input_other + self.input_cached


class _Ev:
    """带 extras / result / send 的最小事件假件。"""

    def __init__(self, umo="aiocqhttp:GroupMessage:9", chain=None, streaming=False):
        self.unified_msg_origin = umo
        self._extras = {}
        self.sent = []
        ctype = (
            SimpleNamespace(name="STREAMING_FINISH") if streaming else None
        )
        self._result = SimpleNamespace(
            chain=list(chain or []), result_content_type=ctype
        )

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key):
        return self._extras.get(key)

    def get_result(self):
        return self._result

    def plain_result(self, text):
        return text

    async def send(self, result):
        self.sent.append(result)


class _Conf:
    """最小 conf 假件：只实现 tokenline 用到的 ``text()``。"""

    def __init__(self, umos="", boom=False):
        self._umos = umos
        self._boom = boom

    def text(self, key, default=""):
        if self._boom:
            raise RuntimeError("conf boom")
        return self._umos


class _Ctx:
    def __init__(self, event, conf):
        self.event = event
        self.conf = conf
        self.req = None
        self.notes = []

    def note(self, msg):
        self.notes.append(msg)


class FakePlain:
    """Plain 假件（类名不是 ``Plain``，输出面清洗不会碰它）。"""

    def __init__(self, text=""):
        self.text = text


# ==================================================================
# service 纯函数
# ==================================================================
class TestParseAndWhitelist(unittest.TestCase):
    def test_parse_umos_splits_all_markers(self):
        self.assertEqual(
            service.parse_umos("a，b、c;d\ne f"),
            ["a", "b", "c", "d", "e f"],
        )

    def test_parse_umos_passthrough_list(self):
        self.assertEqual(service.parse_umos([" x ", "", "y"]), ["x", "y"])

    def test_parse_umos_empty(self):
        self.assertEqual(service.parse_umos(""), [])
        self.assertEqual(service.parse_umos(None), [])
        self.assertEqual(service.parse_umos(123), [])

    def test_in_whitelist(self):
        book = "aiocqhttp:GroupMessage:1，aiocqhttp:GroupMessage:2"
        self.assertTrue(service.in_whitelist(book, "aiocqhttp:GroupMessage:2"))
        self.assertFalse(service.in_whitelist(book, "aiocqhttp:GroupMessage:3"))
        self.assertFalse(service.in_whitelist("", "aiocqhttp:GroupMessage:1"))
        self.assertFalse(service.in_whitelist(book, ""))


class TestExtractUsage(unittest.TestCase):
    def test_none_and_unknown(self):
        self.assertIsNone(service.extract_usage(None))
        self.assertIsNone(service.extract_usage(object()))

    def test_all_zero_is_none(self):
        self.assertIsNone(service.extract_usage(_Usage(0, 0, 0)))

    def test_normal_values(self):
        vals = service.extract_usage(_Usage(input_other=700, input_cached=300, output=50))
        self.assertEqual(vals, {"input": 1000, "cached": 300, "output": 50})

    def test_fallback_without_input_property(self):
        obj = SimpleNamespace(input_other=80, input_cached=20, output=5)
        vals = service.extract_usage(obj)
        self.assertEqual(vals, {"input": 100, "cached": 20, "output": 5})


class TestPickUsage(unittest.TestCase):
    def test_falls_back_to_resp_usage(self):
        event = _Ev()
        resp = SimpleNamespace(usage=_Usage(10, 5, 3))
        picked = service.pick_usage(event, resp)
        self.assertIsNotNone(picked)
        kind, vals = picked
        self.assertEqual(kind, "sum")
        self.assertEqual(vals, {"input": 15, "cached": 5, "output": 3})

    def test_missing_usage_returns_none(self):
        self.assertIsNone(service.pick_usage(_Ev(), SimpleNamespace(usage=None)))
        self.assertIsNone(service.pick_usage(_Ev(), SimpleNamespace()))
        self.assertIsNone(
            service.pick_usage(_Ev(), SimpleNamespace(usage=_Usage(0, 0, 0)))
        )


class TestMergeAndFormat(unittest.TestCase):
    def test_stats_overwrites(self):
        merged = service.merge_extra(
            {"kind": "sum", "input": 1, "cached": 0, "output": 1},
            "stats",
            {"input": 9, "cached": 4, "output": 2},
        )
        self.assertEqual(merged, {"kind": "stats", "input": 9, "cached": 4, "output": 2})

    def test_sum_accumulates(self):
        merged = service.merge_extra(
            {"kind": "sum", "input": 10, "cached": 1, "output": 2},
            "sum",
            {"input": 5, "cached": 2, "output": 3},
        )
        self.assertEqual(merged, {"kind": "sum", "input": 15, "cached": 3, "output": 5})

    def test_prev_not_dict_replaced(self):
        merged = service.merge_extra(None, "sum", {"input": 1, "cached": 0, "output": 2})
        self.assertEqual(merged, {"kind": "sum", "input": 1, "cached": 0, "output": 2})

    def test_format_line(self):
        line = service.format_line({"input": 1234, "output": 567, "cached": 800})
        self.assertEqual(line, "📊 本轮 token：输入 1,234 · 输出 567 · 缓存 800")
        self.assertEqual(service.format_line({"input": 0, "output": 0, "cached": 0}), "")


# ==================================================================
# 功能钩子
# ==================================================================
class TestOnLlmResponse(unittest.TestCase):
    def test_whitelist_miss_sets_nothing(self):
        ev = _Ev(umo="aiocqhttp:GroupMessage:404")
        ctx = _Ctx(ev, _Conf("aiocqhttp:GroupMessage:9"))
        resp = SimpleNamespace(usage=_Usage(10, 0, 1))
        asyncio.run(TokenLineFeature().on_llm_response(ctx, resp))
        self.assertIsNone(ev.get_extra(service.EXTRA_KEY))

    def test_whitelist_hit_stores_sum(self):
        ev = _Ev()
        ctx = _Ctx(ev, _Conf("aiocqhttp:GroupMessage:9"))
        resp = SimpleNamespace(usage=_Usage(10, 4, 1))
        asyncio.run(TokenLineFeature().on_llm_response(ctx, resp))
        data = ev.get_extra(service.EXTRA_KEY)
        self.assertEqual(
            data, {"kind": "sum", "input": 14, "cached": 4, "output": 1}
        )

    def test_no_usage_sets_nothing(self):
        ev = _Ev()
        ctx = _Ctx(ev, _Conf("aiocqhttp:GroupMessage:9"))
        asyncio.run(TokenLineFeature().on_llm_response(ctx, SimpleNamespace()))
        self.assertIsNone(ev.get_extra(service.EXTRA_KEY))

    def test_conf_boom_is_swallowed(self):
        ev = _Ev()
        ctx = _Ctx(ev, _Conf(boom=True))
        resp = SimpleNamespace(usage=_Usage(1, 0, 1))
        asyncio.run(TokenLineFeature().on_llm_response(ctx, resp))  # 不许抛
        self.assertIsNone(ev.get_extra(service.EXTRA_KEY))


class TestOnDecoratingResult(unittest.TestCase):
    def _feat(self):
        feat = TokenLineFeature()
        feat._plain_cls = FakePlain
        return feat

    def _prime(self, ev):
        ev.set_extra(
            service.EXTRA_KEY,
            {"kind": "sum", "input": 14, "cached": 4, "output": 1},
        )

    def test_appends_plain_and_consumes_extra(self):
        ev = _Ev(chain=[FakePlain("正文")])
        self._prime(ev)
        feat = self._feat()
        asyncio.run(feat.on_decorating_result(_Ctx(ev, _Conf())))
        chain = ev._result.chain
        self.assertEqual(len(chain), 2)
        self.assertIn("📊 本轮 token：输入 14", chain[1].text)
        self.assertTrue(chain[1].text.startswith("\n────\n"))
        # 消费后第二次触发不再追加
        asyncio.run(feat.on_decorating_result(_Ctx(ev, _Conf())))
        self.assertEqual(len(ev._result.chain), 2)
        self.assertEqual(ev.sent, [])

    def test_streaming_finish_sends_separately(self):
        ev = _Ev(streaming=True)
        self._prime(ev)
        asyncio.run(self._feat().on_decorating_result(_Ctx(ev, _Conf())))
        self.assertEqual(len(ev.sent), 1, "流式应单独补发一条")
        self.assertIn("📊 本轮 token", ev.sent[0])

    def test_no_extra_is_noop(self):
        ev = _Ev(chain=[FakePlain("正文")])
        asyncio.run(self._feat().on_decorating_result(_Ctx(ev, _Conf())))
        self.assertEqual(len(ev._result.chain), 1)
        self.assertEqual(ev.sent, [])


# ==================================================================
# 运行时分发（main 的两个钩子最终落到功能）
# ==================================================================
class TestRuntimeDispatch(unittest.TestCase):
    def test_response_then_decorating_roundtrip(self):
        from xbnext.runtime import XbnextRuntime

        rt = XbnextRuntime(
            config={
                "enable_token_usage": True,
                "token_usage_umos": "aiocqhttp:GroupMessage:9",
            },
            kv_store=FakeKV(),
            logger=None,
        )
        feat = rt.get_feature("enable_token_usage")
        feat._plain_cls = FakePlain
        ev = _Ev(chain=[FakePlain("正文")])
        resp = SimpleNamespace(usage=_Usage(100, 40, 7))
        try:
            asyncio.run(rt.handle_llm_response(ev, resp))
            asyncio.run(rt.handle_decorating_result(ev))
            self.assertEqual(len(ev._result.chain), 2)
            self.assertIn("输入 140", ev._result.chain[1].text)
            self.assertIsNone(ev.get_extra(service.EXTRA_KEY))
        finally:
            feat._plain_cls = None
            asyncio.run(rt.terminate())

    def test_disabled_master_skips(self):
        from xbnext.runtime import XbnextRuntime

        rt = XbnextRuntime(config={}, kv_store=FakeKV(), logger=None)
        ev = _Ev(chain=[FakePlain("正文")])
        resp = SimpleNamespace(usage=_Usage(100, 40, 7))
        try:
            asyncio.run(rt.handle_llm_response(ev, resp))
            asyncio.run(rt.handle_decorating_result(ev))
            self.assertIsNone(ev.get_extra(service.EXTRA_KEY))
            self.assertEqual(len(ev._result.chain), 1, "关着时不许追加")
        finally:
            asyncio.run(rt.terminate())

    def test_non_whitelisted_umo_skips(self):
        from xbnext.runtime import XbnextRuntime

        rt = XbnextRuntime(
            config={"enable_token_usage": True, "token_usage_umos": ""},
            kv_store=FakeKV(),
            logger=None,
        )
        feat = rt.get_feature("enable_token_usage")
        feat._plain_cls = FakePlain
        ev = _Ev(chain=[FakePlain("正文")])
        resp = SimpleNamespace(usage=_Usage(100, 40, 7))
        try:
            asyncio.run(rt.handle_llm_response(ev, resp))
            asyncio.run(rt.handle_decorating_result(ev))
            self.assertEqual(len(ev._result.chain), 1, "空名单 = 任何会话都不显示")
        finally:
            feat._plain_cls = None
            asyncio.run(rt.terminate())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
