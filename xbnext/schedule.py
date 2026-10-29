# -*- coding: utf-8 -*-
"""每日 HH:MM 调度 —— 全仓唯一实现。

face 表更新与插件定时更新都用「每天几点跑一次」，以前各写了一份
同款计算（face.updater.next_run_delay / updater.next_schedule_delay），
收口到本模块。约定：

- 合法 ``HH:MM``（``04:30`` / ``4:05``）→ 今天该时刻；已过点取明天；
- 非法 / 缺失 → 回落 :data:`DEFAULT_TIME`（``04:30``）；
- 返回值恒 ``> 0``，可直接 ``asyncio.sleep``。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

__all__ = ["DEFAULT_TIME", "next_run_delay"]

#: 默认执行时刻（24 小时制 HH:MM，本地时区）
DEFAULT_TIME = "04:30"

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def next_run_delay(now: datetime, hhmm: Any = None) -> float:
    """距离下一个 ``HH:MM``（本地时区）还有多少秒（恒 ``> 0``）。"""
    text = str(hhmm).strip() if hhmm is not None else ""
    m = _HHMM_RE.match(text)
    if not m:
        m = _HHMM_RE.match(DEFAULT_TIME)
    hour, minute = int(m.group(1)), int(m.group(2))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()
