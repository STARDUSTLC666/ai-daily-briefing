from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..core import (
    EvidenceCard,
    card_is_community_signal,
    card_is_official_personnel_signal,
    card_is_official_social_signal,
    clean_text,
    ensure_dir,
    evidence_is_x_post,
    now_utc,
    parse_x_status,
    project_root,
    social_origin_evidence_indexes,
    url_host,
)
from .browser import find_browser_executable

SCREENSHOT_EVIDENCE_KEYWORDS = (
    "用户反馈", "用户评价", "真实反馈", "实际使用", "实测", "体感",
    "性能", "模型性能", "基准测试", "评测", "测评", "榜单", "排行榜", "跑分", "得分",
    "benchmark", "bench", "leaderboard", "arena", "lmarena", "swe-bench",
    "livecodebench", "aider", "eval", "score", "latency", "throughput",
    "open weights", "open-weight", "weights are now open", "released", "release",
    "开源", "权重",
)

BLOCKED_PAGE_PHRASES = (
    "you've been blocked by network security",
    "you have been blocked",
    "blocked by network security",
    "access denied",
    "request blocked",
    "verify you are human",
    "captcha",
    "403 forbidden",
    "too many requests",
    "404 not found",
    "http error 451",
    "该网页无法正常运作",
    "网页无法正常运作",
    "page not found",
    "this is not the web page you are looking for",
    "sign in to x",
    "log in to x",
    "登录后继续使用 x",
    "before you continue to google",
    "checking your browser",
    "just a moment...",
    "enable javascript and cookies to continue",
    "something went wrong. try reloading",
)

_CJK_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
)


def _is_http_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_aggregator_url(url: str) -> bool:
    host = url_host(url)
    return host == "news.google.com" or host.endswith(".news.google.com")


def _capture_target_url(evidence: dict[str, Any]) -> str:
    url = str(evidence.get("url") or "").strip()
    final_url = str(evidence.get("final_url") or "").strip()
    if (
        str(evidence.get("article_status") or "").lower() == "ok"
        and _is_http_url(final_url)
        and not _is_aggregator_url(final_url)
    ):
        return final_url
    return url


def _evidence_priority(evidence: dict[str, Any], index: int) -> tuple[int, int]:
    reliability = str(evidence.get("reliability") or "").lower()
    tier = str(evidence.get("tier") or "").upper()
    source = clean_text(str(evidence.get("source") or "")).lower()
    capture_url = _capture_target_url(evidence)
    if (
        str(evidence.get("reconciled_official") or "").lower() == "true"
        or reliability.startswith("official")
        or tier == "A"
    ):
        return 0, index
    if (
        str(evidence.get("article_status") or "").lower() == "ok"
        and _is_http_url(capture_url)
        and not _is_aggregator_url(capture_url)
        and reliability in {"media", "aggregator"}
    ):
        return 1, index
    aggregator = (
        reliability == "aggregator"
        or _is_aggregator_url(capture_url)
        or _is_aggregator_url(str(evidence.get("url") or ""))
        or "google news" in source
        or "谷歌新闻" in source
    )
    if reliability == "media" and not aggregator:
        return 2, index
    return (4 if aggregator else 3), index


def _card_needs_screenshot(card: EvidenceCard) -> bool:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card_is_community_signal(card):
        return True
    if card.official_count > 0 and card.media_count == 0 and card.community_count == 0:
        return False
    if card.risk == "yellow" and card.official_count == 0:
        return True
    text = clean_text(
        " ".join(
            [
                card.event_title,
                card.entity,
                *card.key_facts,
                *card.uncertainty,
                *(str(ev.get("title") or "") for ev in card.evidence_links),
                *(str(ev.get("url") or "") for ev in card.evidence_links),
            ]
        )
    ).lower()
    return any(kw.lower() in text for kw in SCREENSHOT_EVIDENCE_KEYWORDS)


def _preferred_evidence_indexes(card: EvidenceCard, required: bool) -> list[int]:
    if required:
        social_indexes = social_origin_evidence_indexes(card)
        if social_indexes:
            return sorted(social_indexes)
    if not card.evidence_links:
        return []
    eligible = [
        idx
        for idx, ev in enumerate(card.evidence_links)
        if str(ev.get("derived_evidence") or "") != "github_api_compare"
    ]
    if not eligible:
        return []
    return [min(eligible, key=lambda idx: _evidence_priority(card.evidence_links[idx], idx))]


