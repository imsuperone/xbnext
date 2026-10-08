# -*- coding: utf-8 -*-
"""撤回取消请求：P16 一期掐请求 + P17 二期连带撤回复。

一期（P16）：notice 识别（纯函数） + 在飞任务登记/取消 —— 真机链路
（aiocqhttp）：撤回 notice 被适配器转成 ``message_str=""`` 的事件进
早期钩子；覆盖识别、登记、取消、摘表、未知撤回静默 no-op。

二期（P17 · 方案②）：接管本条事件的 ``send`` 拿回复 id（``replies``
登记表）→ 撤回时 ``delete_msg``；覆盖接管记账、原方法兜底、分段
累计、在飞补删、str→int 双试、登记表状态机。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.recall import RecallFeature, replies
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


# ==================================================================
# P17 二期 · 连带撤回复（方案②接管本条 send）
# ==================================================================
class _FakeBot:
    """OneBot 客户端替身：记录调用，发送返回 ``message_id``。"""

    def __init__(self, ret_id=777, fail_send=False):
        self.calls = []
        self.ret_id = ret_id
        self.fail_send = fail_send

    async def send_group_msg(self, **kw):
        self.calls.append(("send_group_msg", kw))
        if self.fail_send:
            raise RuntimeError("发送失败")
        return {"message_id": self.ret_id}

    async def send_private_msg(self, **kw):
        self.calls.append(("send_private_msg", kw))
        if self.fail_send:
            raise RuntimeError("发送失败")
        return {"message_id": self.ret_id}

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        return True


class _IntOnlyBot(_FakeBot):
    """delete_msg 只认 int 的协议端：str 先试必须落空、换 int 才成功。"""

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        if isinstance(kw.get("message_id"), str):
            raise RuntimeError("要求 int")
        return True


class _SendEvent:
    """带 bot 的 aiocqhttp 事件替身（能被 _install_capture 认出）。"""

    def __init__(self, mid="cap-1", group="1001", bot=None):
        self.message_obj = SimpleNamespace(
            message_id=mid,
            raw_message={"post_type": "message", "self_id": 555},
        )
        self.message_str = "hi"
        self.bot = _FakeBot() if bot is None else bot
        self._group = group
        self.original_sends = []
        self._has_send_oper = False

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return self._group

    def get_sender_id(self):
        return "666"

    async def send(self, chain):
        # 原方法：接管后只在「退回原发送」时被调用
        self.original_sends.append(chain)
        self._has_send_oper = True


async def _fake_parse(chain):
    """``_parse_segments`` 的测试替身（真实实现需要 astrbot）。"""
    return [{"type": "text", "data": {"text": "ok"}}]


class TestCaptureSend(unittest.TestCase):
    """P17 二期 · 方案②：接管本条 send，记下回复 id。"""

    def setUp(self):
        replies.clear()

    def tearDown(self):
        replies.clear()

    def _run(self, coro):
        asyncio.run(coro)

    async def _prepare(self, feat, ev):
        await feat.on_adapter_message(_Ctx(ev))
        feat._inflight.clear()  # 模拟管线已结束（真实由 done_callback 摘）
        feat._parse_segments = _fake_parse

    def test_group_send_records_reply_id(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-1")
            await self._prepare(feat, ev)
            self.assertTrue(getattr(ev, "_xbnext_send_capture", False))

            await ev.send("CHAIN")

            self.assertEqual(ev.bot.calls[0][0], "send_group_msg")
            kw = ev.bot.calls[0][1]
            self.assertEqual(kw["group_id"], 1001)
            self.assertEqual(kw["self_id"], 555)  # 多连接路由参数
            self.assertEqual(replies.take("cap-1"), (["777"], False))
            self.assertTrue(ev._has_send_oper)  # 指标位等价补齐
            self.assertEqual(ev.original_sends, [])  # 原方法没被碰

        self._run(main())

    def test_private_send_path(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-2", group=None)
            await self._prepare(feat, ev)
            await ev.send("CHAIN")
            self.assertEqual(ev.bot.calls[0][0], "send_private_msg")
            self.assertEqual(ev.bot.calls[0][1]["user_id"], 666)
            self.assertEqual(replies.take("cap-2"), (["777"], False))

        self._run(main())

    def test_parse_failure_falls_back_to_original(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-3")
            await feat.on_adapter_message(_Ctx(ev))
            feat._inflight.clear()
            # 不替换 _parse_segments：astrbot 不可导入 → None → 退回原方法
            await ev.send("CHAIN")
            self.assertEqual(ev.original_sends, ["CHAIN"])
            self.assertEqual(ev.bot.calls, [])
            self.assertEqual(replies.take("cap-3"), ([], False))

        self._run(main())

    def test_send_failure_falls_back_to_original(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-4", bot=_FakeBot(fail_send=True))
            await self._prepare(feat, ev)
            await ev.send("CHAIN")
            self.assertEqual(ev.original_sends, ["CHAIN"])  # 回复没丢
            self.assertEqual(replies.take("cap-4"), ([], False))

        self._run(main())

    def test_no_bot_no_capture(self):
        async def main():
            feat = RecallFeature()
            ev = _normal_event("cap-5")  # 没有 bot（非 aiocqhttp）
            await feat.on_adapter_message(_Ctx(ev))
            self.assertFalse(getattr(ev, "_xbnext_send_capture", False))

        self._run(main())

    def test_empty_message_str_no_capture(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-6")
            ev.message_str = ""  # 空消息不会唤起 LLM，不装
            await feat.on_adapter_message(_Ctx(ev))
            self.assertFalse(getattr(ev, "_xbnext_send_capture", False))

        self._run(main())

    def test_recall_after_send_deletes_reply(self):
        async def main():
            feat = RecallFeature()
            ev = _SendEvent(mid="cap-d")
            await self._prepare(feat, ev)
            await ev.send("CHAIN")  # 记账 ["777"]

            notice = _recall_event("cap-d")  # 撤回 notice（用同一 bot 删）
            notice.bot = ev.bot
            ctx = _Ctx(notice)
            await feat.on_adapter_message(ctx)

            dels = [c for c in ev.bot.calls if c[0] == "delete_msg"]
            self.assertEqual(len(dels), 1)
            self.assertEqual(str(dels[0][1]["message_id"]), "777")
            self.assertEqual(replies.take("cap-d"), ([], False))  # 摘表
            self.assertTrue(any("连带撤回" in n for n in ctx.notes))

        self._run(main())

    def test_delete_retries_with_int_id(self):
        async def main():
            feat = RecallFeature()
            bot = _IntOnlyBot()
            ev = _SendEvent(mid="cap-i", bot=bot)
            done = await feat._delete_replies(ev, ["777"], "cap-i")
            self.assertEqual(done, 1)
            tries = [c for c in bot.calls if c[0] == "delete_msg"]
            self.assertEqual([t[1]["message_id"] for t in tries], ["777", 777])

        self._run(main())

    def test_recall_during_send_marks_pending_then_deletes(self):
        async def main():
            feat = RecallFeature()
            bot = _FakeBot()
            gate = asyncio.Event()

            async def slow_send(**kw):
                bot.calls.append(("send_group_msg", kw))
                await gate.wait()
                return {"message_id": 888}

            bot.send_group_msg = slow_send
            ev = _SendEvent(mid="cap-p", bot=bot)
            await self._prepare(feat, ev)

            task = asyncio.ensure_future(ev.send("CHAIN"))
            await asyncio.sleep(0)  # 走到 gate.wait() 挂住

            notice = _recall_event("cap-p")
            notice.bot = bot
            await feat._recall_replies(_Ctx(notice), notice, "cap-p")

            gate.set()
            await task

            dels = [c for c in bot.calls if c[0] == "delete_msg"]
            self.assertEqual(len(dels), 1)
            self.assertEqual(str(dels[0][1]["message_id"]), "888")
            self.assertEqual(replies.take("cap-p"), ([], False))

        self._run(main())


class TestRepliesRegistry(unittest.TestCase):
    """replies 登记表：状态机 + 分段累计 + 有界清理。"""

    def setUp(self):
        replies.clear()

    def tearDown(self):
        replies.clear()

    def test_begin_keeps_existing_ids_on_resend(self):
        replies.begin("t1")
        replies.record("t1", ["111"])
        replies.begin("t1")  # 分段回复的第二次发送不许清掉旧 id
        self.assertFalse(replies.record("t1", ["222"]))
        self.assertEqual(replies.take("t1"), (["111", "222"], False))

    def test_take_while_sending_marks_pending(self):
        replies.begin("t2")
        self.assertEqual(replies.take("t2"), ([], True))
        self.assertTrue(replies.record("t2", ["333"]))  # True = 该补删
        self.assertEqual(replies.take("t2"), (["333"], False))

    def test_abandon_keeps_ids_without_pending(self):
        replies.begin("t3")
        replies.record("t3", ["444"])
        self.assertEqual(replies.abandon("t3"), [])  # 无补删标记：留 id 等撤回
        self.assertEqual(replies.take("t3"), (["444"], False))

    def test_abandon_returns_ids_when_pending(self):
        replies.begin("t4")
        replies.record("t4", ["555"])
        replies.begin("t4")  # 又开始发下一段
        self.assertEqual(replies.take("t4"), ([], True))  # 标记补删
        self.assertEqual(replies.abandon("t4"), ["555"])  # 交出旧 id 收尾
        self.assertEqual(replies.take("t4"), ([], False))

    def test_empty_trigger_is_noop(self):
        self.assertEqual(replies.take(""), ([], False))
        self.assertFalse(replies.record("", ["1"]))
        replies.begin("")
        self.assertEqual(replies.take("x"), ([], False))

    def test_has_peeks_without_consuming(self):
        """非破坏性探查：撤回确认拿它判断「有没有可取消的东西」。"""
        self.assertFalse(replies.has("h1"))  # 查无此条
        self.assertFalse(replies.has(""))  # 空 id
        replies.begin("h1")
        self.assertTrue(replies.has("h1"))  # 发送在飞
        replies.record("h1", ["111"])
        self.assertTrue(replies.has("h1"))  # 已记回复
        self.assertEqual(replies.take("h1"), (["111"], False))  # 不受影响
        self.assertFalse(replies.has("h1"))  # 取走即空

    def test_has_false_when_only_empty_entry(self):
        """有条目但没 id、没在飞、没补删标记 ⇒ 没东西可删，不算可取消。"""
        replies.begin("h2")
        replies.record("h2", [])  # 发送完成但没拿到 id
        self.assertFalse(replies.has("h2"))

    def test_clear_empties_table(self):
        replies.begin("t5")
        replies.clear()
        self.assertEqual(replies.take("t5"), ([], False))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
