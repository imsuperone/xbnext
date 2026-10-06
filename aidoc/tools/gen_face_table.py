# -*- coding: utf-8 -*-
"""从两个上游源抓取 QQ 表情 ID→中文名表，合并生成 xbnext/features/face/data.py。

源 A（主）：ehForwarderBot/efb-qq-plugin-go-cqhttp  Utils.py::qq_emoji_text_list
            0-255 全量，注释标注 "original text copied from Tim"
源 B（扩展）：Mai-with-u/MaiBot-Napcat-Adapter  qq_emoji_list.py::QQ_FACE
            256-395 + unicode codepoint 段
冲突处理：0-255 以源 A 为准；256+ 取源 B。
"""
import ast
import io
import re
import sys
import urllib.request

SRC_A = "https://raw.githubusercontent.com/ehForwarderBot/efb-qq-plugin-go-cqhttp/master/efb_qq_plugin_go_cqhttp/Utils.py"
SRC_B = "https://raw.githubusercontent.com/Mai-with-u/MaiBot-Napcat-Adapter/main/qq_emoji_list.py"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "xbnext-gen/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def grab_dict(src, name):
    m = re.search(r"^%s\s*(?::[^=\n]*)?=\s*\{" % re.escape(name), src, re.M)
    if not m:
        raise SystemExit("dict %s not found" % name)
    i = m.end() - 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return ast.literal_eval(src[i : j + 1])
    raise SystemExit("unbalanced braces for %s" % name)


def clean(d):
    out = {}
    for k, v in d.items():
        if not isinstance(v, str):
            continue
        v = v.strip()
        if v in ("[Empty]", "", "[Empty]"):
            continue
        m = re.fullmatch(r"\[([^\[\]]+)\]", v)
        name = m.group(1) if m else v
        if name in ("Empty",):
            continue
        name = name.strip()
        # 源 B 写作 "[表情：惊讶]"，剥掉装饰前缀只留名称
        for pfx in ("表情：", "表情:"):
            if name.startswith(pfx):
                name = name[len(pfx):].strip()
                break
        if not name or name == "Empty":
            continue
        out[int(k)] = name
    return out


