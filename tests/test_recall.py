# -*- coding: utf-8 -*-
"""撤回取消请求（P16）：notice 识别（纯函数） + 在飞任务登记/取消。

真机链路（aiocqhttp）：撤回 notice 被适配器转成 ``message_str=""`` 的
事件进早期钩子；本测试覆盖**插件侧**的全部分支 —— 识别、登记、取消、
摘表、未知撤回静默 no-op。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.recall import RecallFeature
from xbnext.features.recall.service import (
    parse_recall,
    raw_sources,
    trigger_id,
)


class TestParseRecall(unittest.TestCase):
    """parse_recall：只认 dict 形态的 group_recall / friend_recall。"""

    def test_group_recall(self):
        raw = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": 123,
            "user_id": 456,
            "operator_id": 456,
            "message_id": 789,
        }
        self.assertEqual(parse_recall(raw), "789")

    def test_friend_recall(self):
        raw = {"post_type": "notice", "notice_type": "friend_recall",
               "user_id": 456, "message_id": "abc"}
        self.assertEqual(parse_recall(raw), "abc")

    def test_int_id_normalized_to_str(self):
        self.assertEqual(
            parse_recall({"notice_type": "group_recall", "message_id": 10086}),
            "10086",
        )

    def test_other_notice_rejected(self):
        """poke / honor 等其它 notice 一律不认，绝不误伤。"""
        self.assertIsNone(parse_recall({"notice_type": "poke", "message_id": 1}))
        self.assertIsNone(parse_recall({"notice_type": "group_upload"}))

    def test_non_dict_rejected(self):
        """字符串（CQ 码 / JSON 串）、None、列表都不认。"""
        self.assertIsNone(parse_recall("[CQ:reply,id=1]"))
        self.assertIsNone(parse_recall(None))
        self.assertIsNone(parse_recall([{"notice_type": "group_recall"}]))

    def test_missing_or_empty_id_rejected(self):
        self.assertIsNone(parse_recall({"notice_type": "group_recall"}))
        self.assertIsNone(parse_recall({"notice_type": "group_recall", "message_id": ""}))
        self.assertIsNone(parse_recall({"notice_type": "group_recall", "message_id": "  "}))


class TestRawSourcesAndTriggerId(unittest.TestCase):
    def test_raw_sources_prefers_message_obj(self):
        obj = SimpleNamespace(raw_message={"notice_type": "group_recall",
                                           "message_id": 7})
        evt = SimpleNamespace(message_obj=obj, raw_message="[CQ:at]")
        self.assertIn(obj.raw_message, raw_sources(evt))

    def test_raw_sources_skips_empty(self):
        evt = SimpleNamespace(message_obj=None, raw_message="")
        self.assertEqual(raw_sources(evt), [])

    def test_trigger_id_from_message_obj_first(self):
        evt = SimpleNamespace(
            message_obj=SimpleNamespace(message_id=42),
            message_id=999,  # event 层兜底，message_obj 命中就不用它
        )
        self.assertEqual(trigger_id(evt), "42")

    def test_trigger_id_int_and_str(self):
        self.assertEqual(trigger_id(SimpleNamespace(message_id=7)), "7")
        self.assertEqual(trigger_id(SimpleNamespace(message_id="m8")), "m8")

    def test_trigger_id_missing(self):
        self.assertEqual(trigger_id(SimpleNamespace()), "")
        self.assertEqual(trigger_id(SimpleNamespace(message_id=None)), "")


class _Ctx:
    """最小 RequestContext 替身：adapter 钩子里只用到 event / note。"""

    def __init__(self, event):
        self.event = event
        self.notes = []

    def note(self, message):
        self.notes.append(message)


def _recall_event(recalled_id, uuid="uuid-recall"):
    """撤回 notice 事件：raw 是 dict，abm.message_id 是 uuid。"""
    return SimpleNamespace(
        message_obj=SimpleNamespace(
            message_id=uuid,
            raw_message={
                "post_type": "notice",
                "notice_type": "group_recall",
                "message_id": recalled_id,
            },
        ),
        message_str="",
    )


def _normal_event(mid):
    """普通消息事件。"""
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_id=mid, raw_message="[CQ:at]"),
        message_str="hi",
    )


class TestRecallFeature(unittest.TestCase):
    """登记 / 取消 / 摘表 —— 全部在真实 asyncio 任务上跑。"""

    def _run(self, coro):
        asyncio.run(coro)

    def test_normal_message_registers_task(self):
        async def main():
            feat = RecallFeature()
            await feat.on_adapter_message(_Ctx(_normal_event(42)))
            # current_task = main 任务本身
            self.assertIn("42", feat._inflight)
            self.assertIs(feat._inflight["42"], asyncio.current_task())
            feat.on_unload()
            self.assertEqual(feat._inflight, {})

        self._run(main())

    def test_cancel_inflight_on_recall(self):
        async def main():
            feat = RecallFeature()

            blocked = asyncio.Event()

            async def holder():
                await blocked.wait()  # 挂着不走，模拟在飞的 LLM 请求

            t = asyncio.create_task(holder())
            feat._track("42", t)

            await feat.on_adapter_message(_Ctx(_recall_event(42)))
            # 给取消一轮落地的机会
            await asyncio.gather(t, return_exceptions=True)
            self.assertTrue(t.cancelled())
            self.assertNotIn("42", feat._inflight)
            # 撤回事件自己的 uuid 不许被登记进去
            self.assertNotIn("uuid-recall", feat._inflight)

        self._run(main())

    def test_recall_unknown_id_is_noop(self):
        async def main():
            feat = RecallFeature()
            ctx = _Ctx(_recall_event(404))  # 查表落空
            await feat.on_adapter_message(ctx)
            self.assertEqual(feat._inflight, {})

        self._run(main())

    def test_done_task_not_cancelled(self):
        async def main():
            feat = RecallFeature()
            t = asyncio.create_task(asyncio.sleep(0))
            await t
            self.assertTrue(t.done())
            feat._track("7", t)
            # 已完成的任务：不 cancel、只被摘表
            await feat.on_adapter_message(_Ctx(_recall_event(7)))
            self.assertNotIn("7", feat._inflight)

        self._run(main())

    def test_done_callback_auto_untracks(self):
        async def main():
            feat = RecallFeature()
            t = asyncio.create_task(asyncio.sleep(0))
            feat._track("9", t)
            await t
            await asyncio.sleep(0)  # done_callback 经 call_soon 调度
            self.assertNotIn("9", feat._inflight)

        self._run(main())

    def test_logs_info_on_hit(self):
        async def main():
            records = []

            class Logger:
                def info(self, msg):
                    records.append(msg)

            feat = RecallFeature()
            feat._log = Logger()
            t = asyncio.create_task(asyncio.Event().wait())
            feat._track("42", t)
            await feat.on_adapter_message(_Ctx(_recall_event(42)))
            await asyncio.gather(t, return_exceptions=True)
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].startswith("[XBNEXT] 撤回命中"))

        self._run(main())

    def test_feature_metadata(self):
        feat = RecallFeature()
        self.assertEqual(feat.key, "enable_recall_cancel")
        self.assertTrue(feat.uses_adapter_hook)
        self.assertEqual(feat.command, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
