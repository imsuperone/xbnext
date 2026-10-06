# -*- coding: utf-8 -*-
"""R3 · QQ 表情数据表。

**现状**：aiocqhttp 适配器把 ``face`` 段排除在 ``message_str`` 之外、
``mface`` 直接 ``continue`` 丢弃，而 ``req.prompt = event.message_str`` ——
所以模型一条表情都收不到。

下表是**起步样例**，仅覆盖最常用的前 15 号标准表情，用来打通链路与测试。
**P2 阶段必须补全权威全量表**（QQ 标准表情 0~146 + mface 商城表情 key），
补全方式见 ``aidoc/02-架构设计.md``。

> ⚠️ 这里宁可少列也不要凭记忆编造 ID → 名称的对应关系，
> 编错比缺更糟：模型会把"流泪"理解成"微笑"。
"""

from __future__ import annotations

from typing import Optional

#: QQ 标准表情 ID → 中文名（**样例，待 P2 补全**）
QQ_FACE = {
    0: "微笑",
    1: "撇嘴",
    2: "色",
    3: "发呆",
    4: "得意",
    5: "流泪",
    6: "害羞",
    7: "闭嘴",
    8: "睡",
    9: "大哭",
    10: "尴尬",
    11: "发怒",
    12: "调皮",
    13: "呲牙",
    14: "偷笑",
}

#: 常见 QQ 小黄脸之外的补充别名（P2 再扩）
QQ_FACE_ALIAS = {
    "得意": "4",
}


def face_name(code: object) -> Optional[str]:
    """按 ID 查表情中文名；查不到返回 ``None``。"""
    try:
        return QQ_FACE.get(int(code))
    except Exception:  # noqa: BLE001
        return None


def mface_name(key: str) -> Optional[str]:
    """按 mface（商城表情）key 查名称。

    mface 的 key 形如 ``270_xxx`` 或纯数字串，仓库暂无权威表，
    **P2 补全**；查不到必须返回 ``None``，由调用方回退成原 key。
    """
    if not isinstance(key, str) or not key:
        return None
    # TODO(P2): 接入 mface key → 表情名权威表
    return None


__all__ = ["QQ_FACE", "QQ_FACE_ALIAS", "face_name", "mface_name"]
