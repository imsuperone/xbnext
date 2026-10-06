/* ==========================================================================
   XBNEXT 控制台 · 页面逻辑
   职责：boot（探活 → 取状态 → 渲染 → 淡入）+ 分类 tab 切换。
   不做轮询、不做写操作 —— 开关在 AstrBot 原生配置页改，这里只读展示。
   ========================================================================== */
(function () {
  "use strict";

  var api = window.XBNextAPI;
  var state = null;

  function $(id) { return document.getElementById(id); }

  /* ------------------------------------------------------------ 连接态 */
  function setConn(status, detail) {
    var el = $("conn");
    if (!el) return;
    var text = { checking: "连接中…", online: "已连接", offline: "未连接" }[status] || status;
    el.textContent = detail ? text + " · " + detail : text;
    el.setAttribute("data-state", status);
  }

  /* ------------------------------------------------------------ 渲染 */
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderFeatures(features) {
    var host = $("feature-list");
    if (!host) return;
    var html = "";
    Object.keys(features).forEach(function (key) {
      var item = features[key] || {};
      var on = !!item.enabled;
      var conflict = item.conflict;
      html += '<article class="card">' +
        '<div class="card-title">' + esc(item.name || key) + "</div>" +
        '<p class="card-desc">' + esc(item.description || "暂无说明") + "</p>" +
        '<div class="card-meta">' +
          '<span class="tag ' + (on ? "on" : "off") + '">' + (on ? "已开启" : "已关闭") + "</span>" +
          '<span class="tag">' + esc(key) + "</span>" +
          (conflict ? '<span class="tag warn">让路：' + esc(conflict) + "</span>" : "") +
        "</div></article>";
    });
    host.innerHTML = html;
  }

  function renderRuntime(data) {
    var astrna = data.astrna || {};
    var line = $("astrna-line");
    if (line) {
      var detail = astrna.installed
        ? "已检测到 <b>" + esc(astrna.package || "") + "</b>" +
          (Object.keys(astrna.switches || {}).length
            ? "（" + esc(JSON.stringify(astrna.switches)) + "）" : "")
        : "未安装 —— XBNEXT 独立工作";
      line.innerHTML = detail;
    }
    var kv = $("kv-line");
    if (kv) {
      kv.innerHTML = data.kv_usable
        ? "<b>可用</b>（插件 KV，前缀 xbnext:）"
        : "<b>不可用</b> —— 回复索引与用户档案不会持久化";
    }
  }

  function render(data) {
    state = data || {};
    var version = $("version");
    if (version) version.textContent = state.version || "—";
    renderFeatures(state.features || {});
    renderRuntime(state);
    var view = $("view-status");
    if (view) view.hidden = false;
  }

  /* ------------------------------------------------------------ 交互 */
  function bindTabs() {
    var tabs = document.querySelectorAll(".cat-tab");
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener("click", function () {
        var cat = tab.getAttribute("data-cat");
        Array.prototype.forEach.call(tabs, function (t) {
          t.classList.toggle("active", t === tab);
        });
        ["features", "runtime"].forEach(function (name) {
          var panel = $("cat-" + name);
          if (panel) panel.hidden = name !== cat;
        });
      });
    });
  }

  function showError(message) {
    var box = $("error");
    if (!box) return;
    box.textContent = message;
    box.classList.add("show");
  }

  function markReady() {
    var loading = $("loading");
    if (loading) loading.remove();
    var app = $("app");
    if (app) app.classList.add("ready");
  }

  /* ------------------------------------------------------------ boot */
  function boot() {
    bindTabs();
    setConn("checking");
    if (!api) {
      showError("网络层未加载：请确认 api.js 在 app.js 之前引入。");
      markReady();
      return;
    }
    api.resolvePrefix()
      .then(function (prefix) {
        setConn("online", api.getBridge() ? "bridge" : "http");
        return api.get("/xbnext/state");
      })
      .then(function (data) {
        render(data);
        setConn("online", api.getBridge() ? "bridge" : "http");
      })
      .catch(function (err) {
        setConn("offline", err && err.message ? err.message : "");
        showError("加载运行状态失败：" + (err && err.message ? err.message : err) +
          "。请确认插件已启用，并检查浏览器控制台。");
      })
      .then(function () {
        markReady();
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
