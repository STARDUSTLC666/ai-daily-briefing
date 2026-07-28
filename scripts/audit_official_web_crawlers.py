from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing.collect import fetch_web_discovery_source
from briefing.config import load_config, load_sources
from briefing.content_enrichment import ArticleResult, fetch_article
from briefing.models import FeedItem, Source, SourceHealth, iso
from briefing.util import clean_text, ensure_dir


DEFAULT_USER_AGENT = "DailyBilibiliBriefing/0.1 (+local; crawler health audit)"
RICH_TEXT_CHARS = 500
RICH_FACT_COUNT = 2


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip("-")
    return text[:80] or "source"


def _item_payload(item: FeedItem | None) -> dict[str, Any]:
    if not item:
        return {}
    return {
        "title": item.title,
        "link": item.link,
        "summary": item.summary,
        "published_at": iso(item.published_at),
        "fetched_at": iso(item.fetched_at),
    }


def _health_payload(health: SourceHealth) -> dict[str, Any]:
    return {
        "source_id": health.source_id,
        "source_name": health.source_name,
        "tier": health.tier,
        "enabled": health.enabled,
        "status": health.status,
        "status_code": health.status_code,
        "item_count": health.item_count,
        "latest_item_at": iso(health.latest_item_at),
        "latency_ms": health.latency_ms,
        "error": health.error,
        "final_url": health.final_url,
        "stale": health.stale,
        "no_date_count": health.no_date_count,
    }


def _article_payload(article: ArticleResult | None) -> dict[str, Any]:
    if not article:
        return {}
    text = clean_text(article.text or article.markdown)
    return {
        "status": article.status,
        "crawler": article.crawler,
        "url": article.url,
        "final_url": article.final_url,
        "status_code": article.status_code,
        "title": article.title,
        "text_chars": len(text),
        "facts_count": len(article.facts or []),
        "facts": (article.facts or [])[:6],
        "images_count": len(article.images or []),
        "images": article.images[:6],
        "screenshot_path": article.screenshot_path,
        "published_at": iso(article.published_at),
        "error": article.error,
        "repair": article.repair,
    }


def _is_generic_item(item: FeedItem | None) -> bool:
    if not item:
        return True
    text = clean_text(" ".join([item.title, item.summary, item.link])).lower()
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
    parsed_path = ""
    try:
        from urllib.parse import urlparse

        parsed_path = urlparse(item.link).path.rstrip("/").lower()
    except Exception:
        parsed_path = ""
    if parsed_path in {"/", "/news", "/blog", "/research", "/models", "/products", "/pricing"}:
        return True
    return any(phrase in text for phrase in ["信息不够", "页面有更新", "官网新闻页", "了解更多"]) and not re.search(
        r"\b(?:gpt|claude|gemini|qwen|deepseek|hunyuan|hy|kimi|llama|mistral|glm|grok|minimax|doubao)[-_\s]?\d[\w.-]*\b",
        text,
    )


def _article_rich_enough(article: ArticleResult | None) -> bool:
    if not article or article.status != "ok":
        return False
    text_chars = len(clean_text(article.text or article.markdown))
    facts = len(article.facts or [])
    images = len(article.images or [])
    screenshot = bool(article.screenshot_path)
    return text_chars >= RICH_TEXT_CHARS or facts >= RICH_FACT_COUNT or (images > 0 and screenshot)


def _verdict(health: SourceHealth, items: list[FeedItem], article: ArticleResult | None) -> tuple[str, str]:
    first = items[0] if items else None
    if health.status != "ok" or not items:
        return "needs_adapter", "发现页没有产出可用详情链接，需检查官网结构或专属适配。"
    if _is_generic_item(first):
        return "needs_adapter", "发现项偏泛，不能直接进入成品，需补抓真实发布页。"
    if article is None:
        return "discovery_ok", "发现页可用；本次未跑详情页 enrichment。"
    if _article_rich_enough(article):
        return "ok", "发现页和详情页都能产出具体信息。"
    if article.status == "ok":
        return "thin", "详情页抓到了，但正文/事实/图片偏薄，需要增强 adapter。"
    return "needs_adapter", "详情页抓取失败，需要兜底或专属适配。"


def _source_rows(args: argparse.Namespace) -> list[Source]:
    sources = [s for s in load_sources(ROOT / "sources.yaml") if s.enabled and s.type == "web" and s.reliability == "official"]
    if args.source_id:
        wanted = {x.strip() for x in args.source_id if x.strip()}
        sources = [s for s in sources if s.id in wanted]
    if args.limit:
        sources = sources[: args.limit]
    return sources


