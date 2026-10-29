# -*- coding: utf-8 -*-
"""版本号解析 —— 全仓唯一实现。

``metadata.yaml`` 是插件版本的唯一来源：本模块同时服务于插件自身
(:data:`xbnext.__version__`) 与更新器读取其它插件版本，避免两套正则漂移。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

_VERSION_RE = re.compile(r'^\s*version:\s*["\']?([^"\'\s\n]+)', re.MULTILINE)


def extract_version(text: str) -> str:
    """从 ``metadata.yaml`` 文本中提取 ``version`` 字段；找不到返回空串。"""
    if not text:
        return ""
    match = _VERSION_RE.search(str(text))
    return match.group(1).strip() if match else ""


def read_version(path: Union[str, Path], default: str = "0.0.0") -> str:
    """读取文件里的版本号；任何失败都回退 ``default``，不让插件起不来。"""
    try:
        return extract_version(Path(path).read_text(encoding="utf-8")) or default
    except Exception:  # noqa: BLE001
        return default


__all__ = ["extract_version", "read_version"]