def evidence_requires_screenshot(
    card: EvidenceCard,
    index: int = 0,
    evidence: dict[str, Any] | None = None,
) -> bool:
    """Return whether the specific `(card, index, evidence)` link requires a verified screenshot."""
    ev = evidence
    if ev is None and 0 <= index < len(card.evidence_links):
        ev = card.evidence_links[index]
    ev = ev or {}
    if str(ev.get("derived_evidence") or "") == "github_api_compare":
        return False
    if not _card_needs_screenshot(card):
        return False
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card_is_community_signal(card):
        social_idxs = social_origin_evidence_indexes(card)
        if social_idxs:
            return index in social_idxs
        return evidence_is_x_post(ev)
    preferred = _preferred_evidence_indexes(card, required=True)
    return index in preferred if preferred else (index == 0)


def _safe_slug(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return (text[:44].strip("-") if text else "") or "source"


def _screenshot_path(out_dir: Path, card: EvidenceCard, evidence: dict[str, Any], slot_number: int) -> Path:
    source = str(evidence.get("source") or "source")
    url = str(evidence.get("url") or "")
    digest = hashlib.sha1(f"{card.cluster_key}|{url}|{slot_number}".encode("utf-8")).hexdigest()[:10]
    return out_dir / "evidence-screenshots" / f"{_safe_slug(source)}-{digest}.png"


def _image_is_nonblank(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 4096:
        return False
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as img:
            gray = img.convert("L").resize((64, 42))
            extrema = gray.getextrema()
            deviation = float(ImageStat.Stat(gray).stddev[0])
            return bool(extrema and extrema[1] - extrema[0] >= 10 and deviation >= 3.0)
    except Exception:
        return path.stat().st_size >= 12_000


def _visible_dom_text(dom: str) -> str:
    visible = re.sub(r"(?is)<(?:script|style|noscript|svg)[^>]*>.*?</(?:script|style|noscript|svg)>", " ", dom or "")
    return clean_text(unescape(re.sub(r"(?s)<[^>]+>", " ", visible)))


def _blocked_page_reason(text: str) -> str:
    lowered = clean_text(text or "").lower()
    for phrase in BLOCKED_PAGE_PHRASES:
        if phrase in lowered:
            return phrase
    return ""


def _page_story_error(dom: str, expected_text: str) -> str:
    expected = clean_text(expected_text or "")
    if not expected:
        return ""
    visible = _visible_dom_text(dom).lower()
    compact_visible = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", visible)
    stop = {
        "about", "after", "announces", "available", "coding", "from", "into", "introduces", "model", "models",
        "official", "release", "released", "support", "this", "update", "updates", "with", "workflow",
        "人工智能", "发布", "推出", "上线", "更新", "新增", "支持", "官方", "工作流",
    }
    raw_tokens = re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", expected.lower())
    tokens = list(dict.fromkeys(t.strip("-_.") for t in raw_tokens if len(t.strip("-_.")) >= 3 and t.strip("-_.") not in stop))[:10]
    if not tokens:
        return ""
    matched = [
        tok for tok in tokens
        if tok in visible or re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", tok) in compact_visible
    ]
    required = 2 if len(tokens) >= 3 else 1
    return "story_identity_mismatch" if len(matched) < required else ""


def _http_unusable_reason(url: str, timeout_seconds: int) -> str:
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "DailyBilibiliBriefing/0.1 (+local)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
            },
            timeout=max(4, min(timeout_seconds, 10)),
            allow_redirects=True,
            stream=True,
        )
    except requests.RequestException:
        return ""
    try:
        if resp.status_code in {404, 410, 429, 451}:
            return f"http_{resp.status_code}"
    finally:
        resp.close()
    return ""


def _social_cache_path(identity: tuple[str, str]) -> Path:
    configured = os.environ.get("BRIEFING_SOCIAL_SCREENSHOT_CACHE_DIR", "").strip()
    root = Path(configured).expanduser() if configured else project_root() / "data" / "evidence-cache" / "x-v2"
    username, status_id = identity
    return root / f"{_safe_slug(username)}-{status_id}.png"


def _restore_social_cache(identity: tuple[str, str], output: Path) -> bool:
    cached = _social_cache_path(identity)
    if not cached.is_file() or not _image_is_nonblank(cached):
        return False
    ensure_dir(output.parent)
    shutil.copy2(cached, output)
    return _image_is_nonblank(output)


