(() => {
  "use strict";

  const THEME_STORAGE_KEY = "briefing-color-scheme";
  const CHARS_PER_SECOND = 4.3;
  const CLICKBAIT_RE = /(震惊|颠覆|史诗级|炸裂|吊打|遥遥领先|彻底改变|王炸|重磅炸弹|封神)/g;
  const TAB_OPTIONS = ["Intro", "模型更新", "开发者工具", "产品体验", "研究观察", "产业信号", "一线消息", "传闻/风向", "速览", "Outro"];

  const app = {
    state: null,
    cover: null,
    manifest: null,
    activeIndex: 0,
    searchQuery: "",
    activeFilter: "all",
    dirty: false,
    dragIndex: null,
    toastTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]
    ));
  }

  function uid(prefix) {
    return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
  }

  function authHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Review-Token": window.__REVIEW_TOKEN__ || "",
    };
  }

  function showToast(message, tone = "info") {
    const el = $("statusToast");
    if (!el) return;
    el.textContent = message;
    el.dataset.tone = tone;
    el.dataset.visible = "true";
    if (app.toastTimer) clearTimeout(app.toastTimer);
    app.toastTimer = setTimeout(() => {
      el.dataset.visible = "false";
    }, 3400);
  }

  function setDirty(flag) {
    app.dirty = Boolean(flag);
    const badge = $("dirtyBadge");
    if (badge) {
      badge.dataset.dirty = app.dirty ? "true" : "false";
      badge.textContent = app.dirty ? "未保存修改" : "已同步";
    }
  }

  /* =====================================================================
   * Theme Management (dark-mode & dark-mode-toggle compliant)
   * ===================================================================== */
  function readStoredTheme() {
    try {
      const val = localStorage.getItem(THEME_STORAGE_KEY);
      return val === "light" || val === "dark" ? val : "system";
    } catch {
      return "system";
    }
  }

  function applyThemeMeta(choice) {
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (!meta) return;
    meta.content = choice === "light" || choice === "dark" ? choice : "light dark";
  }

  function persistThemeChoice(choice) {
    try {
      if (choice === "light" || choice === "dark") {
        localStorage.setItem(THEME_STORAGE_KEY, choice);
      } else {
        localStorage.removeItem(THEME_STORAGE_KEY);
      }
    } catch {
      // Ignore storage restrictions; still apply to current document
    }
    applyThemeMeta(choice);
    syncThemeRadios();
  }

  function syncThemeRadios() {
    const current = readStoredTheme();
    for (const radio of document.querySelectorAll('input[name="colorSchemeChoice"]')) {
      radio.checked = radio.value === current;
    }
    updateSystemSchemeStatus();
  }

  function updateSystemSchemeStatus() {
    const label = $("systemSchemeStatus");
    if (!label || !window.matchMedia) return;
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    label.textContent = `自动适配系统日夜切换（当前系统：${isDark ? "深色" : "浅色"}）`;
  }

  function initThemeControls() {
    syncThemeRadios();
    for (const radio of document.querySelectorAll('input[name="colorSchemeChoice"]')) {
      radio.addEventListener("change", (e) => {
        if (e.target.checked) {
          persistThemeChoice(e.target.value);
        }
      });
    }
    window.addEventListener("storage", (e) => {
      if (e.key === THEME_STORAGE_KEY || e.key === null) {
        applyThemeMeta(readStoredTheme());
        syncThemeRadios();
      }
    });
    if (window.matchMedia) {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      // Never delete stored overrides on OS schedule flip; only refresh descriptive status text.
      mql.addEventListener("change", () => updateSystemSchemeStatus());
    }
  }

  /* =====================================================================
   * Native Dialog Helpers & closedby="any" Bounding-Rect Fallback
   * ===================================================================== */
  function initDialogs() {
    const dialogs = document.querySelectorAll("dialog");
    for (const dialog of dialogs) {
      dialog.addEventListener("close", () => {
        dialog.returnValue = "";
      });
    }

    for (const btn of document.querySelectorAll("[data-close-dialog]")) {
      btn.addEventListener("click", () => {
        const target = $(btn.dataset.closeDialog);
        if (target && target.open) target.close();
      });
    }

    if (!("closedBy" in HTMLDialogElement.prototype)) {
      const isOutside = (event, dialog) => {
        const rect = dialog.getBoundingClientRect();
        return (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        );
      };
      for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
        let pressedOutside = false;
        dialog.addEventListener("pointerdown", (e) => {
          pressedOutside = e.target === dialog && isOutside(e, dialog);
        });
        dialog.addEventListener("pointerup", (e) => {
          if (pressedOutside && e.target === dialog && isOutside(e, dialog)) {
            dialog.close();
          }
          pressedOutside = false;
        });
      }
    }
  }

  /* =====================================================================
   * Copy-Hygiene Linter & Timing Estimation
   * ===================================================================== */
  function estimateSeconds(text) {
    const clean = String(text || "").replace(/\s+/g, "");
    if (!clean.length) return 0;
    return Math.max(1.5, clean.length / CHARS_PER_SECOND);
  }

  function formatMmSs(totalSeconds) {
    const sec = Math.max(0, Math.round(totalSeconds));
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function lintSegmentCopy(seg) {
    const warnings = [];
    const text = String(seg.text || "");
    const title = String(seg.title || "");
    const combined = `${title} ${seg.caption || ""} ${text}`;

    if (/…|\.{3,}/.test(combined)) {
      warnings.push("检测到省略号残片（… 或 ...），请补全完整分句或直接删去。");
    }

    const pairs = [
      ["（", "）"],
      ["(", ")"],
      ["“", "”"],
      ["《", "》"],
      ["「", "」"],
    ];
    for (const [openCh, closeCh] of pairs) {
      const openCount = combined.split(openCh).length - 1;
      const closeCount = combined.split(closeCh).length - 1;
      if (openCount !== closeCount) {
        warnings.push(`标点配对不闭合：${openCh}${closeCh} 数量不一致（${openCount} vs ${closeCount}）。`);
      }
    }

    const hypeMatches = combined.match(CLICKBAIT_RE);
    if (hypeMatches && hypeMatches.length) {
      const unique = [...new Set(hypeMatches)].join("、");
      warnings.push(`包含夸张营销词（${unique}），建议改为客观可核对表述。`);
    }

    if (/https?:\/\/\S+/i.test(text)) {
      warnings.push("口播文稿中混入原始网址链接，TTS 朗读会失真。");
    }

    if (seg.signal_kind === "official_personnel" && !text.includes("未获官方公告确认") && !text.includes("一线消息")) {
      warnings.push("该条为一线个人社交动态，口播需保留“未获官方公告确认”提示。");
    }
    if (seg.signal_kind === "community_rumour" && !text.includes("未获官方公告确认") && !text.includes("传闻")) {
      warnings.push("该条为社区风向/传闻，口播需保留“未获官方公告确认”提示。");
    }

    return warnings;
  }

  /* =====================================================================
   * Filtering & Segment Helpers
   * ===================================================================== */
  function segmentBadgeText(seg, indexInAll) {
    if (seg.kind === "intro") return "开场";
    if (seg.kind === "outro") return "收尾";
    if (seg.kind === "ticker") return "速览";
    let newsOrder = 0;
    for (let i = 0; i <= indexInAll; i++) {
      if (app.state.segments[i]?.kind === "news" && app.state.segments[i]?.enabled !== false) {
        newsOrder += 1;
      }
    }
    return newsOrder > 0 ? String(newsOrder).padStart(2, "0") : "--";
  }

  function matchesFilter(seg) {
    if (app.activeFilter === "enabled" && seg.enabled === false) return false;
    if (app.activeFilter === "headline") {
      if (seg.kind !== "news" || (seg.editorial_tier || "headline") !== "headline") return false;
    }
    if (app.activeFilter === "brief") {
      if (seg.kind !== "news" || seg.editorial_tier !== "brief") return false;
    }
    const q = app.searchQuery.trim().toLowerCase();
    if (!q) return true;
    const blob = [
      seg.title,
      seg.caption,
      seg.text,
      seg.story_id,
      seg.active_tab,
      ...(seg.cards || []).map((c) => `${c.title} ${c.body}`),
    ]
      .join(" ")
      .toLowerCase();
    return blob.includes(q);
  }

  /* =====================================================================
   * Rendering Top Meta & Left Rail
   * ===================================================================== */
  function renderHeaderMeta() {
    if (!app.state) return;
    const segments = app.state.segments || [];
    const enabledSegs = segments.filter((s) => s.enabled !== false);
    const enabledNews = enabledSegs.filter((s) => s.kind === "news");
    const totalSec = enabledSegs.reduce((acc, s) => acc + estimateSeconds(s.text), 0);

    $("runMeta").textContent =
      `期刊日期：${app.state.run_date || "-"} · 启用新闻 ${enabledNews.length} 条 · 预计总口播 ${formatMmSs(totalSec)} · 目录：${app.state.run_dir || ""}`;
    $("countPill").textContent = `${enabledSegs.length} / ${segments.length} 段`;
  }

  function renderSidebarList() {
    const listEl = $("segmentList");
    if (!listEl || !app.state) return;
    const prevScroll = listEl.scrollTop;
    listEl.innerHTML = "";

    const segments = app.state.segments || [];
    let visibleCount = 0;

    segments.forEach((seg, idx) => {
      if (!matchesFilter(seg)) return;
      visibleCount += 1;
      const warnings = lintSegmentCopy(seg);
      const badge = segmentBadgeText(seg, idx);
      const tierLabel = seg.kind === "news" ? ((seg.editorial_tier || "headline") === "brief" ? "快讯" : "头条") : seg.kind;

      const li = document.createElement("li");
      li.className = "segment-row";
      li.dataset.index = String(idx);
      li.dataset.active = idx === app.activeIndex ? "true" : "false";
      li.dataset.disabled = seg.enabled === false ? "true" : "false";
      li.draggable = true;

      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = seg.enabled !== false;
      check.setAttribute("aria-label", `启用段落：${seg.title || badge}`);
      check.dataset.action = "toggle-seg";
      check.dataset.index = String(idx);

      const selectBtn = document.createElement("button");
      selectBtn.type = "button";
      selectBtn.className = "segment-select-btn";
      selectBtn.dataset.action = "select-seg";
      selectBtn.dataset.index = String(idx);
      selectBtn.innerHTML = `
        <span class="segment-row-title">[${escapeHtml(badge)}] ${escapeHtml(seg.title || seg.caption || "未命名段落")}</span>
        <span class="segment-row-meta">
          <span>${escapeHtml(tierLabel)}</span>
          <span>· 卡 ${(seg.cards || []).length}</span>
          <span>· 图 ${(seg.images || []).filter((i) => i.enabled !== false || i.required).length}</span>
          <span>· ~${Math.round(estimateSeconds(seg.text))}s</span>
          ${warnings.length ? `<span class="pill pill-warn">! ${warnings.length}</span>` : ""}
        </span>
      `;

      const orderBox = document.createElement("div");
      orderBox.className = "segment-OrderBtns";

      const upBtn = document.createElement("button");
      upBtn.type = "button";
      upBtn.className = "icon-btn";
      upBtn.textContent = "↑";
      upBtn.disabled = idx === 0;
      upBtn.setAttribute("aria-label", "上移此段");
      upBtn.dataset.action = "move-seg";
      upBtn.dataset.index = String(idx);
      upBtn.dataset.delta = "-1";

      const downBtn = document.createElement("button");
      downBtn.type = "button";
      downBtn.className = "icon-btn";
      downBtn.textContent = "↓";
      downBtn.disabled = idx === segments.length - 1;
      downBtn.setAttribute("aria-label", "下移此段");
      downBtn.dataset.action = "move-seg";
      downBtn.dataset.index = String(idx);
      downBtn.dataset.delta = "1";

      orderBox.append(upBtn, downBtn);
      li.append(check, selectBtn, orderBox);
      listEl.appendChild(li);
    });

    if (visibleCount === 0) {
      const emptyLi = document.createElement("li");
      emptyLi.className = "empty-state";
      emptyLi.textContent = "没有匹配的段落";
      listEl.appendChild(emptyLi);
    }

    listEl.scrollTop = prevScroll;
  }

  /* =====================================================================
   * Rendering Main Segment Editor
   * ===================================================================== */
  function renderCopyMetricsAndLint() {
    const seg = app.state?.segments?.[app.activeIndex];
    if (!seg) return;
    const statsEl = $("copyStatsBar");
    const lintEl = $("copyHygieneList");
    if (statsEl) {
      const charCount = String(seg.text || "").replace(/\s+/g, "").length;
      const secs = estimateSeconds(seg.text);
      statsEl.innerHTML = `
        <span>字数：<strong>${charCount}</strong> 字</span>
        <span>预计播报时长：<strong>${secs.toFixed(1)} 秒</strong>（按 ${CHARS_PER_SECOND} 字/秒估算）</span>
      `;
    }
    if (lintEl) {
      const warnings = lintSegmentCopy(seg);
      lintEl.innerHTML = warnings
        .map((msg) => `<li class="hygiene-item"><span aria-hidden="true">⚠</span><span>${escapeHtml(msg)}</span></li>`)
        .join("");
    }
  }

  function renderEditorPane() {
    const mount = $("editorMount");
    if (!mount || !app.state) return;
    const segments = app.state.segments || [];
    if (!segments.length) {
      mount.innerHTML = `<p class="empty-state">暂无段落，请点击左侧“新增快讯段落”。</p>`;
      return;
    }
    app.activeIndex = Math.max(0, Math.min(app.activeIndex, segments.length - 1));
    const seg = segments[app.activeIndex];
    const cards = Array.isArray(seg.cards) ? seg.cards : (seg.cards = []);
    const images = Array.isArray(seg.images) ? seg.images : (seg.images = []);

    const tabChoices = TAB_OPTIONS.includes(seg.active_tab)
      ? TAB_OPTIONS
      : [seg.active_tab || "模型更新", ...TAB_OPTIONS];

    mount.innerHTML = `
      <div class="editor-header">
        <div class="editor-badges">
          <span class="pill pill-accent">${escapeHtml(seg.kind || "news")}</span>
          ${seg.story_id ? `<span class="pill">story_id: ${escapeHtml(seg.story_id)}</span>` : `<span class="pill">手动段落</span>`}
          ${seg.signal_kind ? `<span class="pill pill-warn">信号源: ${escapeHtml(seg.signal_kind)}</span>` : ""}
        </div>

        <div class="editor-actions">
          <label class="switch-label">
            <input type="checkbox" data-field="enabled" ${seg.enabled === false ? "" : "checked"}>
            <span>进入成片</span>
          </label>
          <button type="button" class="btn btn-small btn-secondary" data-editor-action="move-up" ${app.activeIndex === 0 ? "disabled" : ""}>上移</button>
          <button type="button" class="btn btn-small btn-secondary" data-editor-action="move-down" ${app.activeIndex === segments.length - 1 ? "disabled" : ""}>下移</button>
          ${!seg.story_id && seg.kind === "news" ? `<button type="button" class="btn btn-small btn-danger" data-editor-action="delete-seg">删除段落</button>` : ""}
        </div>
      </div>

      <div class="editor-section">
        <h3>标题与编排元数据</h3>
        <div class="meta-grid">
          <div class="field span-2">
            <label for="segTitleInput">主标题（Title）</label>
            <input type="text" id="segTitleInput" data-field="title" value="${escapeHtml(seg.title || "")}">
          </div>

          <div class="field">
            <label for="segCaptionInput">画面短题（Caption）</label>
            <input type="text" id="segCaptionInput" data-field="caption" value="${escapeHtml(seg.caption || "")}">
          </div>

          <div class="field">
            <label for="segTierSelect">报道层级（Tier）</label>
            <select id="segTierSelect" data-field="editorial_tier">
              <option value="headline" ${(seg.editorial_tier || "headline") === "headline" ? "selected" : ""}>头条精讲（headline）</option>
              <option value="brief" ${seg.editorial_tier === "brief" ? "selected" : ""}>快讯简报（brief）</option>
            </select>
          </div>

          <div class="field">
            <label for="segTabSelect">顶部栏目（Active Tab）</label>
            <select id="segTabSelect" data-field="active_tab">
              ${tabChoices.map((t) => `<option value="${escapeHtml(t)}" ${seg.active_tab === t ? "selected" : ""}>${escapeHtml(t)}</option>`).join("")}
            </select>
          </div>

          <div class="field">
            <label for="segBottomInput">底部时间轴短标（Bottom Label）</label>
            <input type="text" id="segBottomInput" data-field="bottom_active" value="${escapeHtml(seg.bottom_active || "")}">
          </div>
        </div>
      </div>

      <div class="editor-section">
        <h3>口播解说文稿（TTS 与字幕唯一来源）</h3>
        <div class="field">
          <label for="segTextArea" class="sr-only">口播解说文稿</label>
          <textarea id="segTextArea" rows="6" data-field="text">${escapeHtml(seg.text || "")}</textarea>
          <div id="copyStatsBar" class="copy-stats"></div>
          <ul id="copyHygieneList" class="hygiene-list" aria-live="polite"></ul>
        </div>
      </div>

      <div class="editor-section">
        <div class="sidebar-header" style="margin-block-end: 0.6rem;">
          <h3>画面信息卡片（${cards.length} 张）</h3>
          <button type="button" class="btn btn-small btn-secondary" data-editor-action="add-card">+ 添加卡片</button>
        </div>
        <div class="cards-stack">
          ${
            cards.length
              ? cards
                  .map(
                    (card, cIdx) => `
                <div class="beat-card-row">
                  <input type="text" aria-label="卡片图标 ${cIdx + 1}" placeholder="图标" value="${escapeHtml(card.icon || "▣")}" data-card-index="${cIdx}" data-card-field="icon">
                  <input type="text" aria-label="卡片标题 ${cIdx + 1}" placeholder="卡片短标题" value="${escapeHtml(card.title || "")}" data-card-index="${cIdx}" data-card-field="title">
                  <input type="text" aria-label="卡片内容 ${cIdx + 1}" placeholder="高密度事实要点（不截断）" value="${escapeHtml(card.body || "")}" data-card-index="${cIdx}" data-card-field="body">
                  <div class="beat-card-actions">
                    <button type="button" class="icon-btn" data-editor-action="move-card" data-card-index="${cIdx}" data-delta="-1" ${cIdx === 0 ? "disabled" : ""} aria-label="上移卡片">↑</button>
                    <button type="button" class="icon-btn" data-editor-action="move-card" data-card-index="${cIdx}" data-delta="1" ${cIdx === cards.length - 1 ? "disabled" : ""} aria-label="下移卡片">↓</button>
                    <button type="button" class="btn btn-small btn-danger" data-editor-action="delete-card" data-card-index="${cIdx}">删除</button>
                  </div>
                </div>`
                  )
                  .join("")
              : `<p class="field-hint">当前段落无信息卡片（如收尾页）。可按需点击“+ 添加卡片”。</p>`
          }
        </div>
      </div>

      <div class="editor-section">
        <div class="sidebar-header" style="margin-block-end: 0.6rem;">
          <h3>证据截图与配图（${images.length} 张）</h3>
          <label class="btn btn-small btn-secondary file-picker-label">
            <span>+ 上传配图</span>
            <input type="file" accept="image/*" class="file-picker-input" data-editor-upload="append">
          </label>
        </div>

        <div class="evidence-grid">
          ${
            images.length
              ? images
                  .map((img, iIdx) => {
                    const src = img.url || (img.path ? `/media?path=${encodeURIComponent(img.path)}` : "");
                    const isReq = Boolean(img.required);
                    return `
                  <article class="evidence-card">
                    ${
                      src
                        ? `<button type="button" class="thumb-button" data-editor-action="lightbox" data-img-index="${iIdx}" aria-label="点击放大查看：${escapeHtml(img.label || "证据截图")}">
                             <img class="thumb-img" src="${escapeHtml(src)}" alt="${escapeHtml(img.label || "证据截图")}" width="320" height="180" loading="lazy">
                           </button>`
                        : `<div class="thumb-placeholder">无可用图像路径</div>`
                    }
                    <div class="field">
                      <label>图注标题</label>
                      <input type="text" value="${escapeHtml(img.label || "")}" data-img-index="${iIdx}" data-img-field="label">
                    </div>
                    <div class="field">
                      <label>来源标识</label>
                      <input type="text" value="${escapeHtml(img.source || "")}" data-img-index="${iIdx}" data-img-field="source">
                    </div>
                    <div class="evidence-footer">
                      <div class="editor-badges">
                        ${
                          isReq
                            ? `<span class="pill pill-warn" title="manifest.json 标记为必选一手存证截图">🔒 必选存证</span>`
                            : `<label class="switch-label">
                                 <input type="checkbox" data-img-index="${iIdx}" data-img-field="enabled" ${img.enabled === false ? "" : "checked"}>
                                 <span>启用</span>
                               </label>`
                        }
                        ${
                          img.source_url
                            ? `<a href="${escapeHtml(img.source_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-small btn-ghost">原帖链接 ↗</a>`
                            : ""
                        }
                      </div>

                      <div class="beat-card-actions">
                        <label class="btn btn-small btn-secondary file-picker-label">
                          <span>替换</span>
                          <input type="file" accept="image/*" class="file-picker-input" data-editor-upload="replace" data-img-index="${iIdx}">
                        </label>
                        ${
                          isReq
                            ? ""
                            : `<button type="button" class="btn btn-small btn-danger" data-editor-action="delete-img" data-img-index="${iIdx}">移除</button>`
                        }
                      </div>
                    </div>
                  </article>`;
                  })
                  .join("")
              : `<p class="field-hint">暂无证据截图。若该新闻含社交一手存证截图，保存时会自动从 manifest.json 校验并补齐必选截图。</p>`
          }
        </div>
      </div>

      <div class="editor-section">
        <h3>审稿备注（仅内部留档，不进入视频）</h3>
        <div class="field">
          <label for="segNotesArea" class="sr-only">审稿备注</label>
          <textarea id="segNotesArea" rows="2" data-field="notes" placeholder="记录事实核查说明或修改原因…">${escapeHtml(seg.notes || "")}</textarea>
        </div>
      </div>
    `;

    renderCopyMetricsAndLint();
  }

  function renderAll() {
    renderHeaderMeta();
    renderSidebarList();
    renderEditorPane();
  }

  /* =====================================================================
   * Mutations & File Uploads
   * ===================================================================== */
  function moveSegment(fromIdx, toIdx) {
    const segs = app.state?.segments;
    if (!segs || toIdx < 0 || toIdx >= segs.length || fromIdx === toIdx) return;
    const [moved] = segs.splice(fromIdx, 1);
    segs.splice(toIdx, 0, moved);
    if (app.activeIndex === fromIdx) {
      app.activeIndex = toIdx;
    } else if (fromIdx < app.activeIndex && toIdx >= app.activeIndex) {
      app.activeIndex -= 1;
    } else if (fromIdx > app.activeIndex && toIdx <= app.activeIndex) {
      app.activeIndex += 1;
    }
    setDirty(true);
    renderAll();
  }

  async function uploadImageFile(file) {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(reader.result));
      reader.addEventListener("error", () => reject(new Error("读取本地图片失败")));
      reader.readAsDataURL(file);
    });
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ filename: file.name, dataUrl }),
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || "图片上传失败");
    return payload;
  }

  async function saveWorkbenchState() {
    if (!app.state) return;
    const saveBtn = $("saveBtn");
    if (saveBtn) saveBtn.disabled = true;
    try {
      const res = await fetch("/api/save", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(app.state),
      });
      const payload = await res.json();
      if (!res.ok) {
        throw new Error(payload.error || "保存失败");
      }
      app.state = payload.state;
      if (payload.cover) app.cover = payload.cover;
      setDirty(false);
      renderAll();
      showToast(`定稿已保存（共 ${payload.news_count ?? 0} 条新闻），已标记待渲染`);
    } catch (err) {
      showToast(err.message || "保存失败", "error");
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  /* =====================================================================
   * Cover Studio & Provenance Inspector & Lightbox
   * ===================================================================== */
  async function openCoverStudio() {
    try {
      const res = await fetch("/api/cover");
      if (res.ok) app.cover = await res.json();
    } catch {
      // Keep cached cover if any
    }
    const cov = app.cover || {};
    $("coverHeadline").value = cov.headline || "";
    $("coverSubheadline").value = cov.subheadline || "";
    $("coverEyebrow").value = cov.eyebrow || "AI DAILY BRIEF / AI 日报";
    $("coverBadge").value = cov.badge || "AI 动态";
    $("coverHighlights").value = Array.isArray(cov.highlights) ? cov.highlights.join("\n") : "";
    $("coverEntities").value = Array.isArray(cov.entities) ? cov.entities.join(", ") : "";
    $("coverStoryCountHint").textContent = `当前启用新闻数（storyCount）：${cov.storyCount ?? 0} 条`;
    $("coverDialog").showModal();
  }

  async function submitCoverForm(event) {
    event.preventDefault();
    const highlights = $("coverHighlights")
      .value.split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 4);
    const entities = $("coverEntities")
      .value.split(/[,，\n]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 8);

    const payload = {
      eyebrow: $("coverEyebrow").value.trim(),
      headline: $("coverHeadline").value.trim(),
      subheadline: $("coverSubheadline").value.trim(),
      badge: $("coverBadge").value.trim(),
      highlights,
      entities,
    };

    try {
      const res = await fetch("/api/cover", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "保存封面失败");
      app.cover = data.cover;
      $("coverDialog").close();
      showToast("封面文案已更新并写入 review/cover.json");
    } catch (err) {
      showToast(err.message || "保存封面失败", "error");
    }
  }

  async function openProvenanceInspector() {
    if (!app.manifest) {
      try {
        const res = await fetch("/api/manifest");
        if (res.ok) app.manifest = await res.json();
      } catch {
        app.manifest = { selected: [] };
      }
    }
    const container = $("provenanceBody");
    const selected = Array.isArray(app.manifest?.selected) ? app.manifest.selected : [];
    const activeSeg = app.state?.segments?.[app.activeIndex];
    const targetStoryId = activeSeg?.story_id || "";

    const matched = targetStoryId
      ? selected.filter((item) => item?.story_spec?.story_id === targetStoryId)
      : selected;
    const cardsToRender = matched.length ? matched : selected;

    if (!cardsToRender.length) {
      container.innerHTML = `<p class="empty-state">当前运行目录未找到 manifest.json 冻结条目（可能为离线草稿或空数据运行）。</p>`;
    } else {
      container.innerHTML = cardsToRender
        .map((entry) => {
          const spec = entry.story_spec || {};
          const claims = Array.isArray(spec.claims) ? spec.claims : [];
          const evidence = Array.isArray(entry.evidence) ? entry.evidence : [];
          const keyFacts = Array.isArray(entry.key_facts) ? entry.key_facts : [];
          return `
            <article class="provenance-card">
              <h3>${escapeHtml(entry.title || entry.event_title || spec.entity || "新闻条目")} <span class="pill">${escapeHtml(spec.story_id || "")}</span></h3>
              ${
                claims.length
                  ? `<p class="field-hint">冻结断言（Fact Claims）：</p>
                     <ul class="claim-list">
                       ${claims
                         .map(
                           (c) =>
                             `<li><code>${escapeHtml(c.claim_id || "")}</code> [${escapeHtml(c.kind || c.claim_type || "fact")}] ${escapeHtml(c.text || "")}</li>`
                         )
                         .join("")}
                     </ul>`
                  : keyFacts.length
                  ? `<p class="field-hint">原始关键事实（Key Facts）：</p>
                     <ul class="claim-list">
                       ${keyFacts.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}
                     </ul>`
                  : ""
              }
              ${
                evidence.length
                  ? `<p class="field-hint" style="margin-block-start: 0.65rem;">一手证据链接（Evidence Links）：</p>
                     <ul class="source-link-list">
                       ${evidence
                         .map((ev) => {
                           const url = ev.url || ev.final_url || "";
                           const label = ev.source || url || "来源";
                           return `<li>
                             <strong>${escapeHtml(label)}</strong>
                             ${ev.screenshot_status === "captured" ? `<span class="pill pill-accent">已截图</span>` : ""}
                             ${url ? ` — <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>` : ""}
                           </li>`;
                         })
                         .join("")}
                     </ul>`
                  : ""
              }
            </article>
          `;
        })
        .join("");
    }
    $("provenanceDialog").showModal();
  }

  function openLightbox(imageItem) {
    if (!imageItem) return;
    const src = imageItem.url || (imageItem.path ? `/media?path=${encodeURIComponent(imageItem.path)}` : "");
    if (!src) return;
    const imgEl = $("lightboxImg");
    imgEl.src = src;
    imgEl.alt = imageItem.label || "证据原图预览";
    $("lightboxDialogTitle").textContent = imageItem.label || "证据原图预览";
    $("lightboxMeta").textContent = [imageItem.source, imageItem.path].filter(Boolean).join(" · ");
    $("lightboxDialog").showModal();
  }

  /* =====================================================================
   * Event Wiring (100% addEventListener, zero inline handlers)
   * ===================================================================== */
  function bindEvents() {
    $("saveBtn").addEventListener("click", () => saveWorkbenchState());
    $("openCoverBtn").addEventListener("click", () => openCoverStudio());
    $("openProvenanceBtn").addEventListener("click", () => openProvenanceInspector());
    $("openSettingsBtn").addEventListener("click", () => {
      syncThemeRadios();
      $("settingsDialog").showModal();
    });

    $("coverForm").addEventListener("submit", submitCoverForm);
    $("fillLeadHeadlineBtn").addEventListener("click", () => {
      const firstNews = (app.state?.segments || []).find((s) => s.kind === "news" && s.enabled !== false);
      if (firstNews) {
        $("coverHeadline").value = firstNews.title || firstNews.caption || "";
      }
    });

    $("segmentSearch").addEventListener("input", (e) => {
      app.searchQuery = e.target.value || "";
      renderSidebarList();
    });

    for (const pill of document.querySelectorAll(".filter-pill")) {
      pill.addEventListener("click", () => {
        app.activeFilter = pill.dataset.filter || "all";
        for (const p of document.querySelectorAll(".filter-pill")) {
          p.setAttribute("aria-pressed", p === pill ? "true" : "false");
        }
        renderSidebarList();
      });
    }

    $("addNewsBtn").addEventListener("click", () => {
      if (!app.state) return;
      const newSeg = {
        id: uid("seg"),
        kind: "news",
        editorial_tier: "brief",
        enabled: true,
        title: "新增 AI 快讯",
        headline: "新增 AI 快讯",
        caption: "新增 AI 快讯",
        text: "",
        active_tab: "模型更新",
        bottom_active: "",
        cards: [{ icon: "▣", title: "核心事实", body: "" }],
        images: [],
        visual_pages: [],
        notes: "",
      };
      const segs = app.state.segments;
      const outroIdx = segs.findIndex((s) => s.kind === "outro");
      const insertAt = outroIdx >= 0 ? outroIdx : segs.length;
      segs.splice(insertAt, 0, newSeg);
      app.activeIndex = insertAt;
      setDirty(true);
      renderAll();
    });

    /* Sidebar click, change, drag-and-drop & keyboard navigation */
    const listEl = $("segmentList");
    listEl.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const idx = Number(btn.dataset.index);
      if (Number.isNaN(idx)) return;
      if (btn.dataset.action === "select-seg") {
        app.activeIndex = idx;
        renderAll();
      } else if (btn.dataset.action === "move-seg") {
        moveSegment(idx, idx + Number(btn.dataset.delta || 0));
      }
    });

    listEl.addEventListener("change", (e) => {
      const target = e.target;
      if (target.dataset.action === "toggle-seg") {
        const idx = Number(target.dataset.index);
        if (app.state?.segments?.[idx]) {
          app.state.segments[idx].enabled = target.checked;
          setDirty(true);
          renderAll();
        }
      }
    });

    listEl.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const buttons = [...listEl.querySelectorAll(".segment-select-btn")];
      if (!buttons.length) return;
      e.preventDefault();
      const currentPos = buttons.findIndex((b) => Number(b.dataset.index) === app.activeIndex);
      const delta = e.key === "ArrowDown" ? 1 : -1;
      const nextBtn = buttons[Math.max(0, Math.min(buttons.length - 1, currentPos + delta))];
      if (nextBtn) {
        app.activeIndex = Number(nextBtn.dataset.index);
        renderAll();
        const refreshed = listEl.querySelector(`.segment-select-btn[data-index="${app.activeIndex}"]`);
        if (refreshed) refreshed.focus();
      }
    });

    listEl.addEventListener("dragstart", (e) => {
      const row = e.target.closest(".segment-row");
      if (!row) return;
      app.dragIndex = Number(row.dataset.index);
      e.dataTransfer.effectAllowed = "move";
    });

    listEl.addEventListener("dragover", (e) => {
      const row = e.target.closest(".segment-row");
      if (!row || app.dragIndex === null) return;
      e.preventDefault();
      for (const r of listEl.querySelectorAll(".segment-row")) {
        r.dataset.dragOver = r === row ? "true" : "false";
      }
    });

    listEl.addEventListener("dragleave", (e) => {
      const row = e.target.closest(".segment-row");
      if (row) row.dataset.dragOver = "false";
    });

    listEl.addEventListener("drop", (e) => {
      e.preventDefault();
      for (const r of listEl.querySelectorAll(".segment-row")) r.dataset.dragOver = "false";
      const row = e.target.closest(".segment-row");
      if (!row || app.dragIndex === null) return;
      const targetIdx = Number(row.dataset.index);
      const fromIdx = app.dragIndex;
      app.dragIndex = null;
      moveSegment(fromIdx, targetIdx);
    });

    /* Main Editor delegated input/change/click handlers */
    const editorMount = $("editorMount");
    editorMount.addEventListener("input", (e) => {
      const seg = app.state?.segments?.[app.activeIndex];
      if (!seg) return;
      const t = e.target;

      if (t.dataset.field && t.type !== "checkbox") {
        seg[t.dataset.field] = t.value;
        if (t.dataset.field === "title") seg.headline = t.value;
        setDirty(true);
        renderHeaderMeta();
        renderSidebarList();
        if (t.dataset.field === "text" || t.dataset.field === "title" || t.dataset.field === "caption") {
          renderCopyMetricsAndLint();
        }
        return;
      }

      if (t.dataset.cardIndex !== undefined && t.dataset.cardField) {
        const card = seg.cards?.[Number(t.dataset.cardIndex)];
        if (card) {
          card[t.dataset.cardField] = t.value;
          setDirty(true);
        }
        return;
      }

      if (t.dataset.imgIndex !== undefined && t.dataset.imgField && t.type !== "checkbox") {
        const img = seg.images?.[Number(t.dataset.imgIndex)];
        if (img) {
          img[t.dataset.imgField] = t.value;
          setDirty(true);
        }
      }
    });

    editorMount.addEventListener("change", async (e) => {
      const seg = app.state?.segments?.[app.activeIndex];
      if (!seg) return;
      const t = e.target;

      if (t.dataset.field === "enabled" && t.type === "checkbox") {
        seg.enabled = t.checked;
        setDirty(true);
        renderAll();
        return;
      }

      if (t.dataset.field === "editorial_tier" || t.dataset.field === "active_tab") {
        seg[t.dataset.field] = t.value;
        setDirty(true);
        renderSidebarList();
        return;
      }

      if (t.dataset.imgIndex !== undefined && t.dataset.imgField === "enabled") {
        const img = seg.images?.[Number(t.dataset.imgIndex)];
        if (img && !img.required) {
          img.enabled = t.checked;
          setDirty(true);
          renderSidebarList();
        }
        return;
      }

      if (t.dataset.editorUpload && t.files?.[0]) {
        const file = t.files[0];
        try {
          showToast("正在上传图片……");
          const uploaded = await uploadImageFile(file);
          if (t.dataset.editorUpload === "replace") {
            const idx = Number(t.dataset.imgIndex);
            if (seg.images?.[idx]) {
              seg.images[idx] = {
                ...seg.images[idx],
                ...uploaded,
                required: Boolean(seg.images[idx].required),
              };
            }
          } else {
            seg.images.push(uploaded);
          }
          setDirty(true);
          renderAll();
          showToast("图片已上传并关联到当前段落");
        } catch (err) {
          showToast(err.message || "上传失败", "error");
        } finally {
          t.value = "";
        }
      }
    });

    editorMount.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-editor-action]");
      if (!btn) return;
      const seg = app.state?.segments?.[app.activeIndex];
      if (!seg) return;
      const action = btn.dataset.editorAction;

      if (action === "move-up") {
        moveSegment(app.activeIndex, app.activeIndex - 1);
      } else if (action === "move-down") {
        moveSegment(app.activeIndex, app.activeIndex + 1);
      } else if (action === "delete-seg") {
        app.state.segments.splice(app.activeIndex, 1);
        app.activeIndex = Math.max(0, app.activeIndex - 1);
        setDirty(true);
        renderAll();
      } else if (action === "add-card") {
        seg.cards.push({ icon: "▣", title: "", body: "" });
        setDirty(true);
        renderAll();
      } else if (action === "delete-card") {
        seg.cards.splice(Number(btn.dataset.cardIndex), 1);
        setDirty(true);
        renderAll();
      } else if (action === "move-card") {
        const from = Number(btn.dataset.cardIndex);
        const to = from + Number(btn.dataset.delta || 0);
        if (to >= 0 && to < seg.cards.length) {
          const [moved] = seg.cards.splice(from, 1);
          seg.cards.splice(to, 0, moved);
          setDirty(true);
          renderEditorPane();
        }
      } else if (action === "delete-img") {
        const idx = Number(btn.dataset.imgIndex);
        if (seg.images?.[idx] && !seg.images[idx].required) {
          seg.images.splice(idx, 1);
          setDirty(true);
          renderAll();
        }
      } else if (action === "lightbox") {
        openLightbox(seg.images?.[Number(btn.dataset.imgIndex)]);
      }
    });

    /* Global Ctrl+S / Cmd+S & Unsaved Changes Guard */
    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveWorkbenchState();
      }
    });

    window.addEventListener("beforeunload", (e) => {
      if (app.dirty) {
        e.preventDefault();
      }
    });
  }

  /* =====================================================================
   * Bootstrap
   * ===================================================================== */
  async function bootstrap() {
    initThemeControls();
    initDialogs();
    bindEvents();

    try {
      const [stateRes, coverRes, manifestRes] = await Promise.all([
        fetch("/api/state"),
        fetch("/api/cover"),
        fetch("/api/manifest"),
      ]);
      if (!stateRes.ok) throw new Error("无法加载 /api/state");
      app.state = await stateRes.json();
      if (coverRes.ok) app.cover = await coverRes.json();
      if (manifestRes.ok) app.manifest = await manifestRes.json();
      setDirty(false);
      renderAll();
    } catch (err) {
      $("editorMount").innerHTML = `<p class="empty-state">加载失败：${escapeHtml(err.message || String(err))}</p>`;
    }
  }

  bootstrap();
})();
