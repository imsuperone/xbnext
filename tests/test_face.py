# -*- coding: utf-8 -*-
"""R3 · 表情翻译单测（数据表 + 纯逻辑 + 消息链抽取 + 注入链路）。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from conftest import FakeEvent, FakeReq, FakeTextPart, _ROOT  # noqa: F401

from xbnext.config import Config
from xbnext.context import RequestContext
from xbnext.features.face import FaceFeature, data, service


# -- 假消息段（类名即类型名，与 astrbot / OneBot 对齐） ----------------------
class Face:
    def __init__(self, id=None):
        self.id = id


class Mface:
    def __init__(self, summary=None, emoji_id=None, key=None):
        if summary is not None:
            self.summary = summary
        if emoji_id is not None:
            self.emoji_id = emoji_id
        if key is not None:
            self.key = key


class Rps:
    def __init__(self, result=None):
        self.result = result


def make_ctx(message=None, prompt="", raw=None, conf=None):
    """造一个跑 ``on_llm_request`` 所需的最小上下文。"""
    event = FakeEvent(message=message or [])
    if raw is not None:
        event.message_obj = SimpleNamespace(raw_message=raw)
    req = FakeReq(prompt=prompt)
    ctx = RequestContext(
        event=event,
        req=req,
        conf=conf or Config({}),
        text_part_cls=FakeTextPart,
    )
    return ctx


class TestFaceData(unittest.TestCase):
    """权威表：0 号是"惊讶"不是"微笑"（旧样例表是错的，此处锁死）。"""

    def test_known_faces(self):
        self.assertEqual(data.face_name(0), "惊讶")
        self.assertEqual(data.face_name(4), "得意")
        self.assertEqual(data.face_name(14), "微笑")
        self.assertEqual(data.face_name("14"), "微笑")  # 字符串 ID 也能查

    def test_extended_faces(self):
        self.assertEqual(data.face_name(271), "吃瓜")
        self.assertEqual(data.face_name(299), "牛啊")
        self.assertEqual(data.face_name(128513), "呲牙")  # unicode 码点段

    def test_table_is_full_not_a_sample(self):
        """全量表：不再只是 0-14 样例。"""
        self.assertGreaterEqual(len(data.QQ_FACE_ALL), 300)
        self.assertGreaterEqual(len(data.QQ_FACE), 180)
        self.assertGreaterEqual(len(data.QQ_FACE_EXT), 60)

    def test_conflicts_recorded(self):
        """两源冲突必须落在 CONFLICTS 里且以源 A 定稿，方便回溯。"""
        self.assertEqual(
            data.CONFLICTS,
            [(109, "亲亲", "左亲亲"), (181, "骚扰", "戳一戳")],
        )
        self.assertEqual(data.face_name(109), "亲亲")
        self.assertEqual(data.face_name(181), "骚扰")

    def test_unknown_face(self):
        self.assertIsNone(data.face_name(99999))
        self.assertIsNone(data.face_name("not-a-number"))
        self.assertIsNone(data.face_name(None))

    def test_mface_unknown_returns_none(self):
        """商城表情**没有权威 key 表**，必须返回 None 由调用方兜底。"""
        self.assertIsNone(data.mface_name("270_abc"))
        self.assertIsNone(data.mface_name(""))
        self.assertIsNone(data.mface_name(None))

    def test_reverse_lookup(self):
        self.assertEqual(data.face_id("微笑"), 14)
        self.assertEqual(data.face_id("[得意]"), 4)
        self.assertIsNone(data.face_id(None))
        self.assertIsNone(data.face_id("不是表情"))


class TestCleanSummary(unittest.TestCase):
    def test_strips_decoration(self):
        self.assertEqual(service.clean_summary("得意"), "得意")
        self.assertEqual(service.clean_summary("[得意]"), "得意")
        self.assertEqual(service.clean_summary("表情：得意"), "得意")
        self.assertEqual(service.clean_summary("[表情:得意]"), "得意")
        self.assertEqual(service.clean_summary("【哈欠】"), "哈欠")

    def test_rejects_non_names(self):
        self.assertEqual(service.clean_summary(None), "")
        self.assertEqual(service.clean_summary(123), "")
        self.assertEqual(service.clean_summary(""), "")
        self.assertEqual(service.clean_summary("   "), "")
        # 超长不是表情名（防 summary 被塞成一整句话）
        self.assertEqual(service.clean_summary("啊" * (service.MAX_NAME_LEN + 1)), "")


class TestTranslate(unittest.TestCase):
    def test_known_face(self):
        self.assertEqual(service.translate_token("face", 4), "[表情:得意]")

    def test_unknown_face_falls_back_to_id(self):
        self.assertEqual(service.translate_token("face", 99999), "[表情:ID99999]")

    def test_mface_falls_back_to_key(self):
        self.assertEqual(service.translate_token("mface", "270_x"), "[表情:key270_x]")

    def test_summary_uses_segment_name(self):
        self.assertEqual(service.translate_token("summary", "吃瓜"), "[表情:吃瓜]")
        self.assertEqual(
            service.translate_token("summary", "[表情:笑哭]"), "[表情:笑哭]"
        )

    def test_summary_bad_name_falls_back(self):
        self.assertEqual(service.translate_token("summary", ""), "[表情:?]")
        self.assertEqual(service.translate_token("summary", None), "[表情:?]")

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
        out = service.translate_all([("face", 4), ("mface", "k1"), ("face", 99999)])
        self.assertEqual(out, ["[表情:得意]", "[表情:keyk1]", "[表情:ID99999]"])

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

    def test_capped(self):
        """刷一屏表情也不能撑爆请求。"""
        out = service.translate_all([("face", i) for i in range(500)])
        self.assertEqual(len(out), service.MAX_PARTS)


class TestNote(unittest.TestCase):
    def test_wrapped_for_injection(self):
        note = service.build_note([("face", 4), ("face", 5)])
        self.assertTrue(note.startswith(service.NOTE_PREFIX))
        self.assertIn("[表情:得意]", note)
        self.assertIn("[表情:流泪]", note)
        self.assertTrue(note.endswith(service.NOTE_SUFFIX))
        self.assertIn(service.NOTE_MID, note)

    def test_empty_tokens(self):
        self.assertEqual(service.build_note([]), "")
        self.assertEqual(service.build_note(None), "")
        self.assertEqual(service.note_from_parts([]), "")
        self.assertEqual(service.note_from_parts(None), "")

    def test_every_name_placeholder_replaced(self):
        """格式串里出现几次 {name} 就替换几次（配置由管理员控制）。"""
        note = service.build_note([("face", 4)], fmt="<{name}|{name}>")
        self.assertNotIn("{name}", note)
        self.assertIn("<得意|得意>", note)


class TestTokensFromRaw(unittest.TestCase):
    def test_onebot_json_list(self):
        raw = [
            {"type": "text", "data": {"text": "hi"}},
            {"type": "face", "data": {"id": "4"}},
            {
                "type": "mface",
                "data": {"emoji_id": "e1", "key": "k", "summary": "笑哭"},
            },
        ]
        self.assertEqual(
            service.tokens_from_raw(raw),
            [("face", "4"), ("summary", "笑哭")],
        )

    def test_single_dict(self):
        self.assertEqual(
            service.tokens_from_raw({"type": "face", "data": {"id": 9}}),
            [("face", 9)],
        )

    def test_json_string(self):
        raw = '[{"type":"face","data":{"id":"13"}}]'
        self.assertEqual(service.tokens_from_raw(raw), [("face", "13")])

    def test_cq_string(self):
        self.assertEqual(
            service.tokens_from_raw("[CQ:mface,emoji_id=e1,key=abc,summary=得意]"),
            [("summary", "得意")],
        )
        # 没有 summary 就退回 emoji_id（绝不静默丢弃）
        self.assertEqual(
            service.tokens_from_raw("[CQ:mface,emoji_id=e7,key=kk]"),
            [("mface", "e7")],
        )
        self.assertEqual(
            service.tokens_from_raw("[CQ:face,id=13]"),
            [("face", "13")],
        )

    def test_wrapped_message_field(self):
        raw = {"message": [{"type": "face", "data": {"id": 7}}]}
        self.assertEqual(service.tokens_from_raw(raw), [("face", 7)])

    def test_rps_and_dice(self):
        self.assertEqual(
            service.tokens_from_raw([{"type": "dice", "data": {"result": "3"}}]),
            [("summary", "dice 3")],
        )

    def test_garbage(self):
        self.assertEqual(service.tokens_from_raw(None), [])
        self.assertEqual(service.tokens_from_raw("hello"), [])
        self.assertEqual(service.tokens_from_raw("[不是CQ码]"), [])
        self.assertEqual(service.tokens_from_raw({"type": "text"}), [])
        self.assertEqual(service.tokens_from_raw(12345), [])


class TestChainWalk(unittest.TestCase):
    def test_objects(self):
        tokens = FaceFeature._walk_chain([Face(4), Mface(summary="哈欠"), Rps(2)])
        self.assertEqual(tokens, [("face", 4), ("summary", "哈欠"), ("summary", "rps 2")])

    def test_mface_without_summary_uses_key(self):
        tokens = FaceFeature._walk_chain([Mface(emoji_id="e9")])
        self.assertEqual(tokens, [("mface", "e9")])

    def test_dict_segments(self):
        chain = [
            {"type": "text", "data": {"text": "看这个"}},
            {"type": "face", "data": {"id": "271"}},
        ]
        self.assertEqual(FaceFeature._walk_chain(chain), [("face", "271")])

    def test_empty_and_none(self):
        self.assertEqual(FaceFeature._walk_chain(None), [])
        self.assertEqual(FaceFeature._walk_chain([]), [])
        self.assertEqual(FaceFeature._walk_chain([object()]), [])


class TestOnLlmRequest(unittest.TestCase):
    def test_injects_temp_part(self):
        ctx = make_ctx(message=[Face(4), Face(5)])
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 1)
        part = ctx.req.extra_user_content_parts[0]
        self.assertTrue(part.temp)
        self.assertIn("[表情:得意]", part.text)
        self.assertIn("[表情:流泪]", part.text)

    def test_no_message_no_injection(self):
        ctx = make_ctx(message=[])
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 0)

    def test_skips_fragments_already_in_prompt(self):
        """正文里已经写出来的片段不重复注入。"""
        ctx = make_ctx(message=[Face(4)], prompt="我发了 [表情:得意] 你看懂没")
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 0)

    def test_pulls_mface_from_raw_message(self):
        """mface 不进消息链，只能从 OneBot 原始载荷捞回来。"""
        ctx = make_ctx(
            message=[],
            raw='[CQ:mface,emoji_id=e1,key=k,summary=吃瓜]',
        )
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 1)
        self.assertIn("[表情:吃瓜]", ctx.req.extra_user_content_parts[0].text)

    def test_dedupes_chain_and_raw(self):
        """同一条消息链 + 原始载荷都带同一个表情，只注入一次。"""
        ctx = make_ctx(
            message=[Face(4)],
            raw='[{"type":"face","data":{"id":"4"}}]',
        )
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 1)
        note = ctx.req.extra_user_content_parts[0].text
        self.assertEqual(note.count("[表情:得意]"), 1)

    def test_custom_format_from_conf(self):
        conf = Config({"face_format": "{name}"})
        ctx = make_ctx(message=[Face(4)], conf=conf)
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 1)
        self.assertIn("得意", ctx.req.extra_user_content_parts[0].text)
        self.assertNotIn("[表情:得意]", ctx.req.extra_user_content_parts[0].text)

    def test_unknown_id_still_injected(self):
        """未收录 ID 必须兜底成 ID 形式，绝不静默丢弃。"""
        ctx = make_ctx(message=[Face(999999)])
        FaceFeature().on_llm_request(ctx)
        self.assertEqual(ctx.injected, 1)
        self.assertIn("[表情:ID999999]", ctx.req.extra_user_content_parts[0].text)


class TestFeatureWiring(unittest.TestCase):
    """功能实例与配置键接线正确。"""

    def test_feature_meta(self):
        from xbnext.features import FEATURES, get_feature

        feat = get_feature("enable_face_translate")
        self.assertIsNotNone(feat)
        self.assertEqual(feat.key, "enable_face_translate")
        self.assertEqual(feat.order, 30)
        self.assertTrue(feat.description)
        self.assertIn(feat, FEATURES)


if __name__ == "__main__":
    unittest.main()
