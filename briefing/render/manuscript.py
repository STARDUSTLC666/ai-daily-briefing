from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from briefing.core import (
    EvidenceCard,
    card_display_entity,
    card_is_community_signal,
    card_is_official_personnel_signal,
    clause_truncate,
    clean_text,
    is_x_url,
    looks_raw_english,
    normalize_cn_punctuation,
    read_json,
    url_host,
    write_json_atomic,
    write_text_atomic,
)
from briefing.editorial import (
    build_edition_brief,
    build_editorial_plan,
    build_story_spec,
    build_storyboard,
)
from briefing.verify import record_render_contract

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_TAGLINE = "每天几分钟，用一手证据讲清 AI 世界的新变化。"

TOP_TABS = ["开场", "模型更新", "开发者工具", "产品体验", "研究观察", "产业信号", "一线消息", "传闻/风向"]
TAB_COLORS: dict[str, str] = {
    "Intro": "4E7C68",
    "开场": "4E7C68",
    "模型更新": "C75B3C",
    "开发者工具": "2F7B7B",
    "产品体验": "6F5E9A",
    "研究观察": "D08A24",
    "产业信号": "3D668C",
    "一线消息": "9A4C55",
    "传闻/风向": "9A4C55",
    "速览": "2F7B7B",
    "Outro": "4E7C68",
    "收尾": "4E7C68",
}

_BEAT_ICONS = {
    "metric": "▥",
    "price": "¥",
    "availability": "↗",
    "change": "+",
    "method": "◇",
    "fact": "▣",
    "impact": "◎",
    "caution": "!",
}


from dataclasses import asdict, is_dataclass