def main():
    a = clean(grab_dict(fetch(SRC_A), "qq_emoji_text_list"))
    b = clean(grab_dict(fetch(SRC_B), "QQ_FACE"))
    print("A(0-255):", len(a), "B:", len(b))

    merged = dict(a)
    conflicts = []
    for k, v in b.items():
        if k in merged and merged[k] != v:
            conflicts.append((k, merged[k], v))
        else:
            merged.setdefault(k, v)
    for k in sorted(b):
        if k not in merged:
            merged[k] = b[k]
    print("conflicts(A wins):", conflicts)
    print("merged total:", len(merged), "max id:", max(merged))

    # 分组：0-255 / 256-999 / >=10000(codepoint)
    classic = {k: v for k, v in sorted(merged.items()) if k <= 255}
    ext = {k: v for k, v in sorted(merged.items()) if 255 < k < 10000}
    cpoint = {k: v for k, v in sorted(merged.items()) if k >= 10000}
    print("classic", len(classic), "ext", len(ext), "codepoint", len(cpoint))

    def fmt(mapping, per_line=1, indent=4):
        lines = []
        items = list(mapping.items())
        for k, v in items:
            lines.append("%s%d: %s," % (" " * indent, k, _py(v)))
        return "\n".join(lines)

    def _py(s):
        return repr(s)

    out = io.StringIO()
    _hdr = '''# -*- coding: utf-8 -*-
"""R3 · QQ 表情数据表（**权威全量，非手写**）。

本文件的几张表由 ``aidoc/tools/gen_face_table.py`` 从两个上游仓库**脚本抓取生成**，
不靠记忆编造 ID → 名称的对应关系（aidoc/03 红线：编错比缺更糟，
模型会把"流泪"理解成"微笑"）。

**数据来源**

- **主表 ``QQ_FACE``（0~255）**
  `github.com/ehForwarderBot/efb-qq-plugin-go-cqhttp` → `efb_qq_plugin_go_cqhttp/Utils.py`
  的 ``qq_emoji_text_list``（文件内注释标注 *original text copied from Tim*，
  面向 mobileqq v8.8.11）。
- **扩展 ``QQ_FACE_EXT``（256~{ext_max}）+ ``QQ_FACE_CODEPOINT``（unicode 码点段）**
  `github.com/Mai-with-u/MaiBot-Napcat-Adapter` → `qq_emoji_list.py` 的 ``QQ_FACE``
  （该仓库为 NapCat 适配器，实测表情 ID 覆盖到 {ext_max}）。

**冲突处理**：两表在 0~255 只有 {nconf} 处不一致（见下方 ``CONFLICTS``），
一律以**主表（源 A）为准**，源 A 覆盖全 0~255 连续区间。

**查不到怎么办**：``face_name()`` 返回 ``None`` → 调用方渲染成
``[表情:ID233]``，**宁可泄露 ID 也不瞎猜语义**（绝不静默丢弃）。

> ``QQ_FACE_ALIAS`` 是给"自然语言→ID"的反查用的额外补充，与主表同源裁剪而来。
"""

from __future__ import annotations

from typing import Dict, Optional

#: 主表：QQ 标准表情 ID → 中文名（0~255，源 A）
QQ_FACE: Dict[int, str] = {
'''
    _hdr = _hdr.replace("{ext_max}", str(max(ext) if ext else 0))
    _hdr = _hdr.replace("{nconf}", str(len(conflicts)))
    out.write(_hdr)
    out.write(fmt(classic))
    out.write("\n}\n\n")
    out.write("""#: 扩展表：新版 QQ 表情 ID → 中文名（256~%d，源 B）
QQ_FACE_EXT: Dict[int, str] = {
""" % (max(ext) if ext else 0))
    out.write(fmt(ext))
    out.write("\n}\n\n")
    out.write("""#: unicode 码点段（部分新版表情以 emoji 码点作为 ID，源 B）
QQ_FACE_CODEPOINT: Dict[int, str] = {
""")
    out.write(fmt(cpoint))
    out.write("\n}\n\n")

    # 合并成一张总表，便于 O(1) 查询
    out.write("#: 三张表合并后的唯一查询入口（ID → 中文名）\nQQ_FACE_ALL: Dict[int, str] = {}\n")
    out.write("for _t in (QQ_FACE, QQ_FACE_EXT, QQ_FACE_CODEPOINT):\n")
    out.write("    QQ_FACE_ALL.update(_t)\n\n\n")

    # 保留原 QQ_FACE 名字 = 主表（兼容旧用法：data.QQ_FACE[0] == 惊讶）
    # 上面已经叫 QQ_FACE 了，OK。

    out.write("""#: 两表冲突记录（已按源 A 定稿，列出以便回溯核对）\nCONFLICTS = %r\n\n\n""" % (conflicts,))

    out.write('''#: 自然语言别名 → 表情 ID（反查用；与主表同源，未新增记忆内容）
QQ_FACE_ALIAS: Dict[str, int] = {
''')

    alias_lines = []
    seen = {}
    for k, v in sorted(classic.items(), key=lambda kv: kv[0]):
        if v in seen:
            continue
        seen[v] = k
        alias_lines.append("    %s: %d," % (_py(v), k))
    out.write("\n".join(alias_lines))
    out.write("\n}\n\n\n")

    out.write('''def face_name(code: object) -> Optional[str]:
    """按 ID 查表情中文名；查不到返回 ``None``（由调用方兜底，不猜）。"""
    try:
        return QQ_FACE_ALL.get(int(code))
    except Exception:  # noqa: BLE001
        return None


def face_id(name: object) -> Optional[int]:
    """按中文名反查 ID；查不到返回 ``None``。"""
    if not isinstance(name, str) or not name:
        return None
    return QQ_FACE_ALIAS.get(name.strip("[]").strip())


def mface_name(key: object) -> Optional[str]:
    """按 mface（商城表情）key 查名称。

    **商城表情没有公开的权威 key → 名称表**（key 是用户上传表情包的私有
    资源标识，随包变化）。因此本函数**固定返回 ``None``**，由调用方改用
    消息段自带的 ``summary`` 字段（NapCat/aiocqhttp 收包时填的表情中文名）。
    """
    if not isinstance(key, str) or not key:
        return None
    return None


__all__ = [
    "QQ_FACE",
    "QQ_FACE_EXT",
    "QQ_FACE_CODEPOINT",
    "QQ_FACE_ALL",
    "QQ_FACE_ALIAS",
    "CONFLICTS",
    "face_name",
    "face_id",
    "mface_name",
]
''')

    dest = sys.argv[1]
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(out.getvalue())
    print("written:", dest)


if __name__ == "__main__":
    main()
