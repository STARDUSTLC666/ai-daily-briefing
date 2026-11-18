from __future__ import annotations

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

import requests

from briefing.core import (
    MODEL_TOKEN_RE,
    FeedItem,
    Source,
    SourceHealth,
    clean_text,
    ensure_dir,
    iso,
    load_config,
    load_sources,
    project_root,
    write_json_atomic,
    write_text_atomic,
)
from briefing.verify import (
    verify_agent_edit_contract,
    verify_run_dir,
    verify_visual_agent_audit,
)
from briefing.verify.contract import _validate_agent_edits

DEFAULT_AUDIT_USER_AGENT = "DailyBilibiliBriefing/0.1 (+local; crawler health audit)"
RICH_TEXT_CHARS = 500
RICH_FACT_COUNT = 2


def _probe(command: list[str]) -> bool:
    try:
        proc = subprocess.run(command, capture_output=True, timeout=15, check=False)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _find_browser_executable() -> str | None:
    try:
        from briefing.ingest.browser import find_browser_executable  # type: ignore

        found = find_browser_executable()
        if found and Path(str(found)).is_file():
            return str(found)
    except Exception:
        pass

    configured = os.environ.get("BRIEFING_CHROME") or os.environ.get("CHROME")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        configured,
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
        shutil.which("chromium"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe") if local_app_data else None,
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/opt/google/chrome/chrome",
        "/opt/microsoft/msedge/msedge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _detect_crawl_channel(browser_exe: str | None) -> str:
    explicit = os.environ.get("BRIEFING_CRAWL_CHANNEL", "").strip().lower()
    if explicit in {"chrome", "msedge", "chromium"}:
        return explicit
    lowered = (browser_exe or "").lower()
    if "msedge" in lowered or "edge" in lowered:
        return "msedge"
    if "chrome" in lowered:
        return "chrome"
    return "chromium"


def _remotion_cli_path(repo_root: Path) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return repo_root / "remotion" / "node_modules" / ".bin" / f"remotion{suffix}"


def runtime_doctor(config_path: Path | None = None) -> dict[str, Any]:
    """Inspect local runtime prerequisites for briefing ingestion, rendering, and optional publishing."""
    root = project_root().resolve()
    default_cfg = root / "sources.public-web.json" if (root / "sources.public-web.json").exists() else root / "sources.yaml"
    resolved_cfg = (Path(config_path) if config_path else default_cfg).resolve()

    cfg: dict[str, Any] = {}
    sources: list[Source] = []
    config_error = ""
    try:
        cfg = load_config(resolved_cfg)
        sources = load_sources(resolved_cfg)
    except Exception as exc:
        config_error = str(exc)

    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, fix: str, *, required: bool = True) -> None:
        checks.append({"name": name, "ok": bool(ok), "required": required, "fix": "" if ok else fix})

    record("python", sys.version_info >= (3, 11), "Use Python 3.11+ and run scripts/bootstrap.ps1.")
    for mod in ("requests", "PIL", "edge_tts", "crawl4ai"):
        record(mod, importlib.util.find_spec(mod) is not None, "Run scripts/bootstrap.ps1.")
    pw_mod = "playwright" if importlib.util.find_spec("playwright") is not None else ("patchright" if importlib.util.find_spec("patchright") is not None else "")
    record("playwright", bool(pw_mod), "Run scripts/bootstrap.ps1.")

    node = shutil.which("node")
    node_ok = bool(node) and _probe(
        [node, "-e", 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)']
    )
    record("node_22", node_ok, "Install Node.js 22 or later.")

    for tool_name in ("ffmpeg", "ffprobe"):
        tool_path = shutil.which(tool_name)
        record(tool_name, bool(tool_path) and _probe([tool_path, "-version"]), f"Install {tool_name} and add it to PATH.")

    record("remotion", _remotion_cli_path(root).exists(), "Run npm --prefix remotion ci.")

    browser_exe = _find_browser_executable()
    playwright_chromium_ok = False
    if not browser_exe and pw_mod:
        playwright_chromium_ok = _probe(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; from {pw_mod}.sync_api import sync_playwright; "
                "p=sync_playwright().start(); ok=Path(p.chromium.executable_path).is_file(); "
                "p.stop(); raise SystemExit(0 if ok else 1)",
            ]
        )
    record(
        "browser",
        bool(browser_exe) or playwright_chromium_ok,
        "Install Chrome/Edge or set BRIEFING_CHROME to a local browser executable.",
    )

    record(
        "sources",
        bool(sources) and not config_error,
        config_error or "Enable at least one source in the edition configuration.",
    )

    creds_ok = all(os.environ.get(k, "").strip() for k in ("BILI_CLIENT_ID", "BILI_CLIENT_SECRET", "BILI_ACCESS_TOKEN"))
    creds_file_ok = (root / ".local" / "bilibili-credentials.clixml").exists()
    record(
        "bilibili_credentials",
        creds_ok or creds_file_ok,
        "Configure Bilibili Open Platform credentials with scripts/configure_bilibili.ps1.",
        required=False,
    )

    channel = _detect_crawl_channel(browser_exe)
    return {
        "ok": all(row["ok"] for row in checks if row["required"]),
        "profile": str(cfg.get("profile") or "default"),
        "config_path": str(resolved_cfg),
        "browser_executable": browser_exe or "",
        "crawl_channel": channel,
        "source_count": len(sources),
        "checks": checks,
        "publishing_enabled": os.environ.get("BRIEFING_AUTO_PUBLISH") == "1",
    }


