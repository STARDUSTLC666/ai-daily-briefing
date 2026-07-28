from __future__ import annotations

import hashlib
from html import unescape
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .models import EvidenceCard
from .editorial_plan import build_editorial_plan
from .social_signals import card_is_community_signal, card_is_official_personnel_signal, card_is_official_social_signal, social_origin_evidence_indexes
from .util import clean_text
from .writer import select_cards

UTC = timezone.utc

SCREENSHOT_EVIDENCE_KEYWORDS = [
    "用户反馈",
    "用户评价",
    "真实反馈",
    "实际使用",
    "实测",
    "体感",
    "性能",
    "模型性能",
    "基准测试",
    "评测",
    "测评",
    "榜单",
    "排行榜",
    "跑分",
    "得分",
    "benchmark",
    "bench",
    "leaderboard",
    "arena",
    "lmarena",
    "swe-bench",
    "livecodebench",
    "aider",
    "eval",
    "score",
    "latency",
    "throughput",
    "open weights",
    "open-weight",
    "weights are now open",
    "released",
    "release",
    "开源",
    "权重",
]

STRICT_SCREENSHOT_EVIDENCE_KEYWORDS = [
    "用户反馈",
    "用户评价",
    "真实反馈",
    "实际使用",
    "实测",
    "体感",
    "性能",
    "模型性能",
    "基准测试",
    "评测",
    "测评",
    "榜单",
    "排行榜",
    "跑分",
    "得分",
    "benchmark",
    "bench",
    "leaderboard",
    "arena",
    "lmarena",
    "swe-bench",
    "livecodebench",
    "aider",
    "eval",
    "score",
    "latency",
    "throughput",
]

BLOCKED_PAGE_PHRASES = [
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
]


def _social_post_identity(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    match = re.search(r"/([A-Za-z0-9_]{1,32})/status/(\d{3,})", parsed.path, flags=re.I)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def _image_is_nonblank(path: Path) -> bool:
    if not path.exists() or not path.is_file() or path.stat().st_size < 4096:
        return False
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as image:
            gray = image.convert("L").resize((64, 42))
            extrema = gray.getextrema()
            deviation = float(ImageStat.Stat(gray).stddev[0])
            return bool(extrema and extrema[1] - extrema[0] >= 10 and deviation >= 3.0)
    except Exception:
        # A valid browser PNG is normally far larger than this.  Keep the
        # fallback conservative when Pillow is unavailable.
        return path.stat().st_size >= 12_000


def _social_dom_error(dom: str, expected_url: str) -> str:
    identity = _social_post_identity(expected_url)
    if identity is None:
        return ""
    username, status_id = identity
    lowered = dom.lower()
    if username not in lowered:
        return "social_username_missing"
    if status_id not in lowered:
        return "social_status_id_missing"
    visible = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", dom)
    visible = clean_text(unescape(re.sub(r"(?s)<[^>]+>", " ", visible)))
    if len(visible) < 80:
        return "social_post_content_too_thin"
    return ""


def _visible_dom_text(dom: str) -> str:
    visible = re.sub(r"(?is)<(?:script|style|noscript|svg)[^>]*>.*?</(?:script|style|noscript|svg)>", " ", dom or "")
    return clean_text(unescape(re.sub(r"(?s)<[^>]+>", " ", visible)))


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
    tokens = [
        token.strip("-_.").lower()
        for token in re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", expected.lower())
    ]
    tokens = list(dict.fromkeys(token for token in tokens if len(token) >= 3 and token not in stop))[:10]
    if not tokens:
        return ""
    matched = [
        token for token in tokens
        if token in visible or re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", token) in compact_visible
    ]
    required = 2 if len(tokens) >= 3 else 1
    return "story_identity_mismatch" if len(matched) < required else ""


def _enabled() -> bool:
    raw = os.environ.get("BRIEFING_EVIDENCE_SCREENSHOTS", "auto").strip().lower()
    return raw not in {"0", "false", "off", "none", "no"}


def evidence_requires_screenshot(card: EvidenceCard) -> bool:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card):
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
                *[str(e.get("title") or "") for e in card.evidence_links],
                *[str(e.get("url") or "") for e in card.evidence_links],
            ]
        )
    ).lower()
    return any(keyword.lower() in text for keyword in SCREENSHOT_EVIDENCE_KEYWORDS)


