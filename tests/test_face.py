# -*- coding: utf-8 -*-
"""R3 · 表情翻译单测。"""

from __future__ import annotations

import unittest

from conftest import _ROOT  # noqa: F401  触发 sys.path 注入

from xbnext.features.face import data, service


class TestFaceData(unittest.TestCase):
    def test_known_faces(self):
        self.assertEqual(data.face_name(0), "微笑")
        self.assertEqual(data.face_name(4), "得意")
        self.assertEqual(data.face_name("14"), "偷笑")  # 字符串 ID 也能查

    def test_unknown_face(self):
        self.assertIsNone(data.face_name(99999))
        self.assertIsNone(data.face_name("not-a-number"))
        self.assertIsNone(data.face_name(None))

    def test_mface_unknown_returns_none(self):
        """查不到必须返回 None，由调用方回退 —— 绝不能瞎猜语义。"""
        self.assertIsNone(data.mface_name("270_abc"))
        self.assertIsNone(data.mface_name(""))
        self.assertIsNone(data.mface_name(None))


class TestTranslate(unittest.TestCase):
    def test_known_face(self):
        self.assertEqual(service.translate_token("face", 4), "[表情:得意]")

    def test_unknown_face_falls_back_to_id(self):
        self.assertEqual(service.translate_token("face", 99999), "[表情:ID99999]")

    def test_mface_falls_back_to_key(self):
        self.assertEqual(service.translate_token("mface", "270_x"), "[表情:key270_x]")

    def test_unknown_kind(self):
        self.assertEqual(service.translate_token("whatever", 1), "[表情:?]")

    def test_custom_format(self):
        out = service.translate_token("face", 4, fmt="{name}")
        self.assertEqual(out, "得意")

    def test_bad_format_falls_back(self):
        self.assertEqual(service.translate_token("face", 4, fmt=""), "[表情:得意]")
        self.assertEqual(service.translate_token("face", 4, fmt="没有占位符"), "[表情:得意]")


class TestTranslateAll(unittest.TestCase):
    def test_mixed_tokens(self):
        out = service.translate_all([("face", 4), ("mface", "k1"), ("face", 99)])
        self.assertEqual(out, ["[表情:得意]", "[表情:keyk1]", "[表情:ID99]"])

    def test_dedupe(self):
        out = service.translate_all([("face", 4), ("face", 4), ("face", 5)])
        self.assertEqual(out, ["[表情:得意]", "[表情:流泪]"])

    def test_no_dedupe(self):
        out = service.translate_all([("face", 4), ("face", 4)], dedupe=False)
        self.assertEqual(out, ["[表情:得意]", "[表情:得意]"])

    def test_single_element_tuple(self):
        self.assertEqual(service.translate_all([(4,)]), ["[表情:得意]"])

    def test_broken_items_skipped(self):
        out = service.translate_all([(), ("face", 4)])
        self.assertEqual(out, ["[表情:得意]"])

    def test_empty(self):
        self.assertEqual(service.translate_all([]), [])
        self.assertEqual(service.translate_all(None), [])


class TestBuildNote(unittest.TestCase):
    def test_wrapped_for_injection(self):
        note = service.build_note([("face", 4), ("face", 5)])
        self.assertTrue(note.startswith("（本轮消息里出现的表情："))
        self.assertIn("[表情:得意]", note)
        self.assertIn("[表情:流泪]", note)
        self.assertTrue(note.endswith("）"))

    def test_empty_tokens(self):
        self.assertEqual(service.build_note([]), "")
        self.assertEqual(service.build_note(None), "")

    def test_every_name_placeholder_replaced(self):
        """格式串里出现几次 {name} 就替换几次（配置由管理员控制）。"""
        note = service.build_note([("face", 4)], fmt="<{name}|{name}>")
        self.assertNotIn("{name}", note)
        self.assertIn("<得意|得意>", note)


class TestQuoteFeatureWiring(unittest.TestCase):
    """功能实例与配置键接线正确。"""

    def test_feature_meta(self):
        from xbnext.features import get_feature

        feat = get_feature("enable_face_translate")
        self.assertIsNotNone(feat)
        self.assertEqual(feat.key, "enable_face_translate")
        self.assertGreater(feat.order, 0)
        self.assertTrue(feat.description)


if __name__ == "__main__":
    unittest.main()
