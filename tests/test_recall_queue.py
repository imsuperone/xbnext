# -*- coding: utf-8 -*-
"""排队疏通（真机回归 #6）：杀主 run 前还票 + 看门狗通旧伤。

背景：核心把同会话连发的消息排成严格叫号的队，排队票只有 run 正常
完成 / 出错 / 主动中断三条路会结清 —— 硬杀 run 任务会让票永远悬着，
整个会话的排队从此堵死（单发消息正常、连发的 follow-up 全部石沉大海）。

覆盖：

- **还票**：``_cancel_now`` 杀主 run 前先替核心 resolve 排队票
  （只在「被撤的正是这次 run 的触发消息」时动手）；
- **看门狗**：无 run 在跑 + 年龄超 ``stuck_timeout`` + 隔 ``stuck_grace``
  复看仍挂着 ⇒ cancel 解卡；核心拿不到 / 有 run 在跑 / 太新 / 没元数据
  一概不动手。
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.recall import RecallFeature, _RUNNER_UNKNOWN, replies


# ==================================================================
# 替身
# ==================================================================
class _Ctx:
    """最小 RequestContext 替身：event / note。"""

    def __init__(self, event):
        self.event = event
        self.notes = []

    def note(self, message):
        self.notes.append(message)


class _FakeTask:
    """假任务：只被 ``_cancel_now`` 用到（done / cancel），记调用顺序。"""

    def __init__(self, order: list):
        self.order = order
        self.was_cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.order.append("cancel")
        self.was_cancelled = True


class _Runner:
    """假核心 runner：触发消息 id + 手里的排队票 + resolve 记账。"""

    def __init__(self, trigger_mid, pending=2, order=None, raise_on_resolve=False):
        self.run_context = SimpleNamespace(
            context=SimpleNamespace(
                event=SimpleNamespace(
                    message_obj=SimpleNamespace(message_id=trigger_mid)
                )
            )
        )
        self._pending_follow_ups = [f"t{i}" for i in range(pending)]
        self._order = order if order is not None else []
        self.resolved = False
        self._raise = raise_on_resolve

    def _resolve_unconsumed_follow_ups(self):
        if self._raise:
            raise RuntimeError("核心改名了")
        self._order.append("resolve")
        self.resolved = True
        self._pending_follow_ups = []


def _ev(mid, umo="g1_omo"):
    """普通消息事件（带会话标识）。"""
    return SimpleNamespace(
        message_obj=SimpleNamespace(message_id=mid, raw_message=""),
        message_str="hi",
        unified_msg_origin=umo,
    )


class TestSettleBeforeKill(unittest.TestCase):
    """杀主 run 前先还票：顺序必须是 resolve → cancel。"""

    def tearDown(self):
        replies.clear()

    def _cancel(self, make_runner, mid="42"):
        """跑一次 _cancel_now；``make_runner(order)`` 造替身，返回四元组。

        runner 与假任务**共享同一个 order 账本**，才能断言
        resolve / cancel 的先后顺序。
        """
        order: list = []
        feat = RecallFeature()
        task = _FakeTask(order)
        feat._inflight[mid] = task
        feat._meta[mid] = ("g1_omo", time.monotonic())
        runner = make_runner(order)
        feat._active_runner = lambda umo: runner
        asyncio.run(feat._cancel_now(_Ctx(_ev(mid)), _ev(mid), mid, "撤回命中"))
        return runner, order, task, feat

    def test_resolves_before_cancel(self):
        """撤的正是触发消息 ⇒ 先 resolve 再 cancel，票清零。"""
        runner, order, task, feat = self._cancel(
            lambda o: _Runner("42", pending=2, order=o)
        )
        self.assertEqual(order, ["resolve", "cancel"])
        self.assertTrue(runner.resolved)
        self.assertTrue(task.was_cancelled)
        self.assertNotIn("42", feat._inflight)
        self.assertNotIn("42", feat._meta)

    def test_skips_settle_when_recalled_is_follower(self):
        """撤的是排队中的消息 ⇒ 主 run 的票一张不动，任务照旧取消。"""
        runner, order, task, _ = self._cancel(
            lambda o: _Runner("999", pending=2, order=o), mid="42"
        )
        self.assertEqual(order, ["cancel"])
        self.assertFalse(runner.resolved)
        self.assertEqual(len(runner._pending_follow_ups), 2)
        self.assertTrue(task.was_cancelled)

    def test_skips_when_runner_unknown(self):
        """核心拿不到（哨兵）⇒ 老行为照常取消，不碰核心。"""
        _, order, task, _ = self._cancel(lambda o: _RUNNER_UNKNOWN)
        self.assertEqual(order, ["cancel"])
        self.assertTrue(task.was_cancelled)

    def test_skips_when_no_active_runner(self):
        """没有活跃 run（None）⇒ 没票可还，照常取消。"""
        _, order, task, _ = self._cancel(lambda o: None)
        self.assertEqual(order, ["cancel"])
        self.assertTrue(task.was_cancelled)

    def test_no_pending_tickets_no_resolve(self):
        """run 手里没票 ⇒ 不调 resolve。"""
        runner, order, _, _ = self._cancel(
            lambda o: _Runner("42", pending=0, order=o), mid="42"
        )
        self.assertEqual(order, ["cancel"])
        self.assertFalse(runner.resolved)

    def test_resolve_failure_still_cancels(self):
        """resolve 抛错（核心改名）⇒ 只记日志，任务照杀不冒泡。"""
        _, order, task, _ = self._cancel(
            lambda o: _Runner("42", pending=2, order=o, raise_on_resolve=True),
            mid="42",
        )
        self.assertEqual(order, ["cancel"])
        self.assertTrue(task.was_cancelled)

    def test_settle_without_umo_skips(self):
        """事件没会话标识 ⇒ 跳过还票（保守），任务照常取消。"""
        order: list = []
        feat = RecallFeature()
        task = _FakeTask(order)
        feat._inflight["42"] = task
        runner = _Runner("42", pending=2, order=order)
        feat._active_runner = lambda umo: runner
        no_umo = SimpleNamespace(
            message_obj=SimpleNamespace(message_id="42", raw_message=""),
            message_str="",
        )
        asyncio.run(feat._cancel_now(_Ctx(no_umo), no_umo, "42", "撤回命中"))
        self.assertEqual(order, ["cancel"])
        self.assertFalse(runner.resolved)


class TestSweepWatchdog(unittest.TestCase):
    """看门狗：无 run 在跑 + 挂死超时 + 复看仍挂 ⇒ cancel 解卡。"""

    def setUp(self):
        self.feat = RecallFeature()
        self.feat.stuck_timeout = 0.0
        self.feat.stuck_grace = 0.0
        replies.clear()

    def tearDown(self):
        replies.clear()
        self.feat.on_unload()

    async def _stuck_task(self, mid="11", umo="g1_omo"):
        task = asyncio.create_task(asyncio.Event().wait())
        self.feat._track(mid, task, umo)
        return task

    def _run(self, coro):
        asyncio.run(coro)

    async def _two_sweeps(self):
        """两次扫描（stuck_timeout/grace=0 ⇒ 第二次即动手）。"""
        self.feat._sweep_stuck("g1_omo")
        await asyncio.sleep(0)
        first_state = (self.feat._inflight.get("11") is not None, "11" in self.feat._suspect)
        self.feat._sweep_stuck("g1_omo")
        await asyncio.sleep(0)
        return first_state

    def test_marks_then_cancels_after_grace(self):
        async def main():
            self.feat._active_runner = lambda umo: None
            task = await self._stuck_task()
            alive_after_first, marked = await self._two_sweeps()
            self.assertTrue(alive_after_first, "首次只标记，不许杀")
            self.assertTrue(marked, "首次要进疑似表")
            self.assertTrue(task.cancelled(), "复看仍挂着 ⇒ 取消解卡")

        self._run(main())

    def test_skips_when_runner_active(self):
        """有 run 在跑 ⇒ 排队等叫号是正常的，绝不杀。"""

        async def main():
            self.feat._active_runner = lambda umo: object()
            task = await self._stuck_task()
            self.feat._sweep_stuck("g1_omo")
            self.feat._sweep_stuck("g1_omo")
            await asyncio.sleep(0)
            self.assertFalse(task.cancelled())
            task.cancel()
            await asyncio.sleep(0)

        self._run(main())

    def test_skips_when_runner_unknown(self):
        """核心拿不到（哨兵）⇒ 整段跳过，宁不动不误杀。"""

        async def main():
            self.feat._active_runner = lambda umo: _RUNNER_UNKNOWN
            task = await self._stuck_task()
            self.feat._sweep_stuck("g1_omo")
            self.feat._sweep_stuck("g1_omo")
            await asyncio.sleep(0)
            self.assertFalse(task.cancelled())
            self.assertNotIn("11", self.feat._suspect)
            task.cancel()
            await asyncio.sleep(0)

        self._run(main())

    def test_skips_fresh_task(self):
        """年龄没到门槛 ⇒ 不进疑似表，更不杀。"""

        async def main():
            self.feat._active_runner = lambda umo: None
            self.feat.stuck_timeout = 120.0
            task = await self._stuck_task()
            self.feat._sweep_stuck("g1_omo")
            self.feat._sweep_stuck("g1_omo")
            await asyncio.sleep(0)
            self.assertFalse(task.cancelled())
            self.assertNotIn("11", self.feat._suspect)
            task.cancel()
            await asyncio.sleep(0)

        self._run(main())

    def test_skips_task_without_meta(self):
        """两参登记（无会话标识/时刻的老用法）⇒ 无元数据不动手。"""

        async def main():
            self.feat._active_runner = lambda umo: None
            task = asyncio.create_task(asyncio.Event().wait())
            self.feat._track("11", task)  # 无 umo
            self.feat._sweep_stuck("g1_omo")
            self.feat._sweep_stuck("g1_omo")
            await asyncio.sleep(0)
            self.assertFalse(task.cancelled())
            task.cancel()
            await asyncio.sleep(0)

        self._run(main())

    def test_skips_other_umo(self):
        """登记在别的会话 ⇒ 本会话扫描不碰它。"""

        async def main():
            self.feat._active_runner = lambda umo: None
            task = await self._stuck_task(umo="g2_omo")
            self.feat._sweep_stuck("g1_omo")
            await asyncio.sleep(0)
            self.assertFalse(task.cancelled())
            self.assertNotIn("11", self.feat._suspect)
            task.cancel()
            await asyncio.sleep(0)

        self._run(main())

    def test_backdated_task_cancelled_via_hook(self):
        """集成：正常消息钩子入口触发看门狗，老任务解卡、新任务不伤。"""
        feat = self.feat

        async def main():
            feat._active_runner = lambda umo: None
            feat.stuck_timeout = 50.0
            feat.stuck_grace = 0.0
            old_task = await self._stuck_task()  # 手动回拨登记时刻 ⇒ 挂了很久
            feat._meta["11"] = ("g1_omo", time.monotonic() - 100)
            ev = _ev(42)
            ctx = _Ctx(ev)
            await feat.on_adapter_message(ctx)  # 第一次：标记
            await feat.on_adapter_message(ctx)  # 第二次：动手
            await asyncio.sleep(0)
            self.assertTrue(old_task.cancelled(), "老任务应被解卡")
            # 本条消息自己的任务（current_task = main）是新鲜的 ⇒ 不能被杀
            self.assertIn("42", feat._inflight)

        self._run(main())


class TestTrackMeta(unittest.TestCase):
    """_track 的元数据登记 / 摘表。"""

    def tearDown(self):
        replies.clear()

    def test_stores_and_clears_meta(self):
        async def main():
            feat = RecallFeature()
            done = asyncio.Event()

            async def holder():
                done.set()

            task = asyncio.create_task(holder())
            feat._track("5", task, "g1_omo")
            self.assertEqual(feat._meta["5"][0], "g1_omo")
            self.assertIn("5", feat._inflight)
            await task
            await asyncio.sleep(0)  # 让 done 回调跑完
            self.assertNotIn("5", feat._inflight)
            self.assertNotIn("5", feat._meta)
            self.assertNotIn("5", feat._suspect)
            feat.on_unload()

        asyncio.run(main())

    def test_retrack_clears_suspect(self):
        async def main():
            feat = RecallFeature()
            feat._suspect["5"] = 123.0
            task = asyncio.create_task(asyncio.Event().wait())
            feat._track("5", task, "g1_omo")
            self.assertNotIn("5", feat._suspect)
            task.cancel()
            await asyncio.sleep(0)
            feat.on_unload()

        asyncio.run(main())

    def test_on_unload_clears_all(self):
        feat = RecallFeature()
        feat._meta["1"] = ("g1_omo", 1.0)
        feat._suspect["1"] = 2.0
        feat.on_unload()
        self.assertEqual(feat._meta, {})
        self.assertEqual(feat._suspect, {})


if __name__ == "__main__":
    unittest.main()
