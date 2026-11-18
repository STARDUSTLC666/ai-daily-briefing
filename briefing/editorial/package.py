from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import (
    UTC, EvidenceCard, SourceHealth, card_display_entity, card_is_community_signal,
    card_is_official_personnel_signal, clean_text, fmt_dt, format_mmss, has_cjk,
    iso, looks_raw_english, now_utc, parse_date, read_json, url_host,
    write_json_atomic, write_text_atomic,
)
from .claims import build_story_spec
from .plan import EditionBrief, build_edition_brief, build_editorial_plan, build_storyboard
from .quality import content_quality_profile, public_source_status, selection_quality_score, story_category

INTRO_SECONDS, OUTRO_SECONDS, HEADLINE_SECONDS, BRIEF_SECONDS, BILI_DESC_LIMIT = 11, 5, 32, 20, 250
DEFAULT_TAGS = ("AI 日报", "AI", "人工智能", "大模型", "科技早报", "ChatGPT", "开源模型", "AIGC")


def _date_suffix(run_date: str) -> str:
    stamp = (run_date or "")[:10]
    resolved = stamp if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp) else now_utc().strftime("%Y-%m-%d")
    return f"【AI 日报 {resolved}】"


def _fit_title(base: str, suffix: str, max_len: int = 72) -> str:
    cleaned, room = clean_text(base or "AI 日报").strip(" ,，。:：|-｜"), max(12, max_len - len(suffix))
    return (cleaned[:room].rstrip(" ,，。:：|-｜") if len(cleaned) > room else cleaned) + suffix


def _public_entity(card: EvidenceCard) -> str:
    spec = build_story_spec(card)
    if spec.subject and spec.subject != "AI":
        return spec.subject.split("/", 1)[0].strip()
    return (card_display_entity(card, fallback="") or spec.entity or "AI").split("/", 1)[0].strip()


def _timeline_label(card: EvidenceCard) -> str:
    title = re.sub(r"^(?:一线消息|传闻/风向)\s*[｜|:：]\s*", "", build_editorial_plan(card).title)
    entity = _public_entity(card)
    ent_norm, ttl_norm = re.sub(r"\W+", "", entity.lower()), re.sub(r"\W+", "", title.lower())
    core = title if (not entity or entity == "AI" or (ent_norm and ttl_norm.startswith(ent_norm))) else f"{entity}｜{title}"
    if card_is_official_personnel_signal(card):
        return f"一线消息｜{core}"
    if card_is_community_signal(card):
        return f"传闻/风向｜{core}"
    return core


