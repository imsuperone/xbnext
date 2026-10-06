# -*- coding: utf-8 -*-
"""功能注册表 —— **新增功能只需要改这一个文件**。

``FEATURES`` 的书写顺序就是执行顺序（runtime 还会按 ``order`` 再排一次，
两处不一致时以 ``order`` 为准，``order`` 相同则按书写顺序）。

当前四个功能对应四个需求（详见 ``aidoc/01-需求与根因.md``）：

===== ===================== ===== ==========================================
序号   功能                  order 需求
===== ===================== ===== ==========================================
R2     quote_clean           20    引用占位误判成空白图片
R3     face_translate        30    QQ 自带表情模型读不懂
R1     reply_attribution     40    把 A 的话套到 B 头上
R4     user_profile          50    用户自定义档案不被读取
===== ===================== ===== ==========================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import Feature
from .attribution import AttributionFeature
from .face import FaceFeature
from .profile import ProfileFeature
from .quote import QuoteFeature

#: 功能注册表 —— 加功能就在这里加一行。
FEATURES: Tuple[Feature, ...] = (
    QuoteFeature(),
    FaceFeature(),
    AttributionFeature(),
    ProfileFeature(),
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


def feature_meta() -> List[Dict[str, Any]]:
    """导出全部功能的元信息（WebUI 卡片用）。"""
    return [f.describe() for f in all_features()]


__all__ = [
    "Feature",
    "FEATURES",
    "all_features",
    "get_feature",
    "feature_meta",
]