def _store_social_cache(identity: tuple[str, str], output: Path) -> None:
    if not output.is_file() or not _image_is_nonblank(output):
        return
    cached = _social_cache_path(identity)
    ensure_dir(cached.parent)
    tmp = cached.with_suffix(cached.suffix + ".tmp")
    shutil.copy2(output, tmp)
    tmp.replace(cached)


def _social_embed_url(status_id: str) -> str:
    return f"https://platform.twitter.com/embed/Tweet.html?id={status_id}&dnt=true&theme=light&lang=zh-cn"


def _capture_social_embed(
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str,
    browser_exe: str | None = None,
) -> tuple[bool, str]:
    identity = parse_x_status(expected_identity_url or url)
    if not identity:
        return False, "embed_identity_missing"
    username, status_id = identity
    try:
        from .browser import load_sync_playwright

        sync_playwright, PlaywrightTimeoutError = load_sync_playwright()
    except Exception as exc:
        return False, f"embed_playwright_unavailable:{type(exc).__name__}"

    try:
        with sync_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "headless": True,
                "args": ["--disable-background-networking", "--no-first-run", "--no-default-browser-check"],
            }
            if browser_exe:
                launch_kwargs["executable_path"] = browser_exe
            browser = pw.chromium.launch(**launch_kwargs)
            context = browser.new_context(viewport={"width": 720, "height": 1600}, device_scale_factor=2, locale="zh-CN")
            page = context.new_page()
            resp = page.goto(_social_embed_url(status_id), wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            if resp and int(resp.status) >= 400:
                return False, f"embed_http_{int(resp.status)}"
            page.wait_for_timeout(min(5_000, max(2_500, timeout_seconds * 180)))

            target = None
            posts = page.locator("article")
            for idx in range(min(posts.count(), 12)):
                post = posts.nth(idx)
                if post.locator(f"a[href*='/status/{status_id}']").count() <= 0:
                    continue
                if bool(post.evaluate("node => !node.parentElement?.closest('article')")):
                    target = post
                    break
            if target is None:
                return False, "embed_target_post_missing"

            target.evaluate(
                """node => {
                  for (const child of [...node.querySelectorAll(':scope > article')]) child.remove();
                  for (const image of node.querySelectorAll('img[src*=\"pbs.twimg.com/media\"]')) {
                    try {
                      const original = new URL(image.getAttribute('src') || image.src, location.href);
                      original.searchParams.set('name', 'orig');
                      image.removeAttribute('srcset');
                      image.setAttribute('loading', 'eager');
                      image.setAttribute('decoding', 'sync');
                      image.setAttribute('src', original.toString());
                    } catch (_) {}
                  }
                  document.documentElement.style.background = '#ffffff';
                  document.body.style.margin = '0';
                  document.body.style.background = '#ffffff';
                }"""
            )
            page.wait_for_timeout(1_800)
            visible = clean_text(target.inner_text(timeout=5_000))
            if len(visible) < 40 or (f"@{username}".lower() not in visible.lower() and target.locator(f"a[href*='/status/{status_id}']").count() <= 0):
                return False, "embed_post_content_mismatch"
            ensure_dir(output.parent)
            target.screenshot(path=str(output), timeout=timeout_seconds * 1000)
            context.close()
            browser.close()
    except PlaywrightTimeoutError:
        return False, "embed_timeout"
    except Exception as exc:
        return False, clean_text(f"embed_{type(exc).__name__}: {exc}", 220)
    return (True, "captured_official_x_embed") if _image_is_nonblank(output) else (False, "embed_blank_screenshot")


def _redact_browser_error(value: str) -> str:
    text = clean_text(value, 500)
    return re.sub(r"(?i)(auth_token|ct0|sessdata|bili_jct|cookie|token)\s*[:=]\s*[^\s,;}]+", r"\1=[REDACTED]", text)


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30, check=False)
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _run_opencli(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    timeout = max(10, int(timeout_seconds))
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=str(project_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except Exception:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def _opencli_command_prefix() -> list[str]:
    executable = shutil.which("opencli.cmd") or shutil.which("opencli")
    if not executable:
        return []
    exe_path = Path(executable)
    main_js = exe_path.parent / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    node = shutil.which("node.exe") or shutil.which("node")
    if node and main_js.is_file():
        return [node, str(main_js)]
    return [executable]


def _capture_social_with_opencli(
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str,
) -> tuple[bool, str]:
    identity = parse_x_status(expected_identity_url or url)
    prefix = _opencli_command_prefix()
    profile = os.environ.get("BRIEFING_OPENCLI_PROFILE", "daily-briefing").strip()
    if not identity or not prefix or not profile:
        return False, "opencli_unavailable"
    username, status_id = identity
    session = f"brief-shot-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"
    base = [*prefix, "--profile", profile, "browser", session]
    ensure_dir(output.parent)
    try:
        opened = _run_opencli([*base, "open", url, "--window", "background"], timeout_seconds)
        if opened.returncode != 0:
            return False, f"opencli_open:{_redact_browser_error(opened.stderr or opened.stdout)}"
        waited = _run_opencli([*base, "wait", "time", "3"], min(timeout_seconds, 15))
        if waited.returncode != 0:
            return False, f"opencli_wait:{_redact_browser_error(waited.stderr or waited.stdout)}"
        current = _run_opencli([*base, "get", "url"], min(timeout_seconds, 15))
        if current.returncode != 0:
            return False, f"opencli_url:{_redact_browser_error(current.stderr or current.stdout)}"
        current_url = (current.stdout or "").strip().strip('"')
        if parse_x_status(current_url) != identity:
            return False, "opencli_social_identity_mismatch"

        needle = f"'/{username}/status/{status_id}'"
        isolate_script = f"""(() => {{
          const needle = {needle};
          const articles = [...document.querySelectorAll('article[data-testid=tweet]')];
          const exact = articles.find(article => [...article.querySelectorAll('a')]
            .some(link => (link.getAttribute('href') || '').toLowerCase().includes(needle)));
          const target = exact || articles[0];
          const visible = (target?.innerText || '').trim();
          if (!target || visible.length < 40) return {{found:false,text_length:visible.length}};
          const clone = target.cloneNode(true);
          for (const image of clone.querySelectorAll('img[src*=\\'pbs.twimg.com/media\\']')) {{
            try {{
              const original = new URL(image.getAttribute('src') || image.src, location.href);
              original.searchParams.set('name', 'orig');
              image.removeAttribute('srcset');
              image.setAttribute('loading', 'eager');
              image.setAttribute('decoding', 'sync');
              image.setAttribute('src', original.toString());
            }} catch (_) {{}}
          }}
          document.body.replaceChildren(clone);
          const style = document.createElement('style');
          style.textContent = `html,body{{margin:0!important;padding:0!important;min-height:0!important;background:#fff!important;color:#111!important;}}
            body{{display:flex!important;justify-content:center!important;align-items:flex-start!important;}}
            article{{box-sizing:border-box!important;width:min(100%,1000px)!important;margin:0 auto!important;padding:34px 42px!important;border:1px solid #dce3ea!important;border-radius:20px!important;background:#fff!important;}}
            [data-testid=caret],[data-testid=reply],[data-testid=retweet],[data-testid=like],[data-testid=bookmark],[data-testid=share]{{display:none!important;}}`;
          document.head.appendChild(style);
          const height = Math.ceil(clone.getBoundingClientRect().height) + 24;
          return {{found:true,text_length:visible.length,exact:!!exact,height}};
        }})()"""
        isolated = _run_opencli([*base, "eval", isolate_script], min(timeout_seconds, 20))
        if isolated.returncode != 0:
            return False, f"opencli_isolate:{_redact_browser_error(isolated.stderr or isolated.stdout)}"
        try:
            iso_res = json.loads(isolated.stdout or "{}")
        except json.JSONDecodeError:
            return False, "opencli_isolate_invalid_json"
        if not isinstance(iso_res, dict) or iso_res.get("found") is not True:
            return False, "opencli_social_target_missing"

        media_waited = _run_opencli([*base, "wait", "time", "2"], min(timeout_seconds, 12))
        if media_waited.returncode != 0:
            return False, f"opencli_media_wait:{_redact_browser_error(media_waited.stderr or media_waited.stdout)}"
        try:
            capture_height = max(420, min(1100, int(iso_res.get("height") or 700)))
        except (TypeError, ValueError):
            capture_height = 700

        output.unlink(missing_ok=True)
        captured = _run_opencli(
            [*base, "screenshot", str(output), "--width", "1200", "--height", str(capture_height)],
            timeout_seconds,
        )
        if captured.returncode != 0:
            return False, f"opencli_screenshot:{_redact_browser_error(captured.stderr or captured.stdout)}"
        return (True, "captured_logged_social_post") if _image_is_nonblank(output) else (False, "opencli_blank_screenshot")
    except subprocess.TimeoutExpired:
        return False, "opencli_timeout"
    except Exception as exc:
        return False, f"opencli_error:{_redact_browser_error(f'{type(exc).__name__}: {exc}')}"
    finally:
        try:
            _run_opencli([*base, "close"], 10)
        except Exception:
            pass


def _dismiss_consent_banner(page: Any, timeout_error: type[Exception]) -> bool:
    labels = (
        r"reject all|decline|only necessary|仅必要|拒绝全部",
        r"accept all|allow all cookies|同意全部|接受全部",
    )
    selectors = ("button", "[role='button']", "a")
    for label in labels:
        pattern = re.compile(label, re.I)
        for frame in page.frames:
            for selector in selectors:
                control = frame.locator(selector).filter(has_text=pattern).first
                try:
                    if control.count() > 0 and control.is_visible():
                        control.click(timeout=2_500)
                        page.wait_for_timeout(500)
                        return True
                except timeout_error:
                    continue
    return False


def _persistent_profile() -> tuple[str, str]:
    configured = os.environ.get("BRIEFING_BROWSER_USER_DATA_DIR", "").strip()
    default_root = project_root() / ".local" / "browser-profile"
    root = configured or (str(default_root) if default_root.exists() else "")
    profile = os.environ.get("BRIEFING_BROWSER_PROFILE_DIRECTORY", "").strip()
    return root, profile


def _capture_url(
    browser_exe: str,
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str = "",
    expected_text: str = "",
) -> tuple[bool, str]:
    ensure_dir(output.parent)
    output.unlink(missing_ok=True)
    identity = parse_x_status(expected_identity_url or url)
    if identity and _restore_social_cache(identity, output):
        return True, "captured_cached_social_post"
    unusable = _http_unusable_reason(url, timeout_seconds)
    if unusable:
        output.unlink(missing_ok=True)
        return False, unusable

    embed_detail = ""
    opencli_detail = ""
    if identity:
        embed_ok, embed_detail = _capture_social_embed(url, output, timeout_seconds, expected_identity_url or url, browser_exe=browser_exe)
        if embed_ok:
            _store_social_cache(identity, output)
            return True, embed_detail
        opencli_ok, opencli_detail = _capture_social_with_opencli(url, output, timeout_seconds, expected_identity_url or url)
        if opencli_ok:
            _store_social_cache(identity, output)
            return True, opencli_detail

    try:
        from .browser import load_sync_playwright

        sync_playwright, PlaywrightTimeoutError = load_sync_playwright()
    except Exception as exc:
        return False, f"playwright_unavailable:{type(exc).__name__}"

    profile_root, profile_dir = _persistent_profile()
    context = None
    launched_browser = None
    try:
        with sync_playwright() as pw:
            args = ["--disable-extensions", "--disable-background-networking", "--no-first-run", "--no-default-browser-check"]
            if profile_dir:
                args.append(f"--profile-directory={profile_dir}")
            if profile_root:
                ensure_dir(Path(profile_root))
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=profile_root,
                    executable_path=browser_exe,
                    headless=True,
                    viewport={"width": 1360, "height": 900},
                    device_scale_factor=1,
                    args=args,
                )
            else:
                launched_browser = pw.chromium.launch(executable_path=browser_exe, headless=True, args=args)
                context = launched_browser.new_context(viewport={"width": 1360, "height": 900}, device_scale_factor=1)
            page = context.pages[0] if context.pages else context.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=min(8_000, timeout_seconds * 1000))
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(900)
            status = int(resp.status) if resp else 0
            if status >= 400:
                return False, f"http_{status}"
            _dismiss_consent_banner(page, PlaywrightTimeoutError)
            dom = page.content()
            blocked = _blocked_page_reason(dom)
            if blocked:
                return False, f"blocked_page:{blocked}"

            if identity:
                username, status_id = identity
                target = None
                posts = page.locator("article[data-testid='tweet']")
                for idx in range(min(posts.count(), 20)):
                    post = posts.nth(idx)
                    if post.locator(f"a[href*='/{username}/status/{status_id}' i]").count() > 0 or post.locator(f"a[href*='/status/{status_id}']").count() > 0:
                        target = post
                        break
                if target is None:
                    return False, f"original={opencli_detail}; embed={embed_detail}; mirror=social_target_post_missing"
                visible = clean_text(target.inner_text(timeout=5_000))
                if len(visible) < 40:
                    return False, "social_post_content_too_thin"
                target.screenshot(path=str(output), timeout=timeout_seconds * 1000)
            else:
                story_err = _page_story_error(dom, expected_text)
                if story_err:
                    return False, story_err
                heading = page.locator("main h1, article h1, h1").first
                if heading.count() > 0:
                    try:
                        heading.scroll_into_view_if_needed(timeout=3_000)
                    except PlaywrightTimeoutError:
                        pass
                page.screenshot(path=str(output), full_page=False, timeout=timeout_seconds * 1000)
    except PlaywrightTimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, clean_text(f"{type(exc).__name__}: {exc}", 220)
    finally:
        for handle in (context, launched_browser):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    if _image_is_nonblank(output):
        if identity:
            _store_social_cache(identity, output)
        return True, "captured"
    return False, "blank_or_invalid_screenshot"


