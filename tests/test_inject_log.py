# -*- coding: utf-8 -*-
"""P16 · 提示词注入记录：构建（字段/截断） + KV 轮转 + 容错读写。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import FakeEvent, FakeKV, FakeReq, FakeTextPart, _ROOT  # noqa: F401

from xbnext import inject_log
from xbnext.storage import KV


def _kv() -> KV:
    return KV(owner=FakeKV())


def _run(coro):
    return asyncio.run(coro)


class TestBuildEntry(unittest.TestCase):
    def test_basic_fields(self):
        req = FakeReq(prompt="你好")
        req.extra_user_content_parts = [FakeTextPart("引用块"), FakeTextPart("注入段")]
        req.image_urls = ["http://x/a.png", "http://x/b.png"]
        entry = inject_log.build_entry(
            FakeEvent(), ["引用占位清洗·清洗2块"], req
        )
        # FakeEvent 没有 unified_msg_origin → 兜底 get_session_id()
        self.assertEqual(entry["umo"], "aiocqhttp:GroupMessage:1")
        self.assertEqual(entry["actions"], ["引用占位清洗·清洗2块"])
        self.assertEqual(entry["prompt"], "你好")
        self.assertEqual(entry["parts"], ["引用块", "注入段"])
        self.assertEqual(entry["images"], 2)
        self.assertGreater(entry["ts"], 0)

    def test_unified_msg_origin_preferred(self):
        ev = FakeEvent()
        ev.unified_msg_origin = "aiocqhttp:GroupMessage:9"
        self.assertEqual(
            inject_log.build_entry(ev, [], FakeReq())["umo"],
            "aiocqhttp:GroupMessage:9",
        )

    def test_prompt_clipped(self):
        req = FakeReq(prompt="长" * (inject_log.MAX_PROMPT + 100))
        entry = inject_log.build_entry(FakeEvent(), [], req)
        self.assertLessEqual(len(entry["prompt"]), inject_log.MAX_PROMPT + 16)
        self.assertIn("已截断", entry["prompt"])

    def test_parts_capped_and_clipped(self):
        req = FakeReq()
        req.extra_user_content_parts = [
            FakeTextPart("段" * (inject_log.MAX_PART + 50)),
            *[FakeTextPart(f"p{i}") for i in range(inject_log.MAX_PARTS + 5)],
        ]
        entry = inject_log.build_entry(FakeEvent(), [], req)
        self.assertEqual(len(entry["parts"]), inject_log.MAX_PARTS)
        self.assertTrue(entry["parts"][0].endswith("（已截断）"))
        self.assertEqual(entry["parts"][1], "p0")

    def test_non_text_part_placeholder(self):
        req = FakeReq()
        req.extra_user_content_parts = [object()]  # 没有 .text 的组件
        entry = inject_log.build_entry(FakeEvent(), [], req)
        self.assertEqual(entry["parts"], ["<object>"])

    def test_empty_req_degrades(self):
        entry = inject_log.build_entry(FakeEvent(), None, SimpleNamespace())
        self.assertEqual(entry["prompt"], "")
        self.assertEqual(entry["parts"], [])
        self.assertEqual(entry["images"], 0)
        self.assertEqual(entry["actions"], [])


class TestRecordLoad(unittest.TestCase):
    def test_roundtrip_newest_first(self):
        async def main():
            kv = _kv()
            await inject_log.record(kv, {"ts": 1, "prompt": "第一轮"})
            await inject_log.record(kv, {"ts": 2, "prompt": "第二轮"})
            items = await inject_log.load(kv)
            self.assertEqual([x["prompt"] for x in items], ["第二轮", "第一轮"])

        _run(main())

    def test_capped_at_max_items(self):
        async def main():
            kv = _kv()
            for i in range(inject_log.MAX_ITEMS + 5):
                await inject_log.record(kv, {"ts": i, "prompt": f"r{i}"})
            items = await inject_log.load(kv)
            self.assertEqual(len(items), inject_log.MAX_ITEMS)
            # 最新在前，最旧的被淘汰
            self.assertEqual(items[0]["prompt"], f"r{inject_log.MAX_ITEMS + 4}")
            self.assertNotIn({"ts": 0, "prompt": "r0"}, items)

        _run(main())

    def test_load_tolerates_garbage(self):
        async def main():
            self.assertEqual(await inject_log.load(None), [])
            self.assertEqual(await inject_log.load(_kv()), [])

            class Boom:
                async def get(self, key, default=None):
                    raise RuntimeError("boom")

            self.assertEqual(await inject_log.load(Boom()), [])

            kv = _kv()
            await kv.set(inject_log.KEY, {"items": [1, "x", None, {"ok": 1}]})
            self.assertEqual(await inject_log.load(kv), [{"ok": 1}])
            await kv.set(inject_log.KEY, "not-a-dict")
            self.assertEqual(await inject_log.load(kv), [])
            await kv.set(inject_log.KEY, {"items": "nope"})
            self.assertEqual(await inject_log.load(kv), [])

        _run(main())

    def test_record_failure_returns_false(self):
        async def main():
            class Boom:
                async def get(self, key, default=None):
                    raise RuntimeError("boom")

                async def set(self, key, value):
                    raise RuntimeError("boom")

            self.assertFalse(await inject_log.record(Boom(), {"ts": 1}))
            self.assertFalse(await inject_log.record(None, {"ts": 1}))

        _run(main())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
