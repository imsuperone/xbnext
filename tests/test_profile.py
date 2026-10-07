# -*- coding: utf-8 -*-
"""R4 · 用户档案单测（指令语法 + 存储合并 + 注入链路 + 运行时分发）。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from conftest import FakeEvent, FakeKV, FakeReq, FakeTextPart, _ROOT  # noqa: F401

from xbnext import commands
from xbnext.config import Config
from xbnext.context import RequestContext
from xbnext.features import get_feature, get_feature_by_command
from xbnext.features.profile import ProfileFeature, service
from xbnext.features.profile.store import DEFAULT_MAX_LEN, ProfileStore, render
from xbnext.runtime import XbnextRuntime
from xbnext.storage import KV
from xbnext.web import handle_profile_delete, handle_profile_save, handle_profiles


class CmdEvent(FakeEvent):
    """带 ``message_str`` 的事件（指令解析用）。"""

    def __init__(self, message_str="", **kw):
        super().__init__(**kw)
        self.message_str = message_str


class GroupEvent(CmdEvent):
    """群聊事件：带群号（``get_group_id``），分群指令测试用。"""

    def __init__(self, message_str="", gid="111", **kw):
        super().__init__(message_str, **kw)
        self._gid = gid

    def get_group_id(self):
        return self._gid


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


class TestReplyFormatting(unittest.TestCase):
    """回复排版红线（真机反馈「/xbnext 系列回复都乱」）。"""

    def test_no_markdown_stars(self):
        """QQ 纯文本不渲染 markdown，``**关闭**`` 这类星号原样显示像乱码。"""
        out = service.describe({"name": "小明"}, extra=["当前状态：档案注入是关闭的。"])
        self.assertNotIn("**", out)

    def test_usage_no_column_alignment(self):
        """帮助行不许用空格做列对齐 —— QQ 是非等宽字体，对齐即乱。"""
        out = service.describe({})
        self.assertNotRegex(out, r"\S {3,}\S")  # 行内连续 3 个空格
        self.assertNotIn("\n  /xbnext", out)  # 帮助行不再缩进对齐

    def test_scope_label_header(self):
        self.assertIn(
            "你的档案（群 123456）：", service.describe({"name": "x"}, scope="群 123456")
        )
        self.assertIn("（私聊）", service.describe({}, scope="私聊"))

    def test_usage_false_skips_help_block(self):
        """设置回执不再重复"维护方式"帮助块（刚用过指令的人不需要）。"""
        out = service.describe({"name": "小明"}, scope="私聊", usage=False)
        self.assertIn("称呼：小明", out)
        self.assertNotIn("维护方式", out)
        self.assertNotIn("/xbnext profile", out)

    def test_extras_separated_by_blank_line(self):
        out = service.describe({}, extra=["当前状态：x"])
        self.assertIn("\n\n当前状态：x", out)

    def test_scope_label(self):
        self.assertEqual(service.scope_label("123456"), "群 123456")
        self.assertEqual(service.scope_label(""), "私聊")
        self.assertEqual(service.scope_label(None), "私聊")
        self.assertEqual(service.scope_label("  7  "), "群 7")

    def test_status_line_has_no_stars(self):
        """状态行原病灶：``**关闭**`` 星号原样出现在 QQ 里。"""
        feat = ProfileFeature()
        line = " ".join(feat._status_line(Config({"enable_user_profile": False})))
        self.assertNotIn("**", line)
        self.assertIn("关闭", line)
        on = " ".join(feat._status_line(Config({"enable_user_profile": True})))
        self.assertIn("档案已开启", on)
        self.assertNotIn("**", on)


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


class TestSpeakerScope(unittest.TestCase):
    """``_speaker`` 要返回 ``(platform, gid, uid)`` —— gid 是分群维度。"""

    def test_no_group_is_private(self):
        self.assertEqual(
            ProfileFeature._speaker(CmdEvent()), ("aiocqhttp", "", "10001")
        )

    def test_group_id_from_get_group_id(self):
        self.assertEqual(
            ProfileFeature._speaker(GroupEvent(gid="123")),
            ("aiocqhttp", "123", "10001"),
        )

    def test_group_id_from_message_obj(self):
        ev = CmdEvent()
        ev.message_obj = SimpleNamespace(
            group_id="456", sender=SimpleNamespace(user_id="9")
        )
        self.assertEqual(
            ProfileFeature._speaker(ev), ("aiocqhttp", "456", "9")
        )

    def test_zero_none_group_is_private(self):
        self.assertEqual(ProfileFeature._speaker(GroupEvent(gid="0"))[1], "")
        self.assertEqual(ProfileFeature._speaker(GroupEvent(gid="None"))[1], "")

    def test_group_from_unified_msg_origin(self):
        ev = CmdEvent()
        ev.unified_msg_origin = "aiocqhttp/GroupMessage/789"
        self.assertEqual(ProfileFeature._speaker(ev)[1], "789")

    def test_no_event_is_all_empty(self):
        self.assertEqual(ProfileFeature._speaker(None), ("", "", ""))


class TestGroupScopedCommand(unittest.TestCase):
    """档案按群隔离：一个群里改的档案，别的群和私聊都读不到。"""

    @staticmethod
    def _set(gid, name):
        feat = make_feature()
        run(feat.handle_command(GroupEvent(gid=gid), ["称呼", name], Config({})))
        return feat

    def test_set_and_view_scoped_per_group(self):
        feat = make_feature()
        conf = Config({})
        out = run(feat.handle_command(GroupEvent(gid="111"), ["称呼", "A群小明"], conf))
        self.assertIn("群 111", out)  # 回执抬头必须标范围
        run(feat.handle_command(GroupEvent(gid="222"), ["称呼", "B群小明"], conf))

        view_a = run(feat.handle_command(GroupEvent(gid="111"), [], conf))
        self.assertIn("A群小明", view_a)
        self.assertNotIn("B群小明", view_a)
        self.assertIn("（群 111）", view_a)

        view_b = run(feat.handle_command(GroupEvent(gid="222"), [], conf))
        self.assertIn("B群小明", view_b)

        view_p = run(feat.handle_command(CmdEvent(), [], conf))
        self.assertIn("还没有填写档案", view_p)
        self.assertIn("（私聊）", view_p)

    def test_delete_only_touches_own_group(self):
        feat = self._set("111", "A群小明")
        run(feat.handle_command(GroupEvent(gid="222"), ["称呼", "B群小明"], Config({})))
        out = run(feat.handle_command(GroupEvent(gid="111"), ["清空"], Config({})))
        self.assertIn("已清空", out)
        self.assertIn("群 111", out)
        gone = run(feat.handle_command(GroupEvent(gid="111"), [], Config({})))
        self.assertIn("还没有填写档案", gone)
        kept = run(feat.handle_command(GroupEvent(gid="222"), [], Config({})))
        self.assertIn("B群小明", kept)

    def test_set_reply_skips_usage_block(self):
        feat = make_feature()
        out = run(feat.handle_command(GroupEvent(gid="111"), ["称呼", "小明"], Config({})))
        self.assertIn("已更新", out)
        self.assertNotIn("维护方式", out)

    def test_legacy_profile_gets_migration_hint(self):
        """旧数据零迁移落在私聊范围；群里查看时给一行提示，不自动回退读取。"""
        feat = make_feature()
        run(feat._store.set("aiocqhttp", "10001", {"name": "旧称呼"}))

        view = run(feat.handle_command(GroupEvent(gid="111"), [], Config({})))
        self.assertIn("还没有填写档案", view)
        self.assertIn("未分群", view)

        private = run(feat.handle_command(CmdEvent(), [], Config({})))
        self.assertIn("旧称呼", private)  # 私聊直接读到旧档案
        self.assertNotIn("未分群", private)

        # 群里设过之后，提示消失（本群有自己的档案了）
        run(feat.handle_command(GroupEvent(gid="111"), ["称呼", "新称呼"], Config({})))
        view2 = run(feat.handle_command(GroupEvent(gid="111"), [], Config({})))
        self.assertIn("新称呼", view2)
        self.assertNotIn("未分群", view2)


class TestGroupScopedInjection(unittest.TestCase):
    """注入按发言时所在的群取档案。"""

    def test_injection_reads_only_current_group(self):
        feat = make_feature()
        run(feat.handle_command(GroupEvent(gid="111"), ["称呼", "A群小明"], Config({})))

        ctx = make_ctx(event=GroupEvent(gid="111"))
        run(feat.on_llm_request(ctx))
        self.assertEqual(ctx.injected, 1)
        self.assertIn("A群小明", ctx.req.extra_user_content_parts[0].text)

        other = make_ctx(event=GroupEvent(gid="222"))
        run(feat.on_llm_request(other))
        self.assertEqual(other.injected, 0, "别的群不能读到群 111 的档案")

        private = make_ctx()
        run(feat.on_llm_request(private))
        self.assertEqual(private.injected, 0, "私聊不能读到群档案")


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

    def test_execute_failure_returns_friendly_text(self):
        """异常兜底：给用户友好文案，repr/异常内容只进日志（不许泄露）。"""
        rt = self._runtime()
        feat = get_feature_by_command("profile")
        original = feat.__dict__.get("handle_command")

        async def boom(*a, **k):
            raise ValueError("内部秘密细节")

        feat.handle_command = boom
        try:
            out = run(rt.handle_command("profile", CmdEvent("/xbnext profile")))
        finally:
            if original is None:
                feat.__dict__.pop("handle_command", None)
            else:
                feat.handle_command = original
        self.assertIn("执行出错", out)
        self.assertIn("稍后再试", out)
        self.assertNotIn("内部秘密细节", out)
        self.assertNotIn("ValueError", out)

    def test_parse_failure_returns_friendly_text(self):
        rt = self._runtime()
        original = commands.split

        def broken(*a, **k):
            raise ValueError("解析秘密")

        commands.split = broken
        try:
            out = run(rt.handle_command("profile", CmdEvent("/xbnext profile")))
        finally:
            commands.split = original
        self.assertIn("没看懂", out)
        self.assertNotIn("解析秘密", out)


class TestDispatch(unittest.TestCase):
    """单指令分发矩阵（xbdoc/xbimg 同款）：菜单 / 状态 / 档案 / 未知提示。

    全部由插件自己回答 —— 不依赖 AstrBot 指令组的「参数不足」树，
    打错的子指令也不会漏给 LLM 乱答。
    """

    @staticmethod
    def _runtime():
        rt = XbnextRuntime(config={}, kv_store=FakeKV(), logger=None)
        run(rt.on_loaded())
        return rt

    def test_bare_and_help_words_return_menu(self):
        rt = self._runtime()
        menu = run(rt.dispatch(CmdEvent("/xbnext")))
        self.assertIn("🧩【XBNEXT · 指令菜单】", menu)
        self.assertIn("━━━━━━━━━━━━━━━━━━━━", menu)
        self.assertIn("• /xbnext status", menu)
        self.assertIn("• /xbnext profile 清空", menu)
        # 排版红线：无 markdown 星号、无空格列对齐
        self.assertNotIn("**", menu)
        self.assertNotRegex(menu, r"\S {3,}\S")
        for word in ("/xbnext help", "/xbnext h", "/xbnext 菜单", "/xbnext ?"):
            self.assertEqual(run(rt.dispatch(CmdEvent(word))), menu, word)

    def test_status_words(self):
        rt = self._runtime()
        out = run(rt.dispatch(CmdEvent("/xbnext status")))
        self.assertIn("XBNEXT v", out)
        self.assertIn("KV：", out)
        self.assertEqual(run(rt.dispatch(CmdEvent("/xbnext 状态"))), out)

    def test_profile_round_trip_via_dispatch(self):
        rt = self._runtime()
        out = run(rt.dispatch(CmdEvent("/xbnext profile 称呼 小明")))
        self.assertIn("已更新", out)
        view = run(rt.dispatch(CmdEvent("/xbnext profile")))
        self.assertIn("称呼：小明", view)

    def test_unknown_sub_gets_tip_not_menu(self):
        rt = self._runtime()
        out = run(rt.dispatch(CmdEvent("/xbnext foo")))
        self.assertIn("未知子指令", out)
        self.assertIn("「foo」", out)
        self.assertIn("/xbnext 可查看", out)
        self.assertNotIn("🧩【", out, "未知提示不应整份甩菜单")


class TestProfileStoreIndex(unittest.TestCase):
    """档案页签要能列出全部档案，而 AstrBot 的 KV 没有"按键遍历"能力。"""

    @staticmethod
    def _store():
        return ProfileStore(KV(owner=FakeKV()), logger=None)

    def test_set_registers_index_and_lists_row(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "小明"}))
        rows = run(st.list_all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "aiocqhttp")
        self.assertEqual(rows[0]["uid"], "10001")
        self.assertEqual(rows[0]["profile"]["name"], "小明")
        self.assertIn("aiocqhttp|10001", run(st._kv.get_list("profile:index")))

    def test_delete_also_updates_index(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "小明"}))
        self.assertTrue(run(st.delete("aiocqhttp", "10001")))
        self.assertEqual(run(st.list_all()), [])
        self.assertEqual(run(st._kv.get_list("profile:index")), [])

    def test_empty_profile_is_never_listed(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {}))  # normalize 后为空 → 不落库
        self.assertEqual(run(st.list_all()), [])

    def test_two_profiles_coexist(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "小明"}))
        run(st.set("aiocqhttp", "20002", {"name": "小红"}))
        rows = run(st.list_all())
        self.assertEqual({r["uid"] for r in rows}, {"10001", "20002"})

    def test_stale_index_entry_is_pruned(self):
        """档案键被外部删掉后，索引里的孤儿条目要自愈，不能永远报幽灵档案。"""
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "小明"}))
        run(st._kv.delete("profile:aiocqhttp:10001"))
        self.assertEqual(run(st.list_all()), [])
        self.assertEqual(run(st._kv.get_list("profile:index")), [])

    def test_index_entries_survive_store_recreation(self):
        """索引落在 KV 里，换一个 Store 实例（重启插件）仍能列出来。"""
        owner = FakeKV()
        first = ProfileStore(KV(owner=owner), logger=None)
        run(first.set("aiocqhttp", "10001", {"name": "小明"}))
        second = ProfileStore(KV(owner=owner), logger=None)
        rows = run(second.list_all())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["profile"]["name"], "小明")


class TestGroupScopedStore(unittest.TestCase):
    """分群存储：键/记号带群维度，scope 之间互不串、删一个不伤别的。"""

    @staticmethod
    def _store():
        return ProfileStore(KV(owner=FakeKV()), logger=None)

    def test_key_shape(self):
        from xbnext.features.profile.store import key_of

        self.assertEqual(
            key_of("aiocqhttp", "10001", "123"), "profile:aiocqhttp:123:10001"
        )
        self.assertEqual(key_of("aiocqhttp", "10001", ""), "profile:aiocqhttp:10001")
        self.assertEqual(key_of("aiocqhttp", "10001", None), "profile:aiocqhttp:10001")
        self.assertEqual(
            key_of("aiocqhttp", "10001", " 123 "), "profile:aiocqhttp:123:10001"
        )
        # 群号里的 | 会撑爆索引记号，必须剥掉
        self.assertEqual(
            key_of("aiocqhttp", "10001", "1|2"), "profile:aiocqhttp:12:10001"
        )

    def test_scopes_are_isolated(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "A群小明"}, "111"))
        run(st.set("aiocqhttp", "10001", {"name": "B群小明"}, "222"))
        run(st.set("aiocqhttp", "10001", {"name": "私聊小明"}))
        self.assertEqual(run(st.get("aiocqhttp", "10001", "111"))["name"], "A群小明")
        self.assertEqual(run(st.get("aiocqhttp", "10001", "222"))["name"], "B群小明")
        self.assertEqual(run(st.get("aiocqhttp", "10001"))["name"], "私聊小明")
        self.assertEqual(run(st.get("aiocqhttp", "10001", "333")), {}, "没设过的群不能串")

    def test_list_rows_carry_group_and_parse_legacy_token(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "群档案"}, "111"))
        run(st.set("aiocqhttp", "20002", {"name": "旧档案"}))  # 老键形 = 未分群
        rows = {r["uid"]: r for r in run(st.list_all())}
        self.assertEqual(rows["10001"]["group"], "111")
        self.assertEqual(rows["20002"]["group"], "")
        tokens = run(st._kv.get_list("profile:index"))
        self.assertIn("aiocqhttp|111|10001", tokens)
        self.assertIn("aiocqhttp|20002", tokens)

    def test_delete_only_touches_own_scope(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "群档案"}, "111"))
        run(st.set("aiocqhttp", "10001", {"name": "私聊档案"}))
        self.assertTrue(run(st.delete("aiocqhttp", "10001", "111")))
        self.assertEqual(run(st.get("aiocqhttp", "10001", "111")), {})
        self.assertEqual(run(st.get("aiocqhttp", "10001"))["name"], "私聊档案")
        self.assertEqual(len(run(st.list_all())), 1)

    def test_stale_group_token_is_pruned(self):
        st = self._store()
        run(st.set("aiocqhttp", "10001", {"name": "x"}, "111"))
        run(st._kv.delete("profile:aiocqhttp:111:10001"))
        self.assertEqual(run(st.list_all()), [])
        self.assertEqual(run(st._kv.get_list("profile:index")), [])


class TestProfileWebApi(unittest.TestCase):
    """WebUI 档案页签的后端：列表 / 写入 / 删除。"""

    def test_list_starts_empty(self):
        self.assertEqual(run(make_feature().web_list()), [])

    def test_save_then_list_then_delete(self):
        feat = make_feature()
        out = run(feat.web_save(
            {"platform": "aiocqhttp", "uid": "30003", "name": "阿三",
             "facts": "爱睡懒觉", "style": "慵懒"}))
        self.assertFalse(out["deleted"])
        self.assertEqual(out["profile"]["name"], "阿三")

        rows = run(feat.web_list())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["profile"]["facts"], "爱睡懒觉")

        # 三个字段全空 = 删掉整份档案（页面上看到空的，存的就是没有）
        out2 = run(feat.web_save(
            {"platform": "aiocqhttp", "uid": "30003", "name": "", "facts": "", "style": ""}))
        self.assertTrue(out2["deleted"])
        self.assertEqual(run(feat.web_list()), [])

    def test_save_without_identity_is_rejected(self):
        feat = make_feature()
        with self.assertRaises(ValueError):
            run(feat.web_save({"name": "小明"}))

    def test_save_with_group_and_move(self):
        """带 group 保存 = 分群写入；改 group（带 prev_group）= 挪群不留重复行。"""
        feat = make_feature()
        saved = run(feat.web_save(
            {"platform": "aiocqhttp", "uid": "5", "group": "111", "name": "群档案"}))
        self.assertEqual(saved["group"], "111")
        rows = run(feat.web_list())
        self.assertEqual(rows[0]["group"], "111")

        run(feat.web_save(
            {"platform": "aiocqhttp", "uid": "5", "group": "222",
             "prev_group": "111", "name": "群档案"}))
        rows = run(feat.web_list())
        self.assertEqual(len(rows), 1, "挪群后旧范围不能留重复行")
        self.assertEqual(rows[0]["group"], "222")
        self.assertEqual(run(feat._store.get("aiocqhttp", "5", "111")), {})

    def test_delete_with_group_only_touches_that_scope(self):
        feat = make_feature()
        run(feat.web_save(
            {"platform": "aiocqhttp", "uid": "6", "group": "111", "name": "x"}))
        run(feat.web_save({"platform": "aiocqhttp", "uid": "6", "name": "x"}))
        run(feat.web_delete({"platform": "aiocqhttp", "uid": "6", "group": "111"}))
        rows = run(feat.web_list())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group"], "", "私聊那份不能被群删除误伤")

    def test_delete_without_identity_is_rejected(self):
        feat = make_feature()
        with self.assertRaises(ValueError):
            run(feat.web_delete({"platform": "aiocqhttp"}))

    def test_ops_on_unloaded_feature_raise(self):
        fresh = ProfileFeature()
        with self.assertRaises(RuntimeError):
            run(fresh.web_list())
        with self.assertRaises(RuntimeError):
            run(fresh.web_save({"platform": "a", "uid": "1"}))

    def test_handlers_lazy_init_runtime(self):
        """web 端点自己补初始化 —— 没跑过 on_astrbot_loaded 也要能用。"""
        rt = XbnextRuntime(config={}, kv_store=FakeKV(), logger=None)
        self.assertFalse(rt._loaded)

        saved = run(handle_profile_save(
            rt, {"platform": "aiocqhttp", "uid": "7", "name": "阿七"}))
        self.assertTrue(saved["ok"])
        self.assertTrue(rt._loaded, "写档案前应先懒初始化")

        listed = run(handle_profiles(rt))
        self.assertEqual(len(listed["data"]), 1)
        self.assertEqual(listed["data"][0]["uid"], "7")

        deleted = run(handle_profile_delete(rt, {"platform": "aiocqhttp", "uid": "7"}))
        self.assertTrue(deleted["ok"])
        self.assertEqual(run(handle_profiles(rt))["data"], [])


class TestLazyInit(unittest.TestCase):
    """热装的插件收不到 ``on_astrbot_loaded`` —— 入口必须自己补初始化。"""

    @staticmethod
    def _runtime():
        return XbnextRuntime(config={}, kv_store=FakeKV(), logger=None)

    def test_on_loaded_is_idempotent(self):
        rt = self._runtime()
        run(rt.on_loaded())
        feat = rt.get_feature("enable_user_profile")
        store = feat.store
        run(rt.on_loaded())  # 第二次直接返回，不重建存储
        self.assertIs(feat.store, store)
        self.assertTrue(rt._loaded)

    def test_llm_request_triggers_init(self):
        rt = self._runtime()
        self.assertFalse(rt._loaded)
        run(rt.handle_llm_request(FakeEvent(), FakeReq(prompt="你好")))
        self.assertTrue(rt._loaded)

    def test_command_triggers_init(self):
        """真机症状：/xbnext profile 回"档案存储还没就绪"。"""
        rt = self._runtime()
        self.assertFalse(rt._loaded)
        run(rt.handle_command("profile", CmdEvent("/xbnext profile")))
        self.assertTrue(rt._loaded)

    def test_message_sent_triggers_init(self):
        rt = self._runtime()
        self.assertFalse(rt._loaded)
        run(rt.handle_message_sent(FakeEvent()))
        self.assertTrue(rt._loaded)

    def test_ensure_loaded_is_a_noop_when_loaded(self):
        rt = self._runtime()
        run(rt.on_loaded())
        feat = rt.get_feature("enable_user_profile")
        store = feat.store
        run(rt.ensure_loaded())
        self.assertIs(feat.store, store)


if __name__ == "__main__":
    unittest.main()
