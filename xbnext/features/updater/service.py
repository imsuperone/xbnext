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
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TARGET_PLUGINS = [
    "astrbot_plugin_xbimg",
    "astrbot_plugin_xb2img",
    "astrbot_plugin_xbdoc",
    "astrbot_plugin_xbbot",
    "astrbot_plugin_xbbot_beta",
    "astrbot_plugin_xbnext",
]

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

    # 2. 主动探测标准路径及同级开发工作区
    if not disp:
        for b in ("data/plugins", "plugins", ".", ".."):
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

    if disp and disp != name:
        return f"{disp} ({name})"
    return disp or name


class _DynamicNicknames(dict):
    """动态插件名称字典代理：优先从 metadata.yaml 读取 display_name。"""
    def get(self, key, default=None):
        name = str(key or "").strip()
        if not name:
            return default if default is not None else ""
        disp = get_plugin_display_name({"name": name})
        if disp:
            return disp
        return super().get(key, default if default is not None else name)

    def __getitem__(self, key):
        v = self.get(key)
        if v:
            return v
        raise KeyError(key)


PLUGIN_NICKNAMES = _DynamicNicknames()


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
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


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


def extract_version_from_yaml(text: str) -> str:
    """从 metadata.yaml 文本中提取 version 字段。"""
    if not text:
        return ""
    m = re.search(r'(?:^|\n)\s*version:\s*["\']?([^"\'\s\n]+)', text)
    return m.group(1).strip() if m else ""


def format_version(v: str) -> str:
    """统一版本显示格式，如 v1.3.31 或 v26w1014b。"""
    s = str(v or "").strip().strip("'\"")
    if not s or s == "未知":
        return "未知"
    if s.lower().startswith("v"):
        return s
    return f"v{s}"


def parse_version_key(v: str) -> List[Any]:
    """解析版本号为分段权重列表，支持数字 semver 与快照字母版本。"""
    s = str(v or "").strip().lstrip("vV").strip("'\"")
    parts = re.findall(r"\d+|\D+", s)
    res = []
    for p in parts:
        if p.isdigit():
            res.append((0, int(p)))
        else:
            res.append((1, p.lower()))
    return res


def compare_versions(v1: str, v2: str) -> int:
    """比较两个版本号。v1 < v2 返回 -1；v1 == v2 返回 0；v1 > v2 返回 1。"""
    s1 = str(v1 or "").strip().lstrip("vV").strip("'\"")
    s2 = str(v2 or "").strip().lstrip("vV").strip("'\"")
    if s1 == s2:
        return 0
    k1 = parse_version_key(s1)
    k2 = parse_version_key(s2)
    if k1 < k2:
        return -1
    elif k1 > k2:
        return 1
    return 0


