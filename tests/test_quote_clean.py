# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗单测。"""

from __future__ import annotations

import os
import unittest

from conftest import FakeReq  # noqa: F401  触发 sys.path 注入

from xbnext.features.quote import service


class TestFindPlaceholders(unittest.TestCase):
    def test_empty_input(self):
        empty = {"noise": [], "degraded": [], "bare_image": [], "attachment": []}
        self.assertEqual(service.find_placeholders(""), empty)
        self.assertEqual(service.find_placeholders(None), empty)
        self.assertEqual(service.find_placeholders(123), empty)

    def test_noise(self):
        stats = service.find_placeholders("[Empty Text] 后面还有")
        self.assertEqual(stats["noise"], ["[Empty Text]"])
        self.assertFalse(stats["degraded"])

    def test_degraded(self):
        stats = service.find_placeholders("他说：[Image unavailable]")
        self.assertEqual(stats["degraded"], ["[Image unavailable]"])

    def test_bare_image(self):
        stats = service.find_placeholders("一张图：[Image]")
        self.assertEqual(stats["bare_image"], ["[Image]"])

    def test_case_insensitive(self):
        stats = service.find_placeholders("[empty text] [image UNAVAILABLE]")
        self.assertEqual(len(stats["noise"]), 1)
        self.assertEqual(len(stats["degraded"]), 1)

    def test_has_placeholders(self):
        self.assertTrue(service.has_placeholders("x [Empty Text] y"))
        self.assertFalse(service.has_placeholders("正常的一句话"))


class TestCleanPrompt(unittest.TestCase):
    def test_label_policy(self):
        """默认 label：噪声删掉，可证实失败改中文说明。"""
        out, stats = service.clean_prompt("引用内容：[Empty Text]\n图：[Image unavailable]")
        self.assertNotIn("[Empty Text]", out)
        self.assertNotIn("[Image unavailable]", out)
        self.assertIn(service.DEGRADED_LABEL, out)
        self.assertEqual(stats["noise"], 1)
        self.assertEqual(stats["degraded"], 1)

    def test_strip_policy(self):
        out, _ = service.clean_prompt("[Empty Text] x [Image] y", action="strip")
        self.assertNotIn("[Image]", out)
        self.assertNotIn("[Empty Text]", out)
        self.assertIn("x", out)

    def test_keep_policy_untouched(self):
        src = "[Empty Text] 原样"
        out, stats = service.clean_prompt(src, action="keep")
        self.assertEqual(out, src)
        self.assertEqual(stats["noise"], 1)

    def test_label_keeps_bare_image(self):
        """裸 [Image] 至少说明这里曾有图，label 模式不删。"""
        out, _ = service.clean_prompt("看这张 [Image] 好看吗")
        self.assertIn("[Image]", out)

    def test_unknown_action_falls_back_to_label(self):
        out, _ = service.clean_prompt("[Empty Text] x", action="whatever")
        self.assertNotIn("[Empty Text]", out)

    def test_clean_text_unchanged(self):
        src = "这是一句干净的话"
        out, stats = service.clean_prompt(src)
        self.assertEqual(out, src)
        self.assertFalse(any(stats.values()))

    def test_no_extra_blank_lines(self):
        src = "[Empty Text]\n\n[Empty Text]\n正文"
        out, _ = service.clean_prompt(src, action="strip")
        self.assertNotIn("\n\n\n", out)

    def test_non_str_input(self):
        self.assertEqual(service.clean_prompt(None)[0], "")
        self.assertEqual(service.clean_prompt(123)[0], "123")