def _coerce_brief_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _weekday_phrase(run_date: str) -> str:
    try:
        dt = datetime.strptime((run_date or "")[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"今天是{dt.month}月{dt.day}日周{'一二三四五六日'[dt.weekday()]}"


def _public_entity(card: EvidenceCard, spec_entity: str) -> str:
    spec_clean = clean_text(spec_entity)
    disp = clean_text(card_display_entity(card))
    if spec_clean and spec_clean != clean_text(card.entity):
        return spec_clean
    return disp or spec_clean or "AI"


def _unique_story_labels(cards: list[EvidenceCard], specs: list[Any]) -> list[str]:
    labels: list[str] = []
    for idx, (card, spec) in enumerate(zip(cards, specs), 1):
        entity = _public_entity(card, getattr(spec, "entity", "")).replace(" / ", "/")
        brand = entity.split("/", 1)[0].strip() or "AI"
        candidate = f"{idx:02d} {brand}"
        suffix = 2
        while candidate in labels:
            candidate = f"{idx:02d} {brand} {suffix}"
            suffix += 1
        labels.append(candidate)
    return labels


def _classify_tab(card: EvidenceCard, spec: Any) -> str:
    if card_is_official_personnel_signal(card):
        return "一线消息"
    if card_is_community_signal(card):
        return "传闻/风向"
    kind = str(getattr(spec, "kind", "") or "")
    blob = f"{getattr(spec, 'entity', '')} {card.event_title} {' '.join(card.key_facts)}".lower()
    if kind == "industry" or any(k in blob for k in ("融资", "投资", "监管", "离职", "任命", "重组", "合作")):
        return "产业信号"
    if kind in {"model_release", "model_update"} or any(k in blob for k in ("hugging face", "模型", "model")):
        return "模型更新"
    if kind == "developer_release" or any(k in blob for k in ("github", "api", "sdk", "codex", "vllm", "agent", "cli")):
        return "开发者工具"
    if kind == "product_update" or any(k in blob for k in ("chatgpt", "claude", "gemini", "产品", "客户端", "应用")):
        return "产品体验"
    if kind in {"research", "benchmark"} or any(k in blob for k in ("arxiv", "paper", "benchmark", "评测", "论文", "研究")):
        return "研究观察"
    return "模型更新"


def _signal_disclaimer(card: EvidenceCard) -> tuple[str, str]:
    if card_is_official_personnel_signal(card):
        return "official_personnel", "一线消息，未获官方公告确认。"
    if card_is_community_signal(card):
        return "community_rumour", "传闻/风向，未获官方公告确认。"
    return "", ""


def _evidence_rank(ev: dict[str, Any]) -> tuple[int, int]:
    rel = clean_text(str(ev.get("reliability") or "")).lower()
    tier = clean_text(str(ev.get("tier") or "")).upper()
    url = str(ev.get("url") or ev.get("final_url") or "")
    x_post = is_x_url(url)
    if x_post and rel == "official_social":
        prio = 0
    elif x_post and rel in {"official_personnel", "official_staff"}:
        prio = 1
    elif rel in {"official", "official_social"} or tier == "A":
        prio = 2
    elif x_post:
        prio = 3
    elif rel == "media" or tier == "B":
        prio = 4
    else:
        prio = 5
    captured = str(ev.get("screenshot_status") or "") == "captured" and bool(ev.get("screenshot_path"))
    return prio, (0 if captured else 1)


def _public_source_name(ev: dict[str, Any] | None) -> str:
    if not ev:
        return "资料来源"
    url = str(ev.get("url") or ev.get("final_url") or "")
    if is_x_url(url):
        parts = [p for p in re.split(r"/", re.sub(r"^https?://[^/]+", "", url)) if p]
        if parts:
            return f"X / @{parts[0].split('?')[0]}"
    src = clean_text(str(ev.get("source") or ev.get("source_name") or ""))
    src = re.sub(r"(?i)\b(?:rsshub|rss|atom|feed)\b", "", src)
    src = re.sub(r"(?:订阅源|聚合源)", "", src).strip(" /|-：:")
    if src and not src.lower().startswith("codex verified"):
        return src
    host = url_host(url)
    known = {
        "openai.com": "OpenAI",
        "anthropic.com": "Anthropic",
        "deepmind.google": "Google DeepMind",
        "ai.google": "Google AI",
        "github.com": "GitHub",
        "huggingface.co": "Hugging Face",
        "nvidia.com": "NVIDIA",
        "deepseek.com": "DeepSeek",
        "qwenlm.ai": "Qwen",
    }
    return next((label for dom, label in known.items() if host == dom or host.endswith(f".{dom}")), host or "资料来源")


def _source_status_label(card: EvidenceCard) -> str:
    if card_is_official_personnel_signal(card):
        return "一线线索"
    if card_is_community_signal(card):
        return "社区风向"
    if card.official_count > 0:
        return "官方来源"
    if card.source_count > 1:
        return "多源交叉"
    return "公开报道"


def _source_timeline_card(card: EvidenceCard, primary_source: str) -> dict[str, str]:
    pieces = [primary_source]
    if card.latest_published_at is not None:
        local = card.latest_published_at.astimezone(CN_TZ)
        pieces.append(f"{local.month}月{local.day}日 {local:%H:%M} 发布")
    elif card.first_seen_at is not None:
        local = card.first_seen_at.astimezone(CN_TZ)
        pieces.append(f"{local.month}月{local.day}日 {local:%H:%M} 捕获")
    if card.source_count > 1:
        pieces.append(f"{card.source_count} 个来源交叉")
    else:
        pieces.append(_source_status_label(card))
    return {
        "icon": "◇",
        "title": "原文与时间",
        "body": "｜".join(p for p in pieces if clean_text(p)),
    }


def _collect_evidence_visuals(card: EvidenceCard, story_title: str) -> list[dict[str, Any]]:
    ordered = sorted(list(card.evidence_links or []), key=_evidence_rank)
    visuals: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for ev in ordered:
        src_label = clause_truncate(_public_source_name(ev), 48)
        url = str(ev.get("url") or ev.get("final_url") or "")
        shot_path = str(ev.get("screenshot_path") or "")
        status = str(ev.get("screenshot_status") or "")
        required = str(ev.get("screenshot_required") or "").strip().lower() in {"1", "true", "yes"}
        if status == "captured" and shot_path and Path(shot_path).is_file():
            norm = str(Path(shot_path).resolve()).lower()
            if norm not in seen_paths:
                seen_paths.add(norm)
                visuals.append(
                    {
                        "source": src_label,
                        "title": story_title,
                        "url": url,
                        "image": shot_path,
                        "status": "captured",
                        "required": required,
                        "kind": str(ev.get("screenshot_kind") or "web_page"),
                    }
                )
            if not required and visuals:
                continue
        raw_images = str(ev.get("article_images") or "")
        if raw_images and not visuals:
            try:
                imgs = json.loads(raw_images)
            except Exception:
                imgs = []
            if isinstance(imgs, list):
                for img in imgs:
                    if isinstance(img, dict) and img.get("path") and Path(str(img["path"])).is_file():
                        norm = str(Path(str(img["path"])).resolve()).lower()
                        if norm not in seen_paths:
                            seen_paths.add(norm)
                            visuals.append(
                                {
                                    "source": src_label,
                                    "title": story_title,
                                    "url": str(img.get("url") or url),
                                    "image": str(img["path"]),
                                    "status": "image",
                                    "required": required,
                                    "kind": "article_image",
                                }
                            )
                            break
    return visuals


def _evidence_rows(card: EvidenceCard) -> list[dict[str, str]]:
    return [
        {
            "source": str(ev.get("source") or _public_source_name(ev)),
            "url": str(ev.get("url") or ev.get("final_url") or ""),
            "screenshot_required": str(ev.get("screenshot_required") or ""),
            "screenshot_status": str(ev.get("screenshot_status") or ""),
            "screenshot_path": str(ev.get("screenshot_path") or ""),
            "screenshot_kind": str(ev.get("screenshot_kind") or ""),
        }
        for ev in (card.evidence_links or [])[:5]
    ]


def _resolve_tagline(edition_brief: dict[str, Any]) -> str:
    for key in ("tagline", "hook", "audience_promise"):
        val = clean_text(str(edition_brief.get(key) or ""))
        if val and val != "AI 日报" and not looks_raw_english(val):
            return val
    return DEFAULT_TAGLINE


def _normalize_ticker(ticker_items: list[Any]) -> tuple[str, list[dict[str, str]]]:
    spoken_parts: list[str] = []
    card_rows: list[dict[str, str]] = []
    for item in ticker_items:
        if isinstance(item, EvidenceCard):
            entity = clause_truncate(card_display_entity(item) or item.entity or "AI", 18)
            fact = clean_text(item.key_facts[0] if item.key_facts else item.event_title)
        elif isinstance(item, dict):
            entity = clause_truncate(clean_text(str(item.get("entity") or item.get("title") or "AI")), 18)
            fact = clean_text(str(item.get("body") or item.get("text") or item.get("summary") or ""))
        else:
            continue
        if not fact:
            continue
        short_spoken = clause_truncate(fact, 34).rstrip("。；;，, ")
        spoken_parts.append(f"{entity}，{short_spoken}。")
        card_rows.append({"icon": "•", "title": entity, "body": clause_truncate(fact, 54)})
    return "".join(spoken_parts), card_rows


def build_segments(
    cards: list[EvidenceCard],
    *,
    run_date: str = "",
    ticker_items: list[dict[str, Any]] | None = None,
    edition_brief: dict[str, Any] | Any | None = None,
) -> list[dict[str, Any]]:
    """Build intro, news, optional ticker, and outro segments strictly from StorySpec + EditorialPlan + Storyboard."""
    selected = list(cards or [])
    raw_brief = build_edition_brief(selected, run_date=run_date) if edition_brief is None else edition_brief
    brief = _coerce_brief_dict(raw_brief)
    tagline = _resolve_tagline(brief)

    date_phrase = _weekday_phrase(run_date)
    date_clause = f"{date_phrase}，" if date_phrase else ""

    if not selected:
        return [
            {
                "kind": "intro",
                "title": "AI 日报",
                "caption": tagline,
                "hook": tagline,
                "text": f"早上好，{date_clause}这里是 AI 日报。今天的 AI 动态正在核对整理中，稍后用一手证据讲清重点。",
                "active_tab": "Intro",
                "bottom_tabs": ["开场", "收尾"],
                "bottom_active": "开场",
                "cards": [
                    {"icon": "◇", "title": "今日看点", "body": "模型、开发者工具、产品与产业一手变化。"},
                    {"icon": "✓", "title": "核验标准", "body": "优先采用官方公告与可复核页面，单源线索明确标注。"},
                ],
                "visual_pages": [
                    {
                        "kind": "overview",
                        "title": "资讯概览",
                        "lead": tagline,
                        "cards": [
                            {"icon": "◇", "title": "今日看点", "body": "模型、开发者工具、产品与产业一手变化。"},
                            {"icon": "✓", "title": "核验标准", "body": "优先采用官方公告与可复核页面，单源线索明确标注。"},
                        ],
                    }
                ],
            },
            {
                "kind": "outro",
                "title": "播送完毕",
                "caption": "我们明天见",
                "text": "今天的简报播完了，我们明天早上见。",
                "active_tab": "Outro",
                "bottom_tabs": ["开场", "收尾"],
                "bottom_active": "收尾",
                "cards": [],
                "visual_pages": [{"kind": "closing_continuation", "title": "播送完毕", "cards": []}],
            },
        ]

    specs = [build_story_spec(card) for card in selected]
    plans = [build_editorial_plan(card, spec=spec) for card, spec in zip(selected, specs)]
    boards = [
        build_storyboard(card, spec=spec, plan=plan)
        for card, spec, plan in zip(selected, specs, plans)
    ]
    labels = _unique_story_labels(selected, specs)
    has_ticker = False
    ticker_narration, ticker_cards = _normalize_ticker(list(ticker_items or []))
    if ticker_narration and ticker_cards:
        has_ticker = True

    bottom_tabs = ["开场", *labels, *(["速览"] if has_ticker else []), "收尾"]

    intro_cards = [
        {
            "icon": f"{idx:02d}",
            "title": clause_truncate(_public_entity(card, spec.entity), 22),
            "body": clean_text(plan.title or card.event_title),
        }
        for idx, (card, spec, plan) in enumerate(zip(selected[:6], specs[:6], plans[:6]), 1)
    ]
    verified_indices = [
        i
        for i, card in enumerate(selected)
        if not card_is_official_personnel_signal(card) and not card_is_community_signal(card)
    ]
    lead_idx = verified_indices[0] if verified_indices else None
    intro_style = os.environ.get("BRIEFING_INTRO_STYLE", "hook").strip().lower() or "hook"
    if lead_idx is not None and intro_style != "classic":
        hook_line = clause_truncate(clean_text(plans[lead_idx].title or selected[lead_idx].event_title), 44)
        intro_text = f"{hook_line}——早上好，{date_clause}这里是 AI 日报，今天 {len(selected)} 条，马上开始。"
    else:
        intro_text = f"早上好，{date_clause}这里是 AI 日报。过去 24 小时的 AI 新闻，今天 {len(selected)} 条，条条有出处，我们开始。"

    intro_subjects = [
        clause_truncate(_public_entity(selected[i], specs[i].entity), 20)
        for i in (verified_indices[:3] or list(range(min(3, len(selected)))))
    ]
    segments: list[dict[str, Any]] = [
        {
            "kind": "intro",
            "title": "AI 日报",
            "caption": tagline,
            "hook": str(brief.get("hook") or tagline),
            "text": intro_text,
            "active_tab": "Intro",
            "bottom_tabs": bottom_tabs,
            "bottom_active": "开场",
            "cards": intro_cards,
            "visual_pages": [
                {
                    "kind": "overview",
                    "title": "资讯概览",
                    "lead": " · ".join(s for s in intro_subjects if s) or tagline,
                    "cards": intro_cards,
                }
            ],
        }
    ]

    for idx, (card, spec, plan, board, label) in enumerate(
        zip(selected, specs, plans, boards, labels), 1
    ):
        tab = _classify_tab(card, spec)
        raw_sig = str(getattr(plan, "signal_kind", "") or "")
        if raw_sig in {"official_personnel", "community_rumour"}:
            signal_kind = raw_sig
            disclaimer = clean_text(str(getattr(plan, "disclaimer", "") or _signal_disclaimer(card)[1]))
        else:
            signal_kind, disclaimer = _signal_disclaimer(card)

        title = clean_text(plan.title or card.event_title or f"{_public_entity(card, spec.entity)} 新动态")

        preferred_ev = min(card.evidence_links, key=_evidence_rank) if card.evidence_links else None
        source_name = _public_source_name(preferred_ev)
        source_url = str((preferred_ev or {}).get("url") or (preferred_ev or {}).get("final_url") or "")

        beat_cards: list[dict[str, str]] = []
        for beat in board.beats:
            beat_body = clean_text(getattr(beat, "body", ""))
            beat_kind = str(getattr(beat, "kind", None) or getattr(beat, "intent", "fact"))
            if not beat_body or (disclaimer and beat_kind == "caution"):
                continue
            beat_cards.append(
                {
                    "icon": _BEAT_ICONS.get(beat_kind, "▣"),
                    "title": clean_text(getattr(beat, "title", "要点")),
                    "body": beat_body,
                }
            )
            if len(beat_cards) >= 5:
                break

        if disclaimer:
            beat_cards = [{"icon": "!", "title": "来源说明", "body": disclaimer}, *beat_cards]
        provenance_card = _source_timeline_card(card, source_name)
        if not any(c.get("title") == provenance_card["title"] for c in beat_cards):
            beat_cards = [*beat_cards[:5], provenance_card]

        accent = TAB_COLORS.get(tab, "C75B3C")
        evidence_visuals = _collect_evidence_visuals(card, title)
        primary_visual = evidence_visuals[0] if evidence_visuals else None
        lead_line = clean_text(plan.lead or (plan.facts[0] if plan.facts else title))

        visual_pages: list[dict[str, Any]] = [
            {
                "kind": "brief" if (card.editorial_tier or plan.editorial_tier) == "brief" else "cards",
                "title": title,
                "lead": lead_line,
                "source": source_name,
                "source_url": source_url,
                "evidenceVisual": primary_visual,
                "cards": beat_cards,
                "card_count": len(beat_cards),
                "theme": board.template,
                "news_type": spec.kind,
                "accent": accent,
            }
        ]
        for ev_vis in evidence_visuals:
            ev_page_title = "报道摘要" if ev_vis.get("kind") == "source_excerpt_card" else "原文画面"
            visual_pages.append(
                {
                    "kind": "evidence",
                    "title": ev_page_title,
                    "lead": lead_line,
                    "source": ev_vis.get("source") or source_name,
                    "source_url": ev_vis.get("url") or source_url,
                    "evidenceVisual": ev_vis,
                    "background_cards": beat_cards,
                    "cards": [
                        {"title": "消息来源", "body": ev_vis.get("source") or source_name},
                        {"title": "已知事实", "body": clean_text(plan.facts[0]) if plan.facts else lead_line},
                        *(
                            [{"title": "来源说明", "body": disclaimer}]
                            if disclaimer
                            else [{"title": "核验口径", "body": _source_status_label(card)}]
                        ),
                    ],
                }
            )

        spoken = clean_text(plan.narration())
        if disclaimer and disclaimer.rstrip("。") not in spoken:
            spoken = f"{disclaimer.rstrip('。')}。{spoken}"

        segments.append(
            {
                "kind": "news",
                "position": idx,
                "total": len(selected),
                "title": title,
                "headline": title,
                "caption": clause_truncate(title, 44) or title,
                "body": lead_line,
                "text": spoken,
                "story_id": str(spec.story_id),
                "claim_ids": [clean_text(str(cid)) for cid in list(plan.claim_ids or []) if clean_text(str(cid))],
                "generation_path": "structured_editorial_plan",
                "editorial_tier": str(card.editorial_tier or plan.editorial_tier or "headline"),
                "signal_kind": signal_kind,
                "disclaimer": disclaimer,
                "evidence": _evidence_rows(card),
                "active_tab": tab,
                "bottom_tabs": bottom_tabs,
                "bottom_active": label,
                "cards": beat_cards,
                "visual_pages": visual_pages,
            }
        )

    if has_ticker:
        ticker_pages = [
            {
                "kind": "ticker",
                "title": "快讯速览",
                "lead": "今天还有这些官方动态",
                "cards": ticker_cards[offset : offset + 10],
            }
            for offset in range(0, len(ticker_cards), 10)
        ]
        segments.append(
            {
                "kind": "ticker",
                "title": "快讯速览",
                "caption": "快讯速览",
                "text": f"最后是快讯速览。{ticker_narration}",
                "active_tab": "速览",
                "bottom_tabs": bottom_tabs,
                "bottom_active": "速览",
                "cards": ticker_cards[:6],
                "visual_pages": ticker_pages,
            }
        )

    segments.append(
        {
            "kind": "outro",
            "title": "播送完毕",
            "caption": "我们明天见",
            "text": "今天的新闻播完了。置顶评论有个问题等你聊，觉得有用就点个关注，我们明天早上见。",
            "active_tab": "Outro",
            "bottom_tabs": bottom_tabs,
            "bottom_active": "收尾",
            "cards": [],
            "visual_pages": [{"kind": "closing_continuation", "title": "播送完毕", "cards": []}],
        }
    )
    return segments


def _clean_cards(cards: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in cards or []:
        if not isinstance(item, dict):
            continue
        title = clean_text(str(item.get("title") or ""))
        body = clean_text(str(item.get("body") or ""))
        if title or body:
            row: dict[str, str] = {"title": title, "body": body}
            if clean_text(str(item.get("icon") or "")):
                row["icon"] = clean_text(str(item["icon"]))
            out.append(row)
    return out


def write_manuscript(run_dir: Path, segments: list[dict[str, Any]]) -> dict[str, Path]:
    """Write news-script.json and news-script.md into run_dir."""
    root = Path(run_dir).resolve()
    json_path = root / "news-script.json"
    md_path = root / "news-script.md"
    normalized_segments: list[dict[str, Any]] = []
    md_lines = ["# AI 日报新闻稿", "", "这份稿件是视频、配音和字幕的唯一共同来源。", ""]

    for idx, seg in enumerate(segments):
        kind = clean_text(str(seg.get("kind") or "news"))
        title = clean_text(str(seg.get("title") or ""))
        caption = clean_text(str(seg.get("caption") or title))
        text = clean_text(str(seg.get("text") or ""))
        cards = _clean_cards(list(seg.get("cards") or []))
        row: dict[str, Any] = {
            "index": idx,
            "kind": kind,
            "position": seg.get("position"),
            "total": seg.get("total"),
            "story_id": clean_text(str(seg.get("story_id") or "")),
            "claim_ids": [clean_text(str(c)) for c in list(seg.get("claim_ids") or []) if clean_text(str(c))],
            "generation_path": clean_text(str(seg.get("generation_path") or "structured_editorial_plan")),
            "editorial_tier": clean_text(str(seg.get("editorial_tier") or "headline")),
            "title": title,
            "headline": clean_text(str(seg.get("headline") or title)),
            "caption": caption,
            "text": text,
            "active_tab": clean_text(str(seg.get("active_tab") or "")),
            "bottom_tabs": [clean_text(str(t)) for t in list(seg.get("bottom_tabs") or [])],
            "bottom_active": clean_text(str(seg.get("bottom_active") or "")),
            "cards": cards,
            "visual_pages": list(seg.get("visual_pages") or []),
        }
        for optional_key in ("signal_kind", "disclaimer"):
            if clean_text(str(seg.get(optional_key) or "")):
                row[optional_key] = clean_text(str(seg[optional_key]))
        if isinstance(seg.get("evidence"), list) and seg["evidence"]:
            row["evidence"] = [e for e in seg["evidence"] if isinstance(e, dict)]
        normalized_segments.append(row)

        label = "开场" if kind == "intro" else "收尾" if kind == "outro" else "速览" if kind == "ticker" else f"{int(seg.get('position') or idx):02d}"
        md_lines.append(f"## {label} {title}")
        if caption and caption != title:
            md_lines.append(f"- 屏幕标题：{caption}")
        md_lines.append(f"- 解说：{text}")
        if cards:
            md_lines.append("- 画面卡片：")
            for c in cards[:6]:
                md_lines.append(f"  - {c.get('title') or '要点'}：{c.get('body') or ''}")
        md_lines.append("")

    write_json_atomic(json_path, {"version": 1, "segments": normalized_segments})
    write_text_atomic(md_path, "\n".join(md_lines).rstrip() + "\n")
    return {"json": json_path, "md": md_path}


def read_manuscript(path: Path) -> list[dict[str, Any]]:
    payload = read_json(Path(path))
    raw_segments = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(raw_segments, list):
        raise ValueError(f"manuscript has no segments list: {path}")
    return [seg for seg in raw_segments if isinstance(seg, dict)]


def select_manuscript(
    run_dir: Path,
    draft_segments: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select active manuscript (morning-final > final > draft) and sync news-script.{json,md}."""
    root = Path(run_dir).resolve()
    candidates = [
        (root / "review" / "morning-final-script.json", root / "review" / "morning-final-script.md", "morning-reviewed"),
        (root / "review" / "final-script.json", root / "review" / "final-script.md", "reviewed"),
    ]
    for candidate_json, candidate_md, mode in candidates:
        if candidate_json.is_file():
            segments = read_manuscript(candidate_json)
            synced = write_manuscript(root, segments)
            if candidate_md.is_file():
                write_text_atomic(synced["md"], candidate_md.read_text(encoding="utf-8-sig"))
            contract_path = record_render_contract(root, segments, manuscript_path=candidate_json)
            return segments, {
                "mode": mode,
                "source_path": str(candidate_json),
                "manuscript_json": str(candidate_json),
                "manuscript_md": str(candidate_md if candidate_md.is_file() else synced["md"]),
                "render_contract": str(contract_path),
            }

    if draft_segments is None and (root / "news-script.json").is_file():
        segments = read_manuscript(root / "news-script.json")
        paths = {"json": root / "news-script.json", "md": root / "news-script.md"}
        if not paths["md"].is_file():
            paths = write_manuscript(root, segments)
    else:
        paths = write_manuscript(root, list(draft_segments or []))
        segments = read_manuscript(paths["json"])

    contract_path = record_render_contract(root, segments, manuscript_path=paths["json"])
    return segments, {
        "mode": "draft",
        "source_path": str(paths["json"]),
        "manuscript_json": str(paths["json"]),
        "manuscript_md": str(paths["md"]),
        "render_contract": str(contract_path),
    }


def _slide_text(value: Any) -> str:
    raw = clean_text(str(value or "")).replace("\\N", " ")
    return normalize_cn_punctuation(" ".join(raw.split()))


def _timeline_entity(seg: dict[str, Any], fallback_label: str) -> str:
    explicit = clean_text(str(seg.get("timeline_entity") or ""))
    if explicit:
        return explicit
    haystack = " ".join(clean_text(str(seg.get(k) or "")) for k in ("title", "headline", "caption"))
    for entity, pattern in (
        ("Codex", r"\bCodex\b"),
        ("ChatGPT", r"\bChatGPT\b"),
        ("Claude", r"\bClaude\b"),
        ("Gemini", r"\bGemini\b"),
        ("Grok", r"\bGrok\b"),
        ("DeepSeek", r"\bDeepSeek\b|深度求索"),
        ("Qwen", r"\bQwen\b|通义千问"),
        ("Kimi", r"\bKimi\b|月之暗面"),
        ("Anthropic", r"\bAnthropic\b"),
        ("OpenAI", r"\bOpenAI\b"),
        ("Google", r"\bGoogle(?:\s+DeepMind)?\b"),
        ("xAI", r"\bxAI\b"),
        ("Meta", r"\bMeta\b"),
        ("NVIDIA", r"\bNVIDIA\b|英伟达"),
    ):
        if re.search(pattern, haystack, flags=re.IGNORECASE):
            return entity
    return re.sub(r"^\s*\d{1,2}\s*", "", fallback_label).strip(" /·|-") or fallback_label


def _build_timeline_items(segments: list[dict[str, Any]], durations: list[float]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = 0.0
    seen: dict[str, int] = {}
    for idx, (seg, raw_dur) in enumerate(zip(segments, durations)):
        dur = max(0.8, float(raw_dur or 0.8))
        kind = str(seg.get("kind") or "news")
        active_tab = str(seg.get("active_tab") or "Intro")
        if kind == "intro":
            label = "开场"
        elif kind == "outro":
            label = str(seg.get("bottom_active") or "收尾")
        else:
            label = str(seg.get("bottom_active") or seg.get("title") or f"{idx:02d}")
        if label in seen:
            seen[label] += 1
            label = f"{label} {seen[label]}"
        else:
            seen[label] = 1
        entity = "收尾" if kind == "outro" else _timeline_entity(seg, label)
        items.append(
            {
                "label": clause_truncate(_slide_text(label), 18),
                "entity": clause_truncate(_slide_text(entity), 14),
                "headline": _slide_text(seg.get("title") or label),
                "start": round(cursor, 3),
                "end": round(cursor + dur, 3),
                "duration": round(dur, 3),
                "kind": kind,
                "activeTab": active_tab,
            }
        )
        cursor += dur
    return items


def _select_segment_pages(seg: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
    if not pages:
        pages = [{"kind": "cards", "title": seg.get("title") or "", "cards": list(seg.get("cards") or [])}]
    required_pages = [
        p
        for p in pages
        if str(p.get("kind") or "") == "evidence"
        and isinstance(p.get("evidenceVisual"), dict)
        and bool(p["evidenceVisual"].get("required"))
    ]
    base = pages[:1] if duration < 10.5 else pages[:3]
    out = list(base)
    for req in required_pages:
        if req not in out:
            out.append(req)
    return out


def _page_windows(duration: float, count: int) -> list[tuple[float, float]]:
    n = max(1, count)
    if n == 1:
        return [(0.0, duration)]
    if n == 2:
        split = duration * 0.58
        return [(0.0, split), (split, duration)]
    step = duration / n
    return [(i * step, (i + 1) * step) for i in range(n)]


def _slide_kicker(seg: dict[str, Any], active_tab: str) -> str:
    if seg.get("kind") == "news":
        pos = int(seg.get("position") or 0)
        tot = int(seg.get("total") or 0)
        if pos and tot:
            return f"{pos:02d} / {tot:02d}  {active_tab}"
    if active_tab == "Intro":
        return "今日早报"
    if active_tab == "Outro":
        return "收尾"
    return active_tab


def build_remotion_slides(
    segments: list[dict[str, Any]],
    durations: list[float],
    *,
    width: int = 1920,
    height: int = 1080,
    run_label: str = "",
) -> list[dict[str, Any]]:
    """Map timed manuscript segments into untruncated BriefingSlide[] for Remotion's newsroom paginator."""
    slides: list[dict[str, Any]] = []
    timeline_items = _build_timeline_items(segments, durations)
    timeline_total = round(sum(max(0.8, float(d or 0.8)) for d in durations), 3)
    cursor = 0.0

    for seg_idx, (seg, raw_dur) in enumerate(zip(segments, durations)):
        dur = max(0.8, float(raw_dur or 0.8))
        active_tab = str(seg.get("active_tab") or "Intro")
        default_accent = TAB_COLORS.get(active_tab, "C75B3C")
        raw_title = str(seg.get("title") or "AI 日报")
        pages = _select_segment_pages(seg, dur)
        windows = _page_windows(dur, len(pages))

        for page_idx, (page, (win_start, win_end)) in enumerate(zip(pages, windows)):
            slide_dur = max(0.8, win_end - win_start)
            page_copy = dict(page)
            raw_accent = clean_text(str(page_copy.get("accent") or "")).lstrip("#").upper()
            accent = raw_accent if re.fullmatch(r"[0-9A-F]{6}", raw_accent) else default_accent
            page_copy["title"] = _slide_text(page_copy.get("title") or raw_title)
            if "lead" in page_copy:
                page_copy["lead"] = _slide_text(page_copy.get("lead"))
            cards_out: list[dict[str, str]] = []
            for card in list(page_copy.get("cards") or []):
                if isinstance(card, dict):
                    cards_out.append(
                        {
                            "icon": clean_text(str(card.get("icon") or ""))[:4],
                            "title": _slide_text(card.get("title") or "要点"),
                            "body": _slide_text(card.get("body") or ""),
                            "meta": _slide_text(card.get("meta") or ""),
                            "accent": clean_text(str(card.get("accent") or ""))[:8],
                        }
                    )
            page_copy["cards"] = cards_out
            slides.append(
                {
                    "index": len(slides),
                    "segmentIndex": seg_idx,
                    "pageIndex": page_idx,
                    "duration": round(slide_dur, 3),
                    "start": round(cursor + win_start, 3),
                    "end": round(cursor + win_end, 3),
                    "width": int(width),
                    "height": int(height),
                    "runLabel": run_label,
                    "kind": str(seg.get("kind") or "news"),
                    "storyPosition": int(seg.get("position") or 0),
                    "storyTotal": int(seg.get("total") or 0),
                    "activeTab": active_tab,
                    "accent": accent,
                    "title": _slide_text(raw_title),
                    "caption": _slide_text(seg.get("caption") or raw_title),
                    "kicker": _slide_kicker(seg, active_tab),
                    "bottomTabs": [str(t) for t in (seg.get("bottom_tabs") or ["开场", "收尾"])],
                    "bottomActive": str(seg.get("bottom_active") or active_tab),
                    "timelineItems": timeline_items,
                    "timelineTotal": timeline_total,
                    "page": page_copy,
                }
            )
        cursor += dur
    return slides
