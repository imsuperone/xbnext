/* =============================================================================
 * XBNEXT 控制台
 *
 * 与母版（xbimg / xbdoc）同一套交互骨架：
 *   - 深浅色与主题色的**唯一真相源在服务端**（配置键 ui_theme_mode / ui_accent_color），
 *     页面不留本地副本；首帧被 data-boot 挂起，等 state 回来才揭开，避免"先默认后跳变"。
 *   - 分类页签 = category-tabs-bar，内容区 = settings-section.active。
 *   - 开关 / 分段控件 / 输入框直接写服务端配置（POST setting），失败自动回滚到旧值。
 * ========================================================================== */
(function () {
  "use strict";

  var API = window.XbnextApi;
  var $ = function (id) { return document.getElementById(id); };

  var CONFIG = {};
  var SCHEMA = {};
  var STATE = null;
  var accentSaveTimer = null;
  var booted = false;

  /* ========================================================================
   * Toast
   * ====================================================================== */
  function toast(message, kind) {
    var box = $("toastBox");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "m3-toast" + (kind === "bad" ? " badk" : (kind === "ok" ? " okk" : ""));
    var txt = document.createElement("span");
    txt.style.flex = "1 1 auto";
    txt.style.minWidth = "0";
    txt.style.whiteSpace = "pre-wrap";
    txt.style.overflowWrap = "anywhere";
    txt.textContent = String(message == null ? "" : message);
    el.appendChild(txt);
    box.appendChild(el);
    setTimeout(function () {
      el.classList.add("out");
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 320);
    }, 2800);
  }

  /* ========================================================================
   * 颜色工具（照抄母版：只染强调系，surface 保持主题原色）
   * ====================================================================== */
  function mixHex(a, b, t) {
    var pa = [1, 3, 5].map(function (i) { return parseInt(a.substr(i, 2), 16); });
    var pb = [1, 3, 5].map(function (i) { return parseInt(b.substr(i, 2), 16); });
    return "#" + pa.map(function (v, i) {
      return Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, "0");
    }).join("");
  }

  function relLum(hex) {
    var c = [1, 3, 5].map(function (i) {
      var v = parseInt(hex.substr(i, 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function contrastOk(fg, bg) {
    var l1 = relLum(fg), l2 = relLum(bg);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05) >= 3.0;
  }

  function isHex(v) { return /^#[0-9a-fA-F]{6}$/.test(String(v || "").trim()); }

  function applyAccent(raw) {
    var root = document.documentElement;
    var v = typeof raw === "string" ? raw.trim() : "";
    if (!isHex(v)) {
      ["--m3-sys-color-primary", "--m3-sys-color-primary-container",
       "--m3-seg-ink", "--m3-shadow-card"].forEach(function (k) {
        root.style.removeProperty(k);
      });
      return;
    }
    var dark = (root.getAttribute("data-theme") || "light") === "dark";
    var container = dark ? mixHex(v, "#1B2C42", 0.45) : mixHex(v, "#E4EAF2", 0.25);
    var onContainer = dark ? mixHex(v, "#FFFFFF", 0.72) : mixHex(v, "#000000", 0.72);
    root.style.setProperty("--m3-sys-color-primary", v);
    root.style.setProperty("--m3-sys-color-primary-container", container);
    root.style.setProperty("--m3-sys-color-on-primary-container", onContainer);
    // 选中分段的文字色要跟背景有对比，否则换了主题色就读不清
    var segInk = contrastOk(v, container) ? v : (dark ? "#E2E2E6" : "#191C20");
    root.style.setProperty("--m3-seg-ink", segInk);
  }

  function hexToHsv(hex) {
    var r = parseInt(hex.substr(1, 2), 16) / 255;
    var g = parseInt(hex.substr(3, 2), 16) / 255;
    var b = parseInt(hex.substr(5, 2), 16) / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    var h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h: h, s: max ? d / max : 0, v: max };
  }

  function hsvToHex(h, s, v) {
    h = ((h % 360) + 360) % 360;
    var c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    var rgb = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
            : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return "#" + rgb.map(function (n) {
      return Math.round((n + m) * 255).toString(16).padStart(2, "0");
    }).join("");
  }

  /* ========================================================================
   * 主题
   * ====================================================================== */
  var MOON = '<path d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z"/>';
  var SUN = '<path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13c0-.55.45-1 1-1h1c.55 0 1-.45 1-1s-.45-1-1-1H3c-1.66 0-3 1.34-3 3s1.34 3 3 3h1c.55 0 1-.45 1-1s-.45-1-1-1H3c-.55 0-1-.45-1-1zm19 0c0-.55-.45-1-1-1h-1c-.55 0-1-.45-1-1s.45-1 1-1h1c1.66 0 3 1.34 3 3s-1.34 3-3 3h-1c-.55 0-1-.45-1-1s.45-1 1-1h1c.55 0 1-.45 1-1zM12 19c-.55 0-1 .45-1 1v1c0 .55.45 1 1 1s1-.45 1-1v-1c0-.55-.45-1-1-1zm0-16c-.55 0-1 .45-1 1v1c0 .55.45 1 1 1s1-.45 1-1V4c0-.55-.45-1-1-1zm7.07 3.93c-.39-.39-1.02-.39-1.41 0s-.39 1.02 0 1.41l.71.71c.39.39 1.02.39 1.41 0s.39-1.02 0-1.41l-.71-.71zM5.64 18.36c-.39-.39-1.02-.39-1.41 0s-.39 1.02 0 1.41l.71.71c.39.39 1.02.39 1.41 0s.39-1.02 0-1.41l-.71-.71zm12.73.71c.39-.39.39-1.02 0-1.41s-1.02-.39-1.41 0l-.71.71c-.39.39-.39 1.02 0 1.41s1.02.39 1.41 0l.71-.71zM7.05 5.64c.39-.39.39-1.02 0-1.41s-1.02-.39-1.41 0l-.71.71c-.39.39-.39 1.02 0 1.41s1.02.39 1.41 0l.71-.71z"/>';

  function applyTheme(mode) {
    var next = mode === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    var icon = $("themeIcon");
    if (icon) icon.innerHTML = next === "dark" ? SUN : MOON;
    var btn = $("themeToggleBtn");
    if (btn) btn.title = next === "dark" ? "切换到浅色主题" : "切换到深色主题";
    // 主题色的派生依赖 data-theme，换主题后必须重染一次
    if (CONFIG.ui_accent_color) applyAccent(CONFIG.ui_accent_color);
  }

  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    var next = cur === "dark" ? "light" : "dark";
    applyTheme(next);
    writeConfig("ui_theme_mode", next, { quiet: true }).catch(function () {});
  }

  /* ========================================================================
   * 页签
   * ====================================================================== */
  function initTabs() {
    var tabs = document.querySelectorAll(".cat-tab[data-tab]");
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener("click", function () {
        Array.prototype.forEach.call(tabs, function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        var target = tab.getAttribute("data-tab");
        Array.prototype.forEach.call(
          document.querySelectorAll(".settings-section[data-section]"),
          function (sec) {
            sec.classList.toggle("active", sec.getAttribute("data-section") === target);
          }
        );
      });
    });
  }

  /* ========================================================================
   * 配置渲染与写入
   * ====================================================================== */
  function applyControl(key) {
    var sw = document.querySelector('input[type="checkbox"][data-key="' + key + '"]');
    if (sw) sw.checked = !!CONFIG[key];

    var seg = document.querySelector('.m3-segmented-control[data-key="' + key + '"]');
    if (seg) {
      Array.prototype.forEach.call(seg.querySelectorAll(".seg-item"), function (b) {
        b.classList.toggle("active", String(CONFIG[key]) === b.getAttribute("data-val"));
      });
    }

    var inputs = document.querySelectorAll('input.m3-input[data-key="' + key + '"]');
    Array.prototype.forEach.call(inputs, function (inp) {
      if (document.activeElement !== inp) {
        inp.value = CONFIG[key] == null ? "" : String(CONFIG[key]);
      }
    });
  }

  function applyConditions() {
    Object.keys(SCHEMA).forEach(function (key) {
      var cond = SCHEMA[key].condition;
      var ok = true;
      if (cond && typeof cond === "object") {
        Object.keys(cond).forEach(function (k) {
          if (CONFIG[k] !== cond[k]) ok = false;
        });
      }
      Array.prototype.forEach.call(
        document.querySelectorAll('[data-key="' + key + '"]'),
        function (el) {
          if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "text" || el.type === "number")) {
            el.disabled = !ok;
          }
          el.style.opacity = ok ? "" : "0.45";
          el.style.pointerEvents = ok ? "" : "none";
        }
      );
    });
  }

  function renderConfig() {
    Object.keys(CONFIG).forEach(applyControl);
    applyConditions();
  }

  /**
   * 写单个配置项：先乐观更新，失败回滚旧值。
   * 服务端落盘失败时会把内存值也回滚并返回 ok:false，所以前端同样回滚，
   * 保证"页面上看到的 = 实际存下来的"。
   */
  function writeConfig(key, value, opts) {
    opts = opts || {};
    var prev = (key in CONFIG) ? CONFIG[key] : undefined;
    CONFIG[key] = value;
    applyControl(key);
    applyConditions();

    var swLabel = opts.switchLabel;
    if (swLabel) swLabel.classList.add("busy");

    return API.setting({ key: key, value: value }).then(function (res) {
      if (res && typeof res === "object" && "value" in res && res.value !== undefined) {
        CONFIG[key] = res.value;
      }
      applyControl(key);
      applyConditions();
      if (!opts.quiet) toast(opts.successText || "已保存", "ok");
      return res;
    }).catch(function (e) {
      // 回滚：键原本不存在就退回 schema 默认值，不能留着写失败的新值
      if (prev === undefined) {
        var def = SCHEMA[key] ? SCHEMA[key].default : undefined;
        delete CONFIG[key];
        if (def !== undefined) CONFIG[key] = def;
      } else {
        CONFIG[key] = prev;
      }
      applyControl(key);
      applyConditions();
      toast((opts.errorText || "保存失败") + "：" + (e && e.message ? e.message : e), "bad");
      throw e;
    }).then(function (r) {
      if (swLabel) swLabel.classList.remove("busy");
      return r;
    }, function (e) {
      if (swLabel) swLabel.classList.remove("busy");
      throw e;
    });
  }

  function initControls() {
    // 开关
    Array.prototype.forEach.call(
      document.querySelectorAll('input[type="checkbox"][data-key]'),
      function (sw) {
        sw.addEventListener("change", function () {
          var key = sw.getAttribute("data-key");
          var label = sw.closest(".m3-switch");
          var want = sw.checked;
          writeConfig(key, want, { switchLabel: label, successText: "已保存" }).catch(function () {});
        });
      }
    );

    // 分段控件
    Array.prototype.forEach.call(
      document.querySelectorAll(".m3-segmented-control[data-key]"),
      function (seg) {
        var key = seg.getAttribute("data-key");
        seg.addEventListener("click", function (ev) {
          var btn = ev.target.closest ? ev.target.closest(".seg-item") : null;
          if (!btn || btn.disabled) return;
          var val = btn.getAttribute("data-val");
          if (String(CONFIG[key]) === val) return;
          seg.style.pointerEvents = "none";
          writeConfig(key, val, { successText: "已保存" }).catch(function () {})
            .then(function () { seg.style.pointerEvents = ""; applyConditions(); });
        });
      }
    );

    // 文本输入
    Array.prototype.forEach.call(
      document.querySelectorAll("input.m3-input[data-key]"),
      function (inp) {
        var key = inp.getAttribute("data-key");
        var flush = function () {
          var v = inp.value;
          if (String(CONFIG[key] == null ? "" : CONFIG[key]) === v) return;
          writeConfig(key, v, { quiet: true }).catch(function () {});
        };
        inp.addEventListener("change", flush);
        inp.addEventListener("blur", flush);
        inp.addEventListener("keydown", function (ev) { if (ev.key === "Enter") flush(); });
      }
    );
  }

  /* ========================================================================
   * 状态渲染
   * ====================================================================== */
  function renderStatus(st) {
    STATE = st;
    var ver = st.version || "—";
    var tag = document.querySelector(".version-tag");
    if (tag) { tag.textContent = ver; tag.setAttribute("data-ver", ver); }

    if ($("st_version")) $("st_version").textContent = "v" + ver;
    if ($("st_loaded")) {
      $("st_loaded").textContent = st.loaded ? "已加载" : "未加载";
      $("st_loaded").style.color = st.loaded ? "var(--m3-status-green)" : "var(--m3-status-amber)";
    }
    if ($("st_priority")) $("st_priority").textContent = String(st.hook_priority == null ? "—" : st.hook_priority);

    if ($("st_kv")) {
      $("st_kv").textContent = st.kv_usable ? "可用（前缀 xbnext:）" : "不可用（档案与索引将只在内存）";
      $("st_kv").style.color = st.kv_usable ? "var(--m3-status-green)" : "var(--m3-status-amber)";
    }
    if ($("st_channel")) $("st_channel").textContent = API.channel() === "bridge" ? "Plugin Page Bridge" : "HTTP 直连";
  }

  function loadState() {
    return API.state().then(function (st) {
      if (!st || typeof st !== "object") throw new Error("state 返回为空");
      CONFIG = st.config || {};
      SCHEMA = st.schema || {};
      applyTheme(CONFIG.ui_theme_mode === "dark" ? "dark" : "light");
      renderConfig();
      renderStatus(st);
      applyAccent(CONFIG.ui_accent_color || "");
      if (accentState) syncAccentUi();
      return st;
    }).catch(function (e) {
      toast("加载状态失败：" + (e && e.message ? e.message : e), "bad");
      throw e;
    });
  }

  function reveal() {
    if (booted) return;
    booted = true;
    document.documentElement.removeAttribute("data-boot");
  }

  /* ========================================================================
   * 主题色取色器（照母版：SV 面板 + 色相条 + hex + 预设）
   * ====================================================================== */
  var accentState = { h: 210, s: 0.66, v: 0.85 };
  var accentOpen = false;

  function currentAccentHex() {
    var v = (CONFIG.ui_accent_color || "").trim();
    if (isHex(v)) return v.toUpperCase();
    return getComputedStyle(document.documentElement)
      .getPropertyValue("--m3-sys-color-primary").trim().toUpperCase();
  }

  function scheduleAccentSave(hex) {
    clearTimeout(accentSaveTimer);
    accentSaveTimer = setTimeout(function () {
      writeConfig("ui_accent_color", hex, { quiet: true }).catch(function () {});
    }, 500);
  }

  function syncAccentUi() {
    var hex = hsvToHex(accentState.h, accentState.s, accentState.v).toUpperCase();
    var sv = $("accentSv"), svDot = $("accentSvDot");
    var hue = $("accentHue"), hueDot = $("accentHueDot");
    var hexInput = $("accentHex"), cur = $("accentCurrent");
    if (sv) sv.style.background =
      "linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, transparent), hsl(" +
      accentState.h + ", 100%, 50%)";
    if (svDot) { svDot.style.left = (accentState.s * 100) + "%"; svDot.style.top = ((1 - accentState.v) * 100) + "%"; }
    if (hueDot) hueDot.style.left = (accentState.h / 360 * 100) + "%";
    if (hexInput && document.activeElement !== hexInput) hexInput.value = hex;
    if (cur) cur.style.background = hex;
    applyAccent(hex);
  }

  function setAccentHex(hex, save) {
    if (!isHex(hex)) return;
    accentState = hexToHsv(hex.toUpperCase());
    syncAccentUi();
    if (save !== false) scheduleAccentSave(hex.toUpperCase());
  }

  function openAccent() {
    var btn = $("accentPickerBtn"), pop = $("accentPopover");
    if (!btn || !pop) return;
    accentOpen = true;
    accentState = hexToHsv(currentAccentHex());
    pop.hidden = false;
    var r = btn.getBoundingClientRect();
    var w = pop.offsetWidth;
    var left = Math.min(Math.max(8, r.right - w), Math.max(8, window.innerWidth - w - 8));
    pop.style.left = left + "px";
    pop.style.top = (r.bottom + 8) + "px";
    syncAccentUi();
  }

  function closeAccent() {
    var pop = $("accentPopover");
    if (pop) pop.hidden = true;
    accentOpen = false;
  }

  function initAccent() {
    var btn = $("accentPickerBtn"), pop = $("accentPopover");
    if (!btn || !pop) return;
    var sv = $("accentSv"), hue = $("accentHue");
    var hexInput = $("accentHex"), presets = $("accentPresets");

    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      if (accentOpen) closeAccent(); else openAccent();
    });

    function fromPointer(el, ev, handler) {
      var rect = el.getBoundingClientRect();
      var x = Math.min(Math.max(ev.clientX - rect.left, 0), rect.width);
      var y = Math.min(Math.max(ev.clientY - rect.top, 0), rect.height);
      handler(x / rect.width, y / rect.height);
    }

    function drag(el, handler) {
      if (!el) return;
      var active = false;
      el.addEventListener("pointerdown", function (ev) {
        active = true;
        if (el.setPointerCapture) { try { el.setPointerCapture(ev.pointerId); } catch (e) {} }
        ev.preventDefault();
        handler(ev);
      });
      el.addEventListener("pointermove", function (ev) {
        if (!active) return;
        ev.preventDefault();
        handler(ev);
      });
      var stop = function () { active = false; };
      el.addEventListener("pointerup", stop);
      el.addEventListener("pointercancel", stop);
      el.addEventListener("pointerleave", stop);
    }

    drag(sv, function (ev) {
      fromPointer(sv, ev, function (fx, fy) {
        accentState.s = fx;
        accentState.v = 1 - fy;
        syncAccentUi();
        scheduleAccentSave(hsvToHex(accentState.h, accentState.s, accentState.v).toUpperCase());
      });
    });

    drag(hue, function (ev) {
      fromPointer(hue, ev, function (fx) {
        accentState.h = fx * 360;
        syncAccentUi();
        scheduleAccentSave(hsvToHex(accentState.h, accentState.s, accentState.v).toUpperCase());
      });
    });

    if (hexInput) {
      hexInput.addEventListener("change", function () { setAccentHex(hexInput.value); });
      hexInput.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") setAccentHex(hexInput.value);
      });
    }

    if (presets) {
      presets.addEventListener("click", function (ev) {
        var b = ev.target.closest ? ev.target.closest("button[data-color]") : null;
        if (!b) return;
        setAccentHex(b.getAttribute("data-color"));
      });
    }

    document.addEventListener("click", function (ev) {
      if (!accentOpen) return;
      if (ev.target.closest && (ev.target.closest("#accentPopover") || ev.target.closest("#accentPickerBtn"))) return;
      closeAccent();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && accentOpen) closeAccent();
    });
  }

  function resetAccent() {
    writeConfig("ui_accent_color", "", { successText: "已恢复默认主题色" })
      .then(function () { applyAccent(""); if (accentState) syncAccentUi(); })
      .catch(function () {});
  }

  /* ========================================================================
   * 启动
   * ====================================================================== */
  function boot() {
    initTabs();
    initControls();
    initAccent();

    if ($("themeToggleBtn")) $("themeToggleBtn").addEventListener("click", toggleTheme);
    if ($("accentResetBtn")) $("accentResetBtn").addEventListener("click", resetAccent);
    if ($("refreshAllBtn")) $("refreshAllBtn").addEventListener("click", function () {
      loadState().then(function () { toast("已刷新", "ok"); }).catch(function () {});
    });

    loadState().then(reveal, reveal);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
