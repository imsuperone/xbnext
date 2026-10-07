# -*- coding: utf-8 -*-
"""撤回确认询问（P19 · enable_recall_confirm，默认关）。

- 纯逻辑：``recall_meta``（谁撤的 / 哪个会话）、``match_answer``
  （只认精确词，认不出就继续等）；
- 流程：开着开关 ⇒ 撤回先发询问不急着动；答「是」才掐请求 + 撤回复；
  答「否」保留；超时自动取消；**只有撤回者本人的回答算数**；
- 兜底：询问发不出去 ⇒ 回退老行为立刻取消；关着开关 ⇒ 老行为不变。
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.recall import RecallFeature, replies
from xbnext.features.recall.service import (
    ask_text,
    match_answer,
    recall_meta,
)


# ==================================================================
# 纯逻辑
# ==================================================================
class TestRecallMeta(unittest.TestCase):
    def test_group_recall(self):
        raw = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": 1001,
            "user_id": 666,
            "operator_id": 666,
            "message_id": 789,
            "self_id": 555,
        }
        self.assertEqual(
            recall_meta(raw),
            {"message_id": "789", "operator_id": "666", "group_id": "1001"},
        )

    def test_admin_recall_uses_operator(self):
        """管理员撤别人的 message：能操作的是执行者 operator，不是作者 user。"""
        raw = {
            "notice_type": "group_recall",
            "group_id": 1,
            "user_id": 111,
            "operator_id": 222,
            "message_id": 9,
        }
        meta = recall_meta(raw)
        self.assertEqual(meta["operator_id"], "222")
        self.assertNotEqual(meta["operator_id"], str(raw["user_id"]))

    def test_friend_recall_falls_back_to_user(self):
        meta = recall_meta(
            {"notice_type": "friend_recall", "user_id": 666, "message_id": "abc"}
        )
        self.assertEqual(meta, {"message_id": "abc", "operator_id": "666", "group_id": ""})

    def test_non_recall_none(self):
        self.assertIsNone(recall_meta({"notice_type": "poke", "message_id": 1}))
        self.assertIsNone(recall_meta("not a dict"))


class TestMatchAnswer(unittest.TestCase):
    def test_yes_words(self):
        for text in ("是", "是。", "  是  ", "确认", "取消", "「取消」", "YES", "y", "1"):
            self.assertIs(match_answer(text), True, text)

    def test_no_words(self):
        for text in ("否", "否！", "不", "不取消", "保留", "继续", "NO", "n", "0"):
            self.assertIs(match_answer(text), False, text)

    def test_unknown_is_none(self):
        for text in ("哈哈哈", "取消吧", "", "   ", None, 123):
            self.assertIsNone(match_answer(text))

    def test_ask_text_mentions_timeout_and_words(self):
        text = ask_text()
        self.assertIn("30 秒", text)
        self.assertIn("「是」", text)
        self.assertIn("「否」", text)


# ==================================================================
# 流程
# ==================================================================
class _Ctx:
    """最小 RequestContext 替身：event / note / conf.bool。"""

    def __init__(self, event, confirm: bool = False):
        self.event = event
        self.notes = []
        self._confirm = confirm
        outer = self

        class _Conf:
            def bool(self, key, default=False):
                if key == "enable_recall_confirm":
                    return outer._confirm
                return default

        self.conf = _Conf()

    def note(self, message):
        self.notes.append(message)


class _FakeBot:
    """OneBot 客户端替身：记录调用；fail_send 模拟询问发不出去。"""

    def __init__(self, fail_send: bool = False):
        self.calls = []
        self.fail_send = fail_send

    async def send_group_msg(self, **kw):
        self.calls.append(("send_group_msg", kw))
        if self.fail_send:
            raise RuntimeError("发送失败")
        return {"message_id": 900}

    async def send_private_msg(self, **kw):
        self.calls.append(("send_private_msg", kw))
        if self.fail_send:
            raise RuntimeError("发送失败")
        return {"message_id": 901}

    async def call_action(self, action, **kw):
        self.calls.append((action, kw))
        return True


def _notice_event(mid="789", group="1001", operator="666", bot=None, friend=False):
    """撤回 notice 事件（aiocqhttp 形态：raw 是 dict、message_str 空）。"""
    raw = {
        "post_type": "notice",
        "notice_type": "friend_recall" if friend else "group_recall",
        "user_id": int(operator),
        "message_id": int(mid) if str(mid).isdigit() else mid,
        "self_id": 555,
    }
    if not friend:
        raw["group_id"] = int(group)
        raw["operator_id"] = int(operator)
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_id="uuid-recall", raw_message=raw),
        message_str="",
        bot=_FakeBot() if bot is None else bot,
    )


def _answer_event(text, group="1001", sender="666", mid="ans-1"):
    """回答消息事件：能被 stop_event 吞掉。"""
    ev = SimpleNamespace(
        message_obj=SimpleNamespace(message_id=mid, raw_message=""),
        message_str=text,
        stopped=False,
    )

    def _stop():
        ev.stopped = True

    ev.stop_event = _stop
    ev.get_group_id = lambda: group
    ev.get_sender_id = lambda: sender
    return ev


class TestConfirmFlow(unittest.TestCase):
    """开关开 ⇒ 先问再动；只有撤回者本人的回答算数。"""

    def setUp(self):
        replies.clear()

    def tearDown(self):
        replies.clear()

    def _run(self, coro):
        asyncio.run(coro)

    async def _arm(self, feat, confirm=True, bot=None, mid="789", track=True):
        """登记一条在飞任务并触发撤回，返回 (ctx, task, notice)。"""
        blocked = asyncio.Event()

        async def holder():
            await blocked.wait()

        task = None
        if track:
            task = asyncio.create_task(holder())
            feat._track(mid, task)
        notice = _notice_event(mid=mid, bot=bot)
        ctx = _Ctx(notice, confirm=confirm)
        await feat.on_adapter_message(ctx)
        return ctx, task, notice

    # -- 开关分叉 ----------------------------------------------------
    def test_confirm_on_asks_without_cancelling(self):
        async def main():
            feat = RecallFeature()
            ctx, task, notice = await self._arm(feat)
            try:
                bot = notice.bot
                sends = [c for c in bot.calls if c[0] == "send_group_msg"]
                self.assertEqual(len(sends), 1)
                segs = sends[0][1]["message"]
                self.assertEqual(segs[0]["type"], "at")
                self.assertEqual(segs[0]["data"]["qq"], "666")  # @ 撤回者
                self.assertIn("要取消这次回复吗", segs[1]["data"]["text"])
                self.assertEqual(sends[0][1]["self_id"], 555)  # 多连接路由
                # 在飞任务一根毛都没动
                self.assertFalse(task.done())
                self.assertIn("789", feat._awaiting)
                self.assertIn("789", feat._inflight)
                self.assertTrue(any("已发出取消询问" in n for n in ctx.notes))
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    def test_confirm_off_keeps_old_immediate_cancel(self):
        async def main():
            feat = RecallFeature()
            ctx, task, notice = await self._arm(feat, confirm=False)
            try:
                self.assertEqual(notice.bot.calls, [])  # 不发询问
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
                self.assertEqual(feat._awaiting, {})
            finally:
                feat.on_unload()

        self._run(main())

    def test_ask_failure_falls_back_to_immediate_cancel(self):
        async def main():
            feat = RecallFeature()
            bot = _FakeBot(fail_send=True)
            ctx, task, notice = await self._arm(feat, bot=bot)
            try:
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
                self.assertEqual(feat._awaiting, {})
            finally:
                feat.on_unload()

        self._run(main())

    # -- 谁能答 ------------------------------------------------------
    def test_other_user_answer_ignored(self):
        async def main():
            feat = RecallFeature()
            _, task, _ = await self._arm(feat)
            try:
                # 群里别人回「是」：不消费、不掐、询问还挂着
                other = _answer_event("是", sender="999")
                handled = await feat.on_adapter_message(_Ctx(other, confirm=True))
                self.assertFalse(other.stopped)
                self.assertIn("789", feat._awaiting)
                self.assertFalse(task.done())
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    def test_same_user_wrong_group_ignored(self):
        async def main():
            feat = RecallFeature()
            _, task, _ = await self._arm(feat)
            try:
                elsewhere = _answer_event("是", group="2002", sender="666")
                await feat.on_adapter_message(_Ctx(elsewhere, confirm=True))
                self.assertFalse(elsewhere.stopped)
                self.assertIn("789", feat._awaiting)
                self.assertFalse(task.done())
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    def test_operator_unknown_word_not_consumed(self):
        async def main():
            feat = RecallFeature()
            _, task, _ = await self._arm(feat)
            try:
                chatty = _answer_event("哈哈哈", sender="666")
                await feat.on_adapter_message(_Ctx(chatty, confirm=True))
                self.assertFalse(chatty.stopped)
                self.assertIn("789", feat._awaiting)
                self.assertFalse(task.done())
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    # -- 答案落点 ----------------------------------------------------
    def test_operator_yes_cancels_and_recalls(self):
        async def main():
            feat = RecallFeature()
            ctx, task, notice = await self._arm(feat)
            # 假装 bot 已经发过一条回复
            replies.begin("789")
            replies.record("789", ["555"])
            try:
                ans = _answer_event("是", sender="666")
                handled = await feat.on_adapter_message(_Ctx(ans, confirm=True))
                self.assertTrue(ans.stopped)
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
                self.assertEqual(feat._awaiting, {})
                dels = [c for c in notice.bot.calls if c[0] == "delete_msg"]
                self.assertEqual(len(dels), 1)
                self.assertEqual(str(dels[0][1]["message_id"]), "555")
                self.assertEqual(replies.take("789"), ([], False))
            finally:
                feat.on_unload()

        self._run(main())

    def test_operator_no_keeps_everything(self):
        async def main():
            feat = RecallFeature()
            _, task, notice = await self._arm(feat)
            replies.begin("789")
            replies.record("789", ["555"])
            try:
                ans = _answer_event("否", sender="666")
                await feat.on_adapter_message(_Ctx(ans, confirm=True))
                self.assertTrue(ans.stopped)  # 照样吞掉，不唤醒 LLM
                self.assertFalse(task.done())  # 请求没被掐
                self.assertEqual(feat._awaiting, {})
                dels = [c for c in notice.bot.calls if c[0] == "delete_msg"]
                self.assertEqual(dels, [])  # 回复没被撤
                self.assertEqual(replies.take("789"), (["555"], False))
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    # -- 超时兜底 ----------------------------------------------------
    def test_timeout_auto_cancels(self):
        async def main():
            records = []

            class Logger:
                def info(self, msg):
                    records.append(msg)

            feat = RecallFeature()
            feat._log = Logger()
            feat.confirm_timeout = 0.05
            _, task, _ = await self._arm(feat)
            try:
                await asyncio.sleep(0.15)
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(task.cancelled())
                self.assertEqual(feat._awaiting, {})
                self.assertTrue(any("自动取消" in r for r in records))
            finally:
                feat.on_unload()

        self._run(main())

    def test_answer_beats_timeout(self):
        async def main():
            feat = RecallFeature()
            feat.confirm_timeout = 0.05
            _, task, _ = await self._arm(feat)
            try:
                ans = _answer_event("否", sender="666")
                await feat.on_adapter_message(_Ctx(ans, confirm=True))
                await asyncio.sleep(0.12)  # 过了原定超时
                self.assertFalse(task.done())  # 超时任务已随确认取消，没有二次动作
                self.assertEqual(feat._awaiting, {})
            finally:
                feat.on_unload()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self._run(main())

    # -- 好友撤回 ----------------------------------------------------
    def test_private_recall_asks_and_matches_private_answer(self):
        async def main():
            feat = RecallFeature()
            try:
                blocked = asyncio.Event()

                async def holder():
                    await blocked.wait()

                t = asyncio.create_task(holder())
                feat._track("pv-2", t)
                friend = _notice_event(mid="pv-2", operator="666", friend=True)
                await feat.on_adapter_message(_Ctx(friend, confirm=True))

                sends = [c for c in friend.bot.calls if c[0] == "send_private_msg"]
                self.assertEqual(len(sends), 1)
                self.assertEqual(sends[0][1]["user_id"], 666)
                # 好友会话里回答（无群号）
                ans = _answer_event("是", group="", sender="666")
                await feat.on_adapter_message(_Ctx(ans, confirm=True))
                self.assertTrue(ans.stopped)
                await asyncio.gather(t, return_exceptions=True)
                self.assertTrue(t.cancelled())
                self.assertEqual(feat._awaiting, {})
            finally:
                feat.on_unload()

        self._run(main())

    # -- 卸载 --------------------------------------------------------
    def test_unload_clears_awaiting_and_timers(self):
        async def main():
            feat = RecallFeature()
            feat.confirm_timeout = 0.05
            _, task, _ = await self._arm(feat)
            feat.on_unload()
            self.assertEqual(feat._awaiting, {})
            await asyncio.sleep(0.12)
            self.assertFalse(task.done())  # 定时器已取消，没人再动它
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self._run(main())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
