# -*- coding: utf-8 -*-
"""R3 · 表情表权威源自动更新单测（updater 纯函数 + FaceFeature 生命周期）。

红线：测试环境禁网 —— 更新循环的首次动作必须是 ≥60s 的 sleep，
``asyncio.run`` 退出时任务被取消，``pull_overlay`` 在本文件里一律被
monkeypatch 成假实现（计数断言 0）。
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime

from conftest import FakeKV, _ROOT  # noqa: F401

from xbnext.features.face import FaceFeature, data, updater
from xbnext.runtime import XbnextRuntime


class _Log:
    """最小日志捕获（与 test_face._CaptureLog 同形，独立定义避免耦合）。"""

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


def make_rt(kv=None, log=None, **conf):
    return XbnextRuntime(
        config=conf,
        kv_store=kv if kv is not None else FakeKV(),
        logger=log if log is not None else _Log(),
    )


# ==================================================================
# 纯逻辑
# ==================================================================
class TestParseIndex(unittest.TestCase):
    def test_valid_items(self):
        payload = [
            {"emojiId": "0", "describe": "/惊讶", "qcid": 0},
            {"emojiId": "496", "describe": "/阴晴圆缺", "qcid": 496},
            {"emojiId": "\u2600", "describe": "/晴天", "qcid": 9728},
        ]
        self.assertEqual(
            updater.parse_index(payload),
            {0: "惊讶", 496: "阴晴圆缺", 9728: "晴天"},
        )

    def test_bad_items_skipped(self):
        payload = [
            "not-a-dict",
            {"emojiId": "1", "describe": 123, "qcid": 1},  # describe 非 str
            {"emojiId": "2", "describe": "/", "qcid": 2},  # 空名
            {"emojiId": "x", "describe": "/坏", "qcid": -1},  # 非数字且 qcid 非法
            {"emojiId": "3", "describe": "/好", "qcid": 3},
        ]
        self.assertEqual(updater.parse_index(payload), {3: "好"})

    def test_duplicate_key_first_wins(self):
        payload = [
            {"emojiId": "9", "describe": "/先", "qcid": 9},
            {"emojiId": "9", "describe": "/后", "qcid": 9},
        ]
        self.assertEqual(updater.parse_index(payload), {9: "先"})

    def test_bad_payload_returns_empty(self):
        self.assertEqual(updater.parse_index("{broken"), {})
        self.assertEqual(updater.parse_index({"a": 1}), {})  # 非 list
        self.assertEqual(updater.parse_index(None), {})


class TestMergeMissing(unittest.TestCase):
    def test_builtin_excluded_new_kept(self):
        merged = updater.merge_missing(
            {
                4: "得意",  # 内置表已有 → 排除
                999901: "新表情",  # 权威源新增 → 保留
                "abc": "坏键",  # int() 失败 → 跳过
                7: "   ",  # 空名 → 跳过
            }
        )
        self.assertEqual(merged, {999901: "新表情"})

    def test_empty_input(self):
        self.assertEqual(updater.merge_missing({}), {})
        self.assertEqual(updater.merge_missing(None), {})


class TestNextRunDelay(unittest.TestCase):
    def test_before_target_today(self):
        now = datetime(2026, 1, 1, 4, 0, 0)
        self.assertEqual(updater.next_run_delay(now, "04:30"), 1800)

    def test_after_target_tomorrow(self):
        now = datetime(2026, 1, 1, 5, 0, 0)
        self.assertEqual(updater.next_run_delay(now, "04:30"), 84600)

    def test_target_equal_now_is_tomorrow(self):
        now = datetime(2026, 1, 1, 4, 30, 0)
        self.assertEqual(updater.next_run_delay(now, "04:30"), 86400)

    def test_invalid_falls_back_to_default(self):
        now = datetime(2026, 1, 1, 4, 0, 0)
        self.assertEqual(updater.next_run_delay(now, "banana"), 1800)
        self.assertEqual(updater.next_run_delay(now, None), 1800)
        self.assertEqual(updater.next_run_delay(now, "25:99"), 1800)

    def test_single_digit_hour_accepted(self):
        now = datetime(2026, 1, 1, 4, 0, 0)
        self.assertEqual(updater.next_run_delay(now, "4:35"), 2100)


class TestOverlay(unittest.TestCase):
    def setUp(self):
        updater.set_overlay(None)

    def tearDown(self):
        updater.set_overlay(None)

    def test_atomic_bad_key_does_not_wipe(self):
        # 单个坏键只跳过它自己，好键必须留下（旧写法一遇坏键整表作废）
        updater.set_overlay({"999901": "新表情", "zzz": "坏键", "updated_at": "x"})
        self.assertEqual(updater.get_overlay(), {999901: "新表情"})

    def test_builtin_filtered_and_priority(self):
        updater.set_overlay({4: "假得意", 999901: "新表情"})
        self.assertEqual(updater.get_overlay(), {999901: "新表情"})
        # 内置表优先：overlay 里没有 4，查到的仍是内置名
        self.assertEqual(updater.face_name_with_overlay(4), data.face_name(4))
        self.assertEqual(updater.face_name_with_overlay(999901), "新表情")
        self.assertIsNone(updater.face_name_with_overlay(999999))

    def test_roundtrip(self):
        updater.set_overlay({999901: "新表情", 999902: "另一个"})
        stored = updater.overlay_to_stored()
        self.assertEqual(stored, {"999901": "新表情", "999902": "另一个"})
        updater.set_overlay(None)
        restored = updater.overlay_from_stored(
            {"ids": stored, "updated_at": "2026-01-01T04:30:00"}
        )
        self.assertEqual(restored, {999901: "新表情", 999902: "另一个"})

    def test_from_stored_none_clears(self):
        updater.set_overlay({999901: "新表情"})
        updater.overlay_from_stored(None)
        self.assertEqual(updater.get_overlay(), {})

    def test_from_stored_flat_legacy(self):
        restored = updater.overlay_from_stored({"14": "微笑", "999901": "新表情"})
        # 14 是内置键 → set_overlay 过滤掉；999901 保留
        self.assertEqual(restored, {999901: "新表情"})

    def test_added_since(self):
        updater.set_overlay({999901: "a", 999902: "b"})
        self.assertEqual(updater.added_since({999901}), 1)
        self.assertEqual(updater.added_since({999901, 999902}), 0)
        self.assertEqual(updater.added_since(set()), 2)

    def test_updated_at_from_stored(self):
        self.assertEqual(
            updater.updated_at_from_stored(
                {"ids": {}, "updated_at": "2026-01-01T04:30:00"}
            ),
            "2026-01-01T04:30:00",
        )
        self.assertIsNone(updater.updated_at_from_stored(None))
        self.assertIsNone(updater.updated_at_from_stored({}))
        self.assertIsNone(updater.updated_at_from_stored({"updated_at": "  "}))


class TestSleepConstants(unittest.TestCase):
    def test_first_action_sleep_is_at_least_60s(self):
        """红线：任务首动作必须是 ≥60s 的 sleep，测试才可能永不触网。"""
        self.assertGreaterEqual(updater.FIRST_RUN_DELAY, 60)
        self.assertGreaterEqual(updater.SLEEP_SEGMENT, 60)


# ==================================================================
# 门禁
# ==================================================================
class TestShouldPull(unittest.TestCase):
    def test_matrix(self):
        feat = FaceFeature()
        self.assertFalse(feat._should_pull(None))
        self.assertTrue(feat._should_pull(make_rt()))
        self.assertFalse(
            feat._should_pull(make_rt(enable_face_translate=False))
        )
        self.assertFalse(feat._should_pull(make_rt(face_auto_update=False)))
        self.assertFalse(
            feat._should_pull(
                make_rt(enable_face_translate=False, face_auto_update=False)
            )
        )


# ==================================================================
# 生命周期（on_load / on_unload / 更新循环）
# ==================================================================
class TestLifecycle(unittest.TestCase):
    def setUp(self):
        updater.set_overlay(None)
        self._feats = []

    def tearDown(self):
        for feat in self._feats:
            feat.on_unload()
        updater.set_overlay(None)

    def _feat(self):
        feat = FaceFeature()
        self._feats.append(feat)
        return feat

    def test_on_load_restores_overlay_from_kv(self):
        kv = FakeKV()
        kv.data["xbnext:face_overlay"] = {
            "ids": {"999901": "新表情"},
            "updated_at": "2026-01-01T04:30:00",
        }
        rt = make_rt(kv=kv)
        feat = self._feat()

        asyncio.run(feat.on_load(rt))

        self.assertEqual(updater.get_overlay(), {999901: "新表情"})
        self.assertEqual(feat._updated_at, "2026-01-01T04:30:00")
        self.assertIsNotNone(feat._task)
        # 首拉名额已消耗 → 走排程分支（仍保证首次 sleep ≥60s）
        feat.on_unload()
        self.assertIsNone(feat._task)
        self.assertIsNone(feat._runtime)

    def test_on_load_with_empty_kv_starts_fresh(self):
        rt = make_rt()
        feat = self._feat()
        asyncio.run(feat.on_load(rt))
        self.assertEqual(updater.get_overlay(), {})
        self.assertIsNone(feat._updated_at)
        feat.on_unload()

    def test_on_unload_across_loops_does_not_raise(self):
        rt = make_rt()
        feat = self._feat()
        asyncio.run(feat.on_load(rt))  # loop A 里创建任务
        asyncio.run(asyncio.sleep(0))  # loop B
        feat.on_unload()  # 循环外同步调用，不许抛
        self.assertIsNone(feat._task)

    def test_close_task_tolerates_cancel_failure(self):
        class _ExplodingTask:
            def done(self):
                return False

            def cancel(self):
                raise RuntimeError("Event loop is closed")

        feat = self._feat()
        feat._task = _ExplodingTask()
        feat.on_unload()  # 必须吞掉异常
        self.assertIsNone(feat._task)

    def test_close_task_skips_done_task(self):
        class _DoneTask:
            def done(self):
                return True

            def cancel(self):
                raise AssertionError("done 的任务不该再 cancel")

        feat = self._feat()
        feat._task = _DoneTask()
        feat._close_task()
        self.assertIsNone(feat._task)

    def test_loop_first_action_is_sleep_no_network(self):
        """首拉分支：任务先 sleep(60)，0.1s 窗口内绝不调 pull。"""
        calls = []

        async def fake_pull(url=None):
            calls.append(url)
            return {}

        orig = updater.pull_overlay
        updater.pull_overlay = fake_pull
        try:
            feat = self._feat()
            rt = make_rt()

            async def scenario():
                await feat.on_load(rt)
                await asyncio.sleep(0.1)  # 让循环任务跑起来
                feat.on_unload()

            asyncio.run(scenario())
            self.assertEqual(calls, [])
        finally:
            updater.pull_overlay = orig

    def test_loop_scheduled_branch_also_sleeps_first(self):
        """排程分支（KV 里已有 updated_at）：首次 sleep 仍 ≥60s，不触网。"""
        calls = []

        async def fake_pull(url=None):
            calls.append(url)
            return {}

        orig = updater.pull_overlay
        updater.pull_overlay = fake_pull
        try:
            kv = FakeKV()
            kv.data["xbnext:face_overlay"] = {
                "ids": {},
                "updated_at": "2026-01-01T04:30:00",
            }
            feat = self._feat()
            rt = make_rt(kv=kv)

            async def scenario():
                await feat.on_load(rt)
                await asyncio.sleep(0.1)
                feat.on_unload()

            asyncio.run(scenario())
            self.assertEqual(calls, [])
        finally:
            updater.pull_overlay = orig

    def test_loop_gate_off_never_pulls(self):
        """门禁关着 → 循环常驻但绝不 fetch（首拉名额保留）。"""
        calls = []

        async def fake_pull(url=None):
            calls.append(url)
            return {}

        orig = updater.pull_overlay
        updater.pull_overlay = fake_pull
        try:
            feat = self._feat()
            rt = make_rt(face_auto_update=False)

            async def scenario():
                await feat.on_load(rt)
                await asyncio.sleep(0.1)
                feat.on_unload()

            asyncio.run(scenario())
            self.assertEqual(calls, [])
        finally:
            updater.pull_overlay = orig


# ==================================================================
# _pull_once
# ==================================================================
class TestPullOnce(unittest.TestCase):
    def setUp(self):
        updater.set_overlay(None)
        self._feats = []

    def tearDown(self):
        for feat in self._feats:
            feat.on_unload()
        updater.set_overlay(None)

    def _feat(self):
        feat = FaceFeature()
        self._feats.append(feat)
        return feat

    @staticmethod
    def _patch_pull(fake):
        orig = updater.pull_overlay
        updater.pull_overlay = fake
        return orig

    def test_success_writes_kv_and_logs_info(self):
        async def fake_pull(url=None):
            updater.set_overlay({"999901": "新表情"})
            return updater.get_overlay()

        orig = self._patch_pull(fake_pull)
        kv, log = FakeKV(), _Log()
        rt = make_rt(kv=kv, log=log)
        feat = self._feat()
        try:

            async def scenario():
                await feat.on_load(rt)
                return await feat._pull_once(rt)

            ok = asyncio.run(scenario())
        finally:
            updater.pull_overlay = orig

        self.assertTrue(ok)
        stored = kv.data["xbnext:face_overlay"]
        self.assertEqual(stored["ids"], {"999901": "新表情"})
        self.assertTrue(stored["updated_at"])
        self.assertIsNotNone(feat._updated_at)
        infos = log.of("info")
        self.assertTrue(any("[XBNEXT]" in m and "新增" in m for m in infos), infos)
        self.assertEqual(log.of("warning"), [])

    def test_pull_failure_logs_warning_and_keeps_state(self):
        async def fake_pull(url=None):
            raise OSError("network down")

        orig = self._patch_pull(fake_pull)
        kv, log = FakeKV(), _Log()
        rt = make_rt(kv=kv, log=log)
        feat = self._feat()
        try:

            async def scenario():
                await feat.on_load(rt)
                return await feat._pull_once(rt)

            ok = asyncio.run(scenario())
        finally:
            updater.pull_overlay = orig

        self.assertFalse(ok)
        self.assertIsNone(feat._updated_at)
        self.assertNotIn("xbnext:face_overlay", kv.data)
        warns = log.of("warning")
        self.assertTrue(any("拉取表情权威源失败" in m for m in warns), warns)
        self.assertTrue(all("[XBNEXT]" in m for m in warns), warns)
        self.assertEqual(log.of("info"), [])

    def test_no_change_logs_debug(self):
        async def fake_pull(url=None):
            updater.set_overlay({999901: "新表情"})  # 结果与上次相同
            return updater.get_overlay()

        orig = self._patch_pull(fake_pull)
        log = _Log()
        rt = make_rt(log=log)
        feat = self._feat()
        try:

            async def scenario():
                await feat.on_load(rt)
                updater.set_overlay({999901: "新表情"})  # on_load 后再铺底
                return await feat._pull_once(rt)

            ok = asyncio.run(scenario())
        finally:
            updater.pull_overlay = orig

        self.assertTrue(ok)
        self.assertTrue(log.of("debug"))
        self.assertTrue(any("无变化" in m for m in log.of("debug")))
        self.assertEqual(log.of("info"), [])


class TestStats(unittest.TestCase):
    """状态卡统计（P17 · D）：updater 纯函数 + FaceFeature.stats + build_state。"""

    def test_pure_stats_defaults(self):
        updater.set_overlay(None)
        try:
            s = updater.stats(None)
            self.assertEqual(s["builtin"], len(data.QQ_FACE_ALL))
            self.assertEqual(s["overlay"], 0)
            self.assertIsNone(s["updated_at"])
        finally:
            updater.set_overlay(None)

    def test_overlay_count_and_timestamp_passthrough(self):
        updater.set_overlay({"999999001": "测试表情"})
        try:
            s = updater.stats("  2026-10-07T04:30:00  ")
            self.assertEqual(s["overlay"], 1)
            self.assertEqual(s["updated_at"], "2026-10-07T04:30:00")
            self.assertIsNone(updater.stats(123)["updated_at"])  # 非字符串 → None
        finally:
            updater.set_overlay(None)

    def test_feature_stats_reads_instance_timestamp(self):
        feat = FaceFeature()
        self.assertIsNone(feat.stats()["updated_at"])
        feat._updated_at = "2026-10-06T04:30:00"
        st = feat.stats()
        self.assertEqual(st["updated_at"], "2026-10-06T04:30:00")
        self.assertEqual(st["builtin"], len(data.QQ_FACE_ALL))

    def test_build_state_includes_face(self):
        """走真实 runtime 的 get_feature 路径；enabled 取 schema 默认值。"""
        from xbnext.web import build_state

        payload = build_state(make_rt())
        face = payload["face"]
        self.assertEqual(face["builtin"], len(data.QQ_FACE_ALL))
        self.assertTrue(face["enabled"])  # enable_face_translate 默认开
        self.assertEqual(face["overlay"], len(updater.get_overlay()))
        self.assertIn("updated_at", face)


if __name__ == "__main__":
    unittest.main()
