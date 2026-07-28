from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .render_contract import mark_render_required


REVIEW_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>AI 日报审稿台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f2ea;
      --panel: rgba(255,255,255,.88);
      --ink: #24302d;
      --muted: #70817a;
      --line: #d9ded8;
      --accent: #2b8a7e;
      --danger: #b64d4d;
      --shadow: 0 18px 60px rgba(40,53,50,.13);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 10% 0%, rgba(122,188,178,.35), transparent 35%),
        radial-gradient(circle at 85% 8%, rgba(247,203,126,.28), transparent 32%),
        var(--bg);
      color: var(--ink);
    }
    header {
      position: sticky;
      top: 0;
      z-index: 10;
      backdrop-filter: blur(16px);
      background: rgba(245,242,234,.82);
      border-bottom: 1px solid rgba(217,222,216,.8);
      padding: 18px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 24px; letter-spacing: .02em; }
    .sub { margin-top: 5px; color: var(--muted); font-size: 13px; }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    button, .file-btn {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 9px 14px;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 8px 24px rgba(40,53,50,.08);
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.danger { color: var(--danger); }
    button.small { padding: 6px 10px; font-size: 12px; box-shadow: none; }
    main {
      display: grid;
      grid-template-columns: 300px minmax(0,1fr);
      gap: 20px;
      padding: 22px 28px 46px;
    }
    aside, .editor, .segment {
      background: var(--panel);
      border: 1px solid rgba(217,222,216,.8);
      border-radius: 24px;
      box-shadow: var(--shadow);
    }
    aside {
      position: sticky;
      top: 92px;
      height: calc(100vh - 118px);
      overflow: auto;
      padding: 16px;
    }
    .list-item {
      width: 100%;
      border: 1px solid transparent;
      background: rgba(255,255,255,.68);
      text-align: left;
      border-radius: 16px;
      padding: 12px;
      margin-bottom: 8px;
      box-shadow: none;
      display: block;
    }
    .list-item.active { border-color: var(--accent); background: #eef8f5; }
    .list-title { font-weight: 800; line-height: 1.35; }
    .list-meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .editor { padding: 20px; min-height: 70vh; }
    .empty { color: var(--muted); padding: 40px; text-align: center; }
    .segment { padding: 20px; box-shadow: none; }
    .row { display: grid; grid-template-columns: 120px minmax(0,1fr); gap: 12px; align-items: start; margin: 12px 0; }
    label { color: var(--muted); font-weight: 800; padding-top: 8px; }
    input[type="text"], textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      outline: none;
    }
    textarea { min-height: 140px; resize: vertical; line-height: 1.65; }
    .cards, .images { display: grid; gap: 10px; }
    .mini-card, .image-card {
      border: 1px solid var(--line);
      background: rgba(255,255,255,.72);
      border-radius: 18px;
      padding: 12px;
    }
    .mini-card-grid { display: grid; grid-template-columns: 1fr 1.5fr auto; gap: 8px; }
    .image-card {
      display: grid;
      grid-template-columns: 168px minmax(0,1fr) auto;
      gap: 12px;
      align-items: center;
    }
    .thumb {
      height: 96px;
      border-radius: 14px;
      background: #e8ebe7;
      object-fit: cover;
      width: 100%;
      border: 1px solid var(--line);
    }
    .hint { font-size: 12px; color: var(--muted); line-height: 1.55; }
    .topline { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
    .status { color: var(--muted); font-size: 13px; }
    .pill { border-radius: 999px; background: #eef2ef; padding: 5px 9px; color: var(--muted); font-size: 12px; font-weight: 800; }
    .switch { display: flex; align-items: center; gap: 8px; font-weight: 800; color: var(--muted); }
    input[type=file] { display: none; }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      aside { position: static; height: auto; }
      .row, .image-card, .mini-card-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AI 日报审稿台</h1>
      <div class="sub" id="runMeta">加载中……</div>
    </div>
    <div class="toolbar">
      <button onclick="addNews()">新增新闻</button>
      <button onclick="saveState()" class="primary">保存并生成最终稿</button>
      <button onclick="reloadState()">重新加载</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="topline">
        <strong>稿件列表</strong>
        <span class="pill" id="countPill">0 条</span>
      </div>
      <div id="segmentList"></div>
      <p class="hint">提示：关闭“进入成片”相当于删除该条新闻；拖动暂未做，先用上移/下移调整顺序。</p>
    </aside>
    <section class="editor" id="editor"></section>
  </main>
  <script>
    let state = null;
    let active = 0;

    const $ = (id) => document.getElementById(id);

    function uid(prefix) {
      return prefix + "-" + Math.random().toString(16).slice(2) + Date.now().toString(16);
    }

    async function reloadState() {
      const res = await fetch("/api/state");
      state = await res.json();
      active = Math.min(active, Math.max(0, state.segments.length - 1));
      render();
    }

    function segmentLabel(seg, idx) {
      const kind = seg.kind === "intro" ? "开场" : seg.kind === "outro" ? "收尾" : String(idx + 1).padStart(2, "0");
      return `${kind} ${seg.title || seg.caption || "未命名"}`;
    }

    function render() {
      $("runMeta").textContent = `${state.run_date || ""} · ${state.run_dir || ""}`;
      $("countPill").textContent = `${state.segments.filter(s => s.enabled !== false).length} 条`;
      const list = $("segmentList");
      list.innerHTML = "";
      state.segments.forEach((seg, idx) => {
        const btn = document.createElement("button");
        btn.className = "list-item" + (idx === active ? " active" : "");
        btn.onclick = () => { active = idx; render(); };
        btn.innerHTML = `<div class="list-title">${escapeHtml(segmentLabel(seg, idx))}</div>
          <div class="list-meta">${seg.enabled === false ? "不进成片" : "进入成片"} · 卡片 ${seg.cards.length} · 图片 ${seg.images.length}</div>`;
        list.appendChild(btn);
      });
      renderEditor();
    }

    function renderEditor() {
      const root = $("editor");
      if (!state || !state.segments.length) {
        root.innerHTML = '<div class="empty">暂无新闻，点击“新增新闻”。</div>';
        return;
      }
      const seg = state.segments[active];
      root.innerHTML = `
        <div class="segment">
          <div class="topline">
            <div>
              <span class="pill">${escapeHtml(seg.kind || "news")}</span>
              <span class="status">ID: ${escapeHtml(seg.id || "")}</span>
            </div>
            <div class="toolbar">
              <button class="small" onclick="moveSeg(-1)">上移</button>
              <button class="small" onclick="moveSeg(1)">下移</button>
              <button class="small danger" onclick="deleteSeg()">删除</button>
            </div>
          </div>
          <div class="row"><label>进入成片</label><div class="switch"><input type="checkbox" id="enabled" ${seg.enabled === false ? "" : "checked"} onchange="setField('enabled', this.checked)" /> 是</div></div>
          <div class="row"><label>类型</label><select id="kind" onchange="setField('kind', this.value)">
            ${["intro","news","outro"].map(k => `<option value="${k}" ${seg.kind === k ? "selected" : ""}>${k}</option>`).join("")}
          </select></div>
          <div class="row"><label>标题</label><input type="text" value="${escapeAttr(seg.title || "")}" oninput="setField('title', this.value)" /></div>
          <div class="row"><label>屏幕短标题</label><input type="text" value="${escapeAttr(seg.caption || "")}" oninput="setField('caption', this.value)" /></div>
          <div class="row"><label>解说稿</label><textarea oninput="setField('text', this.value)">${escapeHtml(seg.text || "")}</textarea><p class="hint">这里就是最终配音和字幕来源。建议先写新闻稿，再微调卡片。</p></div>
          <div class="row"><label>栏目/时间轴</label><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <input type="text" placeholder="栏目，如 模型发布" value="${escapeAttr(seg.active_tab || "")}" oninput="setField('active_tab', this.value)" />
            <input type="text" placeholder="时间轴标签" value="${escapeAttr(seg.bottom_active || "")}" oninput="setField('bottom_active', this.value)" />
          </div></div>
          <div class="row"><label>画面卡片</label><div>
            <div class="cards" id="cardsBox">${seg.cards.map((card, i) => cardHtml(card, i)).join("")}</div>
            <p><button class="small" onclick="addCard()">添加卡片</button></p>
            <p class="hint">卡片负责“看懂”，不要和解说逐字重复。标题尽量 10 字内，正文尽量 18 字内。</p>
          </div></div>
          <div class="row"><label>图片/截图</label><div>
            <div class="images">${seg.images.map((img, i) => imageHtml(img, i)).join("")}</div>
            <p><label class="file-btn">上传图片<input type="file" accept="image/*" onchange="uploadImage(this.files[0])" /></label></p>
            <p class="hint">图片会作为弹出证据素材进入视频；不想进成片可以取消勾选或删除。</p>
          </div></div>
          <div class="row"><label>审稿备注</label><textarea style="min-height:80px" oninput="setField('notes', this.value)">${escapeHtml(seg.notes || "")}</textarea></div>
        </div>`;
    }

    function cardHtml(card, i) {
      return `<div class="mini-card mini-card-grid">
        <input type="text" placeholder="卡片标题" value="${escapeAttr(card.title || "")}" oninput="setCard(${i}, 'title', this.value)" />
        <input type="text" placeholder="卡片正文" value="${escapeAttr(card.body || "")}" oninput="setCard(${i}, 'body', this.value)" />
        <button class="small danger" onclick="deleteCard(${i})">删除</button>
      </div>`;
    }

    function imageHtml(img, i) {
      const src = img.url || (img.path ? `/media?path=${encodeURIComponent(img.path)}` : "");
      return `<div class="image-card">
        ${src ? `<img class="thumb" src="${escapeAttr(src)}" />` : `<div class="thumb"></div>`}
        <div>
          <input type="text" placeholder="图片说明" value="${escapeAttr(img.label || "")}" oninput="setImage(${i}, 'label', this.value)" />
          <div class="hint">${escapeHtml(img.path || "")}</div>
          <label class="file-btn" style="display:inline-block;margin-top:8px">替换图片<input type="file" accept="image/*" onchange="replaceImage(${i}, this.files[0])" /></label>
        </div>
        <div style="display:grid;gap:8px;justify-items:end">
          <label class="switch"><input type="checkbox" ${img.enabled === false ? "" : "checked"} onchange="setImage(${i}, 'enabled', this.checked)" /> 启用</label>
          <button class="small danger" onclick="deleteImage(${i})">删除</button>
        </div>
      </div>`;
    }

    function setField(key, value) { state.segments[active][key] = value; renderListOnly(); }
    function renderListOnly() {
      const listActive = active;
      const oldScroll = document.querySelector("aside").scrollTop;
      active = listActive;
      const list = $("segmentList");
      list.innerHTML = "";
      state.segments.forEach((seg, idx) => {
        const btn = document.createElement("button");
        btn.className = "list-item" + (idx === active ? " active" : "");
        btn.onclick = () => { active = idx; render(); };
        btn.innerHTML = `<div class="list-title">${escapeHtml(segmentLabel(seg, idx))}</div>
          <div class="list-meta">${seg.enabled === false ? "不进成片" : "进入成片"} · 卡片 ${seg.cards.length} · 图片 ${seg.images.length}</div>`;
        list.appendChild(btn);
      });
      $("countPill").textContent = `${state.segments.filter(s => s.enabled !== false).length} 条`;
      document.querySelector("aside").scrollTop = oldScroll;
    }
    function setCard(i, key, value) { state.segments[active].cards[i][key] = value; }
    function addCard() { state.segments[active].cards.push({title:"", body:""}); renderEditor(); }
    function deleteCard(i) { state.segments[active].cards.splice(i, 1); render(); }
    function setImage(i, key, value) { state.segments[active].images[i][key] = value; if (key === "enabled") renderListOnly(); }
    function deleteImage(i) { state.segments[active].images.splice(i, 1); render(); }
    function deleteSeg() { state.segments.splice(active, 1); active = Math.max(0, active - 1); render(); }
    function moveSeg(delta) {
      const next = active + delta;
      if (next < 0 || next >= state.segments.length) return;
      const [item] = state.segments.splice(active, 1);
      state.segments.splice(next, 0, item);
      active = next;
      render();
    }
    function addNews() {
      state.segments.push({
        id: uid("seg"), kind: "news", enabled: true, title: "新增新闻", caption: "新增新闻",
        text: "", active_tab: "AI 动态", bottom_active: "", cards: [], images: [], visual_pages: [], notes: ""
      });
      active = state.segments.length - 1;
      render();
    }
    async function uploadImage(file) {
      if (!file) return;
      const img = await uploadFile(file);
      state.segments[active].images.push(img);
      render();
    }
    async function replaceImage(i, file) {
      if (!file) return;
      const img = await uploadFile(file);
      state.segments[active].images[i] = {...state.segments[active].images[i], ...img};
      render();
    }
    async function uploadFile(file) {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const res = await fetch("/api/upload", {
        method: "POST",
        headers: {"Content-Type":"application/json", "X-Review-Token": window.__REVIEW_TOKEN__ || ""},
        body: JSON.stringify({filename:file.name, dataUrl})
      });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }
    async function saveState() {
      const res = await fetch("/api/save", {
        method: "POST",
        headers: {"Content-Type":"application/json", "X-Review-Token": window.__REVIEW_TOKEN__ || ""},
        body: JSON.stringify(state)
      });
      const payload = await res.json();
      if (!res.ok) {
        alert(payload.error || "保存失败");
        return;
      }
      state = payload.state;
      alert("已保存最终稿：\\n" + payload.final_script);
      render();
    }
    function escapeHtml(s) {
      return String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function escapeAttr(s) { return escapeHtml(s).replace(/`/g, '&#96;'); }
    reloadState().catch(err => { $("editor").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`; });
  </script>
</body>
</html>
"""


def review_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "review"


def review_state_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "state.json"


def review_draft_state_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "draft-state.json"


def review_final_script_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "final-script.json"


def review_final_md_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "final-script.md"


def review_morning_script_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "morning-final-script.json"


def review_morning_md_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "morning-final-script.md"


def review_index_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "index.html"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    # Atomic replace so concurrent readers (review app, morning merge, verify) never
    # observe a truncated JSON file if this process is killed mid-write.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    os.replace(tmp, path)


def _refresh_cover_story_count(run_dir: Path, news_count: int) -> None:
    """Keep review/cover.json's storyCount aligned with the script's real news count.

    The remotion renderer hard-rejects a persisted storyCount that no longer matches the
    rendered news count; without this refresh, the auto-written cover.json from the evening
    render would fail the next morning-merge or re-reviewed render whenever the count changed.
    """
    path = Path(run_dir).resolve() / "review" / "cover.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("storyCount") == news_count:
        return
    payload["storyCount"] = news_count
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _media_url(path: str | Path) -> str:
    return "/media?path=" + quote(str(path), safe="")


def _safe_upload_name(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).stem).strip("-")[:50] or "image"
    return f"{uuid.uuid4().hex[:12]}-{stem}{suffix}"


def _plain_cards(cards: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(cards, list):
        return rows
    for card in cards:
        if not isinstance(card, dict):
            continue
        title = _safe_text(card.get("title"))
        body = _safe_text(card.get("body") or card.get("content"))
        if title or body:
            rows.append({"title": title, "body": body})
    return rows


def _extract_images_from_pages(pages: list[dict[str, Any]], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clean_pages: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    seen_images: set[tuple[str, str]] = set()
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        evidence = page.get("evidenceVisual")
        if isinstance(evidence, dict):
            raw_path = evidence.get("image") or evidence.get("path") or evidence.get("screenshot_path")
            source_url = _safe_text(page.get("source_url") or evidence.get("url"))
            image_key = (str(raw_path or ""), "" if raw_path else source_url)
            if raw_path and image_key not in seen_images:
                path = Path(str(raw_path))
                if not path.is_absolute():
                    path = run_dir / path
                seen_images.add(image_key)
                images.append(
                    {
                        "id": f"img-{idx}-{uuid.uuid4().hex[:8]}",
                        "label": _safe_text(page.get("title") or evidence.get("title") or "证据截图"),
                        "path": str(path),
                        "url": _media_url(path),
                        "source": _safe_text(page.get("source") or evidence.get("source")),
                        "source_url": source_url,
                        "enabled": True,
                    }
                )
            # A normal cards/brief page may carry a compact evidence preview.
            # Keep that page's information layout and only detach the image;
            # dedicated evidence pages are reconstructed once from ``images``.
            if str(page.get("kind") or "").lower() == "evidence":
                continue
            clean_page = deepcopy(page)
            clean_page.pop("evidenceVisual", None)
            clean_pages.append(clean_page)
            continue
        clean_pages.append(deepcopy(page))
    return clean_pages, images


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _public_evidence_source(evidence: dict[str, Any], fallback: str = "") -> str:
    source_url = _safe_text(evidence.get("url") or evidence.get("screenshot_capture_url"))
    try:
        parsed = urlparse(source_url)
    except ValueError:
        parsed = None
    if parsed and parsed.hostname and parsed.hostname.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        handle = next((part for part in parsed.path.split("/") if part), "")
        if handle:
            return f"X / @{handle}"
    source = _safe_text(evidence.get("source"))
    if source.lower().startswith("codex verified"):
        source = ""
    return source or fallback or (parsed.hostname if parsed and parsed.hostname else "原始来源")


def _merge_required_evidence_images(run_dir: Path, state: dict[str, Any]) -> None:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return

    required_by_story: dict[str, list[dict[str, Any]]] = {}
    for card in list(manifest.get("selected") or []):
        if not isinstance(card, dict):
            continue
        story_spec = card.get("story_spec") if isinstance(card.get("story_spec"), dict) else {}
        story_id = _safe_text(story_spec.get("story_id"))
        if not story_id:
            continue
        rows: list[dict[str, Any]] = []
        for evidence_index, evidence in enumerate(list(card.get("evidence") or [])):
            if not isinstance(evidence, dict):
                continue
            if not _truthy(evidence.get("screenshot_required")):
                continue
            if _safe_text(evidence.get("screenshot_status")).lower() != "captured":
                continue
            raw_path = _safe_text(evidence.get("screenshot_path"))
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = run_dir / path
            path = path.resolve()
            if not path.is_file():
                continue
            source_url = _safe_text(evidence.get("url") or evidence.get("screenshot_capture_url"))
            rows.append(
                {
                    "id": f"required-{story_id[:10]}-{evidence_index:02d}",
                    "label": _safe_text(evidence.get("title") or card.get("title") or "证据截图"),
                    "path": str(path),
                    "url": _media_url(path),
                    "source": _public_evidence_source(evidence),
                    "source_url": source_url,
                    "enabled": True,
                    "required": True,
                }
            )
        if rows:
            required_by_story[story_id] = rows

    for segment in list(state.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        story_id = _safe_text(segment.get("story_id"))
        required = required_by_story.get(story_id) or []
        if not required:
            continue
        images = [image for image in list(segment.get("images") or []) if isinstance(image, dict)]
        fallback_source = next((_safe_text(image.get("source")) for image in images if _safe_text(image.get("source"))), "")
        seen_paths: set[str] = set()
        for image in images:
            raw_path = _safe_text(image.get("path"))
            if raw_path:
                seen_paths.add(str(Path(raw_path).resolve()).lower())
        for image in required:
            normalized_path = str(Path(str(image["path"])).resolve()).lower()
            if normalized_path in seen_paths:
                continue
            if not _safe_text(image.get("source")) and fallback_source:
                image["source"] = fallback_source
            images.append(image)
            seen_paths.add(normalized_path)
        segment["images"] = images


def _state_segment_from_manuscript(seg: dict[str, Any], idx: int, run_dir: Path) -> dict[str, Any]:
    visual_pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
    clean_pages, images = _extract_images_from_pages(visual_pages, run_dir)
    return {
        "id": str(seg.get("id") or f"seg-{idx:03d}-{uuid.uuid4().hex[:8]}"),
        "index": int(seg.get("index") or idx),
        "kind": _safe_text(seg.get("kind") or "news"),
        "position": int(seg.get("position") or 0),
        "total": int(seg.get("total") or 0),
        "story_id": _safe_text(seg.get("story_id")),
        "claim_ids": [_safe_text(value) for value in list(seg.get("claim_ids") or []) if _safe_text(value)],
        "generation_path": _safe_text(seg.get("generation_path")),
        "editorial_tier": _safe_text(seg.get("editorial_tier") or "headline"),
        "enabled": bool(seg.get("enabled", True)),
        "title": _safe_text(seg.get("title") or seg.get("headline") or f"新闻 {idx + 1}"),
        "headline": _safe_text(seg.get("headline") or seg.get("title")),
        "caption": _safe_text(seg.get("caption") or seg.get("title")),
        "text": str(seg.get("text") or ""),
        "active_tab": _safe_text(seg.get("active_tab")),
        "bottom_active": _safe_text(seg.get("bottom_active")),
        "cards": _plain_cards(seg.get("cards")),
        "images": images,
        "visual_pages": clean_pages,
        "notes": _safe_text(seg.get("notes")),
        # Social-signal provenance must survive the review desk: verify's disclaimer and
        # forbidden-claim gates key off signal_kind, and the screenshot-proof gate reads
        # the evidence rows. Dropping them silently disabled those checks for any
        # human-reviewed script.
        "signal_kind": _safe_text(seg.get("signal_kind")),
        "disclaimer": _safe_text(seg.get("disclaimer")),
        "evidence": [row for row in list(seg.get("evidence") or []) if isinstance(row, dict)],
    }


def _load_segments_from_existing_files(run_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        review_final_script_path(run_dir),
        run_dir / "news-script.json",
        run_dir / "script.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        payload = _read_json(path)
        if isinstance(payload, dict):
            segments = payload.get("segments")
        else:
            segments = payload
        if isinstance(segments, list):
            return [seg for seg in segments if isinstance(seg, dict)]
    return []


def _state_from_segments(run_dir: Path, segments: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    run_date = run_dir.name
    if manifest_path.exists():
        try:
            manifest = _read_json(manifest_path)
            run_date = str(manifest.get("run_date") or run_date)
        except Exception:
            pass
    return {
        "version": 1,
        "run_dir": str(run_dir),
        "run_date": run_date,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "segments": [_state_segment_from_manuscript(seg, idx, run_dir) for idx, seg in enumerate(segments)],
    }


def _state_from_cards(run_dir: Path, cards: list[Any]) -> dict[str, Any]:
    from .render import _segments

    return _state_from_segments(run_dir, _segments(cards))


def create_review_package(run_dir: Path, cards: list[Any] | None = None, force: bool = False) -> dict[str, str]:
    run_dir = Path(run_dir).resolve()
    rdir = review_dir(run_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    review_index_path(run_dir).write_text(REVIEW_HTML, encoding="utf-8")

    state_path = review_state_path(run_dir)
    if state_path.exists() and not force:
        return {
            "status": "exists",
            "review_dir": str(rdir),
            "index": str(review_index_path(run_dir)),
            "state": str(state_path),
            "final_script": str(review_final_script_path(run_dir)),
        }

    if cards is not None:
        state = _state_from_cards(run_dir, cards)
    else:
        segments = _load_segments_from_existing_files(run_dir)
        if not segments:
            from .render import _segments

            segments = _segments([])
        state = _state_from_segments(run_dir, segments)

    _write_json(review_draft_state_path(run_dir), state)
    _write_json(state_path, state)
    return {
        "status": "created",
        "review_dir": str(rdir),
        "index": str(review_index_path(run_dir)),
        "state": str(state_path),
        "final_script": str(review_final_script_path(run_dir)),
    }


def _unique_labels(segments: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: dict[str, int] = {}
    news_idx = 0
    for seg in segments:
        kind = str(seg.get("kind") or "news")
        if kind == "intro":
            raw = "开场"
        elif kind == "outro":
            raw = "收尾"
        else:
            news_idx += 1
            raw = _safe_text(seg.get("bottom_active")) or f"{news_idx:02d} {_safe_text(seg.get('title'))[:10]}"
        raw = raw or f"{len(labels) + 1:02d}"
        count = seen.get(raw, 0) + 1
        seen[raw] = count
        labels.append(raw if count == 1 else f"{raw}-{count}")
    return labels


def _final_visual_pages(seg: dict[str, Any], cards: list[dict[str, str]]) -> list[dict[str, Any]]:
    title = _safe_text(seg.get("title") or seg.get("caption") or "新闻")
    pages = [deepcopy(p) for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
    pages = [p for p in pages if "evidenceVisual" not in p]
    if pages:
        pages[0]["title"] = pages[0].get("title") or title
        pages[0]["cards"] = cards
    else:
        pages.append({"kind": "cards", "title": title, "cards": cards})
    if str(seg.get("kind") or "news") == "news":
        pages[0]["kind"] = "brief" if str(seg.get("editorial_tier") or "headline") == "brief" else "cards"
    for image in list(seg.get("images") or []):
        if not isinstance(image, dict) or image.get("enabled") is False:
            continue
        raw_path = image.get("path")
        if not raw_path:
            continue
        label = _safe_text(image.get("label") or "证据截图")
        source = _safe_text(image.get("source") or label)
        source_url = _safe_text(image.get("source_url"))
        pages.append(
            {
                "kind": "evidence",
                "title": label,
                "lead": _safe_text(seg.get("caption") or title),
                "source": source,
                "source_url": source_url,
                "evidenceVisual": {
                    "source": source,
                    "title": label,
                    "url": source_url,
                    "image": str(raw_path),
                    "status": "captured",
                    "required": True,
                },
                "cards": [],
                "review_generated": True,
            }
        )
    return pages


def state_to_final_script(state: dict[str, Any]) -> dict[str, Any]:
    enabled = [seg for seg in list(state.get("segments") or []) if isinstance(seg, dict) and seg.get("enabled") is not False]
    labels = _unique_labels(enabled)
    segments: list[dict[str, Any]] = []
    news_idx = 0
    for idx, (seg, label) in enumerate(zip(enabled, labels)):
        kind = _safe_text(seg.get("kind") or "news")
        if kind == "news":
            news_idx += 1
        title = _safe_text(seg.get("title") or seg.get("headline") or f"新闻 {news_idx or idx + 1}")
        caption = _safe_text(seg.get("caption") or title)
        cards = _plain_cards(seg.get("cards"))
        out = {
            "index": idx,
            "kind": kind,
            "position": news_idx if kind == "news" else 0,
            "total": int(seg.get("total") or 0),
            "story_id": _safe_text(seg.get("story_id")),
            "claim_ids": [_safe_text(value) for value in list(seg.get("claim_ids") or []) if _safe_text(value)],
            "generation_path": _safe_text(seg.get("generation_path")),
            "editorial_tier": _safe_text(seg.get("editorial_tier") or "headline"),
            "title": title,
            "headline": _safe_text(seg.get("headline") or title),
            "caption": caption,
            "text": str(seg.get("text") or ""),
            "active_tab": _safe_text(seg.get("active_tab") or ("Intro" if kind == "intro" else "Outro" if kind == "outro" else "AI 动态")),
            "bottom_tabs": labels,
            "bottom_active": label,
            "cards": cards,
            "visual_pages": _final_visual_pages(seg, cards),
        }
        signal_kind = _safe_text(seg.get("signal_kind"))
        if signal_kind:
            out["signal_kind"] = signal_kind
        disclaimer = _safe_text(seg.get("disclaimer"))
        if disclaimer:
            out["disclaimer"] = disclaimer
        evidence_rows = [row for row in list(seg.get("evidence") or []) if isinstance(row, dict)]
        if evidence_rows:
            out["evidence"] = evidence_rows
        segments.append(out)
    return {
        "version": 1,
        "reviewed_at": _now_iso(),
        "run_date": state.get("run_date") or "",
        "segments": segments,
    }


def _write_final_md(path: Path, payload: dict[str, Any]) -> None:
    lines = ["# AI 日报最终审稿", "", f"保存时间：{payload.get('reviewed_at') or ''}", ""]
    for seg in payload.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("kind") or "news"
        label = "开场" if kind == "intro" else "收尾" if kind == "outro" else f"{int(seg.get('position') or 0):02d}"
        lines.append(f"## {label} {seg.get('title') or ''}")
        lines.append(f"- 屏幕标题：{seg.get('caption') or ''}")
        lines.append(f"- 解说：{seg.get('text') or ''}")
        cards = [c for c in list(seg.get("cards") or []) if isinstance(c, dict)]
        if cards:
            lines.append("- 画面卡片：")
            for card in cards:
                lines.append(f"  - {card.get('title') or ''}：{card.get('body') or ''}")
        image_count = sum(1 for page in list(seg.get("visual_pages") or []) if isinstance(page, dict) and page.get("kind") == "evidence")
        if image_count:
            lines.append(f"- 图片素材：{image_count} 张")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8-sig")


def write_final_script_from_state(run_dir: Path, state: dict[str, Any] | None = None) -> dict[str, str]:
    run_dir = Path(run_dir).resolve()
    if state is None:
        state = _read_json(review_state_path(run_dir))
    _merge_required_evidence_images(run_dir, state)
    state["run_dir"] = str(run_dir)
    state["updated_at"] = _now_iso()
    _write_json(review_state_path(run_dir), state)
    payload = state_to_final_script(state)
    final_json = review_final_script_path(run_dir)
    final_md = review_final_md_path(run_dir)
    _write_json(final_json, payload)
    _write_final_md(final_md, payload)
    _refresh_cover_story_count(
        run_dir,
        sum(1 for seg in list(payload.get("segments") or []) if isinstance(seg, dict) and seg.get("kind") == "news"),
    )
    for stale_path in [review_morning_script_path(run_dir), review_morning_md_path(run_dir)]:
        if stale_path.exists():
            stale_path.unlink()
    mark_render_required(run_dir, final_json)
    return {"final_script": str(final_json), "final_md": str(final_md), "state": str(review_state_path(run_dir))}


def _norm_key(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text[:180]


def _segment_urls(seg: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for page in list(seg.get("visual_pages") or []):
        if not isinstance(page, dict):
            continue
        for key in ["source_url", "url"]:
            value = str(page.get(key) or "").strip()
            if value:
                urls.add(value)
        evidence = page.get("evidenceVisual")
        if isinstance(evidence, dict):
            for key in ["url", "source_url"]:
                value = str(evidence.get(key) or "").strip()
                if value:
                    urls.add(value)
    return urls


def _segment_title_keys(seg: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ["title", "headline", "caption", "bottom_active"]:
        value = _norm_key(seg.get(key))
        if value:
            keys.add(value)
    return keys


def _is_duplicate_segment(candidate: dict[str, Any], existing: list[dict[str, Any]]) -> bool:
    candidate_urls = _segment_urls(candidate)
    candidate_titles = _segment_title_keys(candidate)
    for seg in existing:
        if candidate_urls and candidate_urls.intersection(_segment_urls(seg)):
            return True
        if candidate_titles and candidate_titles.intersection(_segment_title_keys(seg)):
            return True
    return False


def append_morning_updates_to_reviewed_script(
    run_dir: Path,
    cards: list[Any],
    max_new_items: int = 3,
) -> dict[str, Any]:
    """Create a six-o'clock render script without modifying the frozen reviewed script."""
    run_dir = Path(run_dir).resolve()
    reviewed_path = review_final_script_path(run_dir)
    if not reviewed_path.exists():
        raise FileNotFoundError(f"missing reviewed manuscript: {reviewed_path}")

    reviewed_payload = _read_json(reviewed_path)
    reviewed_segments = [deepcopy(seg) for seg in list(reviewed_payload.get("segments") or []) if isinstance(seg, dict)]
    if not reviewed_segments:
        raise ValueError(f"reviewed manuscript has no segments: {reviewed_path}")

    from .render import _segments

    auto_segments = [seg for seg in _segments(cards) if isinstance(seg, dict) and seg.get("kind") == "news"]
    new_segments: list[dict[str, Any]] = []
    existing_news = [seg for seg in reviewed_segments if seg.get("kind") == "news"]
    limit = max(0, int(max_new_items or 0))
    for seg in auto_segments:
        if limit and len(new_segments) >= limit:
            break
        if _is_duplicate_segment(seg, existing_news + new_segments):
            continue
        new_segments.append(deepcopy(seg))

    if new_segments:
        insert_at = len(reviewed_segments)
        for i, seg in enumerate(reviewed_segments):
            if seg.get("kind") == "outro":
                insert_at = i
                break
        combined = reviewed_segments[:insert_at] + new_segments + reviewed_segments[insert_at:]
    else:
        combined = reviewed_segments

    labels = _unique_labels(combined)
    news_idx = 0
    normalized_segments: list[dict[str, Any]] = []
    for idx, (seg, label) in enumerate(zip(combined, labels)):
        item = deepcopy(seg)
        kind = str(item.get("kind") or "news")
        if kind == "news":
            news_idx += 1
            item["position"] = news_idx
        else:
            item["position"] = 0
        item["index"] = idx
        item["bottom_tabs"] = labels
        item["bottom_active"] = label
        normalized_segments.append(item)

    payload = {
        "version": 1,
        "reviewed_at": reviewed_payload.get("reviewed_at") or "",
        "morning_updated_at": _now_iso(),
        "run_date": reviewed_payload.get("run_date") or run_dir.name,
        "base_reviewed_script": str(reviewed_path),
        "auto_appended_count": len(new_segments),
        "segments": normalized_segments,
    }
    final_json = review_morning_script_path(run_dir)
    final_md = review_morning_md_path(run_dir)
    _write_json(final_json, payload)
    _write_final_md(final_md, payload)
    _refresh_cover_story_count(run_dir, news_idx)
    mark_render_required(run_dir, final_json)
    return {
        "final_script": str(final_json),
        "final_md": str(final_md),
        "auto_appended_count": len(new_segments),
        "candidate_count": len(auto_segments),
        "segments_count": len(normalized_segments),
    }


def _script_segments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _read_json(path)
    return [seg for seg in payload if isinstance(seg, dict)] if isinstance(payload, list) else []


def _format_mmss(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0))))
    return f"{total // 60:02d}:{total % 60:02d}"


def _fit_text(text: str, limit: int) -> str:
    text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip()


def _title_candidates_from_segments(segments: list[dict[str, Any]], run_date: str) -> list[str]:
    news = [seg for seg in segments if seg.get("kind") == "news"]
    names: list[str] = []
    for seg in news:
        title = _safe_text(seg.get("title") or seg.get("caption"))
        if not title:
            continue
        lead = re.split(r"[：:，,｜|\s]", title)[0].strip()
        if lead and lead not in names:
            names.append(lead)
        if len(names) >= 2:
            break
    if names:
        base = "、".join(names) + f" 等 {len(news)} 条 AI 动态"
    else:
        base = "AI 日报"
    suffix = f"【AI 日报 {run_date}】"
    title = base + suffix
    return [_fit_text(title, 78), _fit_text(f"今天值得看的 AI 新闻汇总{suffix}", 78)]


def refresh_reviewed_bilibili_outputs(out_dir: Path, quality: str = "1080p", run_date: str | None = None) -> dict[str, str]:
    out_dir = Path(out_dir).resolve()
    run_date = run_date or out_dir.name
    segments = _script_segments(out_dir / "script.json")
    news = [seg for seg in segments if seg.get("kind") == "news"]
    titles = _title_candidates_from_segments(segments, run_date)
    lines = ["# B站投稿草稿", "", "## 标题候选"]
    for title in titles:
        lines.append(f"- {title}")
    lines.extend(["", "## 简介"])
    summary = f"AI 日报，精选 {len(news)} 条值得看的模型、产品和开发者动态。完整时间轴见置顶评论。"
    lines.append(_fit_text(summary, 250))
    lines.extend(["", "时间轴："])
    intro = next((seg for seg in segments if seg.get("kind") == "intro"), None)
    lines.append(f"{_format_mmss(float(intro.get('start') or 0) if intro else 0)} 开场｜AI 日报")
    for seg in news:
        start = _format_mmss(float(seg.get("start") or 0))
        title = _fit_text(_safe_text(seg.get("title") or seg.get("caption") or "AI 动态"), 56)
        lines.append(f"{start} {title}")
    lines.extend(["", "本次的播报完毕。", "", "## 标签", "AI,人工智能,大模型,科技早报,ChatGPT,开源模型,AIGC", "", "## 视频参数", f"- quality: {quality}", "- copyright: 原创", "- no_reprint: 1"])
    (out_dir / "bilibili.md").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    pinned = ["今日时间轴：", "00:00 开场｜AI 日报"]
    for seg in news:
        pinned.append(f"{_format_mmss(float(seg.get('start') or 0))} {_fit_text(_safe_text(seg.get('title') or seg.get('caption') or 'AI 动态'), 56)}")
    pinned.extend(["", "本次的播报完毕。"])
    (out_dir / "pinned-comment.md").write_text("\n".join(pinned) + "\n", encoding="utf-8-sig")

    payload = {
        "title": titles[0] if titles else "AI 日报【AI 日报】",
        "title_candidates": titles,
        "desc": _fit_text(summary, 250),
        "tag": "AI,人工智能,大模型,科技早报,ChatGPT,开源模型,AIGC",
        "copyright": 1,
        "no_reprint": 1,
        "video": str(out_dir / "final.mp4"),
        "cover": str(out_dir / "cover.png"),
        "subtitle": str(out_dir / "subtitles.srt"),
        "pinned_comment": str(out_dir / "pinned-comment.md"),
        "tid": None,
        "tid_note": "B站分区 ID 后续通过开放平台分区接口刷新后填入。",
    }
    (out_dir / "bilibili.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return {"bilibili": str(out_dir / "bilibili.md"), "pinned_comment": str(out_dir / "pinned-comment.md"), "bilibili_json": str(out_dir / "bilibili.json")}


class _ReviewHandler(BaseHTTPRequestHandler):
    run_dir: Path
    auth_token: str = ""

    def _post_authorized(self) -> bool:
        """CSRF/auth guard for state-changing endpoints.

        The token is injected into the served page only, so a cross-site page can
        neither read it (same-origin policy) nor send the custom header without a
        CORS preflight this server never approves. The Origin check is a second
        layer for non-browser callers.
        """
        token = str(self.headers.get("X-Review-Token") or "")
        if not self.auth_token or not hmac.compare_digest(token, self.auth_token):
            return False
        origin = str(self.headers.get("Origin") or "")
        if origin:
            host = (urlparse(origin).hostname or "").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                return False
        return True

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                # Serve the in-code template (not a possibly stale review/index.html from
                # an older run) and inject this server's session token for POST auth.
                html = REVIEW_HTML.replace(
                    "</head>",
                    f"<script>window.__REVIEW_TOKEN__ = {json.dumps(self.auth_token)};</script></head>",
                    1,
                )
                self._send_text(html, content_type="text/html; charset=utf-8")
                return
            if parsed.path == "/api/state":
                self._send_json(_read_json(review_state_path(self.run_dir)))
                return
            if parsed.path == "/media":
                qs = parse_qs(parsed.query)
                raw = qs.get("path", [""])[0]
                path = Path(unquote(raw))
                if not path.is_absolute():
                    path = self.run_dir / path
                if not path.exists() or not _is_inside(path, self.run_dir):
                    self._send_text("not found", status=404)
                    return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._send_text("not found", status=404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if not self._post_authorized():
                self._send_json({"error": "unauthorized: reload the review page and retry"}, status=403)
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8") or "{}")
            if parsed.path == "/api/save":
                result = write_final_script_from_state(self.run_dir, payload)
                state = _read_json(review_state_path(self.run_dir))
                self._send_json({**result, "state": state})
                return
            if parsed.path == "/api/upload":
                filename = str(payload.get("filename") or "image.png")
                data_url = str(payload.get("dataUrl") or "")
                if "," in data_url:
                    data_url = data_url.split(",", 1)[1]
                data = base64.b64decode(data_url)
                upload_dir = review_dir(self.run_dir) / "uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                path = upload_dir / _safe_upload_name(filename)
                path.write_bytes(data)
                self._send_json(
                    {
                        "id": f"img-{uuid.uuid4().hex[:10]}",
                        "label": Path(filename).stem,
                        "path": str(path),
                        "url": _media_url(path),
                        "source": "手动上传",
                        "source_url": "",
                        "enabled": True,
                    }
                )
                return
            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[review] {self.address_string()} - {fmt % args}")


def serve_review(run_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    run_dir = Path(run_dir).resolve()
    if host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("BRIEFING_REVIEW_ALLOW_REMOTE") != "1":
        raise RuntimeError(
            f"refusing to bind the review desk to non-loopback host {host!r}: the app has no login; "
            "set BRIEFING_REVIEW_ALLOW_REMOTE=1 only on a trusted network"
        )
    create_review_package(run_dir, force=False)

    class Handler(_ReviewHandler):
        pass

    Handler.run_dir = run_dir
    Handler.auth_token = uuid.uuid4().hex
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"review_url={url}")
    print(f"run_dir={run_dir}")
    print("按 Ctrl+C 停止审稿台。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("审稿台已停止。")
    finally:
        server.server_close()
