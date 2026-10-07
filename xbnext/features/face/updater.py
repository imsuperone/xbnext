# -*- coding: utf-8 -*-
"""R3 · 表情表权威源的运行时自动更新（QFace ``_index.json``）。

内置表 ``data.py`` 是**发布时**用 ``aidoc/tools/gen_face_table.py`` 抓取生成的，
QQ 出新表情（真机第三轮反馈的 496=阴晴圆缺 就是例子）后要等发版才认识。
本模块让插件在运行时自己从权威源补缺：

**权威源**：`github.com/koishijs/QFace` →
``public/assets/qq_emoji/_index.json``（QQ 官方表情资源索引，
实测 537 条、数字 emojiId 覆盖到 507）。

三层结构：

1. **内置表**（``data.py``，发布时生成）—— 查表时**优先**；
2. **overlay**（运行时拉取的缺口条目，存插件 KV）—— 只补内置表没有的 ID；
3. **``face_name_with_overlay()``** —— 唯一查询入口，内置 → overlay 依次查。

更新任务（FaceFeature.on_load 启动 / on_unload 取消）：

- 首次从未拉过 → 启动后短暂延迟先拉一次（拿新版表情不用等凌晨）；
- 之后按配置 ``face_update_time``（默认 ``"04:30"``）每天拉一次；
- 成功：overlay 整体替换成"权威源 − 内置表"的缺口集并落 KV，
  有新增打一条 INFO，无变化打 DEBUG；失败打 WARNING，
  **异常一律不上抛**（AstrBot 官方原则 4：一个错误不能崩插件）。

本模块**不 import astrbot**：纯逻辑（parse / merge / next_run_delay）
直接 unittest；``fetch_index`` 用 ``asyncio.to_thread`` 包 urllib，
测试不会触网（红线：测试环境禁网，后台任务在 ``asyncio.run`` 退出时被取消）。
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional

from ... import __version__
from .data import QQ_FACE_ALL

#: 权威源索引（koishijs/QFace 的 QQ 官方表情资源清单）
QFACE_INDEX_URL = (
    "https://raw.githubusercontent.com/koishijs/QFace"
    "/master/public/assets/qq_emoji/_index.json"
)

#: 默认更新时间（24 小时制 HH:MM，本地时区）；配置非法时也回落到这里
DEFAULT_UPDATE_TIME = "04:30"

#: 拉取超时（秒）
FETCH_TIMEOUT = 20

#: 条数下限：解析出来比这还少 ⇒ 上游返回了坏数据，整批拒收
MIN_ENTRIES = 100

#: KV 键（KV 封装自动加 ``xbnext:`` 前缀）
OVERLAY_KV_KEY = "face_overlay"

#: 从未拉过时，启动后先等这个秒数再拉第一次
FIRST_RUN_DELAY = 60

#: 更新循环的分段睡眠上限（秒）：每段醒来重读一次配置，热改最迟 1 小时生效
SLEEP_SEGMENT = 3600

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

#: 运行时 overlay：内置表没有的 ID → 名称（发布后 QQ 新加的表情）
_overlay: Dict[int, str] = {}


# ==================================================================
# 纯逻辑（可直接单测）
# ==================================================================
def parse_index(payload: Any) -> Dict[int, str]:
    """解析 QFace ``_index.json`` → ``{id: 名称}``。

    条目结构（实测 537 条）::

        {"emojiId": "0",  "describe": "/惊讶", "qcid": 0, ...}      # 数字 ID
        {"emojiId": "☀", "describe": "/晴天", "qcid": 9728, ...}  # 非数字 → 码点

    规则与生成器 ``gen_face_table.parse_qface`` 一致：

    - ``emojiId`` 纯数字 → 键取该数字；否则键取 ``qcid``（unicode 码点，
      ``qcid <= 0`` 跳过）；
    - ``describe`` 剥掉开头 ``/`` 再 strip，空名跳过；
    - 重复键先到先得；结构不认识（非 list / 非 dict 条目）静默跳过。

    **永不抛异常**：坏 JSON 返回 ``{}``（由 ``fetch_index`` 按
    :data:`MIN_ENTRIES` 拒收）。
    """
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, list):
            return {}
        out: Dict[int, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            desc = item.get("describe")
            if not isinstance(desc, str):
                continue
            name = desc.strip()
            if name.startswith("/"):
                name = name[1:].strip()
            if not name:
                continue
            emoji_id = item.get("emojiId")
            if isinstance(emoji_id, str) and emoji_id.strip().isdigit():
                key = int(emoji_id.strip())
            else:
                qcid = item.get("qcid")
                if not isinstance(qcid, int) or qcid <= 0:
                    continue
                key = qcid
            out.setdefault(key, name)
        return out
    except Exception:  # noqa: BLE001  坏数据整批作废，不冒泡
        return {}


def merge_missing(incoming: Mapping[int, str]) -> Dict[int, str]:
    """算出 overlay = 权威源里**内置表没有**的条目（内置表优先、只补缺）。

    :param incoming: ``parse_index`` 的产物
    :return: ``{id: 名称}``，不含任何已存在于 ``QQ_FACE_ALL`` 的键
    """
    out: Dict[int, str] = {}
    try:
        for key, name in (incoming or {}).items():
            try:
                k = int(key)
            except Exception:  # noqa: BLE001
                continue
            if k in QQ_FACE_ALL:
                continue
            text = str(name).strip()
            if text:
                out[k] = text
    except Exception:  # noqa: BLE001
        return out
    return out


def next_run_delay(now: datetime, hhmm: Any) -> float:
    """距离下一个 ``HH:MM``（本地时区）还有多少秒。

    - 合法 ``HH:MM``（如 ``"04:30"``、``"4:05"``）→ 当天该时刻；
      已过点则取**明天**同一时刻（保证返回值 > 0）；
    - 非法 / 缺失 → 回落 :data:`DEFAULT_UPDATE_TIME`（``"04:30"``）。
    """
    text = str(hhmm).strip() if hhmm is not None else ""
    m = _HHMM_RE.match(text)
    if not m:
        m = _HHMM_RE.match(DEFAULT_UPDATE_TIME)
    hour, minute = int(m.group(1)), int(m.group(2))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ==================================================================
# overlay 查表层（内置表优先、overlay 只补缺）
# ==================================================================
def set_overlay(mapping: Any) -> None:
    """整体替换运行时 overlay（拉取成功 / 从 KV 恢复时用）。

    **先构建局部 dict 再原子换入**：单个坏键只跳过那一个键，不会把
    整批已就绪的条目连坐清空（旧写法一遇坏键整表作废）。
    非 Mapping 入参 / ``None`` ⇒ 清空（跨测试、跨 runtime 不残留）。
    """
    fresh: Dict[int, str] = {}
    if isinstance(mapping, Mapping):
        for key, name in mapping.items():
            try:
                k = int(key)
            except Exception:  # noqa: BLE001  坏键（如 "updated_at"）跳过
                continue
            try:
                text = str(name).strip()
            except Exception:  # noqa: BLE001
                continue
            if k not in QQ_FACE_ALL and text:
                fresh[k] = text
    _overlay.clear()
    _overlay.update(fresh)


def get_overlay() -> Dict[int, str]:
    """返回 overlay 的拷贝（对外只读）。"""
    return dict(_overlay)


def face_name_with_overlay(code: object) -> Optional[str]:
    """查表情中文名：**内置表优先**，查不到再查 overlay；都没有返回 ``None``。

    与 ``data.face_name`` 同契约（不猜语义，由调用方兜底 ``[表情:ID..]``），
    是 :func:`xbnext.features.face.service.translate_token` 的实际入口。
    """
    try:
        key = int(code)
    except Exception:  # noqa: BLE001
        return None
    name = QQ_FACE_ALL.get(key)
    if name:
        return name
    return _overlay.get(key)


# ==================================================================
# 拉取（异步，网络只走这里）
# ==================================================================
def _fetch_sync(url: str) -> str:
    """``urllib`` 同步拉取（在 ``asyncio.to_thread`` 里跑，不阻塞事件循环）。"""
    req = urllib.request.Request(url, headers={"User-Agent": f"xbnext-face/{__version__}"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


async def fetch_index(url: str = QFACE_INDEX_URL) -> Dict[int, str]:
    """拉取权威索引并解析；坏数据 / 网络错误抛 ``ValueError`` / ``OSError``。

    由调用方（更新任务）捕获并打 WARNING —— 这里**不吞异常**，
    否则"拉到了坏数据"和"没拉到"无法区分。
    """
    text = await asyncio.to_thread(_fetch_sync, url)
    mapping = parse_index(text)
    if len(mapping) < MIN_ENTRIES:
        raise ValueError(f"权威索引条数 {len(mapping)} < {MIN_ENTRIES}，疑似坏数据")
    return mapping


async def pull_overlay(url: str = QFACE_INDEX_URL) -> Dict[int, str]:
    """拉取 + 算缺口 + 应用 overlay，返回**新的** overlay（纯内存，不落 KV）。

    ``fetch_index`` 抛什么就往上抛什么（调用方负责日志）。
    """
    incoming = await fetch_index(url)
    merged = merge_missing(incoming)
    set_overlay(merged)
    return get_overlay()


def overlay_from_stored(stored: Any) -> Dict[int, str]:
    """把 KV 里存的 overlay 恢复成 ``{int: str}``（查表用）。

    KV 实际存的形状是 ``{"ids": {...}, "updated_at": iso}``（嵌套），
    也兼容直接存平铺 ``{"14": "微笑"}`` 的旧形状。
    非 Mapping（``None`` / 坏数据）⇒ **清空** overlay —— 防止上一个
    runtime / 上一轮测试的残留串到本轮。
    """
    if not isinstance(stored, Mapping):
        set_overlay(None)
        return {}
    ids = stored.get("ids")
    payload = ids if isinstance(ids, Mapping) else stored
    set_overlay(payload)
    return get_overlay()


def updated_at_from_stored(stored: Any) -> Optional[str]:
    """从 KV 记录里取上次成功更新时间（ISO 字符串）；没有 / 形状不对返回 ``None``。"""
    if isinstance(stored, Mapping):
        value = stored.get("updated_at")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def overlay_to_stored() -> Dict[str, str]:
    """把 overlay 序列化成 KV 友好的 ``{str: str}``。"""
    return {str(k): v for k, v in _overlay.items()}


def added_since(prev: Iterable[int], current: Optional[Mapping[int, str]] = None) -> int:
    """本次结果比上一次多出几个键（用于"有新增打 INFO"）。"""
    try:
        prev_set = {int(k) for k in prev}
        cur = _overlay if current is None else current
        return len(set(int(k) for k in cur) - prev_set)
    except Exception:  # noqa: BLE001
        return 0


def stats(updated_at: Optional[str]) -> Dict[str, Any]:
    """状态卡只读统计（P17 · D）：内置表条数 / overlay 条数 / 上次更新时间。

    ``updated_at`` 由功能实例传入（它记得 KV 恢复与每次成功更新）；
    overlay 直接读运行时模块状态 —— 页面拿到的永远是"当前生效"的值。
    非字符串 / 空白 ⇒ ``None``（前端显示"从未更新"）。
    """
    ts = updated_at.strip() if isinstance(updated_at, str) else ""
    return {
        "builtin": len(QQ_FACE_ALL),
        "overlay": len(_overlay),
        "updated_at": ts or None,
    }


__all__ = [
    "QFACE_INDEX_URL",
    "DEFAULT_UPDATE_TIME",
    "FETCH_TIMEOUT",
    "MIN_ENTRIES",
    "OVERLAY_KV_KEY",
    "FIRST_RUN_DELAY",
    "SLEEP_SEGMENT",
    "parse_index",
    "merge_missing",
    "next_run_delay",
    "set_overlay",
    "get_overlay",
    "face_name_with_overlay",
    "fetch_index",
    "pull_overlay",
    "overlay_from_stored",
    "overlay_to_stored",
    "updated_at_from_stored",
    "added_since",
    "stats",
]