def _capture_all_selected() -> bool:
    raw = os.environ.get("BRIEFING_EVIDENCE_SCREENSHOTS", "auto").strip().lower()
    return raw in {"1", "true", "yes", "all", "always"}


def _capture_required_only() -> bool:
    raw = os.environ.get("BRIEFING_EVIDENCE_SCREENSHOTS", "auto").strip().lower()
    return raw in {"required", "critical", "strict"}


def _browser_exe() -> str | None:
    configured = os.environ.get("BRIEFING_CHROME") or os.environ.get("CHROME")
    candidates = [
        configured,
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _safe_slug(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    if not text:
        text = "source"
    return text[:44].strip("-") or "source"


def _screenshot_path(out_dir: Path, card: EvidenceCard, evidence: dict[str, str], index: int) -> Path:
    source = str(evidence.get("source") or "source")
    url = str(evidence.get("url") or "")
    digest = hashlib.sha1(f"{card.cluster_key}|{url}|{index}".encode("utf-8")).hexdigest()[:10]
    return out_dir / "evidence-screenshots" / f"{_safe_slug(source)}-{digest}.png"


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_aggregator_url(url: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == "news.google.com" or host.endswith(".news.google.com")


def _capture_target_url(evidence: dict[str, str]) -> str:
    """Prefer a validated enriched article URL over its discovery wrapper."""
    url = str(evidence.get("url") or "").strip()
    final_url = str(evidence.get("final_url") or "").strip()
    if (
        str(evidence.get("article_status") or "").lower() == "ok"
        and _is_http_url(final_url)
        and not _is_aggregator_url(final_url)
    ):
        return final_url
    return url


def _evidence_priority(evidence: dict[str, str], index: int) -> tuple[int, int]:
    """Rank canonical visuals: official, enriched original, media, aggregator."""
    reliability = str(evidence.get("reliability") or "").lower()
    tier = str(evidence.get("tier") or "").upper()
    source = clean_text(str(evidence.get("source") or "")).lower()
    capture_url = _capture_target_url(evidence)
    official = (
        str(evidence.get("reconciled_official") or "").lower() == "true"
        or reliability.startswith("official")
        or tier == "A"
    )
    if official:
        return 0, index

    enriched_original = (
        str(evidence.get("article_status") or "").lower() == "ok"
        and _is_http_url(capture_url)
        and not _is_aggregator_url(capture_url)
    )
    if enriched_original and reliability in {"media", "aggregator"}:
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


def _preferred_evidence_indexes(card: EvidenceCard, required: bool) -> list[int]:
    if required:
        social_indexes = social_origin_evidence_indexes(card)
        if social_indexes:
            return sorted(social_indexes)
    if not card.evidence_links:
        return []
    return [min(range(len(card.evidence_links)), key=lambda index: _evidence_priority(card.evidence_links[index], index))]


def _blocked_page_reason(text: str) -> str:
    lowered = clean_text(text or "").lower()
    for phrase in BLOCKED_PAGE_PHRASES:
        if phrase in lowered:
            return phrase
    return ""


def _http_unusable_reason(url: str, timeout_seconds: int) -> str:
    try:
        response = requests.get(
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
        if response.status_code in {404, 410, 429, 451}:
            return f"http_{response.status_code}"
    finally:
        response.close()
    return ""


def _persistent_profile() -> tuple[str, str]:
    configured = os.environ.get("BRIEFING_BROWSER_USER_DATA_DIR", "").strip()
    default_root = Path(__file__).resolve().parents[1] / ".local" / "browser-profile"
    root = configured or (str(default_root) if default_root.exists() else "")
    profile = os.environ.get("BRIEFING_BROWSER_PROFILE_DIRECTORY", "").strip()
    return root, profile


def _redact_browser_error(value: str) -> str:
    text = clean_text(value, 500)
    return re.sub(
        r"(?i)(auth_token|ct0|sessdata|bili_jct|cookie|token)\s*[:=]\s*[^\s,;}]+",
        r"\1=[REDACTED]",
        text,
    )


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the whole child tree: subprocess timeouts only kill the direct child,
    which leaves OpenCLI's Node-launched Chrome accumulating across nights."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                check=False,
            )
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
    timeout = max(10, timeout_seconds)
    popen_kwargs: dict[str, bool] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
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
    # Invoking an npm .cmd shim forces the eval JavaScript through cmd.exe a
    # second time and corrupts nested punctuation. Call OpenCLI's Node entry
    # point directly when the global npm layout is available.
    executable_path = Path(executable)
    main = executable_path.parent / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    node = shutil.which("node.exe") or shutil.which("node")
    if node and main.is_file():
        return [node, str(main)]
    return [executable]


def _capture_social_with_opencli(
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str,
) -> tuple[bool, str]:
    """Capture only the target X article through the logged-in browser bridge.

    The leased tab is reduced to a clone of the target article before capture,
    so the logged-in account, navigation sidebar and recommendations cannot leak
    into the public video frame.
    """

    identity = _social_post_identity(expected_identity_url or url)
    command_prefix = _opencli_command_prefix()
    profile = os.environ.get("BRIEFING_OPENCLI_PROFILE", "daily-briefing").strip()
    if not identity or not command_prefix or not profile:
        return False, "opencli_unavailable"
    username, status_id = identity
    session = f"brief-shot-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}"
    base = [*command_prefix, "--profile", profile, "browser", session]
    output.parent.mkdir(parents=True, exist_ok=True)
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
        if _social_post_identity(current_url) != identity:
            return False, "opencli_social_identity_mismatch"

        # Keep the JavaScript argument free of double quotes. On Windows the
        # npm ``opencli.cmd`` shim is invoked through cmd.exe, whose quoting
        # rules otherwise strip nested double quotes from the eval payload.
        needle = f"'/{username}/status/{status_id}'"
        isolate_script = f"""(() => {{
          const needle = {needle};
          const articles = [...document.querySelectorAll('article[data-testid=tweet]')];
          const exact = articles.find(article => [...article.querySelectorAll('a')]
            .some(link => (link.getAttribute('href') || '').includes(needle)));
          const target = exact || articles[0];
          const visible = (target?.innerText || '').trim();
          if (!target || visible.length < 40) return {{found:false,text_length:visible.length}};
          const clone = target.cloneNode(true);
          for (const image of clone.querySelectorAll('img[src*=\'pbs.twimg.com/media\']')) {{
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
            isolation_result = json.loads(isolated.stdout or "{}")
        except json.JSONDecodeError:
            return False, "opencli_isolate_invalid_json"
        if not isinstance(isolation_result, dict) or isolation_result.get("found") is not True:
            return False, "opencli_social_target_missing"
        # The cloned post now points at X's original media assets rather than
        # responsive thumbnails. Give those images a brief deterministic load
        # window before capture; post text remains available if media fails.
        media_waited = _run_opencli([*base, "wait", "time", "2"], min(timeout_seconds, 12))
        if media_waited.returncode != 0:
            return False, f"opencli_media_wait:{_redact_browser_error(media_waited.stderr or media_waited.stdout)}"
        try:
            capture_height = max(420, min(1100, int(isolation_result.get("height") or 700)))
        except (TypeError, ValueError):
            capture_height = 700

        try:
            output.unlink()
        except OSError:
            pass
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


def _social_embed_url(status_id: str) -> str:
    return f"https://platform.twitter.com/embed/Tweet.html?id={status_id}&dnt=true&theme=light&lang=zh-cn"


def _social_cache_path(identity: tuple[str, str]) -> Path:
    configured = os.environ.get("BRIEFING_SOCIAL_SCREENSHOT_CACHE_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1] / "data" / "evidence-cache" / "x-v2"
    username, status_id = identity
    return root / f"{_safe_slug(username)}-{status_id}.png"


def _restore_social_cache(identity: tuple[str, str], output: Path) -> bool:
    cached = _social_cache_path(identity)
    if not cached.is_file() or not _image_is_nonblank(cached):
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, output)
    return _image_is_nonblank(output)


def _store_social_cache(identity: tuple[str, str], output: Path) -> None:
    if not output.is_file() or not _image_is_nonblank(output):
        return
    cached = _social_cache_path(identity)
    cached.parent.mkdir(parents=True, exist_ok=True)
    temp = cached.with_suffix(cached.suffix + ".tmp")
    shutil.copy2(output, temp)
    temp.replace(cached)


def _capture_social_embed(
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str,
) -> tuple[bool, str]:
    """Capture X's public official embed when the logged page is unavailable.

    The embed is served by X itself, contains the canonical account, status,
    timestamp and media, and does not depend on the user's logged-in timeline
    being rate-limited.  A 2x device scale keeps charts and screenshots legible
    when the evidence card is enlarged in a 1080p video.
    """

    identity = _social_post_identity(expected_identity_url or url)
    if not identity:
        return False, "embed_identity_missing"
    username, status_id = identity
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return False, f"embed_playwright_unavailable:{type(exc).__name__}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-background-networking", "--no-first-run", "--no-default-browser-check"],
            )
            context = browser.new_context(
                viewport={"width": 720, "height": 1600},
                device_scale_factor=2,
                locale="zh-CN",
            )
            page = context.new_page()
            response = page.goto(
                _social_embed_url(status_id),
                wait_until="domcontentloaded",
                timeout=timeout_seconds * 1000,
            )
            if response and int(response.status) >= 400:
                return False, f"embed_http_{int(response.status)}"
            page.wait_for_timeout(min(5_000, max(2_500, timeout_seconds * 180)))

            target = None
            posts = page.locator("article")
            for index in range(min(posts.count(), 12)):
                post = posts.nth(index)
                if post.locator(f"a[href*='/status/{status_id}']").count() <= 0:
                    continue
                is_top_level = bool(post.evaluate("node => !node.parentElement?.closest('article')"))
                if is_top_level:
                    target = post
                    break
            if target is None:
                return False, "embed_target_post_missing"

            # Thread embeds may prepend a parent tweet as a nested article.
            # Remove only those nested context articles, never the target.
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
            if len(visible) < 40 or f"@{username}".lower() not in visible.lower():
                # A repost can show the original author instead of the account
                # that reposted it; the exact status link remains the identity
                # proof in that case.
                if target.locator(f"a[href*='/status/{status_id}']").count() <= 0 or len(visible) < 40:
                    return False, "embed_post_content_mismatch"
            output.parent.mkdir(parents=True, exist_ok=True)
            target.screenshot(path=str(output), timeout=timeout_seconds * 1000)
            context.close()
            browser.close()
    except PlaywrightTimeoutError:
        return False, "embed_timeout"
    except Exception as exc:
        return False, clean_text(f"embed_{type(exc).__name__}: {exc}", 220)
    return (True, "captured_official_x_embed") if _image_is_nonblank(output) else (False, "embed_blank_screenshot")


def _dismiss_consent_banner(page, timeout_error: type[Exception]) -> bool:
    """Dismiss common consent overlays without assuming the control is a button."""
    labels = [
        r"reject all|decline|only necessary|仅必要|拒绝全部",
        r"accept all|allow all cookies|同意全部|接受全部",
    ]
    selectors = ["button", "[role='button']", "a"]
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


_CJK_FONT_CANDIDATES: list[tuple[str, str]] = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    (
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    ),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
]


def _resolve_cjk_font_paths() -> tuple[str, str]:
    """Pick the first available CJK font pair (regular, bold); overridable via env."""
    override = os.environ.get("BRIEFING_CJK_FONT", "").strip()
    bold_override = os.environ.get("BRIEFING_CJK_FONT_BOLD", "").strip() or override
    candidates = ([(override, bold_override)] if override else []) + _CJK_FONT_CANDIDATES
    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            return regular, bold
    raise RuntimeError(
        "no CJK font found for excerpt cards; set BRIEFING_CJK_FONT (and optionally BRIEFING_CJK_FONT_BOLD)"
    )


def _create_excerpt_card(output: Path, evidence: dict[str, str], card: EvidenceCard | None = None) -> bool:
    """Create an explicitly labelled source excerpt visual, never a fake webpage."""
    excerpt = clean_text(str(evidence.get("excerpt") or ""), 700)
    title = clean_text(str(evidence.get("title") or ""), 180)
    if card is not None:
        plan = build_editorial_plan(card)
        title = plan.title or title
        public_facts = [clean_text(fact) for fact in plan.facts if clean_text(fact)]
        if public_facts:
            excerpt = "。".join(public_facts[:2]).rstrip("。") + "。"
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

        def wrap(text: str, font, width: int, max_lines: int) -> list[str]:
            lines: list[str] = []
            current = ""
            for char in text:
                trial = current + char
                if current and draw.textlength(trial, font=font) > width:
                    lines.append(current)
                    current = char
                    if len(lines) >= max_lines:
                        break
                else:
                    current = trial
            if current and len(lines) < max_lines:
                lines.append(current)
            return lines

        y = 198
        for line in wrap(title, title_font, 1190, 3):
            draw.text((80, y), line, font=title_font, fill="#13213C")
            y += 62
        y += 20
        draw.rounded_rectangle((78, y, 1282, 808), radius=22, fill="#EEF4FF")
        y += 34
        for line in wrap(excerpt, regular, 1130, 9):
            draw.text((112, y), line, font=regular, fill="#263B5E")
            y += 46
        host = urlparse(str(evidence.get("url") or "")).netloc.lower().removeprefix("www.")
        draw.text((82, 822), f"来源域名：{host or source}", font=small, fill="#6D7F9F")
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=True)
        return _image_is_nonblank(output)
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        return False


def _excerpt_fallback_allowed(card: EvidenceCard, evidence: dict[str, str]) -> bool:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card_is_community_signal(card):
        return False
    if _social_post_identity(str(evidence.get("url") or "")):
        return False
    reliability = str(evidence.get("reliability") or "").lower()
    return reliability in {"official", "media"} and len(clean_text(str(evidence.get("excerpt") or ""))) >= 60


def _mark_required(card: EvidenceCard, required: bool, preferred_indexes: list[int] | None = None) -> None:
    required_indexes = set(preferred_indexes or []) if required else set()
    for index, evidence in enumerate(card.evidence_links):
        evidence_required = index in required_indexes
        if evidence_required:
            evidence["screenshot_required"] = "true"
            if card_is_official_social_signal(card):
                evidence["screenshot_reason"] = "X 官方账号首发必须保留原帖截图。"
            elif card_is_official_personnel_signal(card):
                evidence["screenshot_reason"] = "X 官方人员一线消息必须保留原帖截图。"
            elif card_is_community_signal(card):
                evidence["screenshot_reason"] = "传闻/风向类信息必须保留源头帖子截图。"
            else:
                evidence["screenshot_reason"] = "反馈、性能、评测或榜单类信息需要源头截图。"
        else:
            evidence.setdefault("screenshot_required", "false")


def prune_evidence_screenshots(out_dir: Path, selected_cards: list[EvidenceCard]) -> dict[str, int | str]:
    """Remove only unreferenced screenshot files from this run's screenshot directory."""
    run_dir = out_dir.resolve()
    candidate_dir = run_dir / "evidence-screenshots"
    if candidate_dir.is_symlink():
        return {"status": "unsafe_scope", "kept": 0, "removed": 0, "failed": 0}
    screenshot_dir = candidate_dir.resolve()
    try:
        screenshot_dir.relative_to(run_dir)
    except ValueError:
        return {"status": "unsafe_scope", "kept": 0, "removed": 0, "failed": 0}
    if not screenshot_dir.is_dir():
        return {"status": "missing", "kept": 0, "removed": 0, "failed": 0}

    referenced: set[Path] = set()
    for card in selected_cards:
        for evidence in card.evidence_links:
            raw_path = str(evidence.get("screenshot_path") or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = out_dir / path
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(screenshot_dir)
            except ValueError:
                continue
            # Capture output is deliberately flat.  Never expand cleanup into
            # nested or sibling directories even if metadata is malformed.
            if len(relative.parts) == 1:
                referenced.add(resolved)

    kept = removed = failed = 0
    for path in screenshot_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        if path.resolve() in referenced:
            kept += 1
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            failed += 1
    return {"status": "ok" if failed == 0 else "partial", "kept": kept, "removed": removed, "failed": failed}


def _capture_url(
    browser: str,
    url: str,
    output: Path,
    timeout_seconds: int,
    expected_identity_url: str = "",
    expected_text: str = "",
) -> tuple[bool, str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.unlink()
    except OSError:
        pass
    identity = _social_post_identity(expected_identity_url or url)
    if identity and _restore_social_cache(identity, output):
        return True, "captured_cached_social_post"
    unusable = _http_unusable_reason(url, timeout_seconds)
    if unusable:
        try:
            output.unlink()
        except OSError:
            pass
        return False, unusable
    if identity:
        embed_ok, embed_detail = _capture_social_embed(
            url,
            output,
            timeout_seconds,
            expected_identity_url or url,
        )
        if embed_ok:
            _store_social_cache(identity, output)
            return True, embed_detail
        opencli_ok, opencli_detail = _capture_social_with_opencli(
            url,
            output,
            timeout_seconds,
            expected_identity_url or url,
        )
        if opencli_ok:
            _store_social_cache(identity, output)
            return True, opencli_detail
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return False, f"playwright_unavailable:{type(exc).__name__}"

    profile_root, profile_directory = _persistent_profile()
    context = None
    launched_browser = None
    try:
        with sync_playwright() as playwright:
            args = ["--disable-extensions", "--disable-background-networking", "--no-first-run", "--no-default-browser-check"]
            if profile_directory:
                args.append(f"--profile-directory={profile_directory}")
            if profile_root:
                Path(profile_root).mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=profile_root,
                    executable_path=browser,
                    headless=True,
                    viewport={"width": 1360, "height": 900},
                    device_scale_factor=1,
                    args=args,
                )
            else:
                launched_browser = playwright.chromium.launch(executable_path=browser, headless=True, args=args)
                context = launched_browser.new_context(viewport={"width": 1360, "height": 900}, device_scale_factor=1)
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=min(8_000, timeout_seconds * 1000))
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(900)
            status = int(response.status) if response else 0
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
                for index in range(min(posts.count(), 20)):
                    post = posts.nth(index)
                    if post.locator(f"a[href*='/{username}/status/{status_id}']").count() > 0:
                        target = post
                        break
                if target is None:
                    return False, f"original={opencli_detail}; embed={embed_detail}; mirror=social_target_post_missing"
                visible = clean_text(target.inner_text(timeout=5_000))
                if len(visible) < 40:
                    return False, "social_post_content_too_thin"
                target.screenshot(path=str(output), timeout=timeout_seconds * 1000)
            else:
                story_error = _page_story_error(dom, expected_text)
                if story_error:
                    return False, story_error
                # Put the article title into view before capturing the same
                # page instance whose DOM was validated above.
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
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if launched_browser is not None:
                launched_browser.close()
        except Exception:
            pass
    if _image_is_nonblank(output):
        if identity:
            _store_social_cache(identity, output)
        return True, "captured"
    return False, "blank_or_invalid_screenshot"


def attach_evidence_screenshots(
    out_dir: Path,
    cards: list[EvidenceCard],
    max_items: int = 9,
    category_limits: dict[str, int] | None = None,
    min_score: int = 0,
    strict_auto: bool = False,
    selected_override: list[EvidenceCard] | None = None,
) -> dict[str, int | str]:
    """Annotate selected cards with optional source screenshots.

    The run should remain publishable if a page blocks headless capture; missing
    screenshots are recorded in metadata so verification and Codex visual QA can
    catch evidence-sensitive stories before upload.
    """

    selected = selected_override if selected_override is not None else select_cards(
        cards,
        max_items=max_items,
        category_limits=category_limits,
        min_score=min_score,
        strict_auto=strict_auto,
    )
    selected_urls = {
        str(evidence.get("url") or "")
        for card in selected
        for evidence in card.evidence_links
        if str(evidence.get("url") or "")
    }
    selected_keys = {card.cluster_key for card in selected}
    target_cards = [
        card
        for card in cards
        if card.cluster_key in selected_keys or any(str(evidence.get("url") or "") in selected_urls for evidence in card.evidence_links)
    ]
    required_cards = {card.cluster_key for card in selected if evidence_requires_screenshot(card)}
    target_cards.sort(key=lambda card: card.cluster_key not in required_cards)
    preferred_indexes: dict[int, list[int]] = {}
    for card in target_cards:
        required = card.cluster_key in required_cards or evidence_requires_screenshot(card)
        preferred_indexes[id(card)] = _preferred_evidence_indexes(card, required)
        _mark_required(card, required, preferred_indexes[id(card)])

    result: dict[str, int | str] = {
        "required_cards": len(required_cards),
        "attempted": 0,
        "captured": 0,
        "skipped": 0,
        "failed": 0,
        "status": "disabled" if not _enabled() else "ok",
    }
    if not selected:
        result["status"] = "no_selected_cards"
        return result
    if not _enabled():
        for card in target_cards:
            for index in preferred_indexes[id(card)]:
                evidence = card.evidence_links[index]
                evidence["screenshot_status"] = "disabled"
        return result

    browser = _browser_exe()
    if not browser:
        result["status"] = "no_browser"
        for card in target_cards:
            for index in preferred_indexes[id(card)]:
                evidence = card.evidence_links[index]
                evidence["screenshot_status"] = "skipped_no_browser"
                result["skipped"] = int(result["skipped"]) + 1
        return result

    capture_all = _capture_all_selected()
    required_only = _capture_required_only()
    timeout_seconds = max(8, int(os.environ.get("BRIEFING_EVIDENCE_SCREENSHOT_TIMEOUT", "28")))
    # The selection limit counts stories, while required X threads can contain
    # several distinct source posts.  Budget by the actual preferred evidence
    # set so one multi-post thread cannot starve every later story.
    default_capture_limit = sum(len(preferred_indexes[id(card)]) for card in target_cards)
    if default_capture_limit <= 0:
        default_capture_limit = max(1, len(selected) if max_items <= 0 else max_items)
    capture_limit = max(1, int(os.environ.get("BRIEFING_EVIDENCE_SCREENSHOT_LIMIT", str(default_capture_limit))))
    capture_cache: dict[str, dict[str, str]] = {}

    for card in target_cards:
        required = card.cluster_key in required_cards or evidence_requires_screenshot(card)
        should_capture = capture_all or not required_only or required or card.risk == "yellow"
        # Required social evidence keeps every source-post index. Other stories
        # receive one canonical visual chosen by the source priority above.
        evidence_indexes = preferred_indexes[id(card)]
        for evidence_index in evidence_indexes:
            evidence = card.evidence_links[evidence_index]
            idx = evidence_index + 1
            evidence_url = str(evidence.get("url") or "")
            if selected_urls and evidence_url not in selected_urls:
                continue
            url = _capture_target_url(evidence)
            if not should_capture:
                evidence.setdefault("screenshot_status", "skipped_not_required")
                result["skipped"] = int(result["skipped"]) + 1
                continue
            if int(result["attempted"]) >= capture_limit:
                evidence["screenshot_status"] = "skipped_limit"
                result["skipped"] = int(result["skipped"]) + 1
                continue
            if not _is_http_url(url):
                evidence["screenshot_status"] = "skipped_invalid_url"
                result["skipped"] = int(result["skipped"]) + 1
                continue
            cache_key = url.rstrip("/")
            if cache_key in capture_cache:
                evidence.update(capture_cache[cache_key])
                result["skipped"] = int(result["skipped"]) + 1
                continue
            existing_path = Path(str(evidence.get("screenshot_path") or ""))
            expected_identity = _social_post_identity(url)
            expected_identity_key = "/".join(expected_identity) if expected_identity else ""
            existing_identity = str(evidence.get("screenshot_identity") or "")
            if (
                str(evidence.get("screenshot_status") or "") == "captured"
                and _image_is_nonblank(existing_path)
                and (not expected_identity_key or existing_identity == expected_identity_key)
            ):
                result["captured"] = int(result["captured"]) + 1
                continue
            output = _screenshot_path(out_dir, card, evidence, idx)
            result["attempted"] = int(result["attempted"]) + 1
            expected_text = " ".join([str(evidence.get("title") or ""), card.event_title])
            ok, detail = _capture_url(browser, url, output, timeout_seconds, url, expected_text)
            capture_url = url
            discovery_url = str(evidence.get("discovery_url") or "")
            if (
                not ok
                and discovery_url != url
                and _is_http_url(discovery_url)
                and str(evidence.get("reconciled_official") or "").lower() != "true"
            ):
                mirror_ok, mirror_detail = _capture_url(browser, discovery_url, output, timeout_seconds, url, expected_text)
                if mirror_ok:
                    ok, detail, capture_url = True, "captured_from_discovery_mirror", discovery_url
                else:
                    detail = f"original={detail}; mirror={mirror_detail}"[:300]
            screenshot_kind = "social_post" if _social_post_identity(url) else "web_page"
            if not ok and _excerpt_fallback_allowed(card, evidence) and _create_excerpt_card(output, evidence, card):
                evidence["screenshot_capture_failure"] = detail
                ok = True
                detail = "captured_source_excerpt_card"
                capture_url = ""
                screenshot_kind = "source_excerpt_card"
            evidence["screenshot_captured_at"] = datetime.now(UTC).isoformat()
            evidence["screenshot_status"] = "captured" if ok else "failed"
            evidence["screenshot_path"] = str(output) if ok else ""
            evidence["screenshot_capture_url"] = capture_url if ok else ""
            evidence["screenshot_identity"] = expected_identity_key if ok and expected_identity_key else ""
            evidence["screenshot_kind"] = screenshot_kind if ok else ""
            if ok:
                evidence.pop("screenshot_error", None)
                capture_cache[cache_key] = {
                    key: str(evidence.get(key) or "")
                    for key in [
                        "screenshot_captured_at",
                        "screenshot_status",
                        "screenshot_path",
                        "screenshot_capture_url",
                        "screenshot_identity",
                        "screenshot_kind",
                    ]
                }
                result["captured"] = int(result["captured"]) + 1
            else:
                evidence["screenshot_error"] = detail
                result["failed"] = int(result["failed"]) + 1
    return result
