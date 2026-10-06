# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗单测。"""

from __future__ import annotations

import os
import unittest

from conftest import FakeReq  # noqa: F401  触发 sys.path 注入

from xbnext.features.quote import service


class TestFindPlaceholders(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(
            service.find_placeholders(""), {"noise": [], "degraded": [], "bare_image": []}
        )
        self.assertEqual(
            service.find_placeholders(None), {"noise": [], "degraded": [], "bare_image": []}
        )
        self.assertEqual(
            service.find_placeholders(123), {"noise": [], "degraded": [], "bare_image": []}
        )

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


if __name__ == "__main__":
    unittest.main()
