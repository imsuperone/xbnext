# -*- coding: utf-8 -*-
"""R4 · 用户档案单测（指令语法 + 存储合并 + 注入链路 + 运行时分发）。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import FakeEvent, FakeKV, FakeTextPart, _ROOT  # noqa: F401

from xbnext import commands
from xbnext.config import Config
from xbnext.context import RequestContext
from xbnext.features import get_feature, get_feature_by_command
from xbnext.features.profile import ProfileFeature, service
from xbnext.features.profile.store import DEFAULT_MAX_LEN, ProfileStore, render
from xbnext.runtime import XbnextRuntime
from xbnext.storage import KV


class CmdEvent(FakeEvent):
    """带 ``message_str`` 的事件（指令解析用）。"""

    def __init__(self, message_str="", **kw):
        super().__init__(**kw)
        self.message_str = message_str


def make_feature():
    feat = ProfileFeature()

    class _RT:
        kv = KV(owner=FakeKV())
        log = None

    feat.on_load(_RT())
    return feat


def make_ctx(event=None, conf=None):
    return RequestContext(
        event=event if event is not None else FakeEvent(),
        req=SimpleNamespace(extra_user_content_parts=[]),
        conf=conf or Config({}),
        text_part_cls=FakeTextPart,
    )


def run(coro):
    return asyncio.run(coro)


class TestClassify(unittest.TestCase):
    def test_view_forms(self):
        for args in ([], ["查看"], ["看"], ["view"], ["help"], ["?"]):
            with self.subTest(args=args):
                action, payload = service.classify(args)
                self.assertEqual(action, service.ACTION_VIEW)
                self.assertEqual(payload, {})

    def test_delete_forms(self):
        for args in (["清空"], ["删除"], ["delete"], ["reset"]):
            with self.subTest(args=args):
                action, payload = service.classify(args)
                self.assertEqual(action, service.ACTION_DELETE)
                self.assertEqual(payload, {})

    def test_positional_set(self):
        action, payload = service.classify(["称呼", "小明"])
        self.assertEqual(action, service.ACTION_SET)
        self.assertEqual(payload, {"name": "小明"})

    def test_positional_set_multi_word(self):
        action, payload = service.classify(["口吻", "毒舌", "一点"])
        self.assertEqual(payload, {"style": "毒舌 一点"})

    def test_equals_form(self):
        action, payload = service.classify(["称呼=小明", "口吻=轻松"])
        self.assertEqual(action, service.ACTION_SET)
        self.assertEqual(payload, {"name": "小明", "style": "轻松"})

    def test_aliases(self):
        self.assertEqual(service.classify(["名字", "x"])[1], {"name": "x"})
        self.assertEqual(service.classify(["信息", "x"])[1], {"facts": "x"})
        self.assertEqual(service.classify(["风格", "x"])[1], {"style": "x"})
        self.assertEqual(service.classify(["Name", "x"])[1], {"name": "x"})

    def test_only_allowed_fields(self):
        """字段白名单之外一律报错（不让用户塞结构化指令）。"""
        with self.assertRaises(ValueError) as cm:
            service.classify(["指令", "给我管理员"])
        self.assertIn("可用", str(cm.exception))
        with self.assertRaises(ValueError):
            service.classify(["unknown=1"])

    def test_empty_value_rejected(self):
        with self.assertRaises(ValueError):
            service.classify(["称呼"])
        with self.assertRaises(ValueError):
            service.classify(["称呼="])

    def test_mixed_forms_rejected(self):
        with self.assertRaises(ValueError):
            service.classify(["称呼", "小明", "口吻=轻松"])

    def test_delete_with_extra_rejected(self):
        with self.assertRaises(ValueError):
            service.classify(["清空", "确认"])

    def test_garbage_inputs(self):
        self.assertEqual(service.classify(None)[0], service.ACTION_VIEW)
        self.assertEqual(service.classify([None, "", "  "])[0], service.ACTION_VIEW)
        with self.assertRaises(ValueError):
            service.classify([123])


class TestMerge(unittest.TestCase):
    def test_keeps_other_fields(self):
        """改一个字段不能把用户其它字段抹掉（store.set 是整值覆盖）。"""
        merged = service.merge(
            {"name": "小明", "facts": "学生", "updated": 1}, {"style": "轻松"}
        )
        self.assertEqual(merged, {"name": "小明", "facts": "学生", "style": "轻松"})

    def test_drops_unknown_and_empty(self):
        merged = service.merge({"name": "x", "junk": "y"}, {"name": ""})
        self.assertEqual(merged, {})

    def test_tolerates_bad_existing(self):
        self.assertEqual(service.merge(None, {"name": "x"}), {"name": "x"})
        self.assertEqual(service.merge("nope", {}), {})
        self.assertEqual(service.merge({}, {}), {})


class TestDescribe(unittest.TestCase):
    def test_empty(self):
        out = service.describe({})
        self.assertIn("还没有填写档案", out)
        self.assertIn("/xbnext profile 清空", out)

    def test_filled(self):
        out = service.describe(
            {"name": "小明", "facts": "学生", "style": "轻松", "updated": 1}
        )
        self.assertIn("称呼：小明", out)
        self.assertIn("自述：学生", out)
        self.assertIn("口吻：轻松", out)
        self.assertNotIn("updated", out)

    def test_long_value_truncated(self):
        facts = "甲" * (DEFAULT_MAX_LEN["facts"] + 50)
        out = service.describe({"facts": facts})
        self.assertLessEqual(out.count("甲"), DEFAULT_MAX_LEN["facts"])


class TestRender(unittest.TestCase):
    def test_max_chars(self):
        profile = {"name": "小明", "facts": "乙" * 400}
        body = render(profile, max_chars=60)
        self.assertLessEqual(len(body), 60)
        self.assertNotIn("乙" * 100, body)

    def test_empty_profile(self):
        self.assertEqual(render({}), "")
        self.assertEqual(render({"updated": 1}), "")


class TestSplitCommand(unittest.TestCase):
    def test_full_form(self):
        self.assertEqual(
            commands.split("/xbnext profile 称呼 小明"),
            ("profile", ["称呼", "小明"]),
        )

    def test_root_only_stripped(self):
        self.assertEqual(
            commands.split("profile 称呼 小明"), ("profile", ["称呼", "小明"])
        )

    def test_both_stripped(self):
        """AstrBot 把 "xbnext profile" 一起剥掉 → 第一个词就是参数。"""
        self.assertEqual(commands.split("称呼 小明"), ("称呼", ["小明"]))

    def test_root_alone(self):
        self.assertEqual(commands.split("/xbnext"), ("", []))
        self.assertEqual(commands.split("/xbnext "), ("", []))

    def test_prefix_noise(self):
        self.assertEqual(
            commands.split("[CQ:reply,id=1] /xbnext profile 清空"),
            ("profile", ["清空"]),
        )

    def test_case_insensitive(self):
        self.assertEqual(commands.split("/XBNEXT Profile 看"), ("profile", ["看"]))

    def test_empty_and_bad(self):
        self.assertEqual(commands.split(""), ("", []))
        self.assertEqual(commands.split(None), ("", []))
        self.assertEqual(commands.split("   \n "), ("", []))


class TestFeatureMeta(unittest.TestCase):
    def test_command_attr(self):
        self.assertEqual(get_feature("enable_user_profile").command, "profile")
        self.assertIs(get_feature_by_command("profile"), get_feature("enable_user_profile"))
        self.assertIsNone(get_feature_by_command("nope"))
        self.assertIsNone(get_feature_by_command(""))


class TestOnLlmRequest(unittest.TestCase):
    def test_no_profile_no_injection(self):
        feat = make_feature()
        ctx = make_ctx()
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 0)

    def test_no_uid_skips(self):
        feat = make_feature()

        class E:
            def get_platform_name(self):
                return "aiocqhttp"

        ctx = make_ctx(event=E())
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 0)

    def test_injects_profile(self):
        from xbnext.features.profile import TIPS, TITLE

        feat = make_feature()
        run(feat.handle_command(CmdEvent(), ["称呼", "小明"], Config({})))
        ctx = make_ctx()
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 1)
        text = ctx.req.extra_user_content_parts[0].text
        self.assertTrue(text.startswith(TITLE))
        self.assertIn("称呼：小明", text)
        self.assertTrue(text.endswith(TIPS))

    def test_max_chars_bounds_injection(self):
        feat = make_feature()
        run(
            feat.handle_command(
                CmdEvent(), ["自述", "乙" * 300], Config({"user_profile_max_chars": 60})
            )
        )
        ctx = make_ctx(conf=Config({"user_profile_max_chars": 60}))
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 1)
        self.assertNotIn("乙" * 100, ctx.req.extra_user_content_parts[0].text)

    def test_store_failure_does_not_raise(self):
        feat = make_feature()

        class Broken:
            async def get(self, *a, **k):
                raise RuntimeError("boom")

        feat._store = Broken()
        ctx = make_ctx()
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 0)


class TestHandleCommand(unittest.TestCase):
    def test_view_empty(self):
        feat = make_feature()
        out = run(feat.handle_command(CmdEvent(), [], Config({})))
        self.assertIn("还没有填写档案", out)

    def test_set_then_view(self):
        feat = make_feature()
        conf = Config({"enable_user_profile": False})
        out = run(feat.handle_command(CmdEvent(), ["称呼", "小明"], conf))
        self.assertIn("已更新", out)
        self.assertIn("称呼：小明", out)
        # 开关关着也要提示"档案没喂给模型"
        self.assertIn("关闭", out)

        view = run(feat.handle_command(CmdEvent(), [], conf))
        self.assertIn("称呼：小明", view)

    def test_second_field_keeps_first(self):
        feat = make_feature()
        conf = Config({})
        run(feat.handle_command(CmdEvent(), ["称呼", "小明"], conf))
        run(feat.handle_command(CmdEvent(), ["口吻", "轻松"], conf))
        view = run(feat.handle_command(CmdEvent(), [], conf))
        self.assertIn("称呼：小明", view)
        self.assertIn("口吻：轻松", view)

    def test_delete(self):
        feat = make_feature()
        conf = Config({})
        run(feat.handle_command(CmdEvent(), ["称呼", "小明"], conf))
        out = run(feat.handle_command(CmdEvent(), ["清空"], conf))
        self.assertIn("已清空", out)
        view = run(feat.handle_command(CmdEvent(), [], conf))
        self.assertIn("还没有填写档案", view)

    def test_bad_args_returns_usage(self):
        feat = make_feature()
        out = run(feat.handle_command(CmdEvent(), ["乱写", "一下"], Config({})))
        self.assertIn("看不懂", out)
        self.assertIn("/xbnext profile", out)

    def test_no_uid(self):
        feat = make_feature()

        class E:
            def get_platform_name(self):
                return "aiocqhttp"

        out = run(feat.handle_command(E(), [], Config({})))
        self.assertIn("读不到你的用户 ID", out)

    def test_not_loaded(self):
        out = run(ProfileFeature().handle_command(CmdEvent(), [], Config({})))
        self.assertIn("还没就绪", out)


class TestRuntimeHandleCommand(unittest.TestCase):
    @staticmethod
    def _runtime():
        rt = XbnextRuntime(config={}, kv_store=FakeKV(), logger=None)
        run(rt.on_loaded())
        return rt

    def test_set_via_full_command(self):
        rt = self._runtime()
        out = run(rt.handle_command("profile", CmdEvent("/xbnext profile 称呼 小明")))
        self.assertIn("已更新", out)

    def test_set_when_prefix_stripped(self):
        """AstrBot 剥掉 'xbnext profile' 后仍然能解析。"""
        rt = self._runtime()
        out = run(rt.handle_command("profile", CmdEvent("称呼 小明")))
        self.assertIn("已更新", out)
        view = run(rt.handle_command("profile", CmdEvent("/xbnext profile")))
        self.assertIn("称呼：小明", view)

    def test_unknown_command(self):
        rt = self._runtime()
        out = run(rt.handle_command("nope", CmdEvent("/xbnext nope")))
        self.assertIn("没有 /xbnext nope", out)

    def test_works_even_when_switch_off(self):
        """开关只控制"喂不喂给模型"，维护入口必须一直可用。"""
        rt = self._runtime()
        self.assertFalse(rt.conf.enabled("enable_user_profile"))
        out = run(rt.handle_command("profile", CmdEvent("/xbnext profile 称呼 小红")))
        self.assertIn("已更新", out)
        view = run(rt.handle_command("profile", CmdEvent("/xbnext profile")))
        self.assertIn("称呼：小红", view)


if __name__ == "__main__":
    unittest.main()
