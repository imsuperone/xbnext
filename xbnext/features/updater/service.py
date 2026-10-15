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
import subprocess
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

# 友好中文名映射
PLUGIN_NICKNAMES = {
    "astrbot_plugin_xbimg": "消息转图 (xbimg)",
    "astrbot_plugin_xb2img": "消息转图轻量 (xb2img)",
    "astrbot_plugin_xbdoc": "文档记忆 (xbdoc)",
    "astrbot_plugin_xbbot": "小白机器人 (xbbot)",
    "astrbot_plugin_xbbot_beta": "小白测试版 (xbbotbeta)",
    "astrbot_plugin_xbnext": "对话细节 (xbnext)",
}


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


def _get_local_commit(plugin_dir: str) -> str:
    """尝试获取本地插件目录的最新 git commit hash（前 7 位）。"""
    if not plugin_dir or not os.path.isdir(plugin_dir):
        return ""
    try:
        # 直接读取 .git/HEAD
        git_dir = os.path.join(plugin_dir, ".git")
        if os.path.isdir(git_dir):
            head_file = os.path.join(git_dir, "HEAD")
            if os.path.isfile(head_file):
                with open(head_file, "r", encoding="utf-8") as f:
                    head_ref = f.read().strip()
                if head_ref.startswith("ref:"):
                    ref_path = os.path.join(git_dir, head_ref[4:].strip().replace("/", os.sep))
                    if os.path.isfile(ref_path):
                        with open(ref_path, "r", encoding="utf-8") as f:
                            return f.read().strip()[:7]
                elif len(head_ref) >= 7:
                    return head_ref[:7]
        # 尝试调用 git rev-parse
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=plugin_dir,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()[:7]
    except Exception:
        pass
    return ""


def _query_remote_github_commit(repo_url: str) -> str:
    """根据 github 仓库地址获取 main / master 分支最新 commit hash（前 7 位）。"""
    if not repo_url or "github.com" not in repo_url:
        return ""
    # 提取 owner / repo
    match = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", repo_url)
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2)
    # 优先 GitHub API
    for branch in ("main", "master"):
        api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-Updater",
            "Accept": "application/vnd.github.v3+json",
        }
        status, data = _http_request(api_url, headers=headers, timeout=6)
        if status == 200 and isinstance(data, dict):
            sha = data.get("sha")
            if sha:
                return str(sha)[:7]
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
            nick = PLUGIN_NICKNAMES.get(name, PLUGIN_NICKNAMES.get(norm_name, name))
            install_src = p.get("install_source") or {}
            repo_url = p.get("repo") or install_src.get("repo") or ""
            root_dir = install_src.get("root_dir_name") or name

            # 探测本地目录（通常在 data/plugins/<root_dir>）
            local_dir = ""
            for base_dir in ("data/plugins", "plugins", "."):
                cand = os.path.join(base_dir, root_dir)
                if os.path.isdir(cand):
                    local_dir = cand
                    break

            local_sha = _get_local_commit(local_dir)
            remote_sha = _query_remote_github_commit(repo_url) if repo_url else ""

            status_text = "已是最新版本"
            is_update_available = False

            if remote_sha and local_sha:
                if remote_sha.lower() != local_sha.lower():
                    is_update_available = True
                    has_new_version = True
                    status_text = f"发现新版本 ({local_sha} → {remote_sha})"
                else:
                    status_text = f"已是最新 ({local_sha})"
            elif remote_sha:
                status_text = f"远程最新 ({remote_sha})"
            else:
                status_text = f"当前版本 {ver}"

            results.append({
                "name": name,
                "nickname": nick,
                "version": ver,
                "repo": repo_url,
                "local_sha": local_sha,
                "remote_sha": remote_sha,
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

            nick = PLUGIN_NICKNAMES.get(name, PLUGIN_NICKNAMES.get(norm_name, name))
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
