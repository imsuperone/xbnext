# -*- coding: utf-8 -*-
"""插件一键更新与检查核心服务。

通过 AstrBot OpenAPI 接口检查并更新选定的插件。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...version import extract_version as extract_version_from_yaml

_MARKET_INDEX: Dict[str, Dict[str, Any]] = {}
_MARKET_CACHE_TS: float = 0.0
_MARKET_CACHE_TTL: float = 900.0  # 15 分钟


def clear_market_cache() -> None:
    """清空市场索引缓存（测试与调试辅助）。"""
    global _MARKET_INDEX, _MARKET_CACHE_TS
    _MARKET_INDEX = {}
    _MARKET_CACHE_TS = 0.0

DEFAULT_TARGET_PLUGINS = [
    "astrbot_plugin_xbimg",
    "astrbot_plugin_xb2img",
    "astrbot_plugin_xbdoc",
    "astrbot_plugin_xbbot",
    "astrbot_plugin_xbbot_beta",
    "astrbot_plugin_xbnext",
]

KNOWN_REPOS: Dict[str, str] = {
    "astrbot_plugin_xbnext": "https://github.com/imsuperone/xbnext",
    "astrbot_plugin_xbbot_beta": "https://github.com/imsuperone/xbtest",
    "astrbot_plugin_xbbot": "https://github.com/imsuperone/xb",
    "astrbot_plugin_xbimg": "https://github.com/imsuperone/xbimg",
    "astrbot_plugin_xb2img": "https://github.com/imsuperone/xb2img",
    "astrbot_plugin_xbdoc": "https://github.com/imsuperone/xbdoc",
    "astrbot_plugin_pokepro": "https://github.com/imsuperone/pokepro",
    "astrbot_plugin_stealer": "https://github.com/imsuperone/stealer",
}

def extract_metadata_field(text: str, field: str) -> str:
    """从 metadata.yaml 文本中提取指定字段（如 display_name / name / version）。"""
    if not text:
        return ""
    m = re.search(rf'(?:^|\n)\s*{field}:\s*["\']?([^"\'\r\n]+)', text)
    return m.group(1).strip() if m else ""


def get_plugin_display_name(p: Dict[str, Any], local_dir: str = "") -> str:
    """从本地 metadata.yaml 或 OpenAPI 返回值中读取插件显示名称，杜绝人工硬编码映射。"""
    name = str(p.get("name") or "").strip()
    norm_name = normalize_plugin_name(name)
    disp = str(p.get("display_name") or "").strip()

    # 1. 尝试从已知 local_dir 读取 metadata.yaml
    if not disp and local_dir and os.path.isdir(local_dir):
        meta_file = os.path.join(local_dir, "metadata.yaml")
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    disp = extract_metadata_field(f.read(), "display_name")
            except Exception:
                pass

    # 2. 主动探测标准路径、同级开发工作区及容器目录
    if not disp:
        _plugins_container = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
        for b in (_plugins_container, "data/plugins", "plugins", ".", ".."):
            if not os.path.isdir(b):
                continue
            for cand in (name, norm_name, name.replace("astrbot_plugin_", "")):
                if not cand:
                    continue
                path = os.path.join(b, cand, "metadata.yaml")
                if os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            disp = extract_metadata_field(f.read(), "display_name")
                            if disp:
                                break
                    except Exception:
                        pass
            if disp:
                break

    # 3. 尝试从已缓存的官方市场索引中读取 display_name
    if not disp and _MARKET_INDEX:
        m_item = _MARKET_INDEX.get(name) or _MARKET_INDEX.get(norm_name)
        if not m_item and name.startswith("astrbot_plugin_"):
            m_item = _MARKET_INDEX.get(name.replace("astrbot_plugin_", ""))
        if m_item and isinstance(m_item, dict):
            disp = str(m_item.get("display_name") or "").strip()

    if disp and disp != name:
        return f"{disp} ({name})"
    return disp or name


def normalize_plugin_name(name: str) -> str:
    """归一化插件名称（支持短名与全名互转）。"""
    s = str(name or "").strip()
    if not s:
        return ""
    if s.startswith("astrbot_plugin_"):
        return s
    short = s.lower().replace("-", "_")
    alias_map = {
        "xbimg": "astrbot_plugin_xbimg",
        "xb2img": "astrbot_plugin_xb2img",
        "xbdoc": "astrbot_plugin_xbdoc",
        "xbbot": "astrbot_plugin_xbbot",
        "xbbotbeta": "astrbot_plugin_xbbot_beta",
        "xbbot_beta": "astrbot_plugin_xbbot_beta",
        "xbnext": "astrbot_plugin_xbnext",
    }
    return alias_map.get(short, f"astrbot_plugin_{short}")


def _make_ssl_context() -> ssl.SSLContext:
    """默认启用证书校验；更新包与 API Key 都走这条链路，不能降级。"""
    return ssl.create_default_context()


def _http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout: int = 15,
) -> Tuple[int, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    ctx = _make_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return resp.status, parsed
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = err_body
        return err.code, parsed
    except Exception as exc:
        return 0, str(exc)


def _extract_err_msg(resp: Any) -> str:
    """从 HTTP 错误响应中提取可读的错误文本（兼顾 FastAPI 422 detail 与标准 message 格式）。"""
    if isinstance(resp, dict):
        if resp.get("message"):
            return str(resp["message"])
        if resp.get("detail"):
            detail = resp["detail"]
            if isinstance(detail, list) and detail:
                first = detail[0]
                if isinstance(first, dict):
                    loc = ".".join(str(x) for x in first.get("loc", []))
                    msg = first.get("msg", "")
                    return f"{loc}: {msg}" if loc else msg
                return str(first)
            return str(detail)
        if resp.get("error"):
            return str(resp["error"])
    elif isinstance(resp, list) and resp:
        return str(resp[0])
    return str(resp or "")


def format_version(v: str) -> str:
    """统一版本显示格式，如 v1.3.31 或 v26w1014b。"""
    s = str(v or "").strip().strip("'\"")
    if not s or s == "未知":
        return "未知"
    if s.lower().startswith("v"):
        return s
    return f"v{s}"


def parse_version_key(v: str) -> List[Any]:
    """把版本串拆成可比权重序列（数字段 ``(0, int)`` / 文本段 ``(1, str)``）。

    忽略 ``v`` 前缀与 ``+build`` 元数据；``.`` / ``_`` / 空白只用于切段，
    不参与比较 —— 因此 ``1.0`` 与 ``1.0.0`` 等价，而 ``26w1014b`` 这类
    日期式版本仍按 ``26 < w < 1014 < b`` 的原始顺序比较。
    """
    s = str(v or "").strip().strip("'\"")
    if s[:1] in ("v", "V"):
        s = s[1:]
    s = s.split("+", 1)[0]
    res: List[Any] = []
    for chunk in re.split(r"[._\s]+", s.strip()):
        if not chunk:
            continue
        for token in re.findall(r"\d+|\D+", chunk):
            res.append((0, int(token)) if token.isdigit() else (1, token.lower()))
    return res


def _compare_keys(k1: List[Any], k2: List[Any]) -> int:
    """按权重序列比较；较短一方用 ``(0, 0)`` 补齐（尾部 0 不构成差异）。"""
    zero = (0, 0)
    for i in range(max(len(k1), len(k2))):
        a = k1[i] if i < len(k1) else zero
        b = k2[i] if i < len(k2) else zero
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


def _split_prerelease(v: str) -> Tuple[str, str]:
    """拆出主版本与预发布段（``1.2.3-beta.1`` → ``("1.2.3", "beta.1")``）。"""
    s = str(v or "").strip().strip("'\"")
    if s[:1] in ("v", "V"):
        s = s[1:]
    s = s.split("+", 1)[0]
    main, sep, pre = s.partition("-")
    return main, (pre if sep else "")


def compare_versions(v1: str, v2: str) -> int:
    """比较两个版本号：v1 < v2 返回 -1；相等返回 0；v1 > v2 返回 1。

    规则：忽略 ``v`` 前缀与 ``+build`` 元数据；``1.0`` 与 ``1.0.0`` 等价；
    同一主版本下预发布版低于正式版（``1.2.3-beta < 1.2.3``）；数字段按
    数值比较，文本段按字典序比较（兼容 ``26w1014b`` 日期式版本）。
    """
    m1, p1 = _split_prerelease(v1)
    m2, p2 = _split_prerelease(v2)
    result = _compare_keys(parse_version_key(m1), parse_version_key(m2))
    if result:
        return result
    if p1 == p2:
        return 0
    if not p1:
        return 1  # 正式版高于同主版本的预发布版
    if not p2:
        return -1
    return _compare_keys(parse_version_key(p1), parse_version_key(p2))


def _get_local_version(p: Dict[str, Any], local_dir: str) -> str:
    """获取本地插件真实版本：优先从本地实际 metadata.yaml 磁盘文件读取，次选 OpenAPI 缓存信息。"""
    if local_dir and os.path.isdir(local_dir):
        meta_file = os.path.join(local_dir, "metadata.yaml")
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    v_meta = extract_version_from_yaml(f.read())
                    if v_meta and v_meta != "未知":
                        return v_meta
            except Exception:
                pass
    v = str(p.get("version") or "").strip()
    return v or "未知"


def _query_remote_version(
    repo_url: str,
    p: Dict[str, Any],
    market_index: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """根据 online_version、AstrBot 官方市场或 GitHub 仓库地址获取远程最新版本号与仓库地址。
    返回 (remote_version, resolved_repo_url)。
    """
    online_ver = str(p.get("online_version") or "").strip()
    if online_ver:
        return online_ver, repo_url

    name = str(p.get("name") or "").strip()
    norm_name = normalize_plugin_name(name)

    # 1. 优先从 AstrBot 官方市场 Registry 索引获取（零网络等待，规避 GitHub 阻断）
    if market_index:
        m_item = market_index.get(name) or market_index.get(norm_name)
        if not m_item and name.startswith("astrbot_plugin_"):
            m_item = market_index.get(name.replace("astrbot_plugin_", ""))
        if m_item and isinstance(m_item, dict):
            m_ver = str(m_item.get("version") or "").strip()
            m_repo = str(m_item.get("repo") or "").strip()
            if m_ver:
                return m_ver, (m_repo or repo_url)

    # 2. 备选 GitHub 仓库检测
    if not repo_url or "github.com" not in repo_url:
        return "", repo_url

    match = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repo_url)
    if not match:
        return "", repo_url
    owner, repo = match.group(1), match.group(2)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater",
        "Accept": "application/vnd.github.v3.raw",
    }

    # 2.1 优先 GitHub Contents API
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/metadata.yaml"
    status, body = _http_request(api_url, headers=headers, timeout=4)
    if status == 200 and isinstance(body, str):
        v = extract_version_from_yaml(body)
        if v:
            return v, repo_url

    # 2.2 备选 raw.githubusercontent.com / jsdelivr
    fallback_urls = [
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/metadata.yaml",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/metadata.yaml",
        f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@main/metadata.yaml",
    ]
    for raw_url in fallback_urls:
        status, body = _http_request(raw_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if status == 200 and isinstance(body, str):
            v = extract_version_from_yaml(body)
            if v:
                return v, repo_url

    return "", repo_url


def _git_pull_plugin(local_dir: str, force: bool = False, timeout: int = 40) -> Tuple[bool, str]:
    """通过系统 Git 真实拉取远端仓库代码（只读 + 快进，不破坏本地修改）。

    安全约束：
    1. 使用单次命令 ``-c safe.directory=<dir>``，**不写全局 Git 配置**；
    2. 拉取前检查已跟踪文件是否有未提交修改，有则拒绝（force 也一样）；
    3. 只做 ``fetch`` + ``merge --ff-only`` / ``pull --ff-only``，**绝不 reset --hard**；
    4. 捕获真实 Git 输出，精准返回拉取状态或失败根因。
    """
    if not local_dir or not os.path.isdir(os.path.join(local_dir, ".git")):
        return False, "非 Git 仓库"

    import subprocess

    def _git(*args: str, tmo: int = 10) -> Any:
        return subprocess.run(
            ["git", "-c", f"safe.directory={local_dir}", *args],
            cwd=local_dir,
            timeout=tmo,
            capture_output=True,
            text=True,
        )

    try:
        # 1. 脏工作区保护：已跟踪文件有修改就拒绝，避免用户本地改动被覆盖
        try:
            st = _git("status", "--porcelain", "--untracked-files=no")
            if (st.stdout or "").strip():
                return False, "本地有未提交修改，已拒绝更新（请先提交或暂存，避免丢失）"
        except Exception:
            pass

        # 2. 探测当前分支
        branch = ""
        try:
            res_b = _git("rev-parse", "--abbrev-ref", "HEAD", tmo=8)
            if res_b.returncode == 0:
                branch = (res_b.stdout or "").strip()
        except Exception:
            pass
        if not branch or branch == "HEAD":
            branch = "main"

        # 3. 探测追踪 remote
        remote = ""
        try:
            res_r = _git("config", "--get", f"branch.{branch}.remote", tmo=8)
            if res_r.returncode == 0:
                remote = (res_r.stdout or "").strip()
        except Exception:
            pass
        if not remote:
            try:
                res_all_r = _git("remote", tmo=8)
                if res_all_r.returncode == 0:
                    remotes = (res_all_r.stdout or "").split()
                    if "origin" in remotes:
                        remote = "origin"
                    elif remotes:
                        remote = remotes[0]
            except Exception:
                pass
        if not remote:
            remote = "origin"

        # 4. 强制模式：fetch 后只允许快进合并，不允许丢弃本地提交
        if force:
            res_fetch = _git("fetch", remote, branch, "--prune", tmo=timeout)
            if res_fetch.returncode == 0:
                res_merge = _git("merge", "--ff-only", f"{remote}/{branch}", tmo=10)
                if res_merge.returncode == 0:
                    out = (res_merge.stdout or res_fetch.stdout or "已快进至最新提交").strip()
                    return True, out
                return False, (res_merge.stderr or res_merge.stdout or "无法快进合并").strip()

        # 5. 常规 pull 模式（只快进）
        res_pull = _git("pull", "--ff-only", remote, branch, tmo=timeout)
        if res_pull.returncode == 0:
            out = (res_pull.stdout or "拉取成功").strip()
            return True, out
        err = (res_pull.stderr or res_pull.stdout or f"exit code {res_pull.returncode}").strip()
        return False, err
    except subprocess.TimeoutExpired:
        return False, f"Git 操作超时 ({timeout}s)"
    except Exception as ex:
        return False, str(ex)


def _git_head_sha(local_dir: str) -> str:
    """读取仓库当前 HEAD 提交 SHA；非 Git 仓库或失败时返回空串。"""
    if not local_dir or not os.path.isdir(os.path.join(local_dir, ".git")):
        return ""
    import subprocess
    try:
        res = subprocess.run(
            ["git", "-c", f"safe.directory={local_dir}", "rev-parse", "HEAD"],
            cwd=local_dir,
            timeout=8,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return (res.stdout or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _download_github_archive(repo_url: str, local_dir: str, branch: str = "", timeout: int = 40) -> Tuple[bool, str]:
    """当 Git 不可用或目录非 Git 仓库时，直接从 GitHub 归档包拉取最新源码并解压覆盖。
    严格保留原有的 data/ 目录与用户数据库、配置，防止数据损毁。
    """
    if not repo_url or "github.com" not in repo_url or not local_dir or not os.path.isdir(local_dir):
        return False, "无有效仓库地址或本地目录"

    m = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repo_url)
    if not m:
        return False, f"无法从 {repo_url} 解析 GitHub 仓库信息"
    owner, repo = m.group(1), m.group(2)

    if not branch and os.path.isdir(os.path.join(local_dir, ".git")):
        try:
            head_p = os.path.join(local_dir, ".git", "HEAD")
            if os.path.isfile(head_p):
                with open(head_p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content.startswith("ref: refs/heads/"):
                    branch = content[len("ref: refs/heads/"):].strip()
        except Exception:
            pass

    import urllib.request

    cand_branches = []
    if branch:
        cand_branches.append(branch)
    for b in ("main", "master", "release/v0.7.45-pack-slim"):
        if b not in cand_branches:
            cand_branches.append(b)

    zip_urls = []
    for b in cand_branches:
        zip_urls.extend([
            f"https://github.com/{owner}/{repo}/archive/refs/heads/{b}.zip",
            f"https://ghproxy.net/https://github.com/{owner}/{repo}/archive/refs/heads/{b}.zip",
            f"https://mirror.ghproxy.com/https://github.com/{owner}/{repo}/archive/refs/heads/{b}.zip",
        ])

    zip_bytes = None
    for u in zip_urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 AstrBot-Updater"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    zip_bytes = resp.read()
                    if zip_bytes:
                        break
        except Exception:
            continue

    if not zip_bytes:
        return False, "下载 GitHub 源码归档包失败（网络连接受限）"

    return _extract_zip_safely(zip_bytes, local_dir)


def _extract_zip_safely(zip_bytes: bytes, local_dir: str) -> Tuple[bool, str]:
    """把 GitHub 归档包安全解压到 ``local_dir``。

    - 拒绝任何逃逸 ``local_dir`` 的路径（``../`` / 绝对路径）；
    - 跳过 ``.git`` / ``data`` / ``__pycache__`` 与运行时数据库；
    - 更新后清理旧 ``__pycache__``，避免加载已删除模块的 ``.pyc``。
    """
    import io
    import shutil
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        namelist = zf.namelist()
        if not namelist:
            return False, "下载的归档包为空"
        prefix = namelist[0].split("/")[0] + "/"

        root = os.path.realpath(local_dir)
        written = 0
        for member in zf.infolist():
            rel_name = member.filename.replace("\\", "/")
            if rel_name.startswith(prefix):
                rel_name = rel_name[len(prefix):]
            if not rel_name or rel_name.endswith("/"):
                continue
            parts = [p for p in rel_name.split("/") if p not in ("", ".")]
            if not parts or parts[0] in (".git", "data", "__pycache__"):
                continue
            if rel_name in ("config.json", "data.db", "storage.db", "xb.db"):
                continue

            target_path = os.path.realpath(os.path.join(root, *parts))
            if target_path != root and not target_path.startswith(root + os.sep):
                return False, f"归档包包含非法路径，已拒绝更新: {member.filename}"
            if os.path.isdir(target_path):
                continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with zf.open(member) as src_f, open(target_path, "wb") as dst_f:
                dst_f.write(src_f.read())
            written += 1

        for cache_dir in Path(root).rglob("__pycache__"):
            try:
                shutil.rmtree(cache_dir)
            except Exception:
                pass

        return True, f"已从 GitHub 归档包覆盖 {written} 个文件并清理旧缓存"
    except Exception as ex:
        return False, f"解压更新失败: {ex}"


class UpdaterService:
    """管理插件检查更新与一键升级逻辑。"""

    def __init__(self, conf: Any = None, context: Any = None, loop: Any = None):
        self.conf = conf
        self.context = context
        try:
            self._loop = loop or asyncio.get_running_loop()
        except Exception:
            self._loop = None

    @property
    def enabled(self) -> bool:
        if self.conf is None:
            return False
        getter = getattr(self.conf, "get", None)
        if callable(getter):
            return bool(getter("enable_updater", False))
        return False

    @property
    def api_key(self) -> str:
        if self.conf is None:
            return ""
        getter = getattr(self.conf, "get", None)
        if callable(getter):
            return str(getter("updater_api_key", "") or "").strip()
        return ""

    @property
    def api_url(self) -> str:
        if self.conf is None:
            return "http://127.0.0.1:6185"
        getter = getattr(self.conf, "get", None)
        val = getter("updater_api_url", "http://127.0.0.1:6185") if callable(getter) else "http://127.0.0.1:6185"
        val = str(val or "http://127.0.0.1:6185").strip().rstrip("/")
        return val or "http://127.0.0.1:6185"

    @property
    def schedule_enabled(self) -> bool:
        if self.conf is None:
            return False
        getter = getattr(self.conf, "get", None)
        if callable(getter):
            return bool(getter("enable_updater_schedule", False))
        return False


    @property
    def schedule_time(self) -> str:
        if self.conf is None:
            return "04:30"
        getter = getattr(self.conf, "get", None)
        val = getter("updater_schedule_time", "04:30") if callable(getter) else "04:30"
        return str(val or "04:30").strip()

    @property
    def target_plugins(self) -> List[str]:
        if self.conf is None:
            return list(DEFAULT_TARGET_PLUGINS)
        getter = getattr(self.conf, "get", None)
        val = getter("updater_plugins", DEFAULT_TARGET_PLUGINS) if callable(getter) else DEFAULT_TARGET_PLUGINS
        items: List[str] = []
        if isinstance(val, list):
            items = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str):
            items = [x.strip() for x in re.split(r"[,，\s\n]+", val.strip()) if x.strip()]
        if not items:
            return list(DEFAULT_TARGET_PLUGINS)
        return [normalize_plugin_name(x) for x in items]

    @property
    def notify_enabled(self) -> bool:
        if self.conf is None:
            return False
        getter = getattr(self.conf, "get", None)
        if callable(getter):
            return bool(getter("enable_update_notify", False))
        return False

    @property
    def notify_qq(self) -> str:
        if self.conf is None:
            return ""
        getter = getattr(self.conf, "get", None)
        val = getter("update_notify_qq", "") if callable(getter) else ""
        return str(val or "").strip()

    @property
    def notify_qqs(self) -> List[str]:
        raw = self.notify_qq
        if not raw:
            return []
        items = [x.strip() for x in re.split(r"[,，;；\s\n\r]+", str(raw)) if x.strip()]
        valid: List[str] = []
        for x in items:
            if x.isdigit() and x not in valid:
                valid.append(x)
        return valid

    @property
    def notify_third_party(self) -> bool:
        if self.conf is None:
            return False
        getter = getattr(self.conf, "get", None)
        if callable(getter):
            return bool(getter("update_notify_third_party", False))
        return False

    def _auth_headers(self, json_body: bool = True) -> Dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def fetch_market_registry_sync(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取 AstrBot 官方插件市场索引（内存缓存 15 分钟），按多种键名快速命中。"""
        global _MARKET_INDEX, _MARKET_CACHE_TS
        now = time.time()
        if not force_refresh and _MARKET_INDEX and (now - _MARKET_CACHE_TS) < _MARKET_CACHE_TTL:
            return _MARKET_INDEX

        data = None
        # 1. 若配置了 OpenAPI，优先从 AstrBot 本地市场端点读取（毫秒级极速）
        if self.api_key:
            status, resp_data = _http_request(
                f"{self.api_url}/api/v1/plugins/market",
                headers=self._auth_headers(),
                timeout=5,
            )
            if status == 200:
                if isinstance(resp_data, dict):
                    data = resp_data.get("data") or resp_data.get("plugins") or resp_data
                elif isinstance(resp_data, list):
                    data = resp_data

        # 2. 从官方市场 Registry 接口拉取
        if not data or not isinstance(data, (dict, list)):
            status, resp_data = _http_request(
                "https://api.soulter.top/astrbot/plugins",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater"},
                timeout=6,
            )
            if status == 200 and isinstance(resp_data, (dict, list)):
                data = resp_data

        if not data:
            return _MARKET_INDEX or {}

        raw_items = []
        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                raw_items = data["data"]
            elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("plugins"), list):
                raw_items = data["data"]["plugins"]
            elif isinstance(data.get("plugins"), list):
                raw_items = data["plugins"]
            else:
                raw_items = list(data.values())
        elif isinstance(data, list):
            raw_items = data

        index: Dict[str, Dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            p_name = str(item.get("name") or "").strip()
            if not p_name:
                continue
            norm = normalize_plugin_name(p_name)
            short = p_name.replace("astrbot_plugin_", "").lower().replace("-", "_")

            entry = {
                "name": p_name,
                "version": str(item.get("version") or "").strip(),
                "repo": str(item.get("repo") or "").strip(),
                "display_name": str(item.get("display_name") or "").strip(),
                "author": str(item.get("author") or "").strip(),
                "desc": str(item.get("desc") or "").strip(),
                "raw": item,
            }
            index[p_name] = entry
            if norm and norm not in index:
                index[norm] = entry
            if short and short not in index:
                index[short] = entry

        _MARKET_INDEX = index
        _MARKET_CACHE_TS = now
        return _MARKET_INDEX

    def scan_local_plugins_sync(self) -> List[Dict[str, Any]]:
        """扫描已安装的插件。优先读取 AstrBot runtime context 实例，备选本地多级目录。"""
        KNOWN_REPOS = {
            "astrbot_plugin_xbnext": "https://github.com/imsuperone/xbnext",
            "astrbot_plugin_xbbot_beta": "https://github.com/imsuperone/xbtest",
            "astrbot_plugin_xbbot": "https://github.com/imsuperone/xb",
            "astrbot_plugin_xbimg": "https://github.com/imsuperone/xbimg",
            "astrbot_plugin_xb2img": "https://github.com/imsuperone/xb2img",
            "astrbot_plugin_xbdoc": "https://github.com/imsuperone/xbdoc",
        }
        found: List[Dict[str, Any]] = []
        seen = set()

        # 1. 优先从 AstrBot 运行时 Context 获取已装载的所有插件 (Stars)
        if self.context and hasattr(self.context, "get_all_stars"):
            try:
                stars = self.context.get_all_stars()
                if stars and isinstance(stars, (list, tuple)):
                    for s in stars:
                        name = str(getattr(s, "name", "") or "").strip()
                        if not name:
                            continue
                        norm = normalize_plugin_name(name)
                        if norm in seen or name in seen:
                            continue
                        seen.add(norm)
                        seen.add(name)
                        ver = str(getattr(s, "version", "") or "未知").strip()
                        repo = str(getattr(s, "repo", "") or "").strip() or KNOWN_REPOS.get(norm, "")
                        disp = str(getattr(s, "display_name", "") or "").strip()
                        root_dir = str(getattr(s, "root_dir_name", "") or name).strip()
                        found.append({
                            "name": name,
                            "version": ver,
                            "repo": repo,
                            "display_name": get_plugin_display_name({"name": name, "display_name": disp}),
                            "root_dir_name": root_dir,
                            "source": "runtime_star",
                        })
            except Exception:
                pass

        # 2. 备选扫描多级文件系统目录
        _plugins_container = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
        astrbot_plugins_dir = ""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            astrbot_plugins_dir = os.path.join(get_astrbot_data_path(), "plugins")
        except Exception:
            pass

        search_bases = []
        for b in (_plugins_container, astrbot_plugins_dir, "data/plugins", "plugins", ".", ".."):
            if b and os.path.isdir(b) and b not in search_bases:
                search_bases.append(b)

        for base in search_bases:
            try:
                for item in os.listdir(base):
                    p_dir = os.path.join(base, item)
                    if not os.path.isdir(p_dir):
                        continue
                    meta_path = os.path.join(p_dir, "metadata.yaml")
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                text = f.read()
                            name = extract_metadata_field(text, "name") or item
                            norm = normalize_plugin_name(name)
                            if norm in seen or name in seen:
                                continue
                            seen.add(norm)
                            seen.add(name)
                            ver = extract_metadata_field(text, "version") or "未知"
                            repo = extract_metadata_field(text, "repo") or KNOWN_REPOS.get(norm, "")
                            disp = extract_metadata_field(text, "display_name") or get_plugin_display_name({"name": name}, p_dir)
                            found.append({
                                "name": name,
                                "version": ver,
                                "repo": repo,
                                "display_name": disp,
                                "root_dir_name": item,
                                "source": "filesystem",
                            })
                        except Exception:
                            pass
            except Exception:
                pass

        return found

    def find_plugin_local_dir(self, p: Dict[str, Any]) -> str:
        """多级探测插件本地代码所在目录（含 AstrBot 部署根目录、父级容器与备用目录）。"""
        name = str(p.get("name") or "").strip()
        norm_name = normalize_plugin_name(name)
        install_src = p.get("install_source") or {}
        root_dir = str(install_src.get("root_dir_name") or p.get("root_dir_name") or name).strip()

        _plugins_container = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
        astrbot_plugins_dir = ""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            astrbot_plugins_dir = os.path.join(get_astrbot_data_path(), "plugins")
        except Exception:
            pass

        search_bases = []
        for b in (_plugins_container, astrbot_plugins_dir, "data/plugins", "plugins", ".", "..", "/AstrBot/data/plugins"):
            if b and os.path.isdir(b) and b not in search_bases:
                search_bases.append(b)

        for base in search_bases:
            for cand in (root_dir, name, norm_name, name.replace("astrbot_plugin_", "")):
                if not cand:
                    continue
                c_path = os.path.join(base, cand)
                if os.path.isdir(c_path) and os.path.isfile(os.path.join(c_path, "metadata.yaml")):
                    return c_path
        return ""

    def fetch_installed_plugins_sync(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """查询 AstrBot 当前已安装的所有插件。未配置密钥或请求异常时自动回退本地扫描。"""
        local_plugins = self.scan_local_plugins_sync()
        if not self.api_key:
            return True, local_plugins, ""

        url = f"{self.api_url}/api/v1/plugins"
        status, data = _http_request(url, headers=self._auth_headers(), timeout=8)
        if status != 200 or not data:
            return True, local_plugins, ""

        raw_list = []
        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict):
            if isinstance(data.get("data"), list):
                raw_list = data["data"]
            elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("plugins"), list):
                raw_list = data["data"]["plugins"]
            elif isinstance(data.get("plugins"), list):
                raw_list = data["plugins"]

        if not raw_list:
            return True, local_plugins, ""

        normalized: List[Dict[str, Any]] = []
        seen = set()
        for p in raw_list:
            if not isinstance(p, dict):
                continue
            meta = p.get("info") or p.get("metadata") or {}
            install_src = p.get("install_source") or {}
            name = str(p.get("name") or meta.get("name") or "").strip()
            if not name:
                continue
            norm = normalize_plugin_name(name)
            seen.add(norm)
            seen.add(name)
            ver = str(p.get("version") or meta.get("version") or "未知").strip()
            repo = str(p.get("repo") or meta.get("repo") or install_src.get("repo") or "").strip()
            disp = str(p.get("display_name") or meta.get("display_name") or "").strip()
            root_dir = str(install_src.get("root_dir_name") or p.get("root_dir_name") or name).strip()
            normalized.append({
                "name": name,
                "version": ver,
                "repo": repo,
                "display_name": disp or get_plugin_display_name({"name": name, "display_name": disp}),
                "root_dir_name": root_dir,
                "source": "openapi",
            })

        for lp in local_plugins:
            lp_name = lp.get("name") or ""
            lp_norm = normalize_plugin_name(lp_name)
            if lp_name not in seen and lp_norm not in seen:
                seen.add(lp_name)
                seen.add(lp_norm)
                normalized.append(lp)

        return True, normalized, ""

    async def fetch_installed_plugins(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        return await asyncio.to_thread(self.fetch_installed_plugins_sync)

    def check_updates_sync(self, selected_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """检查目标插件的更新状态。支持 AstrBot 市场插件与内置插件多维度检测。"""
        if not self.enabled:
            return {
                "ok": False,
                "error": "插件一键更新功能未开启。请在 XBNEXT WebUI「功能开关」中启用。",
            }

        ok, installed, err = self.fetch_installed_plugins_sync()
        if not ok or not installed:
            installed = self.scan_local_plugins_sync()

        market_index = self.fetch_market_registry_sync()

        raw_targets = selected_names if selected_names is not None else self.target_plugins
        targets = {normalize_plugin_name(x) for x in raw_targets if x}
        raw_targets_set = {str(x).strip() for x in raw_targets if str(x).strip()}
        results = []
        has_new_version = False

        for p in installed:
            name = str(p.get("name") or "").strip()
            norm_name = normalize_plugin_name(name)
            is_target = (name in targets or norm_name in targets or name in raw_targets_set)
            if not is_target:
                if not (self.notify_third_party and selected_names is None):
                    continue
                install_src = p.get("install_source") or {}
                repo_check = p.get("repo") or install_src.get("repo") or ""
                is_in_market = bool(
                    market_index and (
                        name in market_index
                        or norm_name in market_index
                        or name.replace("astrbot_plugin_", "") in market_index
                    )
                )
                if not repo_check and not is_in_market:
                    continue

            install_src = p.get("install_source") or {}
            repo_url = p.get("repo") or install_src.get("repo") or ""

            # 探测本地目录（包含同级目录、插件根目录等）
            local_dir = self.find_plugin_local_dir(p)

            local_ver = _get_local_version(p, local_dir)
            remote_ver, repo_url = _query_remote_version(repo_url, p, market_index=market_index)

            m_item = None
            if market_index:
                m_item = market_index.get(name) or market_index.get(norm_name)
                if not m_item and name.startswith("astrbot_plugin_"):
                    m_item = market_index.get(name.replace("astrbot_plugin_", ""))
            is_market_plugin = bool(m_item)
            if m_item and not p.get("display_name"):
                p["display_name"] = m_item.get("display_name")

            nick = get_plugin_display_name(p, local_dir)

            status_text = "已是最新版本"
            is_update_available = False

            if remote_ver and local_ver and local_ver != "未知":
                cmp_res = compare_versions(local_ver, remote_ver)
                if cmp_res < 0:
                    is_update_available = True
                    has_new_version = True
                    market_tag = " [市场]" if is_market_plugin else ""
                    status_text = f"可更新{market_tag} (本地: {format_version(local_ver)} → 远端: {format_version(remote_ver)})"
                elif cmp_res == 0:
                    status_text = f"最新 (本地: {format_version(local_ver)} | 远端: {format_version(remote_ver)})"
                else:
                    status_text = f"最新 (本地: {format_version(local_ver)} | 远端: {format_version(remote_ver)})"
            elif remote_ver:
                status_text = f"远端: {format_version(remote_ver)} (本地未知)"
            elif local_ver and local_ver != "未知":
                status_text = f"本地: {format_version(local_ver)} (远端版本未检出)"
            else:
                status_text = "未知版本"

            results.append({
                "name": name,
                "nickname": nick,
                "version": local_ver,
                "local_version": local_ver,
                "remote_version": remote_ver,
                "repo": repo_url,
                "is_market": is_market_plugin,
                "has_update": is_update_available,
                "status_text": status_text,
            })

        return {
            "ok": True,
            "has_new_version": has_new_version,
            "plugins": results,
        }

    async def check_updates(self, selected_names: Optional[List[str]] = None) -> Dict[str, Any]:
        return await asyncio.to_thread(self.check_updates_sync, selected_names)

    def _request_reload(self, target_name: str) -> Tuple[int, Any]:
        """自适应热重载单个插件，兼容进程内 StarManager 与不同 AstrBot 版本的 OpenAPI 端点规范。"""
        name_clean = str(target_name).strip()

        # 1. 优先尝试进程内 context / star_manager 直接重载（最快且零网络依赖、零 422 风险）
        if self.context:
            try:
                star_mgr = getattr(self.context, "_star_manager", None) or getattr(self.context, "star_manager", None)
                if star_mgr:
                    for method_name in ("reload", "reload_star", "reload_plugin"):
                        fn = getattr(star_mgr, method_name, None)
                        if callable(fn):
                            import inspect
                            sig = inspect.signature(fn)
                            if len(sig.parameters) >= 1:
                                res = fn(name_clean)
                            else:
                                res = fn()
                            if inspect.isawaitable(res):
                                if self._loop and self._loop.is_running():
                                    fut = asyncio.run_coroutine_threadsafe(res, self._loop)
                                    try:
                                        r = fut.result(timeout=25)
                                        if isinstance(r, tuple) and len(r) >= 2 and not r[0]:
                                            return 500, {"error": str(r[1])}
                                    except Exception as exc:
                                        return 500, {"error": str(exc)}
                                else:
                                    try:
                                        asyncio.run(res)
                                    except Exception:
                                        pass
                            return 200, {"ok": True, "source": "context.star_manager"}
            except Exception:
                pass

        # 2. HTTP OpenAPI 多端点自适应重载
        payload_name = json.dumps({"name": name_clean}).encode("utf-8")
        payload_both = json.dumps({"name": name_clean, "plugin_name": name_clean}).encode("utf-8")
        attempts = [
            # Dashboard 插件重载核心端点（带 JSON body，最稳合规）
            (f"{self.api_url}/api/plugin/reload", payload_name, True),
            (f"{self.api_url}/api/plugin/reload", payload_both, True),
            # 专有插件重载端点 /api/v1/plugins/{name}/reload
            (f"{self.api_url}/api/v1/plugins/{name_clean}/reload", None, False),
            (f"{self.api_url}/api/v1/plugins/{name_clean}/reload", b"{}", True),
            # 标准全局重载端点 /api/v1/plugins/reload
            (f"{self.api_url}/api/v1/plugins/reload", payload_name, True),
            (f"{self.api_url}/api/v1/plugins/reload", payload_both, True),
            (f"{self.api_url}/api/v1/plugins/reload", None, False),
            (f"{self.api_url}/api/v1/plugins/reload", b"{}", True),
            # Query 传参 /api/v1/plugins/reload?name={name}
            (f"{self.api_url}/api/v1/plugins/reload?name={name_clean}", None, False),
            (f"{self.api_url}/api/v1/plugins/reload?plugin_name={name_clean}", None, False),
            # Dashboard 根级端点无参兜底
            (f"{self.api_url}/api/plugin/reload", None, False),
            # 失败插件重载端点
            (f"{self.api_url}/api/v1/plugins/failed/{name_clean}/reload", None, False),
        ]
        last_status = 0
        last_resp = None
        for url, post_data, is_json in attempts:
            status, resp = _http_request(
                url,
                method="POST",
                headers=self._auth_headers(json_body=is_json),
                data=post_data,
                timeout=30,
            )
            if status == 200:
                return status, resp
            last_status = status
            last_resp = resp

        return last_status, last_resp

    def _request_astrot_update_api(self, name: str, timeout: int = 40) -> Tuple[int, Any]:
        """请求 AstrBot OpenAPI / Dashboard 插件更新接口。"""
        if not self.api_key:
            return 0, None
        update_payload = json.dumps({"name": name, "plugin_name": name}).encode("utf-8")
        attempts = [
            (f"{self.api_url}/api/v1/plugins/update", update_payload, True),
            (f"{self.api_url}/api/plugin/update", json.dumps({"plugin_name": name, "name": name, "proxy": ""}).encode("utf-8"), True),
            (f"{self.api_url}/api/v1/plugins/{name}/update", update_payload, True),
            (f"{self.api_url}/api/plugin/update", json.dumps({"plugin_name": name}).encode("utf-8"), True),
            (f"{self.api_url}/api/v1/plugins/update?name={name}", None, False),
        ]
        last_s = 0
        last_r = None
        for url, data, is_json in attempts:
            status, resp = _http_request(
                url, method="POST",
                headers=self._auth_headers(json_body=is_json),
                data=data, timeout=timeout
            )
            if status == 200:
                if isinstance(resp, dict) and (resp.get("status") == "error" or resp.get("ok") is False):
                    last_s = 200
                    last_r = resp
                    continue
                return status, resp
            if status != 0:
                last_s = status
                last_r = resp
        return last_s, last_r

    def apply_updates_sync(self, selected_names: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
        """执行一键更新与热重载。支持 Git 源码拉取、GitHub 归档包与 AstrBot OpenAPI 多阶自适应。"""
        if not self.enabled:
            return {
                "ok": False,
                "error": "插件一键更新功能未开启。请在 XBNEXT WebUI「功能开关」中启用。",
            }

        ok, installed, err = self.fetch_installed_plugins_sync()
        if not ok:
            return {"ok": False, "error": err}

        raw_targets = selected_names if selected_names is not None else self.target_plugins
        # 强制更新且未显式指定目标时，只重拉本插件：避免把同群其他插件一起
        # reset/重载，也避免命令执行中把当前插件自己热重载掉。
        if force and selected_names is None:
            raw_targets = ["astrbot_plugin_xbnext"]
        targets = {normalize_plugin_name(x) for x in raw_targets if x}
        raw_targets_set = {str(x).strip() for x in raw_targets if str(x).strip()}

        # 非强制模式且未指定具体插件时，先检查是否有新版本
        if not force and not selected_names:
            chk = self.check_updates_sync()
            if chk.get("ok"):
                need_update = {p["name"] for p in chk.get("plugins", []) if p.get("has_update")}
                if not need_update:
                    return {
                        "ok": True,
                        "no_updates": True,
                        "results": [],
                        "message": "所有选定插件均已是最新版本，无需更新。\n💡 若需重新强制拉取最新代码，请发送【/xbnext 强制更新】。",
                    }
                targets = need_update

        results = []

        for p in installed:
            name = p.get("name") or ""
            norm_name = normalize_plugin_name(name)
            if name not in targets and norm_name not in targets and name not in raw_targets_set:
                continue

            nick = get_plugin_display_name(p)
            local_dir = self.find_plugin_local_dir(p)
            before_ver = _get_local_version(p, local_dir)
            head_before = _git_head_sha(local_dir)

            update_ok = False
            update_details = []

            # 1. 优先尝试本地 Git 真实拉取 (直接在 .git 目录执行，最精准从 GitHub 仓库拉取最新 commit)
            if local_dir and os.path.isdir(os.path.join(local_dir, ".git")):
                git_ok, git_msg = _git_pull_plugin(local_dir, force=force)
                if git_ok:
                    update_ok = True
                    update_details.append(f"Git: {git_msg}")
                else:
                    update_details.append(f"Git失败: {git_msg}")

            # 2. 若无 .git 或 Git 失败，尝试直接从 GitHub 归档包 (zip) 下载覆盖
            if not update_ok and local_dir:
                repo_url = p.get("repo") or KNOWN_REPOS.get(norm_name) or ""
                if repo_url and "github.com" in repo_url:
                    zip_ok, zip_msg = _download_github_archive(repo_url, local_dir)
                    if zip_ok:
                        update_ok = True
                        update_details.append(f"GitHub归档: {zip_msg}")
                    else:
                        update_details.append(f"GitHub归档失败: {zip_msg}")

            # 3. 联动 AstrBot OpenAPI / Dashboard 核心更新接口
            u_status, u_resp = self._request_astrot_update_api(name)
            if u_status == 200:
                if isinstance(u_resp, dict) and (u_resp.get("status") == "error" or u_resp.get("ok") is False):
                    update_details.append(f"API: {_extract_err_msg(u_resp)}")
                else:
                    update_ok = True
                    update_details.append("OpenAPI更新已触发")
            elif u_status != 0:
                update_details.append(f"API失败({u_status}): {_extract_err_msg(u_resp)}")

            # 4. 若上述更新方式均未成功且无本地目录可操作
            if not update_ok:
                err_summary = "; ".join(update_details) or "未找到可用更新源或本地目录"
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": False,
                    "stage": "update",
                    "message": f"更新失败: {err_summary}",
                })
                continue

            # 5. 判定是否真的产生了变更：Git HEAD 未动且版本未变 -> 已是最新。
            #    典型场景：多 Bot 同时收到同一条更新指令，另一个实例已先拉完，
            #    这里 pull 返回 "Already up to date"，不应再谎报「更新成功」。
            head_after = _git_head_sha(local_dir)
            after_ver = _get_local_version(p, local_dir)
            version_changed = bool(
                before_ver and before_ver != "未知"
                and after_ver and after_ver != "未知"
                and before_ver != after_ver
            )
            git_changed = bool(head_before and head_after and head_before != head_after)
            if not force and not (version_changed or git_changed):
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": True,
                    "stage": "no_change",
                    "no_change": True,
                    "message": "已是最新版本，无需更新",
                    "before_version": before_ver,
                    "after_version": after_ver,
                })
                continue

            # 6. 执行热重载：自适应适配不同 AstrBot 版本的重载接口。
            #    更新自身时先不重载：等指令回复发出去后，由 apply_updates 延迟重载，
            #    否则命令执行到一半模块被换掉，回复可能丢失。
            if name == "astrbot_plugin_xbnext" and self._loop is not None:
                msg = "文件已更新，将在回复后自动重载"
                if after_ver and after_ver != "未知":
                    msg = f"文件已更新至 {format_version(after_ver)}，将在回复后自动重载"
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": True,
                    "stage": "deferred_reload",
                    "deferred_reload": True,
                    "message": msg,
                    "before_version": before_ver,
                    "after_version": after_ver,
                })
                continue

            r_status, r_resp = self._request_reload(name)

            if r_status != 200:
                err_msg = _extract_err_msg(r_resp)
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": False,
                    "stage": "reload",
                    "message": f"重载失败 ({r_status}): {err_msg}",
                })
                continue

            # 7. 版本真实比对与状态汇报（杜绝假更新假汇报）
            if before_ver and after_ver and before_ver != "未知" and after_ver != "未知" and before_ver != after_ver:
                msg = f"更新并重载成功 ({format_version(before_ver)} → {format_version(after_ver)})"
            elif after_ver and after_ver != "未知":
                msg = f"更新并重载成功 ({format_version(after_ver)})"
            else:
                msg = "更新并重载成功"

            results.append({
                "name": name,
                "nickname": nick,
                "ok": True,
                "stage": "done",
                "message": msg,
                "before_version": before_ver,
                "after_version": after_ver,
            })

        # 非强制模式：把「没有任何变更」的插件从结果里剔除，聊天端/WebUI
        # 只展示真正更新的与失败的；全部没变则收敛成一条结论。
        if not force:
            results = [r for r in results if not r.get("no_change")]
            if not results:
                return {
                    "ok": True,
                    "no_updates": True,
                    "results": [],
                    "message": "所有选定插件均已是最新版本，无需更新。\n💡 若需重新强制拉取最新代码，请发送【/xbnext 强制更新】。",
                }

        return {"ok": True, "results": results}

    async def apply_updates(self, selected_names: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
        try:
            self._loop = asyncio.get_running_loop()
        except Exception:
            pass
        res = await asyncio.to_thread(self.apply_updates_sync, selected_names, force)
        # 自身更新是延迟重载的：给回复留出时间，再真正热重载。
        try:
            if any(r.get("deferred_reload") for r in (res.get("results") or [])):
                asyncio.create_task(self._delayed_self_reload())
        except Exception:
            pass
        return res

    async def _delayed_self_reload(self, delay: float = 2.0) -> None:
        """延迟重载本插件，确保指令回复已经发出。"""
        await asyncio.sleep(delay)
        await asyncio.to_thread(self._request_reload, "astrbot_plugin_xbnext")
