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
    try { localStorage.setItem("xbnext_ui_accent", isHex(v) ? v : ""); } catch (e) {}
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
    try { localStorage.setItem("xbnext_ui_theme", (mode === "dark" || mode === "light") ? mode : ""); } catch (e) {}
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
  var _tabLoaded = {};

  function ensureTabLoaded(target) {
    if (target === "tab-tuning" && !_tabLoaded["tab-tuning"]) {
      _tabLoaded["tab-tuning"] = true;
      tkLoadGroups(true);
    } else if (target === "tab-profile" && !_tabLoaded["tab-profile"]) {
      _tabLoaded["tab-profile"] = true;
      pfLoad(true);
    }
  }

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
        ensureTabLoaded(target);
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

    var inputs = document.querySelectorAll('.m3-input[data-key="' + key + '"]');
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
          if (
            (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "text" || el.type === "number")) ||
            el.tagName === "TEXTAREA"
          ) {
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
      document.querySelectorAll(".m3-input[data-key]"),
      function (inp) {
        var key = inp.getAttribute("data-key");
        var flush = function () {
          var v = inp.value;
          if (String(CONFIG[key] == null ? "" : CONFIG[key]) === v) return;
          writeConfig(key, v, { quiet: true }).catch(function () {});
        };
        inp.addEventListener("change", flush);
        inp.addEventListener("blur", flush);
        inp.addEventListener("keydown", function (ev) {
          // textarea 里 Enter 是换行，保存交给 change / blur
          if (ev.key === "Enter" && inp.tagName !== "TEXTAREA") flush();
        });
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

    // 表情表统计（P17·D）
    var face = st.face || {};
    if ($("st_face_builtin")) {
      $("st_face_builtin").textContent =
        (face.builtin == null ? "—" : face.builtin) + " 条";
    }
    if ($("st_face_overlay")) {
      $("st_face_overlay").textContent =
        (face.overlay == null ? "—" : face.overlay) + " 条";
    }
    if ($("st_face_updated")) {
      var faceUpd = "从未更新";
      if (face.updated_at) {
        try { faceUpd = new Date(face.updated_at).toLocaleString(); }
        catch (e) { faceUpd = face.updated_at; }
      }
      $("st_face_updated").textContent = faceUpd;
    }
    if ($("st_face_note")) {
      var faceNote = "";
      if (!face.enabled) {
        faceNote = "⚠️「QQ 表情翻译」未启用 —— 不翻译，表也不再自动更新。";
      } else if (st.config && !st.config.face_auto_update) {
        faceNote = "ℹ️ 自动更新已关（face_auto_update）—— 表停在当前版本。";
      }
      $("st_face_note").textContent = faceNote;
      $("st_face_note").style.display = faceNote ? "" : "none";
    }
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

  /* ========================================================================
   * 提示词注入记录（P16：运行状态底部入口）
   * 全部走 textContent 渲染（mkEl）—— prompt 是模型输出，绝不 innerHTML。
   * ====================================================================== */
  // 超过这个长度默认折叠，点「展开全部」看全文（P17·C）
  var INJ_CLAMP_CHARS = 300;

  function appendClamped(parent, text) {
    var clamped = text.length > INJ_CLAMP_CHARS;
    var pre = mkEl("pre", clamped ? "inj-pre is-clamped" : "inj-pre", text);
    parent.appendChild(pre);
    if (!clamped) return;
    var btn = mkEl("button", "inj-toggle", "展开全部");
    btn.setAttribute("type", "button");
    btn.addEventListener("click", function () {
      var nowClamped = pre.classList.toggle("is-clamped");
      btn.textContent = nowClamped ? "展开全部" : "收起";
    });
    parent.appendChild(btn);
  }

  function renderInjectLog(items) {
    var box = $("injectLogList");
    if (!box) return;
    box.textContent = "";
    if (!items || !items.length) {
      box.appendChild(mkEl("div", "card-note", "暂无记录 —— 有 LLM 请求跑过之后这里就会有。"));
      return;
    }
    items.forEach(function (it, idx) {
      var card = mkEl("div", "inj-item");
      var head = mkEl("div", "inj-item-head");
      // 列表新在前：idx 0 是最新一轮，不是「第 1 轮最早」
      head.appendChild(mkEl("span", "inj-item-title",
        idx === 0 ? "最新一轮" : idx + " 轮前"));
      if (it.ts) {
        try {
          head.appendChild(mkEl("span", "inj-item-time",
            new Date(it.ts * 1000).toLocaleString()));
        } catch (e) { /* 时间格式化失败就只显示轮次 */ }
      }
      var actions = Array.isArray(it.actions) ? it.actions : [];
      head.appendChild(mkEl("span", "inj-item-actions",
        actions.length ? actions.join("、") : "无注入"));
      card.appendChild(head);
      card.appendChild(mkEl("div", "inj-item-umo",
        (it.umo || "（未知会话）") + (it.images ? "　图片 " + it.images + " 张" : "")));
      var parts = Array.isArray(it.parts) ? it.parts : [];
      parts.forEach(function (p, i) {
        card.appendChild(mkEl("div", "inj-item-label", "注入段 " + (i + 1)));
        appendClamped(card, String(p));
      });
      card.appendChild(mkEl("div", "inj-item-label", "正文 prompt（清洗后）"));
      appendClamped(card, String(it.prompt || "（空）"));
      box.appendChild(card);
    });
  }

  function loadInjectLog() {
    var btn = $("injRefreshBtn");
    var box = $("injectLogList");
    if (btn) { btn.disabled = true; btn.textContent = "加载中…"; }
    return API.injectLog().then(function (res) {
      var items = (res && res.items) || [];
      renderInjectLog(items);
      if (btn) { btn.disabled = false; btn.textContent = "刷新记录"; }
    }).catch(function (e) {
      if (box) {
        box.textContent = "";
        box.appendChild(mkEl("div", "card-note",
          "加载失败：" + (e && e.message ? e.message : e)));
      }
      if (btn) { btn.disabled = false; btn.textContent = "查看最近注入"; }
    });
  }

  function initInjectLog() {
    if ($("injRefreshBtn")) $("injRefreshBtn").addEventListener("click", function () {
      loadInjectLog();
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
   * 用户档案页签（按「平台 · 群」分组分类展示 —— 分群之后不分类又会乱）
   * ====================================================================== */
  var pfRows = [];
  var pfCurrent = null;   // {platform, uid, group, isNew}

  function mkEl(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = String(text);
    return el;
  }

  /** 范围标签：群号 → 「群 123456」，没有群号 → 「私聊 / 未分群」。 */
  function pfScopeLabel(group) {
    var g = String(group == null ? "" : group).trim();
    return g ? "群 " + g : "私聊 / 未分群";
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
    // 分组分类：按「平台 · 群」分桶，每桶一个分组标题
    var buckets = [];
    var byKey = {};
    pfRows.forEach(function (row) {
      var platform = String(row.platform || "unknown");
      var group = String(row.group || "").trim();
      var key = platform + "|" + group;
      var b = byKey[key];
      if (!b) {
        b = byKey[key] = { platform: platform, group: group, rows: [] };
        buckets.push(b);
      }
      b.rows.push(row);
    });
    buckets.sort(function (a, b) {
      if (a.platform !== b.platform) return a.platform < b.platform ? -1 : 1;
      if (!a.group !== !b.group) return a.group ? 1 : -1;  // 私聊 / 未分群排最前
      if (a.group === b.group) return 0;
      var na = /^\d+$/.test(a.group);
      var nb = /^\d+$/.test(b.group);
      if (na && nb) return Number(a.group) - Number(b.group);
      return a.group < b.group ? -1 : 1;
    });
    buckets.forEach(function (b) {
      var head = mkEl("div", "pf-group-head");
      head.appendChild(mkEl("span", "pf-group-title",
        b.platform + " · " + pfScopeLabel(b.group)));
      head.appendChild(mkEl("span", "pf-group-count", b.rows.length + " 份"));
      box.appendChild(head);
      b.rows.forEach(function (row) { box.appendChild(pfRowEl(row)); });
    });
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

  function pfRefreshQuickPickers(selectedGroup) {
    var gSel = $("pfQuickGroup");
    var uSel = $("pfQuickUser");
    var seenGroups = (STATE && Array.isArray(STATE.seen_groups)) ? STATE.seen_groups : [];
    var seenUsers = (STATE && Array.isArray(STATE.seen_users)) ? STATE.seen_users : [];
    var groupMap = {};
    seenGroups.forEach(function (g) {
      var gid = String(g.group_id || "").trim();
      if (gid) groupMap[gid] = String(g.group_name || gid);
    });
    tkCachedGroups.forEach(function (g) {
      var gid = String(g.group_id || "").trim();
      if (gid && !groupMap[gid]) groupMap[gid] = String(g.group_name || gid);
    });
    pfRows.forEach(function (r) {
      var gid = String(r.group || "").trim();
      if (gid && !groupMap[gid]) groupMap[gid] = gid;
    });

    if (gSel) {
      while (gSel.firstChild) gSel.removeChild(gSel.firstChild);
      var opt0 = mkEl("option", "", "— 快速选择群聊（或私聊）—");
      opt0.value = "__none__";
      gSel.appendChild(opt0);
      var optPriv = mkEl("option", "", "私聊 / 未分群");
      optPriv.value = "";
      gSel.appendChild(optPriv);
      Object.keys(groupMap).sort().forEach(function (gid) {
        var gname = groupMap[gid];
        var label = (gname && gname !== gid) ? (gname + " (" + gid + ")") : ("群 " + gid);
        var opt = mkEl("option", "", label);
        opt.value = gid;
        gSel.appendChild(opt);
      });
      if (selectedGroup != null) {
        gSel.value = selectedGroup;
      } else {
        gSel.value = "__none__";
      }
    }

    if (uSel) {
      while (uSel.firstChild) uSel.removeChild(uSel.firstChild);
      var uOpt0 = mkEl("option", "", "— 快速选择近期发言群友 —");
      uOpt0.value = "";
      uSel.appendChild(uOpt0);
      var filterGid = selectedGroup != null ? String(selectedGroup).trim() : "";
      var seenU = {};
      seenUsers.slice().reverse().forEach(function (u) {
        var uid = String(u.uid || "").trim();
        var ug = String(u.group || "").trim();
        if (!uid) return;
        if (filterGid && ug && ug !== filterGid) return;
        var key = (u.platform || "aiocqhttp") + "|" + ug + "|" + uid;
        if (seenU[key]) return;
        seenU[key] = true;
        var uname = String(u.name || uid);
        var scopeStr = ug ? ("群" + ug) : "私聊";
        var opt = mkEl("option", "", uname + " (" + uid + " · " + scopeStr + ")");
        opt.value = JSON.stringify({
          platform: u.platform || "aiocqhttp",
          group: ug,
          uid: uid,
          name: (uname && uname !== uid) ? uname : ""
        });
        uSel.appendChild(opt);
      });
    }
  }

  function pfOpen(row, isNew) {
    pfCurrent = {
      platform: String((row && row.platform) || (isNew ? "aiocqhttp" : "")),
      uid: String((row && row.uid) || ""),
      group: String((row && row.group) || "").trim(),
      isNew: !!isNew
    };
    var p = (row && row.profile) || {};
    if ($("pfQuickPickRow")) $("pfQuickPickRow").style.display = isNew ? "" : "none";
    if (isNew) pfRefreshQuickPickers(pfCurrent.group || null);
    if ($("pfPlatform")) { $("pfPlatform").value = pfCurrent.platform || "aiocqhttp"; $("pfPlatform").disabled = !isNew; }
    if ($("pfUid")) { $("pfUid").value = pfCurrent.uid; $("pfUid").disabled = !isNew; }
    // 群号始终可改：改了等于把档案挪到另一个范围（保存时带 prev_group 删旧键）
    if ($("pfGroup")) $("pfGroup").value = pfCurrent.group;
    if ($("pfName")) $("pfName").value = p.name || "";
    if ($("pfFacts")) $("pfFacts").value = p.facts || "";
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
    var group = $("pfGroup") ? $("pfGroup").value.trim() : "";
    if (!platform || !uid) { toast("请填写平台与用户 ID", "bad"); return; }
    var btn = $("pfSaveBtn");
    if (btn) btn.disabled = true;
    var payload = {
      platform: platform,
      group: group,
      uid: uid,
      name: $("pfName") ? $("pfName").value : "",
      facts: $("pfFacts") ? $("pfFacts").value : ""
    };
    // 编辑已有档案时带上原范围：挪群后服务端会删掉旧键，不留重复行
    var wasNew = !pfCurrent || pfCurrent.isNew;
    if (!wasNew) payload.prev_group = pfCurrent.group || "";
    API.profileSave(payload).then(function (res) {
      var deleted = res && res.deleted;
      var label = pfScopeLabel(group);
      toast(deleted ? "档案已清空（" + label + "，字段都为空）"
                    : "档案已保存（" + label + "）", "ok");
      var back = { platform: platform, group: group, uid: uid, profile: (res && res.profile) || {} };
      if (wasNew && deleted) pfClose();
      else pfOpen(back, false);
      return pfLoad(true);
    }).catch(function (e) {
      toast("保存失败：" + (e && e.message ? e.message : e), "bad");
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  function pfDelete(row) {
    var group = String(row.group || "").trim();
    var label = pfScopeLabel(group);
    API.profileDelete({ platform: row.platform, group: group, uid: row.uid }).then(function () {
      toast("已删除 " + row.platform + " · " + label + " · " + row.uid + " 的档案", "ok");
      if (pfCurrent && !pfCurrent.isNew &&
          pfCurrent.platform === row.platform && pfCurrent.uid === row.uid &&
          pfCurrent.group === group) pfClose();
      return pfLoad(true);
    }).catch(function (e) {
      toast("删除失败：" + (e && e.message ? e.message : e), "bad");
    });
  }

  function initProfile() {
    if ($("pfRefreshBtn")) $("pfRefreshBtn").addEventListener("click", function () { pfLoad(false); });
    if ($("pfNewBtn")) $("pfNewBtn").addEventListener("click", function () { pfOpen({ platform: "aiocqhttp", profile: {} }, true); });
    if ($("pfQuickGroup")) {
      $("pfQuickGroup").addEventListener("change", function () {
        var val = $("pfQuickGroup").value;
        if (val === "__none__") return;
        if ($("pfGroup")) $("pfGroup").value = val;
        pfRefreshQuickPickers(val);
      });
    }
    if ($("pfQuickUser")) {
      $("pfQuickUser").addEventListener("change", function () {
        var raw = $("pfQuickUser").value;
        if (!raw) return;
        try {
          var u = JSON.parse(raw);
          if ($("pfPlatform") && u.platform) $("pfPlatform").value = u.platform;
          if ($("pfGroup") && u.group != null) $("pfGroup").value = u.group;
          if ($("pfUid") && u.uid) $("pfUid").value = u.uid;
          if ($("pfName") && !$("pfName").value.trim() && u.name) $("pfName").value = u.name;
        } catch (e) {}
      });
    }
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
          pfDelete({ platform: pfCurrent.platform, uid: pfCurrent.uid, group: pfCurrent.group });
          return;
        }
        delBtn.textContent = "确认删除";
        clearTimeout(delTimer);
        delTimer = setTimeout(function () { delBtn.textContent = "删除这份档案"; }, 3000);
      });
    }
  }

  /* ========================================================================
   * Token 白名单 · 群聊开关表（与 xbbot_beta / xbimg 同款）
   * ====================================================================== */
  var tkCachedGroups = [];

  function tkParseUmos(text) {
    // 黏连自救：单行 input 剥掉换行后名单会黏成一坨，按「数字 + 新 UMO 开头」补回换行
    var raw = String(text || "").replace(
      /(\d)(?=[A-Za-z_][A-Za-z0-9_\-]*:[A-Za-z0-9_\-]*Message:)/g,
      "$1\n"
    );
    var seen = {};
    var out = [];
    raw.split(/[,，、;；\n]/).forEach(function (s) {
      s = s.trim();
      if (s && !seen[s]) { seen[s] = true; out.push(s); }
    });
    return out;
  }

  function tkLoadGroups(silent) {
    var btn = $("tkGroupsBtn");
    var box = $("tkGroupsBox");
    if (!box) return;
    if (!silent) {
      box.textContent = "";
      box.appendChild(pfNote("正在拉取群列表…"));
    }
    if (btn) btn.disabled = true;
    var p = silent ? Promise.resolve() : loadState().catch(function () {});
    p.then(function () { return API.groups(); })
      .then(function (rows) {
        if (btn) btn.disabled = false;
        tkCachedGroups = Array.isArray(rows) ? rows : [];
        tkRenderGroups();
        if (!silent) toast("群列表已更新（共 " + tkCachedGroups.length + " 个群）", "ok");
      }, function (e) {
        if (btn) btn.disabled = false;
        tkRenderGroups();
        if (!silent) toast("获取群列表失败：" + (e && e.message ? e.message : e), "bad");
      });
  }

  function tkRenderGroups() {
    var box = $("tkGroupsBox");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);

    var inp = $("inp_token_usage_umos");
    var have = tkParseUmos(inp ? inp.value : "");
    var haveSet = {};
    have.forEach(function (u) { haveSet[u] = true; });

    // 合并服务端拉到的群 + 已在白名单里的 UMO
    var merged = [];
    var seenUmo = {};
    tkCachedGroups.forEach(function (g) {
      var umo = String(g.umo || "");
      if (!umo || seenUmo[umo]) return;
      seenUmo[umo] = true;
      merged.push({
        umo: umo,
        group_id: String(g.group_id || ""),
        group_name: String(g.group_name || "")
      });
    });
    have.forEach(function (umo) {
      if (seenUmo[umo]) return;
      seenUmo[umo] = true;
      var parts = umo.split(":");
      var gid = parts.length >= 3 ? parts.slice(2).join(":") : umo;
      merged.push({
        umo: umo,
        group_id: gid,
        group_name: ""
      });
    });

    var qEl = $("tkGroupSearch");
    var kw = qEl && qEl.value ? qEl.value.trim().toLowerCase() : "";
    var filtered = merged.filter(function (g) {
      if (g.umo && /webchat|probe/i.test(g.umo)) return false;
      if (!kw) return true;
      return (g.group_name && g.group_name.toLowerCase().indexOf(kw) >= 0) ||
             (g.group_id && g.group_id.toLowerCase().indexOf(kw) >= 0) ||
             (g.umo && g.umo.toLowerCase().indexOf(kw) >= 0);
    });

    // 已开启的排前面
    filtered.sort(function (a, b) {
      var ea = !!haveSet[a.umo];
      var eb = !!haveSet[b.umo];
      if (ea !== eb) return ea ? -1 : 1;
      return String(a.group_id).localeCompare(String(b.group_id));
    });

    if (!filtered.length) {
      box.appendChild(pfNote(kw ? "没有匹配的群聊或会话。" : "暂未获取到群聊列表（可在上方手动输入群号添加，或点击「刷新群列表」）。"));
      return;
    }

    var table = mkEl("table", "group-switch-table");
    var thead = mkEl("thead");
    var trHead = mkEl("tr");
    trHead.appendChild(mkEl("th", "", "群聊名称"));
    trHead.appendChild(mkEl("th", "", "群号"));
    trHead.appendChild(mkEl("th", "group-cell-action", "显示开关"));
    thead.appendChild(trHead);
    table.appendChild(thead);

    var tbody = mkEl("tbody");
    filtered.forEach(function (g) {
      var umo = g.umo;
      var enabled = !!haveSet[umo];
      var tr = mkEl("tr", enabled ? "is-enabled" : "");

      var nameStr = g.group_name || "未命名会话";
      var tdName = mkEl("td", "group-cell-name", nameStr);
      tdName.title = nameStr;
      var tdId = mkEl("td", "group-cell-id", g.group_id || umo);
      tdId.title = umo;
      var tdAct = mkEl("td", "group-cell-action");

      var swLabel = mkEl("label", "m3-switch");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.setAttribute("data-umo", umo);
      cb.checked = enabled;
      cb.addEventListener("change", function () { tkToggleUmo(umo, cb.checked); });
      var slider = mkEl("span", "switch-slider");
      swLabel.appendChild(cb);
      swLabel.appendChild(slider);
      tdAct.appendChild(swLabel);

      tr.appendChild(tdName);
      tr.appendChild(tdId);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function tkToggleUmo(umo, on) {
    var inp = $("inp_token_usage_umos");
    if (!inp || !umo) return;
    var have = tkParseUmos(inp.value);
    var idx = have.indexOf(umo);
    if (on && idx >= 0) return;
    if (!on && idx < 0) return;
    if (on) have.push(umo);
    else have.splice(idx, 1);
    inp.value = have.join("\n");
    writeConfig("token_usage_umos", inp.value, { quiet: true }).then(function () {
      tkRenderGroups();
      toast(on ? "已开启该群 Token 用量展示" : "已关闭该群 Token 用量展示", "ok");
    }, function () {
      tkRenderGroups();
    });
  }

  function tkAddManual() {
    var addInp = $("tkGroupAddInput");
    if (!addInp) return;
    var raw = String(addInp.value || "").trim();
    if (!raw) {
      toast("请输入群号或会话 UMO", "bad");
      return;
    }
    var umo = raw;
    if (/^\d+$/.test(raw)) {
      var prefix = "aiocqhttp";
      if (tkCachedGroups.length && tkCachedGroups[0].umo) {
        var p0 = String(tkCachedGroups[0].umo).split(":")[0];
        if (p0) prefix = p0;
      }
      umo = prefix + ":GroupMessage:" + raw;
    }
    addInp.value = "";
    tkToggleUmo(umo, true);
  }

  function initTokenline() {
    if ($("tkGroupsBtn")) $("tkGroupsBtn").addEventListener("click", function () { tkLoadGroups(false); });
    if ($("tkGroupSearch")) $("tkGroupSearch").addEventListener("input", tkRenderGroups);
    if ($("tkGroupAddBtn")) $("tkGroupAddBtn").addEventListener("click", tkAddManual);
    if ($("tkGroupAddInput")) {
      $("tkGroupAddInput").addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); tkAddManual(); }
      });
    }
    if ($("inp_token_usage_umos")) {
      $("inp_token_usage_umos").addEventListener("change", tkRenderGroups);
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
    initInjectLog();
    initTokenline();
    watchSystemTheme();

    if ($("themeToggleBtn")) $("themeToggleBtn").addEventListener("click", toggleTheme);
    if ($("accentResetBtn")) $("accentResetBtn").addEventListener("click", resetAccent);
    if ($("refreshAllBtn")) $("refreshAllBtn").addEventListener("click", function () {
      loadState().then(function () {
        _tabLoaded["tab-tuning"] = true;
        tkLoadGroups(true);
        toast("已刷新", "ok");
      }).catch(function () {});
      _tabLoaded["tab-profile"] = true;
      pfLoad(false);
    });

    loadState().then(function () {
      reveal();
      // 揭开后再套一次主题色：data-boot 期间的内联变量可能被首帧覆盖
      applyAccent(CONFIG.ui_accent_color || "");
      setTimeout(function () { ensureTabLoaded("tab-tuning"); }, 120);
      setTimeout(function () { ensureTabLoaded("tab-profile"); }, 280);
    }, reveal);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
