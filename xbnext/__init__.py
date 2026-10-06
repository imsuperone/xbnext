# -*- coding: utf-8 -*-
"""XBNEXT 运行时包。

分层（自上而下，上层可依赖下层，禁止反向依赖）::

    main.py              Star 入口薄壳（只转发钩子）
      └─ xbnext/
         ├─ runtime.py    调度中心：按固定顺序调用 features
         ├─ context.py    RequestContext：单次请求上下文
         ├─ features/     功能模块 —— 一功能一目录，新增功能只改这里
         ├─ injector.py   唯一注入出口（temp part + sanitize）
         ├─ switches.py   开关对账 + WebUI 配置写入
         ├─ storage.py    插件 KV 封装（懒加载 / 写穿 / 降级）
         ├─ config.py     配置读取（含 _conf_schema.json 默认值回退）
         └─ web/          WebUI 后端 API

本模块**不 import 任何 astrbot 符号**，保证 ``import xbnext`` 在裸 Python
环境下可用（单测依赖这一点）。
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_xbnext"
DISPLAY_NAME = "XBNEXT"
KV_PREFIX = "xbnext"

_METADATA_PATH = Path(__file__).resolve().parent.parent / "metadata.yaml"


def _read_version() -> str:
    """从 ``metadata.yaml`` 读版本号 —— **唯一版本来源**。

    代码里不许出现版本字面量（aidoc/03 红线 §2）；照 AstrNa
    ``main.py::_read_plugin_version()`` 的做法，任何失败回退成占位值。
    """
    try:
        text = _METADATA_PATH.read_text(encoding="utf-8")
        match = re.search(r'^version:\s*["\']?([^"\'\n]+)["\']?\s*$', text, re.M)
        if match and match.group(1).strip():
            return match.group(1).strip()
    except Exception:  # noqa: BLE001  读不到不能让插件起不来
        pass
    return "0.0.0"


#: 插件版本号（读自 metadata.yaml）。三处必须同步：metadata.yaml /
#: CHANGELOG.md 首条 / README.md 版本行，``tests/test_structure.py`` 机械校验。
__version__ = _read_version()

#: 所有钩子统一使用的 priority（数值越大越靠后，方向待真机实测）；
#: 取值理由与可能的相互作用见 aidoc/02-架构设计.md §2。
HOOK_PRIORITY = 1000

__all__ = [
    "PLUGIN_NAME",
    "DISPLAY_NAME",
    "KV_PREFIX",
    "HOOK_PRIORITY",
    "__version__",
]