def _resolve_cjk_font_paths() -> tuple[str, str]:
    override = os.environ.get("BRIEFING_CJK_FONT", "").strip()
    bold_override = os.environ.get("BRIEFING_CJK_FONT_BOLD", "").strip() or override
    candidates = ([(override, bold_override)] if override else []) + list(_CJK_FONT_CANDIDATES)
    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            return regular, bold
    raise RuntimeError("no CJK font found for excerpt cards; set BRIEFING_CJK_FONT")


def _excerpt_fallback_allowed(card: EvidenceCard, evidence: dict[str, Any]) -> bool:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card_is_community_signal(card):
        return False
    if evidence_is_x_post(evidence):
        return False
    reliability = str(evidence.get("reliability") or "").lower()
    return reliability in {"official", "media"} and len(clean_text(str(evidence.get("excerpt") or ""))) >= 60


def _create_excerpt_card(output: Path, evidence: dict[str, Any], card: EvidenceCard | None = None) -> bool:
    """Render an explicitly labelled Pillow excerpt fallback card ('原文摘录 · 非网页截图')."""
    title = clean_text(str(evidence.get("title") or (card.event_title if card else "")), 180)
    excerpt = clean_text(str(evidence.get("excerpt") or ""), 700)
    if card is not None and not excerpt:
        cleaned_facts = [
            re.sub(r"^(?:来源摘要|官方信息)\s*[:：]\s*", "", clean_text(f))
            for f in card.key_facts
            if clean_text(f) and not f.startswith(("首要来源", f"{card.entity} 相关事件"))
        ]
        if cleaned_facts:
            excerpt = clean_text("。".join(cleaned_facts[:2]).rstrip("。") + "。", 700)
    source = clean_text(str(evidence.get("source") or "来源"), 80)
    if len(excerpt) < 60 or not title:
        return False

    try:
        from PIL import Image, ImageDraw, ImageFont

        regular_path, bold_path = _resolve_cjk_font_paths()
        regular = ImageFont.truetype(regular_path, 28)
        small = ImageFont.truetype(regular_path, 21)
        title_font = ImageFont.truetype(bold_path, 42)
        badge_font = ImageFont.truetype(bold_path, 22)

        image = Image.new("RGB", (1360, 900), "#F7FAFF")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((42, 40, 1318, 860), radius=34, fill="#FFFFFF", outline="#D9E5FF", width=3)
        draw.rounded_rectangle((78, 76, 392, 122), radius=23, fill="#3568FF")
        draw.text((102, 84), "原文摘录 · 非网页截图", font=badge_font, fill="#FFFFFF")
        draw.text((80, 152), source, font=small, fill="#5A6B8A")

        def _wrap(text: str, font: Any, width: int, max_lines: int) -> list[str]:
            lines: list[str] = []
            current = ""
            for ch in text:
                trial = current + ch
                if current and draw.textlength(trial, font=font) > width:
                    lines.append(current)
                    current = ch
                    if len(lines) >= max_lines:
                        break
                else:
                    current = trial
            if current and len(lines) < max_lines:
                lines.append(current)
            return lines

        y = 198
        for line in _wrap(title, title_font, 1190, 3):
            draw.text((80, y), line, font=title_font, fill="#13213C")
            y += 62
        y += 20
        draw.rounded_rectangle((78, y, 1282, 808), radius=22, fill="#EEF4FF")
        y += 34
        for line in _wrap(excerpt, regular, 1130, 9):
            draw.text((112, y), line, font=regular, fill="#263B5E")
            y += 46
        host = url_host(evidence.get("url")) or source
        draw.text((82, 822), f"来源域名：{host}", font=small, fill="#6D7F9F")
        ensure_dir(output.parent)
        image.save(output, format="PNG", optimize=True)
        return _image_is_nonblank(output)
    except Exception:
        output.unlink(missing_ok=True)
        return False