class TestReport(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(service.report({"noise": 0, "degraded": 0, "bare_image": 0}), "无占位符")

    def test_nonzero(self):
        self.assertEqual(
            service.report({"noise": 2, "degraded": 1, "bare_image": 0}), "noise×2、degraded×1"
        )


class TestDeadImageUrls(unittest.TestCase):
    def test_filters_non_urls(self):
        bad = service.dead_image_urls(
            ["https://a/b.png", "/data/missing.png", "", 123, "data:image/png;base64,xx"]
        )
        self.assertEqual(bad, ["/data/missing.png"])

    def test_non_list(self):
        self.assertEqual(service.dead_image_urls(None), [])
        self.assertEqual(service.dead_image_urls("x"), [])

    def test_live_urls_kept(self):
        """http/https/data 与未知 scheme 离线无法验证，一律保留。"""
        for value in (
            "https://a/b.png",
            "http://a/b.png",
            "data:image/png;base64,AAAA",
            "ftp://a/b.png",
        ):
            with self.subTest(value=value):
                self.assertFalse(service.is_dead_image_url(value))

    def test_unknown_type_not_touched(self):
        """非字符串 / 空白不是我们能理解的类型，绝不当成死链删掉。"""
        self.assertFalse(service.is_dead_image_url(""))
        self.assertFalse(service.is_dead_image_url("   "))
        self.assertFalse(service.is_dead_image_url(None))
        self.assertFalse(service.is_dead_image_url(123))
        self.assertFalse(service.is_dead_image_url(b"x"))

    def test_local_path_uses_exists(self):
        exists = lambda p: p == "/alive.png"  # noqa: E731
        self.assertTrue(service.is_dead_image_url("/gone.png", exists=exists))
        self.assertFalse(service.is_dead_image_url("/alive.png", exists=exists))

    def test_exists_raiser_keeps_item(self):
        """判定函数抛异常时保守保留，绝不误删。"""
        def boom(_):
            raise RuntimeError("boom")

        self.assertFalse(service.is_dead_image_url("/x.png", exists=boom))

    def test_file_url_decoded(self):
        seen = []

        def exists(p):
            seen.append(p)
            return False

        service.is_dead_image_url("file:///C:/tmp/a%20b.png", exists=exists)
        self.assertTrue(seen)
        self.assertEqual(seen[0], "C:/tmp/a b.png")

    def test_split_preserves_order(self):
        exists = lambda p: p.endswith("ok.png")  # noqa: E731
        alive, dead = service.split_image_urls(
            ["1.png", "2ok.png", "https://a/x.png", "3.png"], exists=exists
        )
        self.assertEqual(alive, ["2ok.png", "https://a/x.png"])
        self.assertEqual(dead, ["1.png", "3.png"])


class TestDegradBare(unittest.TestCase):
    def test_default_keeps_bare(self):
        out, _ = service.clean_prompt("图：[Image] 看看")
        self.assertIn("[Image]", out)

    def test_degrade_bare_when_confirmed_dead(self):
        out, stats = service.clean_prompt(
            "图：[Image] 看看", degrade_bare=True
        )
        self.assertNotIn("[Image]", out)
        self.assertIn(service.DEGRADED_LABEL, out)
        self.assertEqual(stats["bare_image"], 1)

    def test_degrade_bare_noop_on_clean_text(self):
        src = "没有占位符"
        out, stats = service.clean_prompt(src, degrade_bare=True)
        self.assertEqual(out, src)
        self.assertFalse(any(stats.values()))


class TestQuoteFeatureWiring(unittest.TestCase):
    """功能级：死路径过滤 + 正文降级联动（P3 落地项）。"""

    @staticmethod
    def _ctx(prompt, urls, conf=None):
        from xbnext.config import Config
        from xbnext.context import RequestContext
        from conftest import FakeEvent, FakeReq, FakeTextPart

        event = FakeEvent(message=[])
        req = FakeReq(prompt=prompt, image_urls=urls)
        return RequestContext(
            event=event, req=req, conf=conf or Config({}), text_part_cls=FakeTextPart
        )

    def test_drops_dead_path_and_degrades_bare(self):
        from xbnext.features.quote import QuoteFeature

        alive = os.path.abspath(__file__)
        dead = os.path.join(os.path.dirname(__file__), "__no_such__.png")
        ctx = self._ctx("引用的图：[Image]，看看", [dead, alive])
        QuoteFeature().on_llm_request(ctx)
        self.assertEqual(ctx.req.image_urls, [alive])
        self.assertNotIn("[Image]", ctx.req.prompt)
        self.assertIn(service.DEGRADED_LABEL, ctx.req.prompt)

    def test_keeps_everything_when_images_alive(self):
        from xbnext.features.quote import QuoteFeature

        alive = os.path.abspath(__file__)
        ctx = self._ctx("引用的图：[Image]，看看", [alive])
        QuoteFeature().on_llm_request(ctx)
        self.assertEqual(ctx.req.image_urls, [alive])
        # 没有死路径 → 裸 [Image] 保留（它至少说明"这里曾有一张图"）
        self.assertIn("[Image]", ctx.req.prompt)

    def test_switch_off_leaves_urls_alone(self):
        from xbnext.config import Config
        from xbnext.features.quote import QuoteFeature

        dead = os.path.join(os.path.dirname(__file__), "__no_such__.png")
        conf = Config({"quote_drop_dead_images": False})
        ctx = self._ctx("看 [Image]", [dead], conf=conf)
        QuoteFeature().on_llm_request(ctx)
        self.assertEqual(ctx.req.image_urls, [dead])

    def test_keeps_bare_when_only_placeholders_in_prompt(self):
        from xbnext.features.quote import QuoteFeature

        ctx = self._ctx("正常一句话", [])
        QuoteFeature().on_llm_request(ctx)
        self.assertEqual(ctx.req.prompt, "正常一句话")


class TestAttachmentMarkers(unittest.TestCase):
    """附件引用标记 → 中文归因标签（真机第六轮：「还是会说有图、附件」）。"""

    def test_quoted_file_label_drops_path(self):
        out, stats = service.clean_prompt(
            "[File Attachment in quoted message: name 报告.docx, path C:/tmp/报告.docx]"
        )
        self.assertIn("［引用消息中的文件附件：报告.docx］", out)
        self.assertNotIn("path", out)
        self.assertNotIn("C:/tmp", out)
        self.assertEqual(stats["attachment"], 1)

    def test_current_file_label(self):
        out, _ = service.clean_prompt(
            "[File Attachment: name a.txt, path /data/a.txt]"
        )
        self.assertIn("［消息中的文件附件：a.txt］", out)
        self.assertNotIn("/data/", out)

    def test_quoted_audio_without_name(self):
        out, _ = service.clean_prompt(
            "[Audio Attachment in quoted message: path /data/x.amr]"
        )
        self.assertEqual(out, "［引用消息中的语音附件］")

    def test_video_ref_variant(self):
        out, _ = service.clean_prompt(
            "[Video Attachment in quoted message: name 视频, ref 12345]"
        )
        self.assertIn("［引用消息中的视频附件：视频］", out)
        self.assertNotIn("ref", out)

    def test_strip_removes_attachment_markers(self):
        out, _ = service.clean_prompt(
            "看 [File Attachment: name a.txt, path /a.txt]", action="strip"
        )
        self.assertNotIn("Attachment", out)
        self.assertIn("看", out)

    def test_keep_policy_untouched(self):
        src = "[Audio Attachment: path /x.amr]"
        out, stats = service.clean_prompt(src, action="keep")
        self.assertEqual(out, src)
        self.assertEqual(stats["attachment"], 1)

    def test_voice_unavailable_degraded(self):
        """core 的 [Voice unavailable] 与其它 unavailable 同级处理。"""
        out, stats = service.clean_prompt("语音：[Voice unavailable]")
        self.assertIn(service.DEGRADED_LABEL, out)
        self.assertEqual(stats["degraded"], 1)


class TestRepairQuoteBlock(unittest.TestCase):
    """引用块被清空后补事实说明，不留半截空壳（第六轮根因之二）。"""

    def test_empty_body_with_sender(self):
        src = "<Quoted Message>\n(Rinne.): [Empty Text]\n</Quoted Message>"
        out, _ = service.clean_prompt(src)
        self.assertNotIn("[Empty Text]", out)
        self.assertIn("(Rinne.): （此消息没有文字内容）", out)
        self.assertIn("<Quoted Message>", out)

    def test_empty_body_without_sender(self):
        src = "<Quoted Message>\n[Empty Text]\n</Quoted Message>"
        out, _ = service.clean_prompt(src)
        self.assertNotIn("[Empty Text]", out)
        self.assertIn(service.QUOTED_EMPTY_NOTE, out)

    def test_real_content_untouched(self):
        src = "<Quoted Message>\n(Rinne.): 这是有内容的引用\n</Quoted Message>"
        out, _ = service.clean_prompt(src)
        self.assertEqual(out, src)

    def test_partial_content_kept(self):
        src = "<Quoted Message>\n(X): 前文 [Empty Text] 后文\n</Quoted Message>"
        out, _ = service.clean_prompt(src)
        self.assertNotIn("[Empty Text]", out)
        self.assertIn("前文", out)
        self.assertIn("后文", out)
        # 正文非空 ⇒ 不触发补写
        self.assertNotIn(service.QUOTED_EMPTY_NOTE, out)

    def test_direct_non_string_and_no_marker(self):
        self.assertIsNone(service.repair_quote_block(None))
        src = "普通文本"
        self.assertEqual(service.repair_quote_block(src), src)

    def test_strip_mode_repairs_too(self):
        src = "<Quoted Message>\n(X): [Empty Text]\n</Quoted Message>"
        out, _ = service.clean_prompt(src, action="strip")
        self.assertIn(service.QUOTED_EMPTY_NOTE, out)


class TestCleanParts(unittest.TestCase):
    """``extra_user_content_parts`` 就地清洗（第六轮根因：模型唯一的内容）。"""

    @staticmethod
    def _part(text: str) -> "FakeTextPart":
        from conftest import FakeTextPart

        return FakeTextPart(text=text)

    def test_rewrites_degraded_marker(self):
        parts = [self._part("[Image unavailable]")]
        changed, removed = service.clean_parts(parts)
        self.assertEqual((changed, removed), (1, 0))
        self.assertIn(service.DEGRADED_LABEL, parts[0].text)

    def test_removes_noise_only_part(self):
        parts = [self._part("[Empty Text]"), self._part("正常的")]
        changed, removed = service.clean_parts(parts)
        self.assertEqual((changed, removed), (0, 1))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, "正常的")

    def test_repairs_quote_block_part(self):
        parts = [self._part("<Quoted Message>\n(Light): [Empty Text]\n</Quoted Message>")]
        changed, removed = service.clean_parts(parts)
        self.assertEqual((changed, removed), (1, 0))
        self.assertIn("（此消息没有文字内容）", parts[0].text)
        self.assertNotIn("[Empty Text]", parts[0].text)

    def test_foreign_part_identity_preserved(self):
        """共存红线：不含占位符的他方 part 连对象引用都不动。"""
        foreign = self._part("别人的注入内容，不含占位符")
        parts = [foreign]
        changed, removed = service.clean_parts(parts)
        self.assertEqual((changed, removed), (0, 0))
        self.assertIs(parts[0], foreign)

    def test_list_object_identity_preserved(self):
        """原地修改：外部持有的同一个 list 引用不失效。"""
        parts = [self._part("[Empty Text]"), self._part("保留")]
        holder = parts
        service.clean_parts(parts)
        self.assertIs(holder, parts)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].text, "保留")

    def test_keep_action_noop(self):
        src = "[Image unavailable]"
        parts = [self._part(src)]
        changed, removed = service.clean_parts(parts, action="keep")
        self.assertEqual((changed, removed), (0, 0))
        self.assertEqual(parts[0].text, src)

    def test_non_list_input(self):
        self.assertEqual(service.clean_parts(None), (0, 0))
        self.assertEqual(service.clean_parts("x"), (0, 0))
        self.assertEqual(service.clean_parts([]), (0, 0))

    def test_part_without_text_untouched(self):
        obj = object()
        parts = [obj]
        changed, removed = service.clean_parts(parts)
        self.assertEqual((changed, removed), (0, 0))
        self.assertIs(parts[0], obj)


class TestQuoteFeaturePartsWiring(unittest.TestCase):
    """真机第六轮场景：单引用+@无正文 —— **prompt 为空也必须清洗内容块**。"""

    @staticmethod
    def _ctx(prompt, parts):
        from xbnext.config import Config
        from xbnext.context import RequestContext
        from conftest import FakeEvent, FakeReq, FakeTextPart

        event = FakeEvent(message=[])
        req = FakeReq(prompt=prompt, image_urls=[])
        for part in parts:
            req.extra_user_content_parts.append(part)
        return RequestContext(
            event=event, req=req, conf=Config({}), text_part_cls=FakeTextPart
        )

    def test_cleans_parts_when_prompt_empty(self):
        from conftest import FakeTextPart
        from xbnext.features.quote import QuoteFeature

        quote_part = FakeTextPart(
            text="<Quoted Message>\n(Light): [Empty Text]\n</Quoted Message>"
        )
        attach_part = FakeTextPart(
            text="[File Attachment in quoted message: name 报告.docx, path /tmp/报告.docx]"
        )
        ctx = self._ctx("", [quote_part, attach_part])
        QuoteFeature().on_llm_request(ctx)
        texts = [p.text for p in ctx.req.extra_user_content_parts]
        self.assertEqual(len(texts), 2)
        self.assertTrue(any("（此消息没有文字内容）" in t for t in texts))
        self.assertTrue(any("引用消息中的文件附件：报告.docx" in t for t in texts))
        self.assertFalse(
            any("[Empty Text]" in t or "path " in t for t in texts), texts
        )
        self.assertEqual(ctx.parts_cleaned, 2)

    def test_parts_counter_zero_when_nothing_to_clean(self):
        from conftest import FakeTextPart
        from xbnext.features.quote import QuoteFeature

        ctx = self._ctx("普通一句话", [FakeTextPart(text="别人的参考资料")])
        QuoteFeature().on_llm_request(ctx)
        self.assertEqual(ctx.parts_cleaned, 0)
        self.assertEqual(ctx.req.extra_user_content_parts[0].text, "别人的参考资料")

    def test_prompt_and_parts_both_cleaned(self):
        from conftest import FakeTextPart
        from xbnext.features.quote import QuoteFeature

        ctx = self._ctx(
            "引用：[Empty Text] 他说 [Image unavailable]",
            [FakeTextPart(text="[Image unavailable]")],
        )
        QuoteFeature().on_llm_request(ctx)
        self.assertNotIn("[Empty Text]", ctx.req.prompt)
        self.assertNotIn("[Image unavailable]", ctx.req.prompt)
        self.assertNotIn("[Image unavailable]", ctx.req.extra_user_content_parts[0].text)
        self.assertEqual(ctx.parts_cleaned, 1)


if __name__ == "__main__":
    unittest.main()
