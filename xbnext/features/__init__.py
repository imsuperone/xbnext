# -*- coding: utf-8 -*-
"""功能注册表 —— **新增功能只需要改这一个文件**。

``FEATURES`` 的书写顺序就是执行顺序（runtime 还会按 ``order`` 再排一次，
两处不一致时以 ``order`` 为准，``order`` 相同则按书写顺序）。

当前功能与需求的对应（详见 ``aidoc/01-需求与根因.md``；
顺序即 ``order``，``runtime`` 还会再排一次，两处不一致以 ``order`` 为准）：

===== ===================== ===== ==========================================
序号   功能                  order 需求
===== ===================== ===== ==========================================
R8     image_slim            15    历史旧图每轮重发，输入 token 浪费
R2     quote_clean           20    引用占位误判成空白图片
R3     face_translate        30    QQ 自带表情模型读不懂
R1     reply_attribution     40    把 A 的话套到 B 头上
R4     user_profile          50    用户自定义档案不被读取
R6     recall                90    撤回后请求照飞、回复不跟着撤
R9     tokenline             95    看不见本轮 token 成本
===== ===================== ===== ==========================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import Feature
from .attribution import AttributionFeature
from .face import FaceFeature
from .imageslim import ImageSlimFeature
from .profile import ProfileFeature
from .quote import QuoteFeature
from .recall import RecallFeature
from .tokenline import TokenLineFeature

#: 功能注册表 —— 加功能就在这里加一行。
FEATURES: Tuple[Feature, ...] = (
    ImageSlimFeature(),
    QuoteFeature(),
    FaceFeature(),
    AttributionFeature(),
    ProfileFeature(),
    RecallFeature(),
    TokenLineFeature(),
)


def all_features() -> List[Feature]:
    """返回按执行顺序排好的功能列表。"""
    return sorted(FEATURES, key=lambda f: f.order)


def get_feature(key: str) -> Optional[Feature]:
    """按配置键取功能实例；找不到返回 ``None``。"""
    for feat in FEATURES:
        if feat.key == key:
            return feat
    return None


def get_feature_by_command(name: str) -> Optional[Feature]:
    """按子指令名取功能实例（``/xbnext <name>``）；找不到返回 ``None``。"""
    if not name:
        return None
    for feat in FEATURES:
        if feat.command and feat.command == name:
            return feat
    return None


__all__ = [
    "Feature",
    "FEATURES",
    "all_features",
    "get_feature",
    "get_feature_by_command",
]
