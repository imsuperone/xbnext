# -*- coding: utf-8 -*-
"""灵性主动交互服务（纯逻辑与平台通信层）。

包含：
1. 意图感知智能插话判定（摒弃死板随机骰子，根据求助提问意图、自定义关键词与好感度决定是否主动参与）；
2. 灵性主动提示词注入包装（引导模型以自然生动的群友口吻融入讨论，避免机械式回复）；
3. 定时冷场破冰冒泡生成；
4. 跨平台安全群消息投递。
"""

from __future__ import annotations

import asyncio
import datetime
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "should_proactively_reply",
    "build_proactive_prompt_hint",
    "pick_icebreaker",
    "send_group_message",
    "record_group_activity",
    "reset_cooldowns",
    "parse_custom_keywords",
]

#: 通用提问与求助疑问词库
QUESTION_MARKERS = (
    "怎么", "为什么", "如何", "求推荐", "求问", "有无", "有没有",
    "谁知道", "什么情况", "懂哥", "请问", "咋办", "怎么办", "谁懂",
    "有人在吗", "人呢", "哪位大佬", "求助", "帮看下", "有啥", "有什么",
    "能不能", "可以吗", "推荐个", "求个",
)

#: 通用情绪共鸣与社交时刻词库
EMOTION_MARKERS = (
    "好累", "不想上班", "加班", "emo", "破防", "救命", "笑死",
    "太难了", "好难受", "睡不着", "失眠", "好无聊", "无聊死了",
    "摸鱼", "太离谱了", "无语了", "气死我了", "心累", "呜呜呜",
)

#: 默认群级冷却时间（秒，防止短时间连续主动插话）
DEFAULT_COOLDOWN_SECONDS = 45.0

#: 内存冷却记录：group_id -> 上次主动触发时间戳
_GROUP_LAST_REPLY_TS: Dict[str, float] = {}

#: 内存群活跃记录：group_id -> 上次群消息时间戳
_GROUP_LAST_SEEN_TS: Dict[str, float] = {}

#: 内存破冰记录：group_id -> 上次破冰时间戳
_GROUP_LAST_ICEBREAK_TS: Dict[str, float] = {}

#: 内存动态破冰间隔：group_id -> 下次触发所需的冷场秒数
_GROUP_NEXT_ICEBREAK_DELAY: Dict[str, float] = {}


def reset_cooldowns() -> None:
    """清理内存冷却缓存（测试用）。"""
    _GROUP_LAST_REPLY_TS.clear()
    _GROUP_LAST_SEEN_TS.clear()
    _GROUP_LAST_ICEBREAK_TS.clear()
    _GROUP_NEXT_ICEBREAK_DELAY.clear()


def record_group_activity(group_id: str) -> None:
    """记录群活跃时间戳。"""
    gid = str(group_id or "").strip()
    if gid:
        _GROUP_LAST_SEEN_TS[gid] = time.time()


