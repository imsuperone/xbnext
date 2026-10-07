/* =============================================================================
 * XBNEXT WebUI 网络层
 *
 * 与母版（xbimg / xbbot_beta）同一套契约：
 *   1. 优先走 Plugin Page Bridge：window.AstrBotPluginPage → window.bridge
 *      → window.parent.AstrBotPluginPage → window.parent.bridge（都要求有 apiGet）
 *   2. 没有 bridge 时回退 fetch，并在 API_PREFIXES 里逐个试前缀
 *   3. 404 = 前缀没对上，换下一个；**其余非 2xx 不换前缀也不重放**
 *      （请求已被路由处理过，重放对 POST 尤其危险）
 *   4. GET 命中的前缀缓存起来，后续 POST 直接用，避免写请求被试错
 *
 * 响应统一是 {ok:true, data} / {ok:false, error}，bridge 与 HTTP 同形状，
 * 所以只需 unwrap() 一次归一。
 * ========================================================================== */
(function (global) {
  "use strict";

  var PLUGIN_ID = "astrbot_plugin_xbnext";
  var BRIDGE_TIMEOUT_MS = 30000;
  var HTTP_TIMEOUT_MS = 25000;

  var API_PREFIXES = [
    "/api/plugins/" + PLUGIN_ID + "/",
    "/" + PLUGIN_ID + "/",
    "api/",
    "./api/",
    ""
  ];

  var _WORKING_PREFIX = null;
  var _PROBE_P = null;
  var _channel = "unknown";

  function getBridge() {
    if (global.AstrBotPluginPage && typeof global.AstrBotPluginPage.apiGet === "function") {
      return global.AstrBotPluginPage;
    }
    if (global.bridge && typeof global.bridge.apiGet === "function") {
      return global.bridge;
    }
    if (global.parent && global.parent.AstrBotPluginPage &&
        typeof global.parent.AstrBotPluginPage.apiGet === "function") {
      return global.parent.AstrBotPluginPage;
    }
    if (global.parent && global.parent.bridge &&
        typeof global.parent.bridge.apiGet === "function") {
      return global.parent.bridge;
    }
    return null;
  }

  function withTimeout(promise, ms, label) {
    if (!global.AbortController) return promise;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, ms);
    var guarded = (promise && typeof promise.then === "function") ? promise : Promise.resolve(promise);
    return guarded.then(
      function (v) { clearTimeout(timer); return v; },
      function (e) {
        clearTimeout(timer);
        if (e && (e.name === "AbortError" || String(e.message || "").indexOf("aborted") >= 0)) {
          throw new Error("请求超时(" + Math.round(ms / 1000) + "s): " + label);
        }
        throw e;
      }
    );
  }

  function httpFetch(url, options) {
    var ctrl = ("AbortController" in global) ? new AbortController() : null;
    var timer = ctrl
      ? setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, HTTP_TIMEOUT_MS)
      : null;
    var opts = Object.assign({}, options || {});
    if (ctrl) opts.signal = ctrl.signal;
    return fetch(url, opts).then(function (res) {
      if (timer) clearTimeout(timer);
      return res.text().then(function (text) {
        var body = null;
        try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
        return { status: res.status, body: body, text: text };
      });
    }, function (err) {
      if (timer) clearTimeout(timer);
      throw err;
    });
  }

  /** 404 = 这个前缀没接上，可换下一个；其余状态码说明请求已被处理。 */
  function isPrefixMiss(status) {
    return status === 404;
  }

  function buildUrl(prefix, endpoint, params) {
    var ep = String(endpoint || "").replace(/^\/+/, "");
    var url = prefix + ep;
    var search = "";
    if (params && typeof params === "object") {
      var usp = new URLSearchParams();
      Object.keys(params).forEach(function (k) {
        var v = params[k];
        if (v !== undefined && v !== null && v !== "") usp.append(k, v);
      });
      search = usp.toString();
    }
    return search ? (url + (url.indexOf("?") >= 0 ? "&" : "?") + search) : url;
  }

  /** GET：从缓存前缀起步，404 时换下一个；成功后缓存命中的前缀。 */
  function fetchWithProbe(endpoint, params) {
    if (_WORKING_PREFIX !== null) {
      return httpFetch(buildUrl(_WORKING_PREFIX, endpoint, params), { method: "GET" })
        .then(function (r) {
          if (isPrefixMiss(r.status)) { _WORKING_PREFIX = null; return probeLoop(); }
          return finishHttp(r, endpoint);
        });
    }
    return probeLoop();

    function probeLoop() {
      var i = 0;
      function next() {
        if (i >= API_PREFIXES.length) {
          return Promise.reject(new Error("所有接口前缀均未命中: " + endpoint));
        }
        var prefix = API_PREFIXES[i++];
        return httpFetch(buildUrl(prefix, endpoint, params), { method: "GET" }).then(function (r) {
          if (isPrefixMiss(r.status)) return next();
          if (r.status >= 200 && r.status < 300) _WORKING_PREFIX = prefix;
          return finishHttp(r, endpoint);
        });
      }
      return next();
    }
  }

  /** POST：绝不逐前缀试错。没有已知前缀时先用 GET ping 探一次。 */
  function postWithProbe(endpoint, data) {
    var run = _WORKING_PREFIX !== null
      ? Promise.resolve(_WORKING_PREFIX)
      : fetchWithProbe("ping", null).then(function () { return _WORKING_PREFIX; });
    return run.then(function (prefix) {
      var url = buildUrl(prefix === null ? "" : prefix, endpoint, null);
      return httpFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data === undefined ? null : data)
      }).then(function (r) {
        if (isPrefixMiss(r.status)) {
          throw new Error("接口路径未命中（前缀=" + (prefix || "空") + "）: " + endpoint);
        }
        return finishHttp(r, endpoint);
      });
    });
  }

  function finishHttp(r, endpoint) {
    if (r.status < 200 || r.status >= 300) {
      var msg = (r.body && (r.body.error || r.body.message)) ||
                (r.text || "").slice(0, 160) || ("HTTP " + r.status);
      throw new Error("HTTP " + r.status + " " + endpoint + ": " + msg);
    }
    if (r.body === null) throw new Error("接口返回的不是 JSON: " + endpoint);
    _channel = "http";
    return unwrap(r.body);
  }

  function unwrap(res) {
    if (res && typeof res === "object" && "ok" in res) {
      if (!res.ok) throw new Error(res.error || "请求失败");
      return "data" in res ? res.data : res;
    }
    return res;
  }

  function getJson(endpoint, params) {
    var b = getBridge();
    if (!b) return fetchWithProbe(endpoint, params);
    var p;
    try { p = b.apiGet(endpoint, params || {}); }
    catch (e) { return fetchWithProbe(endpoint, params); }
    return withTimeout(Promise.resolve(p), BRIDGE_TIMEOUT_MS, endpoint).then(
      function (res) { _channel = "bridge"; return unwrap(res); },
      function () {
        // bridge 报错（含超时）→ GET 是幂等的，安全回退到 HTTP 前缀探测
        _channel = "http";
        return fetchWithProbe(endpoint, params);
      }
    );
  }

  function postJson(endpoint, data) {
    var b = getBridge();
    if (b && typeof b.apiPost === "function") {
      var p;
      try { p = b.apiPost(endpoint, data); }
      catch (e) { return Promise.reject(e); }
      _channel = "bridge";
      return withTimeout(Promise.resolve(p), BRIDGE_TIMEOUT_MS, endpoint).then(unwrap);
    }
    // 没有 bridge 或 bridge 只暴露了 apiGet → 走 HTTP（前缀先用 GET ping 探好）
    return postWithProbe(endpoint, data);
  }

  global.XbnextApi = {
    PLUGIN_ID: PLUGIN_ID,
    getBridge: getBridge,
    ping: function () { return getJson("ping", null); },
    state: function () { return getJson("state", null); },
    /** 写单个配置项：setting({key, value}) */
    setting: function (payload) { return postJson("setting", payload); },
    /** 列出全部用户档案：profiles() -> [{platform, uid, profile, updated}] */
    profiles: function () { return getJson("profiles", null); },
    /** 写一份档案：profileSave({platform, uid, name, facts})，字段全空即删除 */
    profileSave: function (payload) { return postJson("profile_save", payload); },
    /** 删一份档案：profileDelete({platform, uid}) */
    profileDelete: function (payload) { return postJson("profile_delete", payload); },
    /** 最近 10 轮提示词注入记录：injectLog() -> {items:[{ts,umo,actions,prompt,parts,images}]} */
    injectLog: function () { return getJson("inject_log", null); },
    channel: function () { return _channel; },
    prefix: function () { return _WORKING_PREFIX; }
  };
})(window);
