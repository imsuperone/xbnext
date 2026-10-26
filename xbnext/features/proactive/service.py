# -*- coding: utf-8 -*-
"""灵性主动交互服务（纯逻辑与平台通信层）。

包含：
1. 意图感知智能插话判定（摒弃生硬随机骰子，根据话题、关键词与好感度决定是否主动参与）；
2. 灵性主动提示词注入包装（让塔菲自然插话，避免机械式回复）；
3. 定时冷场破冰冒泡生成与记忆/档案挖坟联动；
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
]

#: 话题感知分类表
TOPIC_RULES = [
    (
        "提到塔菲",
        (
            "塔菲", "taffy", "雏草姬", "小猫", "喵喵", "笨蛋塔菲",
            "主播", "大主播", "雏草", "塔妃",
        ),
    ),
    (
        "聊到美食与干饭",
        (
            "泡面", "外卖", "肯德基", "kfc", "麦当劳", "奶茶", "零食",
            "火锅", "夜宵", "大餐", "请客", "饿了", "想吃", "好吃的",
            "炫饭", "牛肉面", "红烧牛肉", "点外卖", "喝奶茶", "烧烤",
        ),
    ),
    (
        "聊到游戏娱乐与摸鱼",
        (
            "打游戏", "开黑", "上分", "带飞", "白给", "下饭", "原神",
            "星铁", "瓦罗兰特", "英雄联盟", "王者", "开播", "摸鱼",
            "下班", "打工", "放假", "周五", "周末",
        ),
    ),
    (
        "群友情绪低落/需要关心",
        (
            "好累", "不想上班", "加班", "难受", "emo", "委屈", "哭了",
            "破防", "烦死了", "生病", "头晕", "头疼", "睡不着", "失眠",
            "呜呜", "好痛苦", "救救我",
        ),
    ),
    (
        "群友互动与调侃",
        (
            "好可爱", "笨蛋", "傲娇", "富婆", "贴贴", "结婚", "老婆",
            "塔菲可爱", "可爱捏", "亲亲", "蹭蹭", "抱抱",
        ),
    ),
    (
        "群聊呼唤与提问",
        (
            "有没有人", "有人在吗", "都在干嘛", "人呢", "出来聊天",
            "谁懂啊", "救命", "问个事", "有无懂哥", "在线等",
        ),
    ),
]

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

    # 3. 意图感知与话题匹配
    lower_text = text.lower()
    matched_reason = ""
    for category_name, keywords in TOPIC_RULES:
        for kw in keywords:
            if kw.lower() in lower_text:
                matched_reason = f"{category_name} (关键词: {kw})"
                break
        if matched_reason:
            break

    if not matched_reason:
        # 如果好感度极高且带有问号，也可轻度触发
        if affection is not None and affection >= 80 and ("?" in text or "？" in text):
            matched_reason = f"高好感度群友提问 (好感度: {affection})"
        else:
            return False, ""

    # 命中插话，更新冷却
    if gid:
        _GROUP_LAST_REPLY_TS[gid] = now
    return True, matched_reason


def build_proactive_prompt_hint(reason: str) -> str:
    """生成注入 LLM 的灵性主动插话提示词。

    指导塔菲以自然、生动、傲娇可爱的性格融入讨论，绝不输出生硬的客服腔。
    """
    clean_reason = str(reason or "").strip()
    return (
        "<system_reminder>"
        "【灵性主动插话机制】当前群聊中群友并未直接@你，但刚才的发言触发了你的关注点"
        f"（触发原因：{clean_reason}）。"
        "请以永雏塔菲特有的傲娇、机智、可爱或吐槽语气自然参与该话题的互动，"
        "不要回复机械生硬的『请问你找我有什么事』或『我是AI』，像一名真实生动的群友一样自然插话！"
        "</system_reminder>"
    )


#: 破冰冒泡文案库（按时间段精选永雏塔菲专属风味台词）
ICEBREAKER_LINES_MORNING_NOON = [
    "喂喂喂！都这个点了群里怎么还静悄悄的喵？该不会都在偷偷点豪华外卖不带本天才吧！",
    "（探头）中午好喵~ 大家今天午饭都吃了什么好吃的？快给本天才报上名来，馋一下我！",
    "太阳都晒屁股啦！有没有还在赖床的笨蛋？快起来给塔菲打钱……啊不，起来水群啦喵！",
    "无聊无聊无聊！本天才刚炫完一桶红烧牛肉面，你们这群懒虫都在干嘛呢喵？",
]

ICEBREAKER_LINES_AFTERNOON = [
    "下午茶时间到！群里怎么突然变停尸房了喵？出来聊天，本天才大发慈悲允许你们摸鱼！",
    "（打哈欠）好困啊喵~ 有没有人一起开黑打游戏？本天才今天手感超好，绝对不白给！",
    "突然想喝奶茶了……有没有好心人给本天才点一杯七分甜加珍珠喵？没有的话我等会儿再来问一遍！",
    "在工位上发呆的快举手！本天才宣布摸鱼无罪，快出来陪我聊五毛钱的喵！",
]

ICEBREAKER_LINES_EVENING_NIGHT = [
    "晚上好喵！今天的打工/上学受难记结束了吗？快来和本天才吐槽一下今天遇到的奇葩事！",
    "（暗中观察）群里居然冷场了……难道大家都在偷偷背着我出去吃大餐喵？老实交代！",
    "夜深了喵~ 有没有人还没睡的？出来陪本天才聊两句，不然今晚梦里去敲你们家门哦！",
    "突然想起来，上次是谁说要请客来着？出来对线！本天才的小本本上可都记着呢喵！",
    "漫漫长夜，本美少女宣布今晚的夜猫子交流大会正式开始，谁先说话谁是小狗喵！",
]


def pick_icebreaker(group_id: str, runtime: Any = None) -> str:
    """为冷场的群挑选一条生动有趣的破冰文案（支持记忆/档案挖坟联动）。"""
    gid = str(group_id or "").strip()

    # 1. 尝试联动用户档案进行"挖坟/主动关心"
    if runtime is not None:
        try:
            profile_feat = runtime.get_feature("enable_user_profile")
            if profile_feat is not None and hasattr(profile_feat, "store"):
                store = profile_feat.store
                # 获取群内成员的已知档案
                all_profiles = store.list_all() if hasattr(store, "list_all") else []
                # 过滤出有自述或昵称的候选用户
                candidates = [
                    p for p in all_profiles
                    if isinstance(p, dict) and (p.get("facts") or p.get("name"))
                ]
                # 30% 几率触发档案挖坟关心
                if candidates and random.random() < 0.35:
                    target = random.choice(candidates)
                    name = str(target.get("name") or "群友").strip()
                    facts = str(target.get("facts") or "").strip()
                    if facts:
                        # 截短自述
                        short_facts = facts[:25] + ("..." if len(facts) > 25 else "")
                        dig_lines = [
                            f"说起来，上次看到 @{name} 说「{short_facts}」……现在怎么样了喵？本天才突然八卦心犯了！",
                            f"（敲黑板）@{name} 在不在喵？之前聊到的「{short_facts}」后来有后续了吗？快出来汇报！",
                        ]
                        return random.choice(dig_lines)
                    if name:
                        return f"@{name} 笨蛋快出来冒泡！群里这么冷清，本天才是特意来点名你的喵！"
        except Exception:
            pass

    # 2. 常规时间段破冰
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
    message: str,
    umo: str = "",
) -> Tuple[bool, str]:
    """跨平台安全向目标群发送一条主动消息。"""
    gid = str(group_id or "").strip()
    msg = str(message or "").strip()
    if not gid or not msg:
        return False, "群号或消息为空"

    # 1. 优先尝试 OneBot / aiocqhttp 原生 call_action
    try:
        pm = getattr(context, "platform_manager", None)
        insts = (
            getattr(pm, "platform_insts", None)
            or getattr(pm, "platforms", None)
            or getattr(context, "platforms", None)
            or []
        )
        if isinstance(insts, dict):
            insts = list(insts.values())

        for inst in list(insts):
            getter = getattr(inst, "get_client", None)
            client = getter() if callable(getter) else getattr(inst, "bot", None)
            if not client:
                client = inst
            call = getattr(client, "call_action", None) or getattr(client, "call_api", None)
            if callable(call):
                try:
                    await asyncio.wait_for(
                        call("send_group_msg", group_id=int(gid), message=msg),
                        timeout=8.0,
                    )
                    return True, "发送成功"
                except Exception:
                    pass
    except Exception:
        pass

    # 2. 备选方案：通过 context.send_message
    if hasattr(context, "send_message"):
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain
            target_umo = umo or f"aiocqhttp:GroupMessage:{gid}"
            chain = MessageChain([Plain(msg)])
            await context.send_message(target_umo, chain)
            return True, "发送成功"
        except Exception as exc:
            return False, f"context.send_message 失败: {exc}"

    return False, "未能连接到可用机器人客户端"