def parse_custom_keywords(raw: Any) -> List[str]:
    """解析自定义关注关键词列表。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    raw_str = str(raw).strip()
    if not raw_str:
        return []
    tokens = re.split(r"[,，;\s\n]+", raw_str)
    return [t.strip().lower() for t in tokens if t.strip()]


def should_proactively_reply(
    msg_str: str,
    sender_id: str,
    group_id: str,
    conf: Any = None,
    affection: Optional[int] = None,
    now_ts: Optional[float] = None,
) -> Tuple[bool, str]:
    """判定是否应当对当前群消息进行意图感知智能插话。

    返回:
        (should_reply: bool, reason: str)
    """
    text = str(msg_str or "").strip()
    if not text:
        return False, ""

    # 1. 过滤过短、过长或指令类消息
    if len(text) < 2 or len(text) > 200:
        return False, ""
    if text.startswith(("/", "!", "！", "、", "#", "$", "￥")):
        return False, ""
    # 纯数字、纯标点、纯英文字母缩写（如 111, ???, 666, ok）不插话
    cleaned = re.sub(r"[\s\d\W_]+", "", text)
    if not cleaned:
        return False, ""

    gid = str(group_id or "").strip()
    now = float(now_ts if now_ts is not None else time.time())

    # 2. 检查群插话冷却
    last_reply = _GROUP_LAST_REPLY_TS.get(gid, 0.0)
    cooldown = DEFAULT_COOLDOWN_SECONDS
    # 好感度加权：好感度高时插话冷却略微缩短
    if affection is not None and affection >= 60:
        cooldown = max(25.0, cooldown * 0.7)

    if now - last_reply < cooldown:
        return False, "群冷却中"

    lower_text = text.lower()
    matched_reason = ""

    # 3. 读取配置项（自定义关键词、意图识别开关与回复概率）
    custom_kws_raw = ""
    enable_intent = True
    reply_chance = 80
    if conf is not None:
        if hasattr(conf, "text"):
            custom_kws_raw = conf.text("proactive_keywords", "")
        if hasattr(conf, "bool"):
            enable_intent = conf.bool("proactive_intent_question", True)
        if hasattr(conf, "int"):
            reply_chance = conf.int("proactive_reply_chance", 80)
        elif hasattr(conf, "get"):
            custom_kws_raw = str(conf.get("proactive_keywords") or "")
            enable_intent = bool(conf.get("proactive_intent_question", True))
            reply_chance = int(conf.get("proactive_reply_chance", 80))

    # 自定义关键词优先匹配
    custom_kws = parse_custom_keywords(custom_kws_raw)
    for kw in custom_kws:
        if kw in lower_text:
            matched_reason = f"命中关注关键词: {kw}"
            break

    # 4. 通用社交意图感知（提问/求助/互动/情绪共鸣）
    if not matched_reason and enable_intent:
        has_q_mark = ("?" in text or "？" in text or text.endswith(("吗", "呢", "吧")))
        for marker in QUESTION_MARKERS:
            if marker in lower_text:
                if has_q_mark or len(marker) >= 3 or ("求" in marker):
                    matched_reason = f"感知到求助提问 (关键词: {marker})"
                    break
        if not matched_reason:
            for marker in EMOTION_MARKERS:
                if marker in lower_text:
                    matched_reason = f"感知到社交共鸣 (共鸣词: {marker})"
                    break

    # 5. 好感度加持感知
    if not matched_reason:
        if affection is not None and affection >= 80 and ("?" in text or "？" in text):
            matched_reason = f"高好感度群友提问 (好感度: {affection})"
        else:
            return False, ""

    # 6. 插话概率判定
    if reply_chance < 100:
        chance = max(10, min(100, reply_chance))
        if random.randint(1, 100) > chance:
            return False, f"未通过插话概率判定({chance}%)"

    # 命中插话，更新冷却
    if gid:
        _GROUP_LAST_REPLY_TS[gid] = now
    return True, matched_reason


def build_proactive_prompt_hint(reason: str, custom_hint: str = "") -> str:
    """生成注入 LLM 的灵性主动插话提示词。

    指导模型以自然、生动、符合人设的口吻融入群聊，绝不输出生硬的客服腔。
    """
    clean_reason = str(reason or "").strip()
    if custom_hint and str(custom_hint).strip():
        base = str(custom_hint).strip()
        if "{reason}" in base:
            base = base.replace("{reason}", clean_reason)
        return f"<system_reminder>{base}</system_reminder>"

    return (
        "<system_reminder>"
        "【灵性主动插话机制】当前群聊中群友并未直接@你，但刚才的发言触发了你的关注点"
        f"（触发原因：{clean_reason}）。"
        "请根据你的角色人设，以自然、生动、幽默或温暖的语气自然参与该话题的互动，"
        "不要回复机械生硬的『请问你找我有什么事』或『我是AI』，像一名真实生动的群友一样自然插话！"
        "</system_reminder>"
    )


#: 破冰冒泡文案库（按时间段精选通用生动台词）
ICEBREAKER_LINES_MORNING_NOON = [
    "（探头）中午好呀~ 大家今天午饭都吃了什么好吃的？求推荐！",
    "太阳晒屁股啦！群里怎么这么安静，都在忙什么呢~",
    "出来冒个泡~ 大家今天状态怎么样？",
    "中午好！有人在摸鱼水群吗，快出来聊五毛钱的~",
]

ICEBREAKER_LINES_AFTERNOON = [
    "下午茶时间到！群里都在摸鱼还是在忙呀？出来聊两句~",
    "（伸懒腰）好安静，有没有人来水水群？有在打游戏的吗~",
    "突然有点犯困，大家下午都在干嘛呢？",
    "在工位上发呆的快举手！出来聊两句提提神~",
]

ICEBREAKER_LINES_EVENING_NIGHT = [
    "晚上好呀！今天大家都过得怎么样？快来吐槽分享一下~",
    "夜深了~ 还有没有夜猫子没睡的？出来冒个泡呗！",
    "忙碌的一天结束啦，群里今晚怎么这么安静呀？",
    "夜猫子交流大会开始~ 谁还没睡的快出来打个卡！",
]


def pick_icebreaker(group_id: str, runtime: Any = None) -> str:
    """为冷场的群挑选一条生动有趣的破冰文案。"""
    now_hour = datetime.datetime.now().hour
    if 10 <= now_hour < 14:
        return random.choice(ICEBREAKER_LINES_MORNING_NOON)
    elif 14 <= now_hour < 18:
        return random.choice(ICEBREAKER_LINES_AFTERNOON)
    else:
        return random.choice(ICEBREAKER_LINES_EVENING_NIGHT)


async def send_group_message(
    context: Any,
    group_id: str,
    text: str,
) -> bool:
    """通过 AstrBot 核心投递主动群消息（纯文本）。"""
    gid = str(group_id or "").strip()
    msg = str(text or "").strip()
    if not gid or not msg or context is None:
        return False

    # 1. 尝试 context.send_message
    try:
        fn = getattr(context, "send_message", None)
        if callable(fn):
            res = fn(gid, msg)
            if asyncio.iscoroutine(res):
                await res
            return True
    except Exception:  # noqa: BLE001
        pass

    # 2. 尝试 context.get_platform_adapter 或 send_to_group
    for method_name in ("send_to_group", "send_group_msg"):
        fn = getattr(context, method_name, None)
        if callable(fn):
            try:
                res = fn(gid, msg)
                if asyncio.iscoroutine(res):
                    await res
                return True
            except Exception:  # noqa: BLE001
                pass

    return False
