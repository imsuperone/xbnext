# -*- coding: utf-8 -*-
"""全语言谐音梗与跨语种双关认知引擎（Wordplay Feature）。

解决痛点：LLM 基于 subword token 运作，天然缺乏声学拼读意识，面对中英混合谐音（duck不必、
深藏blue、more名其妙）、中文拼音近音双关、日文空耳/冷笑话（ダジャレ）以及欧美多语言 puns 时，
极易误从字面死板理解，甚至像老学究一样去机械解释梗的来源。

本功能向 LLM 注入高效的双轨声学认知引导，让永雏塔菲秒懂梗意，并以特有的傲娇、机智与俏皮
语气犀利吐槽或用更刁钻的谐音梗反击回去！
"""

from __future__ import annotations

from typing import Any

from ..base import Feature

WORDPLAY_HINT = (
    "<system_reminder>\n"
    "【全语言谐音梗与跨语种双关感知引擎】\n"
    "1. 声学拼读双轨思维：当群友发言中出现看似生硬、不合字面逻辑、夹杂外语词汇或疑似问答急转弯时，"
    "严禁仅从字面义理解！必须在后台启动声学拼读解码器，进行多语言发音与近音比对：\n"
    "   - 中文谐音：拼音同音、近音、方言口音、字音借代（如：脚滑=狡猾，去相馆=去象馆，公鸡打鸣=工期大名）；\n"
    "   - 中英混杂双关：英文发音与中文音译（如：duck不必=大可不必，深藏blue=深藏不露，more名其妙=莫名其妙，star腾=瞎折腾，tony带水=拖泥带水，无可t-bag=无可替代，生无可coke=生无可恋，无fuck说=无话可说，book思议=不可思议）；\n"
    "   - 日语/小语种空耳与双关：日语音读训读谐音（ダジャレ / Dajare，如 アルミ缶の上にあるみかん）、经典空耳；\n"
    "   - 英语/外语双关语（Puns & Homophones）：同音多义词双关（如 knight/night, bear/bare, sea/see, wait/weight, break a leg/cast 等）。\n"
    "2. 互动规范：\n"
    "   - 严禁像一本正经的教科书 AI 那样去拆解解释梗的来历（如『这句话是谐音梗，出自……』，这种死板发言极其破坏气氛！）；\n"
    "   - 保持当前角色性格与语调，自然接梗、幽默吐槽或机智反击；亦可顺水推舟用更绝妙刁钻的谐音梗怼回去！\n"
    "</system_reminder>"
)


class WordplayFeature(Feature):
    """全语言谐音梗与跨语种双关认知引擎。"""

    key = "enable_wordplay"
    name = "全语言谐音梗与双关"
    description = "赋予模型对中文、中英混合、日语空耳及世界多语言谐音梗的声学秒懂感知能力"
    order = 55

    async def on_llm_request(self, ctx: Any) -> None:
        if not ctx.enabled(self.key):
            return
        ctx.inject(WORDPLAY_HINT)
        ctx.note("谐音梗与多语言双关认知已注入")


__all__ = ["WordplayFeature", "WORDPLAY_HINT"]
