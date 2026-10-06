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
   * Toast —— 与 xbdoc / xbimg / xbbot_beta 同款：
   * 右下堆叠 pill + 类型色 + 类型图标 + 长文本自动延长 + 点击复制 + 1.2s 去重
   * ====================================================================== */
  var _lastToastText = "";
  var _lastToastTime = 0;
  var _TOAST_MAX = 4;
  var _TOAST_ICON = {
    ok: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>',
    bad: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>'
  };
  var _TOAST_COPY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>';
  var _TOAST_DONE_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';

  /** 类型自动识别：失败类 → bad，完成类 → ok，其余 → info（显式传参优先） */
  function _toastTypeOf(msg, explicit) {
    if (explicit === "ok" || explicit === "bad" || explicit === "info") return explicit;
    var s = String(msg == null ? "" : msg);
    if (/失败|错误|异常|超出|并非|不支持|无法|不存在|未包含|超时|未检出|非法|未命中/.test(s)) return "bad";
    if (/请先|请填写|请稍候|请至少|正在|尚未|暂未/.test(s)) return "info";
    if (/已|成功|完成|就绪|生效|完毕|恢复默认|保存/.test(s)) return "ok";
    return "info";
  }

  /** 沙箱里 navigator.clipboard 常被禁，退到 execCommand；都不行则选中文本让用户手动复制 */
  function _copyText(text) {
    var s = String(text == null ? "" : text);
    if (!s) return Promise.resolve(false);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(s).then(function () { return true; },
          function () { return _copyFallback(s); });
      }
    } catch (e) { /* 落到兜底 */ }
    return Promise.resolve(_copyFallback(s));
  }

  function _copyFallback(s) {
    try {
      var ta = document.createElement("textarea");
      ta.value = s;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;padding:0;border:0;";
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, s.length);
      var ok = document.execCommand("copy");
      ta.remove();
      return !!ok;
    } catch (e) { return false; }
  }

  function toast(message, kind, duration) {
    var text = String(message == null ? "" : message);
    var now = Date.now();
    if (text && text === _lastToastText && now - _lastToastTime < 1200) return;
    _lastToastText = text;
    _lastToastTime = now;

    var box = $("toastContainer");
    if (!box) return;
    var type = _toastTypeOf(text, kind);

    var el = document.createElement("div");
    el.className = "m3-toast" + (type === "ok" ? " okk" : (type === "bad" ? " badk" : ""));
    el.setAttribute("role", "status");

    var ic = document.createElement("span");
    ic.className = "m3-toast-ic";
    ic.innerHTML = _TOAST_ICON[type] || _TOAST_ICON.info;
    el.appendChild(ic);

    var body = document.createElement("div");
    body.className = "m3-toast-text";
    body.textContent = text;
    el.appendChild(body);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "m3-toast-copy";
    btn.title = "复制这条通知";
    btn.setAttribute("aria-label", "复制这条通知");
    btn.innerHTML = _TOAST_COPY_SVG;
    el.appendChild(btn);

    // 长文本给更久，留出阅读与复制时间
    var life = duration || Math.min(9000, Math.max(3000, 2400 + text.length * 45));
    var timer = 0;
    function dismiss() {
      el.classList.add("out");
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
    }
    function arm(ms) { clearTimeout(timer); timer = setTimeout(dismiss, ms); }
    function doCopy() {
      _copyText(text).then(function (ok) {
        if (ok) {
          btn.innerHTML = _TOAST_DONE_SVG;
          btn.classList.add("done");
          btn.title = "已复制";
          setTimeout(function () {
            btn.innerHTML = _TOAST_COPY_SVG;
            btn.classList.remove("done");
            btn.title = "复制这条通知";
          }, 1600);
          arm(Math.max(life, 4000));
        } else {
          try {
            var sel = window.getSelection();
            var range = document.createRange();
            range.selectNodeContents(body);
            sel.removeAllRanges();
            sel.addRange(range);
          } catch (e) { /* 忽略 */ }
          btn.title = "自动复制失败，已选中文本，请手动 Ctrl+C";
          arm(Math.max(life, 6000));
        }
      });
    }
    btn.addEventListener("click", function (ev) { ev.stopPropagation(); doCopy(); });
    el.addEventListener("click", function () {
      try { if (String(window.getSelection())) return; } catch (e) { /* 忽略 */ }
      doCopy();
    });

    box.appendChild(el);
    while (box.children.length > _TOAST_MAX) box.removeChild(box.firstChild);
    arm(life);
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

  function systemTheme() {
    return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches)
      ? "dark" : "light";
  }

  /** 配置值 → 实际主题：light / dark 之外（含空串）一律跟随系统，与 xbdoc / xbimg 一致。 */
  function resolveTheme(mode) {
    return mode === "dark" || mode === "light" ? mode : systemTheme();
  }

  function applyTheme(mode) {
    var next = resolveTheme(mode);
    document.documentElement.setAttribute("data-theme", next);
    var icon = $("themeIcon");
    if (icon) icon.innerHTML = next === "dark" ? SUN : MOON;
    var btn = $("themeToggleBtn");
    if (btn) btn.title = next === "dark" ? "切换到浅色主题" : "切换到深色主题";
    // 主题色的派生依赖 data-theme，换主题后必须无条件重染一次
    // （以前这里有 `if (CONFIG.ui_accent_color)` 的判断，默认色下换主题会丢派生色，
    //  表现为"必须再点一次主题色按钮才恢复"）
    applyAccent(CONFIG.ui_accent_color || "");
  }

  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    var next = cur === "dark" ? "light" : "dark";
    applyTheme(next);
    // 与系统一致时存回空串 = 回到"跟随系统"，不留一个和系统一样的硬锁
    var stored = next === systemTheme() ? "" : next;
    writeConfig("ui_theme_mode", stored, { quiet: true }).catch(function () {});
  }

  /** 配置写入成功/回滚都要把界面偏好重新套一次，保证"看到的 = 存下来的"。 */
  function applyUiPref(key) {
    if (key === "ui_accent_color") applyAccent(CONFIG.ui_accent_color || "");
    else if (key === "ui_theme_mode") applyTheme(CONFIG.ui_theme_mode);
  }

  /** 系统深浅色变化时，只有在"跟随系统"状态下才跟着变。 */
  function watchSystemTheme() {
    if (!window.matchMedia) return;
    var mq;
    try { mq = window.matchMedia("(prefers-color-scheme: dark)"); } catch (e) { return; }
    if (!mq) return;
    var onChange = function () {
      var mode = CONFIG.ui_theme_mode;
      if (mode !== "dark" && mode !== "light") applyTheme(mode);
    };
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
    else if (typeof mq.addListener === "function") mq.addListener(onChange);
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
    applyUiPref(key);

    var swLabel = opts.switchLabel;
    if (swLabel) swLabel.classList.add("busy");

    return API.setting({ key: key, value: value }).then(function (res) {
      if (res && typeof res === "object" && "value" in res && res.value !== undefined) {
        CONFIG[key] = res.value;
      }
      applyControl(key);
      applyConditions();
      applyUiPref(key);
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
      applyUiPref(key);
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
      applyTheme(CONFIG.ui_theme_mode);
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
   * 用户档案页签
   * ====================================================================== */
  var pfRows = [];
  var pfCurrent = null;   // {platform, uid, isNew}

  function mkEl(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = String(text);
    return el;
  }

  function m3Btn(cls, text) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "m3-btn " + cls;
    b.textContent = text;
    return b;
  }

  function truncate(s, n) {
    var t = String(s == null ? "" : s);
    return t.length > n ? t.slice(0, n) + "…" : t;
  }

  function pfNote(text) { return mkEl("div", "card-note", text); }

  function pfRowEl(row) {
    var wrap = mkEl("div", "pf-row");
    var main = mkEl("div", "pf-row-main");

    var id = mkEl("div", "pf-row-id");
    id.textContent = (row.platform || "unknown") + " / " + (row.uid || "unknown");
    if (row.updated) {
      var when = mkEl("span", "pf-when");
      try {
        when.textContent = "　更新于 " + new Date(row.updated * 1000).toLocaleString();
      } catch (e) { when.textContent = ""; }
      id.appendChild(when);
    }
    main.appendChild(id);

    var p = row.profile || {};
    var parts = [];
    if (p.name) parts.push("称呼：" + p.name);
    if (p.facts) parts.push("自述：" + truncate(p.facts, 70));
    if (p.style) parts.push("口吻：" + truncate(p.style, 40));
    main.appendChild(mkEl("div", "pf-row-sub", parts.length ? parts.join("　|　") : "（空档案）"));

    var actions = mkEl("div", "pf-actions");
    var edit = m3Btn("secondary-btn", "编辑");
    edit.addEventListener("click", function () { pfOpen(row, false); });
    var del = m3Btn("danger-btn", "删除");
    pfConfirmable(del, "删除", "确认删除", function () { pfDelete(row); });
    actions.appendChild(edit);
    actions.appendChild(del);

    wrap.appendChild(main);
    wrap.appendChild(actions);
    return wrap;
  }

  /** 两步确认：沙箱里原生 confirm 被拦截，危险操作改成"再点一次才执行"。 */
  function pfConfirmable(btn, idle, armed, run) {
    var timer = 0;
    btn.addEventListener("click", function () {
      if (btn.textContent === armed) {
        clearTimeout(timer);
        btn.textContent = idle;
        run();
        return;
      }
      btn.textContent = armed;
      clearTimeout(timer);
      timer = setTimeout(function () { btn.textContent = idle; }, 3000);
    });
  }

  function pfRender(rows) {
    var box = $("pfList");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    pfRows = Array.isArray(rows) ? rows : [];
    if (!pfRows.length) {
      box.appendChild(pfNote("还没有任何档案 —— 在群里发 /xbnext profile 称呼 小明，或点右上角「新建档案」。"));
      return;
    }
    pfRows.forEach(function (row) { box.appendChild(pfRowEl(row)); });
  }

  function pfLoad(silent) {
    var box = $("pfList");
    if (box && !silent) {
      while (box.firstChild) box.removeChild(box.firstChild);
      box.appendChild(pfNote("正在加载…"));
    }
    return API.profiles().then(function (rows) {
      pfRender(rows);
      return rows;
    }).catch(function (e) {
      var msg = e && e.message ? e.message : e;
      if (box) {
        while (box.firstChild) box.removeChild(box.firstChild);
        box.appendChild(pfNote("档案列表加载失败：" + msg));
      }
      if (!silent) toast("档案列表加载失败：" + msg, "bad");
      return null;
    });
  }

  function pfShowEditor(show) {
    var ed = $("pfEditor");
    if (ed) ed.classList.toggle("is-hidden", !show);
  }

  function pfOpen(row, isNew) {
    pfCurrent = {
      platform: String((row && row.platform) || ""),
      uid: String((row && row.uid) || ""),
      isNew: !!isNew
    };
    var p = (row && row.profile) || {};
    if ($("pfPlatform")) { $("pfPlatform").value = pfCurrent.platform; $("pfPlatform").disabled = !isNew; }
    if ($("pfUid")) { $("pfUid").value = pfCurrent.uid; $("pfUid").disabled = !isNew; }
    if ($("pfName")) $("pfName").value = p.name || "";
    if ($("pfFacts")) $("pfFacts").value = p.facts || "";
    if ($("pfStyle")) $("pfStyle").value = p.style || "";
    if ($("pfEditorTitle")) $("pfEditorTitle").textContent = isNew ? "新建档案" : "编辑档案";
    if ($("pfDeleteBtn")) $("pfDeleteBtn").textContent = isNew ? "取消新建" : "删除这份档案";
    pfShowEditor(true);
    var first = $("pfName");
    if (first) { try { first.focus(); } catch (e) { /* 忽略 */ } }
  }

  function pfClose() {
    pfCurrent = null;
    var btn = $("pfDeleteBtn");
    if (btn) btn.textContent = "删除这份档案";
    pfShowEditor(false);
  }

  function pfSave() {
    var platform = $("pfPlatform") ? $("pfPlatform").value.trim() : "";
    var uid = $("pfUid") ? $("pfUid").value.trim() : "";
    if (!platform || !uid) { toast("请填写平台与用户 ID", "bad"); return; }
    var btn = $("pfSaveBtn");
    if (btn) btn.disabled = true;
    API.profileSave({
      platform: platform,
      uid: uid,
      name: $("pfName") ? $("pfName").value : "",
      facts: $("pfFacts") ? $("pfFacts").value : "",
      style: $("pfStyle") ? $("pfStyle").value : ""
    }).then(function (res) {
      var deleted = res && res.deleted;
      toast(deleted ? "档案已清空（三个字段都为空）" : "档案已保存", "ok");
      if (pfCurrent && !pfCurrent.isNew) pfOpen({ platform: platform, uid: uid, profile: (res && res.profile) || {} }, false);
      else if (deleted) pfClose();
      else pfOpen({ platform: platform, uid: uid, profile: (res && res.profile) || {} }, false);
      return pfLoad(true);
    }).catch(function (e) {
      toast("保存失败：" + (e && e.message ? e.message : e), "bad");
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  function pfDelete(row) {
    API.profileDelete({ platform: row.platform, uid: row.uid }).then(function () {
      toast("已删除 " + row.platform + "/" + row.uid + " 的档案", "ok");
      if (pfCurrent && !pfCurrent.isNew &&
          pfCurrent.platform === row.platform && pfCurrent.uid === row.uid) pfClose();
      return pfLoad(true);
    }).catch(function (e) {
      toast("删除失败：" + (e && e.message ? e.message : e), "bad");
    });
  }

  function initProfile() {
    if ($("pfRefreshBtn")) $("pfRefreshBtn").addEventListener("click", function () { pfLoad(false); });
    if ($("pfNewBtn")) $("pfNewBtn").addEventListener("click", function () { pfOpen({ profile: {} }, true); });
    if ($("pfSaveBtn")) $("pfSaveBtn").addEventListener("click", pfSave);
    if ($("pfCancelBtn")) $("pfCancelBtn").addEventListener("click", pfClose);
    if ($("pfDeleteBtn")) {
      var delBtn = $("pfDeleteBtn");
      var delTimer = 0;
      delBtn.addEventListener("click", function () {
        if (!pfCurrent || pfCurrent.isNew) { pfClose(); return; }
        if (delBtn.textContent === "确认删除") {
          clearTimeout(delTimer);
          delBtn.textContent = "删除这份档案";
          pfDelete({ platform: pfCurrent.platform, uid: pfCurrent.uid });
          return;
        }
        delBtn.textContent = "确认删除";
        clearTimeout(delTimer);
        delTimer = setTimeout(function () { delBtn.textContent = "删除这份档案"; }, 3000);
      });
    }
  }

  /* ========================================================================
   * 启动
   * ====================================================================== */
  function boot() {
    initTabs();
    initControls();
    initAccent();
    initProfile();
    watchSystemTheme();

    if ($("themeToggleBtn")) $("themeToggleBtn").addEventListener("click", toggleTheme);
    if ($("accentResetBtn")) $("accentResetBtn").addEventListener("click", resetAccent);
    if ($("refreshAllBtn")) $("refreshAllBtn").addEventListener("click", function () {
      loadState().then(function () { toast("已刷新", "ok"); }).catch(function () {});
      pfLoad(false);
    });

    loadState().then(function () {
      reveal();
      // 揭开后再套一次主题色：data-boot 期间的内联变量可能被首帧覆盖
      applyAccent(CONFIG.ui_accent_color || "");
      pfLoad(true);
    }, reveal);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
