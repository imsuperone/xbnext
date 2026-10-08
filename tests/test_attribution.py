# -*- coding: utf-8 -*-
"""R1 · 回复指向单测（说明文案 + 事件抽取 + 落库/注入链路）。"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from conftest import FakeKV, FakeTextPart, _ROOT  # noqa: F401

from xbnext.config import Config
from xbnext.context import RequestContext
from xbnext.features.attribution import (
    AttributionFeature,
    at_targets,
    message_id_of,
    message_text,
    quoted_message_id,
    quoted_sender,
    sender_of,
    service,
    session_scope,
)
from xbnext.storage import KV


# -- 假消息段（类名即类型名） ------------------------------------------------
class Reply:
    def __init__(self, id=None, sender_id=None, sender_nickname=None, message_str=""):
        if id is not None:
            self.id = id
        if sender_id is not None:
            self.sender_id = sender_id
        if sender_nickname is not None:
            self.sender_nickname = sender_nickname
        if message_str:
            self.message_str = message_str


class At:
    def __init__(self, qq=None, name=None):
        if qq is not None:
            self.qq = qq
        if name is not None:
            self.name = name


class FakeEvent:
    """比 conftest 的 FakeEvent 多出 message_obj / unified_msg_origin。"""

    def __init__(
        self,
        sender_id="10001",
        nickname="小明",
        message=None,
        message_id="9001",
        message_str="",
        umo="aiocqhttp/group/123",
        self_id="88888",
    ):
        self.message = message or []
        self.message_str = message_str
        self.unified_msg_origin = umo
        self.message_obj = SimpleNamespace(
            sender=SimpleNamespace(user_id=sender_id, nickname=nickname),
            message_id=message_id,
            message_str=message_str,
            self_id=self_id,
        )

    def get_session_id(self):
        return self.unified_msg_origin


def make_ctx(event=None, req=None, conf=None):
    return RequestContext(
        event=event if event is not None else FakeEvent(),
        req=req if req is not None else SimpleNamespace(extra_user_content_parts=[]),
        conf=conf or Config({"enable_reply_attribution": True}),
        text_part_cls=FakeTextPart,
    )


def make_feature():
    feat = AttributionFeature()

    class _RT:
        kv = KV(owner=FakeKV())
        log = None
        conf = Config({})

    feat.on_load(_RT())
    return feat


def run(coro):
    return asyncio.run(coro)


class TestRelTime(unittest.TestCase):
    def test_buckets(self):
        now = 1_700_000_000
        self.assertEqual(service.rel_time(now, now), "刚刚")
        self.assertEqual(service.rel_time(now - 59, now), "刚刚")
        self.assertEqual(service.rel_time(now - 120, now), "2 分钟前")
        self.assertEqual(service.rel_time(now - 7200, now), "2 小时前")
        self.assertEqual(service.rel_time(now - 172800, now), "2 天前")

    def test_bad_input(self):
        self.assertEqual(service.rel_time(0, 100), "")
        self.assertEqual(service.rel_time("x", 100), "")
        self.assertEqual(service.rel_time(10, None), "")


class TestWho(unittest.TestCase):
    def test_full(self):
        self.assertEqual(service.who(("123", "小明")), "小明（ID 123）")

    def test_partial(self):
        self.assertEqual(service.who(("", "小明")), "小明")
        self.assertEqual(service.who(("123", "")), "ID 123")
        self.assertEqual(service.who(("", "")), "未知")
        self.assertEqual(service.who(None), "未知")

    def test_sanitized(self):
        """身份字段必须过 sanitize（aidoc/03 红线）。"""
        self.assertEqual(service.who(("1", "A<B>")), "A《B》（ID 1）")
        self.assertNotIn("\x00", service.who(("1", "a\x00b")))


class TestBuildHint(unittest.TestCase):
    def test_empty_when_nothing_to_disambiguate(self):
        self.assertEqual(service.build_hint(current=("1", "A")), "")
        self.assertEqual(service.build_hint(), "")
        self.assertEqual(service.build_hint(current=("1", "A"), history=[]), "")

    def test_quoted_sender_triggers_hint(self):
        out = service.build_hint(current=("1", "A"), quoted=("2", "C"))
        self.assertIn(service.TITLE, out)
        self.assertIn("当前发言人：A（ID 1）", out)
        self.assertIn("被引用消息的发送者：C（ID 2）", out)
        self.assertTrue(out.endswith(service.TAIL))

    def test_only_quoted_no_current(self):
        """当前发言人拿不到时仍要给出被引用者（至少说明引用来自谁）。"""
        out = service.build_hint(current=("", ""), quoted=("2", "C"))
        self.assertIn("被引用消息的发送者：C（ID 2）", out)
        self.assertNotIn("- 当前发言人：", out)

    def test_at_targets(self):
        out = service.build_hint(current=("1", "A"), ats=[("9", "小红"), ("10", "")])
        self.assertIn("被 @ 的对象：小红（ID 9）、ID 10", out)

    def test_history_numbered(self):
        now = int(time.time())
        # store 返回的就是"新的在前"，history_lines 只负责渲染不重排
        history = [
            {"target_id": "2", "target_name": "B", "ts": now - 30},
            {"target_id": "1", "target_name": "A", "ts": now - 300},
        ]
        out = service.build_hint(current=("3", "C"), history=history, depth=3, now=now)
        self.assertIn("你最近 2 次回复的对象（新的在前）：", out)
        self.assertIn("1. B（ID 2） · 刚刚", out)
        self.assertIn("2. A（ID 1） · 5 分钟前", out)

    def test_depth_limits_history(self):
        history = [{"target_id": str(i), "target_name": f"U{i}", "ts": 1} for i in range(10)]
        out = service.build_hint(current=("99", "X"), history=history, depth=2, now=100)
        self.assertIn("你最近 2 次", out)
        self.assertNotIn("U9", out)
        self.assertEqual(service.build_hint(current=("99", "X"), history=history, depth=0), "")

    def test_matched_line(self):
        out = service.build_hint(
            current=("1", "A"),
            matched={"target_id": "5", "target_name": "老王"},
        )
        self.assertIn("你引用的这条消息，你当时回应过它", out)
        self.assertIn("老王（ID 5）", out)

    def test_junk_history_ignored(self):
        out = service.build_hint(current=("1", "A"), history=[None, "x", 3])
        self.assertEqual(out, "")


class TestSamePersonBranch(unittest.TestCase):
    """同人 / 异人分支（真机第二轮：同人引用自己仍写「以上是不同的人」）。"""

    def test_key_of(self):
        self.assertEqual(service.key_of(("1", "A")), "id:1")
        self.assertEqual(service.key_of(("", "A")), "name:A")
        self.assertEqual(service.key_of(("", "")), "")
        self.assertEqual(service.key_of(None), "")
        # 命名空间隔离：别人的昵称恰好是你的 QQ 号也不能判成同一个人
        self.assertNotEqual(service.key_of(("10086", "")), service.key_of(("", "10086")))

    def test_history_items_filters_junk_and_depth(self):
        """深度先切、坏数据后滤 —— 与 ``history_lines`` 原行为一致。"""
        items = [{"target_id": "1"}, None, "x", {"target_id": "2"}]
        self.assertEqual(service.history_items(items, 4), [{"target_id": "1"}, {"target_id": "2"}])
        self.assertEqual(service.history_items(items, 2), [{"target_id": "1"}])
        self.assertEqual(service.history_items(items, 0), [])
        self.assertEqual(service.history_items(items, "bad"), [])

    def test_quoting_self_is_same_person(self):
        out = service.build_hint(current=("1", "A"), quoted=("1", "A"))
        self.assertIn(service.TAIL_SAME, out)
        self.assertNotIn(service.TAIL, out)
        self.assertIn(service.SELF_MARK, out)

    def test_bot_quoted_tail_is_soft(self):
        """第九轮弱化：不抬「被引用」的显眼度 + 明令别点破引用。"""
        self.assertNotIn("注意：", service.TAIL_BOT_QUOTED)  # 不再用「注意」开头
        self.assertIn("不要在回复里点出", service.TAIL_BOT_QUOTED)  # 明说别点破
        self.assertIn("不是别人的发言", service.TAIL_BOT_QUOTED)  # 第八轮：区分自己/别人
        self.assertIn("当前发言人这条新消息", service.TAIL_BOT_QUOTED)  # 重心仍在新消息

    def test_quoting_bot_is_own_reply(self):
        """引用 bot 自己的旧回复 → BOT 标注 + TAIL_BOT_QUOTED（真机第八轮）。"""
        out = service.build_hint(
            current=("1", "A"), quoted=("bot", "薇拉"), self_id="bot"
        )
        self.assertIn(service.BOT_MARK, out)
        self.assertIn(service.TAIL_BOT_QUOTED, out)
        self.assertNotIn(service.SELF_MARK, out)
        self.assertNotIn(service.TAIL, out)
        self.assertNotIn(service.TAIL_SAME, out)

    def test_quoting_bot_with_history_still_bot_tail(self):
        """带着回复历史（会话里多段 bot 旧消息）时，尾注仍是"那是你自己的"。"""
        history = [{"target_id": "1", "target_name": "A", "ts": 5}]
        out = service.build_hint(
            current=("1", "A"),
            quoted=("bot", "薇拉"),
            history=history,
            depth=3,
            now=100,
            self_id="bot",
        )
        self.assertIn(service.TAIL_BOT_QUOTED, out)
        self.assertNotIn(service.TAIL, out)

    def test_bot_judged_by_id_not_nickname(self):
        """只比 ID：昵称撞车也不能判成 bot 自己（不猜红线）。"""
        out = service.build_hint(
            current=("1", "A"), quoted=("2", "薇拉"), self_id="bot"
        )
        self.assertNotIn(service.BOT_MARK, out)
        self.assertNotIn(service.TAIL_BOT_QUOTED, out)

    def test_missing_self_id_falls_back_conservative(self):
        """取不到 self_id → 不判 bot，保守回落 TAIL。"""
        out = service.build_hint(current=("1", "A"), quoted=("bot", "薇拉"))
        self.assertNotIn(service.TAIL_BOT_QUOTED, out)
        self.assertIn(service.TAIL, out)

    def test_history_all_self_is_same_person(self):
        history = [{"target_id": "1", "target_name": "A", "ts": 1}]
        out = service.build_hint(current=("1", "A"), history=history, depth=3, now=100)
        self.assertTrue(out.endswith(service.TAIL_SAME), out)

    def test_everyone_self_is_same_person(self):
        history = [{"target_id": "1", "target_name": "A", "ts": 1}]
        out = service.build_hint(
            current=("1", "A"),
            quoted=("1", "A"),
            ats=[("1", "A")],
            history=history,
            depth=3,
            now=100,
        )
        self.assertTrue(out.endswith(service.TAIL_SAME), out)

    def test_self_quoted_but_others_exist(self):
        """引用自己、但历史里还有别人 → 只能说"引用的是本人"，不能说"都是同一个人"。"""
        history = [{"target_id": "2", "target_name": "B", "ts": 1}]
        out = service.build_hint(
            current=("1", "A"), quoted=("1", "A"), history=history, depth=3, now=100
        )
        self.assertTrue(out.endswith(service.TAIL_SELF_QUOTED), out)
        self.assertIn(service.SELF_MARK, out)
        self.assertNotIn("都是同一个人", out)

    def test_unknown_identity_falls_back_to_tail(self):
        """身份未知一律回落「不同的人」（保守，绝不猜）。"""
        out = service.build_hint(current=("1", "A"), ats=[("", "")])
        self.assertTrue(out.endswith(service.TAIL), out)
        self.assertNotIn(service.SELF_MARK, out)

    def test_unknown_current_falls_back_to_tail(self):
        """当前发言人拿不到 → 不能宣称"都是同一个人"。"""
        history = [{"target_id": "1", "target_name": "A", "ts": 1}]
        out = service.build_hint(current=("", ""), history=history, depth=3, now=100)
        self.assertTrue(out.endswith(service.TAIL), out)

    def test_name_only_match_counts_as_same(self):
        """只有昵称时两边昵称一致 → 认定同一个人。"""
        out = service.build_hint(current=("", "A"), quoted=("", "A"))
        self.assertTrue(out.endswith(service.TAIL_SAME), out)

    def test_mixed_id_and_name_does_not_match(self):
        """一边只有 ID、一边只有昵称 → 命名空间不同，判不出就不判。"""
        out = service.build_hint(current=("1", ""), quoted=("", "1"))
        self.assertTrue(out.endswith(service.TAIL), out)


class TestExtraction(unittest.TestCase):
    def test_sender(self):
        ev = FakeEvent(sender_id="42", nickname="阿强")
        self.assertEqual(sender_of(ev), ("42", "阿强"))

    def test_sender_fallback_getter(self):
        ev = SimpleNamespace(message=[], get_sender_id=lambda: "77")
        self.assertEqual(sender_of(ev), ("77", ""))

    def test_quoted_sender(self):
        ev = FakeEvent(message=[Reply(id="5", sender_id="9", sender_nickname="老王")])
        self.assertEqual(quoted_sender(ev), ("9", "老王"))

    def test_quoted_sender_unknown_no_guess(self):
        """引用拿不到被引用者（QQ 官方 Bot）→ 返回空对，绝不猜。"""
        ev = FakeEvent(message=[Reply(id="5")])
        self.assertEqual(quoted_sender(ev), ("", ""))

    def test_quoted_message_id(self):
        ev = FakeEvent(message=[Reply(id="5", sender_id="9")])
        self.assertEqual(quoted_message_id(ev), "5")
        self.assertEqual(quoted_message_id(FakeEvent()), "")

    def test_at_targets(self):
        ev = FakeEvent(
            message=[At(qq="88888", name="bot"), At(qq="all"), At(qq="66", name="阿红")],
            self_id="88888",
        )
        self.assertEqual(at_targets(ev, "88888"), [("66", "阿红")])

    def test_scope(self):
        self.assertEqual(session_scope(FakeEvent(umo="aiocqhttp/group/123")), "group")
        self.assertEqual(session_scope(FakeEvent(umo="aiocqhttp/c2c/1")), "private")
        self.assertEqual(session_scope(FakeEvent(umo="")), "")
        ev = FakeEvent(umo="")
        ev.get_session_id = lambda: "aiocqhttp:ChannelMessage:9"
        self.assertEqual(session_scope(ev), "channel")

    def test_message_id_and_text(self):
        ev = FakeEvent(message_id="9001", message_str="你好")
        self.assertEqual(message_id_of(ev), "9001")
        self.assertEqual(message_text(ev), "你好")


class TestFeatureHooks(unittest.TestCase):
    def test_message_sent_records_current_speaker(self):
        feat = make_feature()
        ev = FakeEvent(sender_id="10001", nickname="小明", message_str="在吗")
        ctx = make_ctx(event=ev)
        run(feat.on_message_sent(ctx))
        items = run(feat._store.load(ctx.event.get_session_id()))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target_id"], "10001")
        self.assertEqual(items[0]["target_name"], "小明")
        self.assertEqual(items[0]["scope"], "group")
        self.assertEqual(items[0]["message_id"], "9001")
        self.assertTrue(items[0]["hash"])

    def test_message_sent_skips_own_message(self):
        feat = make_feature()
        ev = FakeEvent(sender_id="88888", nickname="bot", self_id="88888")
        run(feat.on_message_sent(make_ctx(event=ev)))
        items = run(feat._store.load(ev.get_session_id()))
        self.assertEqual(items, [])

    def test_message_sent_skips_unknown_sender(self):
        feat = make_feature()
        ev = FakeEvent(sender_id="", nickname="")
        run(feat.on_message_sent(make_ctx(event=ev)))
        self.assertEqual(run(feat._store.load(ev.get_session_id())), [])

    def test_llm_request_injects_when_history_exists(self):
        feat = make_feature()
        ev0 = FakeEvent(sender_id="10001", nickname="小明")
        run(feat.on_message_sent(make_ctx(event=ev0)))

        ev1 = FakeEvent(sender_id="20002", nickname="小红")
        ctx = make_ctx(event=ev1)
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 1)
        text = ctx.req.extra_user_content_parts[0].text
        self.assertIn(service.TITLE, text)
        self.assertIn("当前发言人：小红（ID 20002）", text)
        self.assertIn("小明（ID 10001）", text)
        self.assertIn("不要把任何一方的立场", text)

    def test_llm_request_no_context_no_injection(self):
        feat = make_feature()
        ctx = make_ctx(event=FakeEvent())
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 0)

    def test_llm_request_matches_quoted_message_id(self):
        feat = make_feature()
        ev0 = FakeEvent(sender_id="10001", nickname="小明", message_id="9001")
        run(feat.on_message_sent(make_ctx(event=ev0)))

        # B 引用了 bot 之前回应过的那条 9001
        ev1 = FakeEvent(
            sender_id="20002",
            nickname="小红",
            message=[Reply(id="9001", sender_id="10001", sender_nickname="小明")],
        )
        ctx = make_ctx(event=ev1)
        run(feat.on_llm_request(ctx))
        text = ctx.req.extra_user_content_parts[0].text
        self.assertIn("当时的回应对象：小明（ID 10001）", text)

    def test_llm_request_self_quote_says_same_person(self):
        """真机第二轮 bug：小明引用自己的历史发言，文案仍写「以上是不同的人」。"""
        feat = make_feature()
        ev0 = FakeEvent(sender_id="10001", nickname="小明", message_id="9001")
        run(feat.on_message_sent(make_ctx(event=ev0)))

        ev1 = FakeEvent(
            sender_id="10001",
            nickname="小明",
            message=[Reply(id="9001", sender_id="10001", sender_nickname="小明")],
        )
        ctx = make_ctx(event=ev1)
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 1)
        text = ctx.req.extra_user_content_parts[0].text
        self.assertIn(service.TAIL_SAME, text)
        self.assertNotIn(service.TAIL, text)
        self.assertIn(service.SELF_MARK, text)
        self.assertIn("被引用消息的发送者：小明（ID 10001）", text)

    def test_depth_zero_skips(self):
        feat = make_feature()
        run(feat.on_message_sent(make_ctx(event=FakeEvent())))
        ctx = make_ctx(conf=Config({"reply_scope_depth": 0}))
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 0)


if __name__ == "__main__":
    unittest.main()