def _read_strict_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def automation_status(
    run_dir: Path,
    *,
    config_path: Path | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Read-only state machine returning `stage`, `ok`, `blocked`, and unified `next_command`."""
    run_dir = Path(run_dir).resolve()
    root = project_root().resolve()
    default_cfg = root / "sources.public-web.json" if (root / "sources.public-web.json").exists() else root / "sources.yaml"
    resolved_cfg = (Path(config_path) if config_path else default_cfg).resolve()
    expected_today = today or datetime.now().strftime("%Y-%m-%d")

    def state_payload(
        stage: str,
        next_command: str = "",
        *,
        errors: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        is_blocked = stage in {"blocked", "submission_unknown"}
        return {
            "ok": not is_blocked,
            "blocked": is_blocked,
            "stage": stage,
            "next_command": next_command,
            "run_dir": str(run_dir),
            "errors": list(errors or []),
            "note": note,
        }

    try:
        if run_dir.name != expected_today:
            return state_payload(
                "blocked",
                errors=[f"Run directory date {run_dir.name!r} does not match today ({expected_today})."],
            )

        upload = _read_strict_object(run_dir / "bilibili-upload-result.json")
        if upload.get("status") == "submitted" and upload.get("dry_run") is False:
            return state_payload("submitted", note="Archive already submitted to Bilibili.")
        if upload.get("status") == "submitting":
            return state_payload(
                "submission_unknown",
                errors=["Submission outcome is unknown; check the Bilibili creator center before any retry."],
            )

        manifest = _read_strict_object(run_dir / "manifest.json")
        if not manifest:
            if run_dir.exists() and any(run_dir.iterdir()):
                return state_payload(
                    "blocked",
                    errors=["An incomplete run directory already exists without manifest.json; inspect it before preparing."],
                )
            return state_payload(
                "new",
                f'py -3 -m briefing prepare --date {run_dir.name} --config "{resolved_cfg}" --runs-dir "{run_dir.parent}"',
            )

        if manifest.get("qa_fixture") is True:
            return state_payload(
                "blocked",
                errors=["Design samples and QA fixtures must never enter daily publication."],
            )

        package = manifest.get("package_quality") or {}
        if isinstance(package, dict) and package.get("ok") is False:
            return state_payload(
                "no_edition",
                errors=[str(package.get("reason") or "No sufficiently verified news; skip this edition.")],
            )

        policy = manifest.get("agent_policy") or {}
        agent_required = isinstance(policy, dict) and policy.get("required") is True

        if agent_required:
            if not (run_dir / "review" / "agent-audit.json").exists():
                return state_payload(
                    "source_review",
                    note="Inspect sources and copy, write review/agent-audit.json, then finalize review.",
                )
            if not (run_dir / "review" / "agent-contract.json").exists():
                edit_errors = _validate_agent_edits(run_dir)
                if edit_errors:
                    return state_payload("blocked", errors=edit_errors)
                return state_payload(
                    "finalize_review",
                    f'py -3 -m briefing review --finalize --run-dir "{run_dir}"',
                )
            contract_errors = verify_agent_edit_contract(run_dir)
            if contract_errors:
                return state_payload("blocked", errors=contract_errors)
        elif (run_dir / "review" / "state.json").exists() and not (run_dir / "review" / "final-script.json").exists():
            return state_payload(
                "finalize_review",
                f'py -3 -m briefing review --finalize --run-dir "{run_dir}"',
            )

        render_info = _read_strict_object(run_dir / "render-info.json")
        pending_render = (run_dir / "review" / "render-required.json").exists()
        if not (run_dir / "final.mp4").exists() or render_info.get("status") != "ok" or pending_render:
            return state_payload("render", f'py -3 -m briefing render --run-dir "{run_dir}"')

        if agent_required:
            if not (run_dir / "review" / "visual-qa" / "visual-qa-input.json").exists():
                return state_payload("render", f'py -3 -m briefing render --run-dir "{run_dir}"')
            if not (run_dir / "review" / "visual-qa" / "visual-agent-audit.json").exists():
                return state_payload(
                    "visual_review",
                    note="Inspect rendered video and extracted frames, then write review/visual-qa/visual-agent-audit.json.",
                )
            vis_errors = verify_visual_agent_audit(run_dir)
            if vis_errors:
                return state_payload("blocked", errors=vis_errors)

        run_state = _read_strict_object(run_dir / "run-state.json")
        auto_gate_ok = (manifest.get("automatic_quality_gate") or {}).get("ok") is True
        state_qa_passed = run_state.get("status") == "QA_PASSED"

        if not (auto_gate_ok and state_qa_passed):
            in_progress_check = verify_run_dir(run_dir, allow_in_progress=True)
            if not in_progress_check.get("ok"):
                return state_payload(
                    "blocked",
                    errors=list(in_progress_check.get("errors") or []),
                )
            return state_payload("verify", f'py -3 -m briefing verify --run-dir "{run_dir}"')

        verification = verify_run_dir(run_dir)
        if not verification.get("ok"):
            return state_payload("blocked", errors=list(verification.get("errors") or []))

        return state_payload("ready", f'py -3 -m briefing publish --run-dir "{run_dir}"')
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return state_payload("blocked", errors=[str(exc)])


def _is_generic_discovery_item(item: FeedItem | None) -> bool:
    if not item:
        return True
    title = clean_text(item.title).lower()
    if title in {
        "news",
        "新闻",
        "blog",
        "research",
        "models",
        "首页",
        "home",
        "release notes",
        "announcement",
        "announcements",
        "release",
        "developers",
        "developer",
        "api pricing",
        "pricing",
        "立即体验",
        "开始对话",
        "技术博客",
        "联系我们",
        "用户协议",
        "隐私政策",
        "服务条款",
        "模型发布",
        "研究成果",
        "发布",
        "上线",
        "更新",
        "contact",
        "contact us",
    }:
        return True
    try:
        parsed_path = urlparse(item.link).path.rstrip("/").lower()
    except Exception:
        parsed_path = ""
    if parsed_path in {"", "/", "/news", "/blog", "/research", "/models", "/products", "/pricing"}:
        return True
    combined = clean_text(f"{item.title} {item.summary} {item.link}").lower()
    return any(p in combined for p in ("信息不够", "页面有更新", "官网新闻页", "了解更多")) and not bool(
        MODEL_TOKEN_RE.search(combined)
    )


def _discover_web_source(source: Source, *, user_agent: str) -> tuple[SourceHealth, list[FeedItem]]:
    try:
        from ..ingest import discover_web_source

        return discover_web_source(source, defaults={"user_agent": user_agent})
    except Exception:
        pass

    started = time.monotonic()
    try:
        resp = requests.get(source.url, headers={"User-Agent": user_agent}, timeout=source.timeout_seconds)
        latency_ms = round((time.monotonic() - started) * 1000)
        status = "ok" if resp.status_code < 400 else f"http_{resp.status_code}"
        return (
            SourceHealth(
                source_id=source.id,
                source_name=source.name,
                tier=source.tier,
                enabled=source.enabled,
                status=status,
                status_code=resp.status_code,
                item_count=0,
                latency_ms=latency_ms,
                final_url=str(resp.url or source.url),
            ),
            [],
        )
    except Exception as exc:
        return (
            SourceHealth(
                source_id=source.id,
                source_name=source.name,
                tier=source.tier,
                enabled=source.enabled,
                status="error",
                latency_ms=round((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            ),
            [],
        )


def _fetch_web_article(item: FeedItem, *, user_agent: str, timeout: int, asset_dir: Path | None) -> dict[str, Any] | None:
    try:
        from ..ingest import enrich_feed_items

        probe_item = FeedItem(
            source_id=item.source_id,
            source_name=item.source_name,
            source_tier=item.source_tier,
            source_reliability=item.source_reliability,
            title=item.title,
            link=item.link,
            guid=item.guid,
            summary=item.summary,
            published_at=item.published_at,
            fetched_at=item.fetched_at,
            raw=dict(item.raw or {}),
        )
        enrich_feed_items(
            [probe_item],
            defaults={"user_agent": user_agent, "timeout_seconds": timeout, "content_enrichment_max_items": 1},
            asset_dir=asset_dir,
        )
        enrichment = (probe_item.raw or {}).get("article_enrichment") or {}
        excerpts = (probe_item.raw or {}).get("evidence_excerpts") or []
        return {
            "status": str(enrichment.get("status") or ("ok" if probe_item.summary else "empty")),
            "text": probe_item.summary or "",
            "facts": [row.get("text", "") for row in excerpts if isinstance(row, dict) and row.get("text")],
            "images": enrichment.get("images") or [],
            "screenshot_path": enrichment.get("screenshot_path") or "",
        }
    except Exception:
        return None


def _audit_verdict(health: SourceHealth, items: list[FeedItem], article: Any) -> tuple[str, str]:
    first = items[0] if items else None
    if health.status != "ok" or not items:
        return "needs_adapter", "发现页没有产出可用详情链接，需检查官网结构或专属适配。"
    if _is_generic_discovery_item(first):
        return "needs_adapter", "发现项偏泛，不能直接进入成品，需补抓真实发布页。"
    if article is None:
        return "discovery_ok", "发现页可用；未执行详情页抓取。"
    article_status = str(getattr(article, "status", "") or (article.get("status") if isinstance(article, dict) else ""))
    text_body = clean_text(
        str(getattr(article, "text", "") or getattr(article, "markdown", "") or (article.get("text") if isinstance(article, dict) else "") or "")
    )
    facts = getattr(article, "facts", None) if not isinstance(article, dict) else article.get("facts")
    images = getattr(article, "images", None) if not isinstance(article, dict) else article.get("images")
    screenshot = getattr(article, "screenshot_path", None) if not isinstance(article, dict) else article.get("screenshot_path")

    if article_status == "ok" and (
        len(text_body) >= RICH_TEXT_CHARS or len(facts or []) >= RICH_FACT_COUNT or (bool(images) and bool(screenshot))
    ):
        return "ok", "发现页和详情页都能产出具体信息。"
    if article_status == "ok":
        return "thin", "详情页抓到了，但正文/事实/图片偏薄，需要增强 adapter。"
    return "needs_adapter", "详情页抓取失败，需要兜底或专属适配。"


def _format_audit_markdown(rows: list[dict[str, Any]], counts: dict[str, int]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "无"
    lines = [
        f"# 主流 AI 官网爬虫健康检查（{today}）",
        "",
        f"- 汇总：{summary}",
        "",
        "| 源 | 发现 | 首条详情 | 正文 | 事实 | 结论 | 备注 |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        src = row["source"]
        hlth = row["health"]
        first = row["first_item"]
        art = row["article"]
        title = clean_text(str(first.get("title") or ""))
        link = str(first.get("link") or "")
        title_cell = f"[{title}]({link})" if title and link else "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{src['name']} `{src['id']}`",
                    f"{hlth.get('status', '-')} / {hlth.get('item_count', 0)}",
                    title_cell,
                    str(art.get("text_chars") or "-"),
                    str(art.get("facts_count") or "-"),
                    str(row["verdict"]),
                    clean_text(str(row["note"]), 80),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def audit_web_sources(
    config_path: Path | None = None,
    *,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a clean discovery & article health audit across enabled `type == 'web'` official sources."""
    resolved_cfg = (Path(config_path) if config_path else (project_root() / "sources.yaml")).resolve()
    cfg = load_config(resolved_cfg)
    defaults = cfg.get("defaults") or {}
    default_timeout = int(defaults.get("timeout_seconds", 18))
    user_agent = str(defaults.get("user_agent") or DEFAULT_AUDIT_USER_AGENT)

    web_sources = [s for s in load_sources(resolved_cfg) if s.enabled and s.type == "web" and s.is_official]
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    resolved_out = ensure_dir(Path(out_dir).resolve()) if out_dir is not None else None

    for source in web_sources:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", source.id).strip("-")[:80] or "source"
        asset_dir = ensure_dir(resolved_out / "assets" / slug) if resolved_out else None
        try:
            health, items = _discover_web_source(source, user_agent=user_agent)
            first = items[0] if items else None
            article = (
                _fetch_web_article(
                    first,
                    user_agent=user_agent,
                    timeout=source.timeout_seconds or default_timeout,
                    asset_dir=asset_dir,
                )
                if first
                else None
            )
            verdict, note = _audit_verdict(health, items, article)
            article_text = clean_text(
                str(
                    getattr(article, "text", "")
                    or getattr(article, "markdown", "")
                    or (article.get("text") if isinstance(article, dict) else "")
                    or ""
                )
            )
            facts = (getattr(article, "facts", None) if not isinstance(article, dict) else article.get("facts")) if article else []
            row = {
                "source": {
                    "id": source.id,
                    "name": source.name,
                    "tier": source.tier,
                    "region": source.region,
                    "url": source.url,
                },
                "health": {
                    "source_id": health.source_id,
                    "status": health.status,
                    "status_code": health.status_code,
                    "item_count": health.item_count,
                    "latest_item_at": iso(health.latest_item_at),
                    "latency_ms": health.latency_ms,
                    "error": health.error,
                    "final_url": health.final_url,
                },
                "first_item": (
                    {
                        "title": first.title,
                        "link": first.link,
                        "summary": first.summary,
                        "published_at": iso(first.published_at),
                    }
                    if first
                    else {}
                ),
                "article": (
                    {
                        "status": str(getattr(article, "status", "") or (article.get("status") if isinstance(article, dict) else "")),
                        "text_chars": len(article_text),
                        "facts_count": len(facts or []),
                    }
                    if article is not None
                    else {}
                ),
                "verdict": verdict,
                "note": note,
            }
        except Exception as exc:
            verdict = "needs_adapter"
            row = {
                "source": {
                    "id": source.id,
                    "name": source.name,
                    "tier": source.tier,
                    "region": source.region,
                    "url": source.url,
                },
                "health": {},
                "first_item": {},
                "article": {},
                "verdict": verdict,
                "note": f"{type(exc).__name__}: {exc}",
            }
        counts[verdict] = counts.get(verdict, 0) + 1
        rows.append(row)

    report: dict[str, Any] = {
        "ok": counts.get("needs_adapter", 0) == 0,
        "config_path": str(resolved_cfg),
        "total": len(rows),
        "counts": counts,
        "sources": rows,
    }
    if resolved_out is not None:
        json_path = resolved_out / "official-web-crawler-health.json"
        md_path = resolved_out / "official-web-crawler-health.md"
        write_json_atomic(json_path, report)
        write_text_atomic(md_path, _format_audit_markdown(rows, counts))
        report["json_report"] = str(json_path)
        report["markdown_report"] = str(md_path)
    return report