def _audit_source(source: Source, args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    health, items = fetch_web_discovery_source(source, user_agent=args.user_agent)
    first = items[0] if items else None
    article: ArticleResult | None = None
    if first and not args.discovery_only:
        asset_dir = ensure_dir(out_dir / "assets" / _slug(source.id))
        article = fetch_article(
            first.link,
            user_agent=args.user_agent,
            timeout=args.timeout,
            use_crawl4ai=not args.no_crawl4ai,
            auto_repair=not args.no_auto_repair,
            asset_dir=asset_dir,
            capture_screenshot=not args.no_screenshot,
        )
    verdict, note = _verdict(health, items, article)
    return {
        "source": {
            "id": source.id,
            "name": source.name,
            "tier": source.tier,
            "region": source.region,
            "url": source.url,
        },
        "health": _health_payload(health),
        "first_item": _item_payload(first),
        "article": _article_payload(article),
        "verdict": verdict,
        "note": note,
    }


def _row_for_exception(source: Source, message: str, verdict: str = "needs_adapter") -> dict[str, Any]:
    return {
        "source": {"id": source.id, "name": source.name, "tier": source.tier, "region": source.region, "url": source.url},
        "health": {},
        "first_item": {},
        "article": {},
        "verdict": verdict,
        "note": message,
    }


def _audit_source_isolated(source: Source, args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-source-id",
        source.id,
        "--out-dir",
        str(out_dir),
        "--timeout",
        str(args.timeout),
        "--user-agent",
        args.user_agent,
    ]
    if args.discovery_only:
        cmd.append("--discovery-only")
    if args.no_crawl4ai:
        cmd.append("--no-crawl4ai")
    if args.no_auto_repair:
        cmd.append("--no-auto-repair")
    if args.no_screenshot:
        cmd.append("--no-screenshot")
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.source_timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _row_for_exception(source, f"单源健康检查超过 {args.source_timeout} 秒，已隔离为需适配。", verdict="timeout")
    if proc.returncode != 0:
        tail = clean_text((proc.stderr or proc.stdout or "")[-800:])
        return _row_for_exception(source, f"单源健康检查失败：exit={proc.returncode}; {tail}")
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        tail = clean_text((proc.stdout or proc.stderr or "")[-800:])
        return _row_for_exception(source, f"单源结果解析失败：{type(exc).__name__}: {exc}; {tail}")


def _write_markdown(path: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    lines = [
        f"# 主流 AI 官网爬虫健康检查（{_today()}）",
        "",
        f"- crawl4ai：{'关闭' if args.no_crawl4ai else '开启'}",
        f"- 详情页 enrichment：{'关闭' if args.discovery_only else '开启'}",
        f"- 截图：{'关闭' if args.no_screenshot else '开启'}",
        f"- 汇总：{', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or '无'}",
        "",
        "| 源 | 发现 | 首条详情 | 正文 | 事实 | 图片 | 截图 | 结论 | 备注 |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        source = row["source"]
        health = row["health"]
        item = row["first_item"]
        article = row["article"]
        title = clean_text(item.get("title", "")) if item else ""
        link = item.get("link", "") if item else ""
        title_cell = f"[{title}]({link})" if title and link else "-"
        screenshot = "有" if article.get("screenshot_path") else "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{source['name']} `{source['id']}`",
                    f"{health.get('status')} / {health.get('item_count')}",
                    title_cell,
                    str(article.get("text_chars", "-") or "-"),
                    str(article.get("facts_count", "-") or "-"),
                    str(article.get("images_count", "-") or "-"),
                    screenshot,
                    row["verdict"],
                    clean_text(row["note"], 80),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 判定说明",
            "",
            "- ok：发现页和详情页都能产出具体信息。",
            "- discovery_ok：只验证了发现页，没跑详情页。",
            "- thin：详情页能抓到，但事实/正文/图片偏薄，需要增强。",
            "- needs_adapter：发现页或详情页不可用，需要通用修复或专属 adapter。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit official web crawlers for mainstream AI briefing sources.")
    parser.add_argument("--source-id", action="append", default=[], help="Only audit this source id; may be repeated.")
    parser.add_argument("--limit", type=int, default=0, help="Limit source count for smoke tests.")
    parser.add_argument("--out-dir", default=str(ROOT / "runs" / f"_crawler-health-{_today()}"))
    parser.add_argument("--timeout", type=int, default=int((load_config(ROOT / "sources.yaml").get("defaults", {}) or {}).get("timeout_seconds", 18)))
    parser.add_argument("--source-timeout", type=int, default=75, help="Hard timeout seconds per source in isolated mode.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--discovery-only", action="store_true", help="Only test official-page discovery, skip article enrichment.")
    parser.add_argument("--no-crawl4ai", action="store_true", help="Use requests/adapters only for article enrichment.")
    parser.add_argument("--no-auto-repair", action="store_true", help="Do not install/repair Crawl4AI or Playwright automatically.")
    parser.add_argument("--no-screenshot", action="store_true", help="Do not ask Crawl4AI to capture screenshots.")
    parser.add_argument("--no-isolate", action="store_true", help="Run all sources in the current process; useful for debugging only.")
    parser.add_argument("--child-source-id", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    out_dir = ensure_dir(Path(args.out_dir))
    if args.child_source_id:
        matches = [s for s in _source_rows(argparse.Namespace(source_id=[args.child_source_id], limit=0)) if s.id == args.child_source_id]
        if not matches:
            print(json.dumps({"error": f"source not found: {args.child_source_id}"}, ensure_ascii=False))
            return 2
        row = _audit_source(matches[0], args, out_dir)
        print(json.dumps(row, ensure_ascii=False))
        return 0

    rows: list[dict[str, Any]] = []
    sources = _source_rows(args)
    for idx, source in enumerate(sources, 1):
        print(f"[{idx}/{len(sources)}] {source.id} {source.url}", flush=True)
        try:
            row = _audit_source(source, args, out_dir) if args.no_isolate else _audit_source_isolated(source, args, out_dir)
        except Exception as exc:  # pragma: no cover - command hardening
            row = _row_for_exception(source, f"健康检查脚本异常：{type(exc).__name__}: {exc}")
        rows.append(row)

    json_path = out_dir / "official-web-crawler-health.json"
    md_path = out_dir / "official-web-crawler-health.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    _write_markdown(md_path, rows, args)
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