def _seg_seconds(seg: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if seg.get(key) is not None:
            try:
                val = float(seg[key])
                return val / 1000.0 if key.endswith("_ms") else val
            except (TypeError, ValueError):
                continue
    return 0.0


def _compute_chapters(selected_cards: list[EvidenceCard], script_payload: Any) -> tuple[list[dict[str, Any]], int]:
    raw_segs: list[dict[str, Any]] = []
    if isinstance(script_payload, list):
        raw_segs = [r for r in script_payload if isinstance(r, dict)]
    elif isinstance(script_payload, dict):
        raw_segs = next(([r for r in script_payload[k] if isinstance(r, dict)] for k in ("segments", "chapters", "items", "scenes") if isinstance(script_payload.get(k), list)), [])
    news_segs = [s for s in raw_segs if s.get("kind") == "news" or s.get("story_id") or (not s.get("kind") and str(s.get("title") or "")[:1].isdigit())]
    specs = [build_story_spec(c) for c in selected_cards]
    by_story = {str(s.get("story_id")): s for s in news_segs if s.get("story_id")}
    by_cluster = {str(s.get("cluster_key")): s for s in news_segs if s.get("cluster_key")}
    chapters, cursor, matched_any = [], float(INTRO_SECONDS), False
    for idx, (card, spec) in enumerate(zip(selected_cards, specs)):
        seg = by_story.get(spec.story_id) or by_cluster.get(card.cluster_key) or (news_segs[idx] if len(news_segs) >= len(selected_cards) else None)
        fallback_dur = float(BRIEF_SECONDS if card.editorial_tier == "brief" else HEADLINE_SECONDS)
        if seg is not None:
            matched_any = True
            start = _seg_seconds(seg, "start", "start_sec", "start_seconds", "start_ms") or cursor
            dur = _seg_seconds(seg, "duration", "duration_sec", "duration_seconds", "duration_ms") or fallback_dur
            cursor = start + max(1.0, dur)
        else:
            start, dur = cursor, fallback_dur
            cursor += dur
        start_int = max(0, int(round(start)))
        chapters.append({"start_seconds": start_int, "duration_seconds": max(1, int(round(dur))), "timestamp": format_mmss(start_int), "label": _timeline_label(card), "story_id": spec.story_id, "cluster_key": card.cluster_key})
    return chapters, int(round(cursor + (0 if matched_any and raw_segs else OUTRO_SECONDS)))


def _title_candidates(selected_cards: list[EvidenceCard], run_date: str, brief: EditionBrief) -> list[str]:
    suffix = _date_suffix(run_date)
    pool = [c for c in selected_cards if not card_is_official_personnel_signal(c) and not card_is_community_signal(c)] or selected_cards
    lead_title = build_editorial_plan(pool[0]).title if pool else brief.lead_title
    second_title = build_editorial_plan(pool[1]).title if len(pool) >= 2 else ""
    ent_str = "、".join(list(dict.fromkeys(e for c in selected_cards if (e := _public_entity(c)) and e != "AI"))[:2]) or "AI"
    bases: list[str] = []
    if lead_title and second_title and len(f"{lead_title}；{second_title}") <= max(12, 72 - len(suffix)):
        bases.append(f"{lead_title}；{second_title}")
    if lead_title and has_cjk(lead_title) and not looks_raw_english(lead_title, min_letters=24):
        bases.append(lead_title)
    bases.extend([f"AI 日报：{ent_str} 等 {len(selected_cards)} 条动态", f"{ent_str} 领衔：今日 AI 产品与技术动态", "AI 模型、工具与行业动态速览"])
    return list(dict.fromkeys(_fit_title(b, suffix) for b in bases))[:5]


def build_bilibili_payload(
    run_dir: Path, *, run_date: str, selected_cards: list[EvidenceCard],
    script_payload: dict[str, Any] | None = None, edition_brief: EditionBrief | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    brief = edition_brief or build_edition_brief(selected_cards, run_date=run_date)
    chapters, duration_seconds = _compute_chapters(selected_cards, script_payload if script_payload is not None else read_json(run_dir / "script.json", default=None))
    titles = _title_candidates(selected_cards, run_date, brief)
    entities = list(dict.fromkeys(e for c in selected_cards if (e := _public_entity(c)) and e != "AI"))
    summary = f"今天的 AI 日报：{'、'.join(entities[:4])} 等动态，一次看完。" if entities else "今天的 AI 日报：模型、工具、开源与产业动态，一次看完。"
    raw_desc = f"{summary}\n\n{brief.comment_question}\n完整时间轴见置顶评论。"
    desc = raw_desc[: BILI_DESC_LIMIT - 1].rstrip() if len(raw_desc) >= BILI_DESC_LIMIT else raw_desc
    timeline_lines = ["00:00 开场｜AI 日报", *(f"{ch['timestamp']} {ch['label']}" for ch in chapters)]
    bili_md = "\n".join(["# B站投稿草稿", "", "## 标题候选", *(f"- {t}" for t in titles), "", "## 简介", summary, "", "时间轴：", *timeline_lines, "", "本次播报完毕。", "", "## 标签", ",".join(DEFAULT_TAGS)]) + "\n"
    pinned_md = "\n".join(["今日时间轴：", *timeline_lines, "", brief.comment_question or "本次播报完毕。"]) + "\n"
    return {
        "title": titles[0] if titles else f"AI 日报{_date_suffix(run_date)}", "title_candidates": titles, "desc": desc, "summary": summary,
        "cover_hook": brief.hook, "comment_question": brief.comment_question, "tag": ",".join(DEFAULT_TAGS), "copyright": 1, "no_reprint": 1,
        "video": str(run_dir / "final.mp4"), "cover": str(run_dir / "cover.png"), "subtitle": str(run_dir / "subtitles.srt"),
        "pinned_comment": str(run_dir / "pinned-comment.md"), "tid": None, "duration_seconds": duration_seconds,
        "chapters": chapters, "bilibili_markdown": bili_md, "pinned_comment_markdown": pinned_md,
    }


def _card_from_manifest_entry(entry: dict[str, Any]) -> EvidenceCard:
    pub = parse_date(str(entry.get("latest_published_at") or ""))
    evidence = [dict(ev) for ev in (entry.get("evidence") or []) if isinstance(ev, dict)]
    plan_dict = entry.get("editorial_plan") or {}
    return EvidenceCard(
        cluster_key=str(entry.get("cluster_key") or ""), event_title=str(entry.get("event_title") or plan_dict.get("title") or ""),
        entity=str(entry.get("entity") or "AI"), risk=str(entry.get("risk") or "green"), confidence=int(entry.get("confidence") or 80),
        selected=True, reason="manifest_reloaded", source_count=int(entry.get("source_count") or max(1, len(evidence))),
        official_count=int(entry.get("official_count") or 0), media_count=int(entry.get("media_count") or 0),
        community_count=int(entry.get("community_count") or 0), first_seen_at=pub or now_utc(), latest_published_at=pub,
        key_facts=[str(f) for f in (entry.get("key_facts") or plan_dict.get("facts") or [])], evidence_links=evidence,
        uncertainty=[str(u) for u in (entry.get("uncertainty") or [])], score=float(entry.get("score") or 75.0),
        editorial_tier=str(entry.get("editorial_tier") or "brief"), source_cluster_keys=[str(k) for k in (entry.get("source_cluster_keys") or [])],
    )


def refresh_bilibili_outputs(run_dir: Path, script_payload: dict[str, Any] | None = None) -> dict[str, Path]:
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json", default={}) or {}
    run_date = str(manifest.get("run_date") or "")
    selected_cards = [_card_from_manifest_entry(r) for r in (manifest.get("selected") or []) if isinstance(r, dict)]
    b_raw = manifest.get("edition_brief")
    brief = (
        EditionBrief(
            run_date=str(b_raw.get("run_date") or run_date), hook=str(b_raw.get("hook") or ""), tagline=str(b_raw.get("tagline") or ""),
            lead_story_id=str(b_raw.get("lead_story_id") or ""), lead_title=str(b_raw.get("lead_title") or ""),
            story_count=int(b_raw.get("story_count") or len(selected_cards)), comment_question=str(b_raw.get("comment_question") or ""),
        ) if isinstance(b_raw, dict) and b_raw.get("hook") else build_edition_brief(selected_cards, run_date=run_date)
    )
    bili = build_bilibili_payload(run_dir, run_date=run_date, selected_cards=selected_cards, script_payload=script_payload, edition_brief=brief)
    return {
        "bilibili_json": write_json_atomic(run_dir / "bilibili.json", {k: v for k, v in bili.items() if k not in {"bilibili_markdown", "pinned_comment_markdown"}}),
        "bilibili_md": write_text_atomic(run_dir / "bilibili.md", str(bili["bilibili_markdown"])),
        "pinned_comment": write_text_atomic(run_dir / "pinned-comment.md", str(bili["pinned_comment_markdown"])),
    }


def _render_markdown_reports(health: list[SourceHealth], all_cards: list[EvidenceCard], selected_cards: list[EvidenceCard]) -> tuple[str, str, str]:
    stamp, chosen_keys = now_utc().strftime("%Y-%m-%d %H:%M UTC"), {k for c in selected_cards for k in [c.cluster_key, *(c.source_cluster_keys or [])]}
    src_lines = ["# 新闻源健康报告", "", f"生成时间：{stamp}", "", "| Tier | 来源 | 状态 | HTTP | 条目 | 最新时间 | 延迟 | 备注 |", "|---|---|---:|---:|---:|---|---:|---|"]
    for h in sorted(health, key=lambda x: (x.tier, x.source_name)):
        notes = [*([h.error.replace("|", "/")[:80]] if h.error else []), *(["stale"] if h.stale else []), *([f"no_date={h.no_date_count}"] if h.no_date_count else [])]
        src_lines.append(f"| {h.tier} | {h.source_name} | {h.status} | {h.status_code or ''} | {h.item_count} | {fmt_dt(h.latest_item_at)} | {h.latency_ms or ''}ms | {' '.join(notes)} |")

    fc_lines = ["# 事实核查报告", "", f"生成时间：{stamp}", "", f"今日候选：{len(all_cards)} 条；最终入选：{len(selected_cards)} 条。", ""]
    for card in all_cards:
        spec = build_story_spec(card)
        plan = build_editorial_plan(card, spec=spec)
        in_pkg = bool({card.cluster_key, *(card.source_cluster_keys or [])} & chosen_keys)
        fc_lines.extend([f"## {plan.title}", "", f"- 原始标题：{card.event_title}", f"- 实体：{card.entity} / 分类：{plan.category} / 来源状态：{public_source_status(card)}", f"- 决策：{'入选本期' if in_pkg else '未入选'}（{card.reason}） / 时间：{fmt_dt(card.latest_published_at)}"])
        if spec.disclaimer:
            fc_lines.append(f"- 信号说明：{spec.disclaimer}")
        fc_lines.extend([*(f"- 核验事实：{f}" for f in plan.facts), *(f"- 证据：[{ev.get('source')} / {ev.get('tier')}] [{str(ev.get('title') or 'source').replace('|', '/')}]({ev.get('url')})" for ev in card.evidence_links), ""])

    rejected = [c for c in all_cards if not ({c.cluster_key, *(c.source_cluster_keys or [])} & chosen_keys)]
    rej_lines = ["# 剔除与未入选候选", "", f"生成时间：{stamp}", "", *(["暂无剔除候选。"] if not rejected else [])]
    for idx, c in enumerate(rejected[:30], 1):
        rej_lines.extend([f"## {idx}. {c.event_title}", f"- 实体：{c.entity} / 分类：{story_category(c)} / 风险：{c.risk} / 原因：{c.reason}", ""])
    return "\n".join(src_lines) + "\n", "\n".join(fc_lines).rstrip() + "\n", "\n".join(rej_lines).rstrip() + "\n"


def _evaluate_package_quality(selected_cards: list[EvidenceCard], source_coverage: dict[str, Any], *, min_publish_items: int = 1) -> dict[str, Any]:
    headlines = [c for c in selected_cards if c.editorial_tier == "headline"]
    headline_safe = all(not card_is_community_signal(c) and not card_is_official_personnel_signal(c) for c in headlines)
    reliable = [c for c in selected_cards if not card_is_community_signal(c) and not card_is_official_personnel_signal(c)]
    entities = {c.entity.strip().lower() for c in selected_cards if c.entity.strip()}
    sources = {str(ev.get("source") or "").strip().lower() or url_host(ev.get("url") or "") for c in selected_cards for ev in c.evidence_links} - {""}
    issues = [
        *([f"selected {len(selected_cards)}, below minimum {min_publish_items}"] if len(selected_cards) < min_publish_items else []),
        *([f"expected at most one headline, got {len(headlines)}"] if len(headlines) > 1 else []),
        *(["headline is an X/community signal"] if not headline_safe else []),
        *(["at least one non-X/community verified story is required"] if not reliable else []),
    ]
    content_ok = not issues
    x_cov = (source_coverage or {}).get("x") or {}
    coverage_ok = not bool(x_cov.get("required")) or x_cov.get("status") == "healthy"
    publish_issues = [*issues, *([] if coverage_ok else [str(x_cov.get("reason") or "required source coverage is unavailable")])]
    return {
        "ok": content_ok, "publish_allowed": content_ok and coverage_ok, "edition_mode": "rolling_24h" if content_ok else "insufficient_content",
        "minimum_publish_items": min_publish_items, "selected_items": len(selected_cards), "headline_items": len(headlines),
        "headline_safe": headline_safe, "reliable_items": len(reliable), "distinct_entities": len(entities),
        "distinct_primary_sources": len(sources), "content_issues": issues, "publish_issues": publish_issues, "reason": "; ".join(publish_issues),
    }


def write_package(
    out_dir: Path, *, run_date: str, run_id: str, health: list[SourceHealth],
    all_cards: list[EvidenceCard], selected_cards: list[EvidenceCard],
    provenance: dict[str, Any], source_coverage: dict[str, Any],
    ticker_items: list[dict[str, Any]] | None = None, extra_manifest: dict[str, Any] | None = None,
) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    edition_brief = build_edition_brief(selected_cards, run_date=run_date)
    bili = build_bilibili_payload(out_dir, run_date=run_date, selected_cards=selected_cards, edition_brief=edition_brief)
    pkg_quality = _evaluate_package_quality(selected_cards, source_coverage, min_publish_items=int((extra_manifest or {}).get("minimum_publish_items") or 1))

    selected_entries, q_scores = [], []
    for card in selected_cards:
        spec = build_story_spec(card)
        plan = build_editorial_plan(card, spec=spec)
        board = build_storyboard(card, spec=spec, plan=plan)
        q_prof = content_quality_profile(card, spec=spec, plan=plan)
        q_scores.append(int(q_prof["score"]))
        selected_entries.append({
            "cluster_key": card.cluster_key, "source_cluster_keys": list(card.source_cluster_keys or [card.cluster_key]),
            "story_id": spec.story_id, "entity": _public_entity(card), "event_title": plan.title, "raw_event_title": card.event_title,
            "risk": card.risk, "confidence": card.confidence, "score": card.score, "editorial_tier": card.editorial_tier,
            "category": plan.category, "source_status": public_source_status(card), "signal_kind": spec.signal_kind,
            "disclaimer": spec.disclaimer, "selection_quality_score": selection_quality_score(card, spec=spec, plan=plan),
            "content_quality": q_prof, "source_count": card.source_count, "official_count": card.official_count,
            "media_count": card.media_count, "community_count": card.community_count, "latest_published_at": iso(card.latest_published_at),
            "key_facts": list(card.key_facts), "uncertainty": list(card.uncertainty),
            "story_spec": {"story_id": spec.story_id, "entity": spec.entity, "subject": spec.subject, "kind": spec.kind, "action": spec.action, "source_status": spec.source_status, "signal_kind": spec.signal_kind, "semantic_headline": spec.semantic_headline, "disclaimer": spec.disclaimer, "claims": [c.to_dict() for c in spec.claims]},
            "claims": [c.to_dict() for c in spec.claims],
            "editorial_plan": plan.to_dict(), "storyboard_template": board.template, "storyboard": board.to_cards(), "evidence": list(card.evidence_links),
        })

    src_md, fc_md, rej_md = _render_markdown_reports(health, all_cards, selected_cards)
    sources_path, fact_check_path, rejected_path = write_text_atomic(out_dir / "sources.md", src_md), write_text_atomic(out_dir / "fact-check.md", fc_md), write_text_atomic(out_dir / "rejected-candidates.md", rej_md)
    bili_md_path, pinned_path = write_text_atomic(out_dir / "bilibili.md", str(bili["bilibili_markdown"])), write_text_atomic(out_dir / "pinned-comment.md", str(bili["pinned_comment_markdown"]))
    bili_json_path = write_json_atomic(out_dir / "bilibili.json", {k: v for k, v in bili.items() if k not in {"bilibili_markdown", "pinned_comment_markdown"}})

    manifest_payload: dict[str, Any] = {
        "run_id": run_id, "run_date": run_date, "generated_at": datetime.now(UTC).isoformat(),
        "provenance": dict(provenance or {}), "source_coverage": dict(source_coverage or {}),
        "edition_brief": edition_brief.to_dict(), "ticker_items": list(ticker_items or []),
        "package_quality": pkg_quality, "publish_allowed": bool(pkg_quality["publish_allowed"]),
        "counts": {"sources": len(health), "healthy_sources": sum(1 for h in health if h.status in {"ok", "ok_web_pending_adapter"}), "candidate_cards": len(all_cards), "selected_cards": len(selected_cards)},
        "content_quality": {"average_score": round(sum(q_scores) / len(q_scores), 1) if q_scores else 0.0, "min_score": min(q_scores) if q_scores else 0},
        "title_candidates": bili["title_candidates"],
        "outputs": {
            "manifest": str(out_dir / "manifest.json"), "sources": str(sources_path), "fact_check": str(fact_check_path),
            "rejected_candidates": str(rejected_path), "bilibili_md": str(bili_md_path), "bilibili_json": str(bili_json_path),
            "pinned_comment": str(pinned_path), "final_video": str(out_dir / "final.mp4"), "cover": str(out_dir / "cover.png"),
            "subtitles": str(out_dir / "subtitles.srt"), "script": str(out_dir / "script.json"),
        },
        "selected": selected_entries,
    }
    if extra_manifest:
        manifest_payload.update(extra_manifest)
    manifest_path = write_json_atomic(out_dir / "manifest.json", manifest_payload)
    return {"manifest": manifest_path, "sources": sources_path, "fact_check": fact_check_path, "rejected_candidates": rejected_path, "bilibili_md": bili_md_path, "bilibili_json": bili_json_path, "pinned_comment": pinned_path}
