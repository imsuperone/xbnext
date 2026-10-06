# -*- coding: utf-8 -*-
"""R2 · 引用占位清洗单测。"""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