def _mark_card_requirements(card: EvidenceCard) -> list[int]:
    needs = _card_needs_screenshot(card)
    preferred = _preferred_evidence_indexes(card, required=needs)
    required_set = set(preferred) if needs else set()
    for idx, ev in enumerate(card.evidence_links):
        if idx in required_set:
            ev["screenshot_required"] = "true"
            if card_is_official_social_signal(card):
                ev["screenshot_reason"] = "X 官方账号首发必须保留原帖截图。"
            elif card_is_official_personnel_signal(card):
                ev["screenshot_reason"] = "X 官方人员一线消息必须保留原帖截图。"
            elif card_is_community_signal(card):
                ev["screenshot_reason"] = "传闻/风向类信息必须保留源头帖子截图。"
            else:
                ev["screenshot_reason"] = "反馈、性能、评测或榜单类信息需要源头截图。"
        else:
            ev.setdefault("screenshot_required", "false")
    return preferred


def attach_evidence_screenshots(
    cards: list[EvidenceCard],
    out_dir: Path,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture and attach verified screenshots (or labelled excerpt fallbacks) onto `cards`."""
    cfg = defaults or {}
    mode = str(os.environ.get("BRIEFING_EVIDENCE_SCREENSHOTS") or cfg.get("evidence_screenshots") or "auto").strip().lower()
    enabled = mode not in {"0", "false", "off", "none", "no"}
    capture_all = mode in {"1", "true", "yes", "all", "always"}
    required_only = mode in {"required", "critical", "strict"}

    target_cards = list(cards)
    required_keys = {c.cluster_key for c in target_cards if _card_needs_screenshot(c)}
    target_cards.sort(key=lambda c: c.cluster_key not in required_keys)

    preferred_map: dict[int, list[int]] = {id(c): _mark_card_requirements(c) for c in target_cards}
    result: dict[str, Any] = {
        "required_cards": len(required_keys),
        "attempted": 0,
        "captured": 0,
        "skipped": 0,
        "failed": 0,
        "status": "disabled" if not enabled else "ok",
    }
    if not target_cards:
        result["status"] = "no_selected_cards"
        return result
    if not enabled:
        for c in target_cards:
            for idx in preferred_map[id(c)]:
                c.evidence_links[idx]["screenshot_status"] = "disabled"
        return result

    exe = find_browser_executable()
    if exe is None:
        result["status"] = "no_browser"
        for c in target_cards:
            for idx in preferred_map[id(c)]:
                c.evidence_links[idx]["screenshot_status"] = "skipped_no_browser"
                result["skipped"] = int(result["skipped"]) + 1
        return result

    timeout_seconds = max(8, int(os.environ.get("BRIEFING_EVIDENCE_SCREENSHOT_TIMEOUT") or cfg.get("screenshot_timeout_seconds") or 28))
    default_limit = max(1, sum(len(preferred_map[id(c)]) for c in target_cards))
    capture_limit = max(1, int(os.environ.get("BRIEFING_EVIDENCE_SCREENSHOT_LIMIT") or cfg.get("screenshot_limit") or default_limit))
    capture_cache: dict[str, dict[str, str]] = {}

    for card in target_cards:
        required = card.cluster_key in required_keys
        should_capture = capture_all or not required_only or required or card.risk == "yellow"
        for ev_idx in preferred_map[id(card)]:
            ev = card.evidence_links[ev_idx]
            url = _capture_target_url(ev)
            if not should_capture:
                ev.setdefault("screenshot_status", "skipped_not_required")
                result["skipped"] = int(result["skipped"]) + 1
                continue
            if int(result["attempted"]) >= capture_limit:
                ev["screenshot_status"] = "skipped_limit"
                result["skipped"] = int(result["skipped"]) + 1
                continue
            if not _is_http_url(url):
                ev["screenshot_status"] = "skipped_invalid_url"
                result["skipped"] = int(result["skipped"]) + 1
                continue
            cache_key = url.rstrip("/")
            if cache_key in capture_cache:
                ev.update(capture_cache[cache_key])
                result["skipped"] = int(result["skipped"]) + 1
                continue

            expected_identity = parse_x_status(url)
            expected_id_key = f"{expected_identity[0]}/{expected_identity[1]}" if expected_identity else ""
            existing_path = Path(str(ev.get("screenshot_path") or ""))
            if (
                str(ev.get("screenshot_status") or "") == "captured"
                and _image_is_nonblank(existing_path)
                and (not expected_id_key or str(ev.get("screenshot_identity") or "") == expected_id_key)
            ):
                result["captured"] = int(result["captured"]) + 1
                continue

            output = _screenshot_path(out_dir, card, ev, ev_idx + 1)
            result["attempted"] = int(result["attempted"]) + 1
            expected_text = f"{ev.get('title', '')} {card.event_title}"
            ok, detail = _capture_url(str(exe), url, output, timeout_seconds, url, expected_text)
            capture_url = url
            discovery_url = str(ev.get("discovery_url") or "")
            if (
                not ok
                and discovery_url != url
                and _is_http_url(discovery_url)
                and str(ev.get("reconciled_official") or "").lower() != "true"
            ):
                mirror_ok, mirror_detail = _capture_url(str(exe), discovery_url, output, timeout_seconds, url, expected_text)
                if mirror_ok:
                    ok, detail, capture_url = True, "captured_from_discovery_mirror", discovery_url
                else:
                    detail = f"original={detail}; mirror={mirror_detail}"[:300]

            screenshot_kind = "social_post" if expected_identity else "web_page"
            if not ok and _excerpt_fallback_allowed(card, ev) and _create_excerpt_card(output, ev, card):
                ev["screenshot_capture_failure"] = detail
                ok = True
                detail = "captured_source_excerpt_card"
                capture_url = ""
                screenshot_kind = "source_excerpt_card"

            ev["screenshot_captured_at"] = now_utc().isoformat()
            ev["screenshot_status"] = "captured" if ok else "failed"
            ev["screenshot_path"] = str(output) if ok else ""
            ev["screenshot_capture_url"] = capture_url if ok else ""
            ev["screenshot_identity"] = expected_id_key if (ok and expected_id_key) else ""
            ev["screenshot_kind"] = screenshot_kind if ok else ""
            if ok:
                ev.pop("screenshot_error", None)
                capture_cache[cache_key] = {
                    k: str(ev.get(k) or "")
                    for k in (
                        "screenshot_captured_at",
                        "screenshot_status",
                        "screenshot_path",
                        "screenshot_capture_url",
                        "screenshot_identity",
                        "screenshot_kind",
                    )
                }
                result["captured"] = int(result["captured"]) + 1
            else:
                ev["screenshot_error"] = detail
                result["failed"] = int(result["failed"]) + 1

    return result


def prune_evidence_screenshots(out_dir: Path, selected_cards: list[EvidenceCard]) -> list[str]:
    """Remove unreferenced PNG files from `out_dir / 'evidence-screenshots'` and return removed paths."""
    run_dir = Path(out_dir).resolve()
    candidate_dir = run_dir / "evidence-screenshots"
    if candidate_dir.is_symlink() or not candidate_dir.is_dir():
        return []
    screenshot_dir = candidate_dir.resolve()
    try:
        screenshot_dir.relative_to(run_dir)
    except ValueError:
        return []

    referenced: set[Path] = set()
    for card in selected_cards:
        for ev in card.evidence_links:
            raw_path = str(ev.get("screenshot_path") or "").strip()
            if not raw_path:
                continue
            p = Path(raw_path)
            if not p.is_absolute():
                p = out_dir / p
            resolved = p.resolve()
            try:
                rel = resolved.relative_to(screenshot_dir)
            except ValueError:
                continue
            if len(rel.parts) == 1:
                referenced.add(resolved)

    removed: list[str] = []
    for path in sorted(screenshot_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        if path.resolve() in referenced:
            continue
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed
