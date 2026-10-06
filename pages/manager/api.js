/* ==========================================================================
   XBNEXT 控制台 · 网络层
   bridge 优先 + 多前缀 HTTP 回退（对齐 xbimg/pages/manager/api.js 的探测模式）：
     1) 同一 tab 会话缓存前缀 → 后续请求零探测开销
     2) 并发去重 → 多处同时调用只发一次探测
     3) 逐个试探 /ping → 命中即采用
   ========================================================================== */
(function () {
  "use strict";

  var PLUGIN = "astrbot_plugin_xbnext";
  var CANDIDATE_PREFIXES = [
    "",
    "/api/plugins/" + PLUGIN,
    "/api/plugins/" + PLUGIN.toLowerCase()
  ];
  var PING_PATH = "/xbnext/ping";
  var REQUEST_TIMEOUT_MS = 20000;

  /** 已探测成功的前缀（同一 tab 内复用） */
  var sessionWindow = typeof window !== "undefined" ? window : {};
  /** 并发去重：同一时刻只允许一个探测在跑 */
  var pendingProbe = null;

  function getBridge() {
    if (typeof window === "undefined") return null;
    var bridge = window.astrbotPluginBridge;
    return bridge && typeof bridge.request === "function" ? bridge : null;
  }

  /** bridge 请求（带超时兜底，AstrBot 的 HTTP 库不保证可中断） */
  function bridgeRequest(path, body) {
    var bridge = getBridge();
    if (!bridge) return Promise.reject(new Error("bridge unavailable"));
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        reject(new Error("bridge request timeout"));
      }, REQUEST_TIMEOUT_MS);
      Promise.resolve(
        body === undefined ? bridge.request(path) : bridge.request(path, body)
      ).then(
        function (value) { clearTimeout(timer); resolve(value); },
        function (err) { clearTimeout(timer); reject(err); }
      );
    });
  }

  /** 用某个前缀探活 */
  function probePrefix(prefix) {
    return fetch(prefix + PING_PATH, { method: "GET", cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        return data && data.status === "ok" ? prefix : null;
      })
      .catch(function () { return null; });
  }

  /** 解析接口前缀：bridge 直连 → 已缓存 → 并发探测 HTTP 前缀 */
  function resolvePrefix() {
    if (sessionWindow.__xbnextPrefix !== undefined) {
      return Promise.resolve(sessionWindow.__xbnextPrefix);
    }
    if (getBridge()) {
      // bridge 场景前缀为空串，直接短路
      sessionWindow.__xbnextPrefix = "";
      return Promise.resolve("");
    }
    if (sessionWindow.__xbnextPrefixPromise) {
      return sessionWindow.__xbnextPrefixPromise;
    }
    sessionWindow.__xbnextPrefixPromise = Promise.all(
      CANDIDATE_PREFIXES.map(probePrefix)
    ).then(function (results) {
      var hit = "";
      for (var i = 0; i < results.length; i++) {
        if (results[i] !== null) { hit = results[i]; break; }
      }
      sessionWindow.__xbnextPrefix = hit;
      sessionWindow.__xbnextPrefixPromise = null;
      return hit;
    });
    return sessionWindow.__xbnextPrefixPromise;
  }

  /** 统一 GET：bridge 与 HTTP 两种响应形态归一到 data */
  function get(path) {
    return resolvePrefix().then(function (prefix) {
      if (getBridge()) {
        return bridgeRequest(prefix + path).then(function (res) {
          if (!res || res.status !== "ok") {
            throw new Error((res && res.message) || "请求失败");
          }
          return res.data;
        });
      }
      return fetch(prefix + path, { method: "GET", cache: "no-store" }).then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
    });
  }

  /** 统一 POST JSON */
  function post(path, body) {
    return resolvePrefix().then(function (prefix) {
      if (getBridge()) {
        return bridgeRequest(prefix + path, { body: body || {} }).then(function (res) {
          if (!res || res.status !== "ok") {
            throw new Error((res && res.message) || "保存失败");
          }
          return res.data;
        });
      }
      return fetch(prefix + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {})
      }).then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
    });
  }

  window.XBNextAPI = {
    PLUGIN: PLUGIN,
    getBridge: getBridge,
    resolvePrefix: resolvePrefix,
    get: get,
    post: post
  };
})();