def _get_local_version(p: Dict[str, Any], local_dir: str) -> str:
    """获取本地插件版本，优先从 OpenAPI 插件信息读取，次选本地 metadata.yaml。"""
    v = str(p.get("version") or "").strip()
    if v and v != "未知":
        return v
    if local_dir and os.path.isdir(local_dir):
        meta_file = os.path.join(local_dir, "metadata.yaml")
        if os.path.isfile(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    v_meta = extract_version_from_yaml(f.read())
                    if v_meta:
                        return v_meta
            except Exception:
                pass
    return v or "未知"


def _query_remote_version(repo_url: str, p: Dict[str, Any]) -> str:
    """根据 github 仓库地址或 online_version 获取远程最新版本号。"""
    online_ver = str(p.get("online_version") or "").strip()
    if online_ver:
        return online_ver

    if not repo_url or "github.com" not in repo_url:
        return ""
    match = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repo_url)
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater",
        "Accept": "application/vnd.github.v3.raw",
    }

    # 1. 优先 GitHub Contents API 直出 raw YAML
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/metadata.yaml"
    status, body = _http_request(api_url, headers=headers, timeout=6)
    if status == 200 and isinstance(body, str):
        v = extract_version_from_yaml(body)
        if v:
            return v

    # 2. 备选 raw.githubusercontent.com / jsdelivr
    fallback_urls = [
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/metadata.yaml",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/metadata.yaml",
        f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@main/metadata.yaml",
    ]
    for raw_url in fallback_urls:
        status, body = _http_request(raw_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if status == 200 and isinstance(body, str):
            v = extract_version_from_yaml(body)
            if v:
                return v

    return ""


class UpdaterService:
    """管理插件检查更新与一键升级逻辑。"""

    def __init__(self, conf: Any):
        self.conf = conf

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

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def fetch_installed_plugins_sync(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        """通过 OpenAPI 查询 AstrBot 当前已安装的所有插件。"""
        if not self.api_key:
            return False, [], "未配置 OpenAPI 密钥（需前往 WebUI 填写并保存）。"
        url = f"{self.api_url}/api/v1/plugins"
        status, data = _http_request(url, headers=self._auth_headers(), timeout=10)
        if status != 200:
            err_msg = data.get("message") if isinstance(data, dict) else str(data)
            return False, [], f"OpenAPI 请求失败 (HTTP {status}): {err_msg}"
        if not isinstance(data, dict) or "data" not in data:
            return False, [], "OpenAPI 返回结构异常"
        return True, data.get("data") or [], ""

    async def fetch_installed_plugins(self) -> Tuple[bool, List[Dict[str, Any]], str]:
        return await asyncio.to_thread(self.fetch_installed_plugins_sync)

    def check_updates_sync(self, selected_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """检查目标插件的更新状态。"""
        if not self.enabled:
            return {
                "ok": False,
                "error": "插件一键更新功能未开启。请在 XBNEXT WebUI「功能开关」中启用并配置 OpenAPI 密钥。",
            }
        if not self.api_key:
            return {
                "ok": False,
                "error": "未配置 OpenAPI 密钥。请在 XBNEXT WebUI「功能开关」中填入 API Key。",
            }

        ok, installed, err = self.fetch_installed_plugins_sync()
        if not ok:
            return {"ok": False, "error": err}

        raw_targets = selected_names if selected_names is not None else self.target_plugins
        targets = {normalize_plugin_name(x) for x in raw_targets if x}
        results = []
        has_new_version = False

        for p in installed:
            name = p.get("name") or ""
            norm_name = normalize_plugin_name(name)
            if name not in targets and norm_name not in targets:
                continue

            ver = str(p.get("version") or "未知")
            install_src = p.get("install_source") or {}
            repo_url = p.get("repo") or install_src.get("repo") or ""
            root_dir = install_src.get("root_dir_name") or name

            # 探测本地目录（通常在 data/plugins/<root_dir>）
            local_dir = ""
            for base_dir in ("data/plugins", "plugins", ".", ".."):
                for cand in (root_dir, name, norm_name):
                    c_path = os.path.join(base_dir, cand)
                    if os.path.isdir(c_path):
                        local_dir = c_path
                        break
                if local_dir:
                    break

            nick = get_plugin_display_name(p, local_dir)
            local_ver = _get_local_version(p, local_dir)
            remote_ver = _query_remote_version(repo_url, p)

            status_text = "已是最新版本"
            is_update_available = False

            if remote_ver and local_ver and local_ver != "未知":
                cmp_res = compare_versions(local_ver, remote_ver)
                if cmp_res < 0:
                    is_update_available = True
                    has_new_version = True
                    status_text = f"{format_version(local_ver)} → {format_version(remote_ver)}"
                elif cmp_res == 0:
                    status_text = f"最新 ({format_version(local_ver)})"
                else:
                    status_text = f"较新 ({format_version(local_ver)})"
            elif remote_ver:
                status_text = f"云端 {format_version(remote_ver)}"
            elif local_ver:
                status_text = f"本地 {format_version(local_ver)}"
            else:
                status_text = "未知版本"

            results.append({
                "name": name,
                "nickname": nick,
                "version": local_ver,
                "local_version": local_ver,
                "remote_version": remote_ver,
                "repo": repo_url,
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

    def apply_updates_sync(self, selected_names: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
        """执行一键更新与热重载。force=True 时强制拉取并重载所有选定插件。"""
        if not self.enabled:
            return {
                "ok": False,
                "error": "插件一键更新功能未开启。请在 XBNEXT WebUI「功能开关」中启用并配置 OpenAPI 密钥。",
            }
        if not self.api_key:
            return {
                "ok": False,
                "error": "未配置 OpenAPI 密钥。请在 XBNEXT WebUI「功能开关」中填入 API Key。",
            }

        ok, installed, err = self.fetch_installed_plugins_sync()
        if not ok:
            return {"ok": False, "error": err}

        raw_targets = selected_names if selected_names is not None else self.target_plugins
        targets = {normalize_plugin_name(x) for x in raw_targets if x}
        
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
            if name not in targets and norm_name not in targets:
                continue

            nick = get_plugin_display_name(p)
            update_url = f"{self.api_url}/api/v1/plugins/{name}/update"
            reload_url = f"{self.api_url}/api/v1/plugins/{name}/reload"

            # 1. 执行更新
            u_status, u_resp = _http_request(
                update_url,
                method="POST",
                headers=self._auth_headers(),
                data=json.dumps({"name": name}).encode("utf-8"),
                timeout=45,
            )

            if u_status != 200:
                err_msg = u_resp.get("message") if isinstance(u_resp, dict) else str(u_resp)
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": False,
                    "stage": "update",
                    "message": f"更新失败 ({u_status}): {err_msg}",
                })
                continue

            # 2. 执行热重载
            r_status, r_resp = _http_request(
                reload_url,
                method="POST",
                headers=self._auth_headers(),
                data=b"",
                timeout=30,
            )

            if r_status != 200:
                err_msg = r_resp.get("message") if isinstance(r_resp, dict) else str(r_resp)
                results.append({
                    "name": name,
                    "nickname": nick,
                    "ok": False,
                    "stage": "reload",
                    "message": f"重载失败 ({r_status}): {err_msg}",
                })
                continue

            results.append({
                "name": name,
                "nickname": nick,
                "ok": True,
                "stage": "done",
                "message": "更新并重载成功",
            })

        return {"ok": True, "results": results}

    async def apply_updates(self, selected_names: Optional[List[str]] = None, force: bool = False) -> Dict[str, Any]:
        return await asyncio.to_thread(self.apply_updates_sync, selected_names, force)
