from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..core import (
    EvidenceCard, canonical_url, card_has_x_post, card_is_community_signal,
    card_is_official_personnel_signal, clean_text, extract_hf_model_id,
    has_arxiv_evidence, model_family_key, parse_x_status, url_host,
)
from .claims import StorySpec, build_story_spec, is_renderable_cn
from .plan import build_editorial_plan
from .quality import mainstream_priority_rank, selection_quality_score, story_category

GENERIC_DISCOVERY_TITLES = frozenset({"news", "新闻", "blog", "博客", "research", "研究", "models", "model", "模型", "docs", "documentation", "文档", "pricing", "价格", "首页", "home", "updates", "动态"})
THIN_PHRASES = ("官网新闻页", "页面有更新", "页面出现更新", "信息不够", "信息不足", "了解更多", "release metadata", "未写变更", "未写明具体变更", "仅显示版本号", "只给出版本号")
BRIEF_NOISE_PHRASES = ("会议议程", "演讲嘉宾", "峰会报名", "课程", "招聘", "人才观", "信用卡", "股价", "解禁", "类人意识", "脑内小剧场", "全面反攻", "硝烟弥漫")
BROAD_PRODUCT_ANCHORS = frozenset({"chatgpt", "codex", "copilot", "claude", "gemini", "qwen", "deepseek", "kimi", "minimax", "mistral", "llama", "grok", "vllm", "ollama"})
DIGEST_TITLE_RE = re.compile(
    r"(?:^|[|｜/·\s])(?:ai|科技|大模型|人工智能)\s*(?:周报|早报|日报|晚报)(?:$|[|｜/·\s-])|\b(?:weekly|daily|morning)\s+(?:digest|roundup|brief(?:ing)?)\b|(?:^|[|｜/·\s])8\s*点\s*[1一]\s*氪",
    flags=re.I,
)
GITHUB_CHANGE_TERMS = ("新增", "修复", "改进", "变更", "移除", "弃用", "支持", "兼容", "breaking", "fix", "feat")
REPO_ARTIFACT_TERMS = ("license", "许可", "权重", "weights", "safetensors", "gguf", "mlx", "参数", "上下文", "下载", "api")
RESEARCH_METHOD_TERMS = ("提出", "方法", "架构", "算法", "数据集", "训练", "推理", "实验")
RESEARCH_RESULT_TERMS = ("结果", "提升", "降低", "超过", "优于", "准确率", "得分", "延迟", "吞吐", "成本", "%", "倍")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _missing_required_screenshot(card: EvidenceCard) -> bool:
    return any(_truthy(ev.get("screenshot_required")) and (str(ev.get("screenshot_status") or "") != "captured" or not ev.get("screenshot_path")) for ev in card.evidence_links)


def _is_unsplit_digest(title: str) -> bool:
    cleaned = clean_text(title)
    if not cleaned:
        return False
    if DIGEST_TITLE_RE.search(cleaned):
        return True
    if len(re.findall(r"[;；]", cleaned)) < 2:
        return False
    parts = [clean_text(p) for p in re.split(r"[;；]+", cleaned) if len(clean_text(p)) >= 4]
    subjects = {
        subj for seg in parts
        if (m := re.match(r"^(.{2,28}?)(?:发布|推出|上线|开放|更新|升级|宣布|回应|收购|起诉|融资|离职|任命)", seg))
        and (subj := re.sub(r"\W+", "", m.group(1).lower())) not in {"", "同时", "此外", "并", "还", "随后", "官方"}
    }
    return len(parts) >= 3 and len(subjects) >= 2


def _is_generic_title(title: str) -> bool:
    return re.sub(r"^(?:官网|官方网站|官方)\s*", "", clean_text(title).lower()).strip(" -_:：/｜|。.!！？?") in GENERIC_DISCOVERY_TITLES


def _is_github_release(card: EvidenceCard) -> bool:
    urls = " ".join(str(ev.get("url") or "").lower() for ev in card.evidence_links)
    sources = " ".join(str(ev.get("source") or "").lower() for ev in card.evidence_links)
    return "/releases/tag/" in urls or "github release" in sources


def _is_model_repo(card: EvidenceCard) -> bool:
    urls = " ".join(str(ev.get("url") or "").lower() for ev in card.evidence_links)
    sources = " ".join(str(ev.get("source") or "").lower() for ev in card.evidence_links)
    return "huggingface.co/" in urls or "modelscope.cn/" in urls or "hugging face" in sources or "模型仓库" in card.event_title


def is_publishable_card(card: EvidenceCard, *, strict_auto: bool = True) -> bool:
    if not card.selected or card.risk not in {"green", "yellow"} or _missing_required_screenshot(card):
        return False
    if _is_unsplit_digest(card.event_title) or _is_generic_title(card.event_title):
        return False
    blob = clean_text(" ".join([card.event_title, *card.key_facts])).lower()
    if any(p in blob for p in BRIEF_NOISE_PHRASES) or (any(p in blob for p in THIN_PHRASES) and len(card.key_facts) <= 1):
        return False
    spec = build_story_spec(card)
    public_claims = [c for c in spec.claims if c.renderable and c.verifiable and c.score >= 28]
    if strict_auto and not public_claims:
        return False
    if card_is_official_personnel_signal(card) and strict_auto:
        return card_has_x_post(card) and bool(public_claims)
    if card_is_community_signal(card) and strict_auto:
        return any(c.score >= 34 for c in public_claims)
    if _is_model_repo(card):
        return any(c.score >= 36 and any(t in c.text.lower() for t in REPO_ARTIFACT_TERMS) for c in public_claims)
    if has_arxiv_evidence(card):
        return any(any(m in c.text for m in RESEARCH_METHOD_TERMS) and any(r in c.text for r in RESEARCH_RESULT_TERMS) for c in public_claims)
    if _is_github_release(card):
        return any(any(t in c.text.lower() for t in GITHUB_CHANGE_TERMS) for c in public_claims)
    if strict_auto:
        plan = build_editorial_plan(card, spec=spec)
        return bool(plan.facts) and is_renderable_cn(plan.title)
    return bool(public_claims or card.key_facts)


def is_briefable_card(card: EvidenceCard, *, strict_auto: bool = True) -> bool:
    if not card.selected or card.risk not in {"green", "yellow"} or _missing_required_screenshot(card):
        return False
    if _is_unsplit_digest(card.event_title) or _is_generic_title(card.event_title):
        return False
    if not any(str(ev.get("url") or ev.get("final_url") or "").strip() for ev in card.evidence_links):
        return False
    if any(p in clean_text(" ".join([card.event_title, *card.key_facts])).lower() for p in BRIEF_NOISE_PHRASES):
        return False
    spec = build_story_spec(card)
    claims = [c for c in spec.claims if c.renderable and c.verifiable and c.score >= 20 and c.evidence_urls]
    if strict_auto and card.official_count <= 0:
        title_norms = {re.sub(r"\W+", "", clean_text(t).lower()) for t in [card.event_title, *(str(ev.get("title") or "") for ev in card.evidence_links)] if clean_text(t)}
        claims = [c for c in claims if re.sub(r"\W+", "", c.text.lower()) not in title_norms]
    return bool(claims)


def _recount_publishers(evidence: list[dict[str, str]]) -> tuple[int, int, int, int]:
    official, media, community = set(), set(), set()
    for row in evidence:
        raw_url = str(row.get("final_url") or row.get("url") or "").strip()
        x_st, host = parse_x_status(raw_url), url_host(raw_url)
        hf = extract_hf_model_id(raw_url) if host in {"github.com", "huggingface.co", "modelscope.cn"} else ""
        ident = f"x.com/{x_st[0]}" if x_st else (f"{host}/{hf.split('/', 1)[0].lower()}" if hf else (host if host and host != "news.google.com" else re.sub(r"\s+", "", clean_text(str(row.get("source") or "")).lower()) or raw_url.lower()))
        if not ident:
            continue
        rel, tier = str(row.get("reliability") or "").lower(), str(row.get("tier") or "").upper()
        (community if rel in {"official_personnel", "official_staff"} else official if (rel in {"official", "official_social"} or tier == "A") else media if (rel in {"media", "primary_media"} or tier == "B") else community).add(ident)
    return len(official | media | community), len(official), len(media), len(community)


def _card_merge_key(card: EvidenceCard, reconciled_urls: set[str]) -> str:
    for ev in card.evidence_links:
        if str(ev.get("reliability") or "").lower() in {"official", "official_social"} or str(ev.get("tier") or "").upper() == "A":
            if (canon := canonical_url(ev.get("final_url") or ev.get("url") or "")) in reconciled_urls:
                return f"official-url|{canon}"
    if _is_model_repo(card):
        for ev in card.evidence_links:
            if hf_id := extract_hf_model_id(str(ev.get("url") or "")):
                return f"hf|{model_family_key(hf_id)}"
        if hf_title := extract_hf_model_id(card.event_title):
            return f"hf|{model_family_key(hf_title)}"
    if card_is_official_personnel_signal(card):
        handles = {p[0] for ev in card.evidence_links if (p := parse_x_status(ev.get("url") or ev.get("final_url") or ""))}
        spec = build_story_spec(card)
        return f"personnel|{next(iter(handles))}|{spec.family_key}" if len(handles) == 1 and spec.family_key else ""
    fam = build_story_spec(card).family_key
    return f"story|{fam}" if fam and (fam.startswith("model|") or fam.rsplit("|", 1)[-1] not in BROAD_PRODUCT_ANCHORS) else ""


def merge_related_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    reconciled_urls = {canonical_url(ev.get("final_url") or ev.get("url") or "") for c in cards for ev in c.evidence_links if _truthy(ev.get("reconciled_official"))} - {""}
    groups: dict[str, list[EvidenceCard]] = {}
    for card in cards:
        groups.setdefault(_card_merge_key(card, reconciled_urls) or f"single|{card.cluster_key}", []).append(card)
    merged_list: list[EvidenceCard] = []
    for mkey, group in groups.items():
        if len(group) == 1:
            only = group[0]
            merged_list.append(only if only.source_cluster_keys else replace(only, source_cluster_keys=[only.cluster_key]))
            continue
        ordered = sorted(group, key=lambda c: (c.risk == "green", c.official_count > 0, c.official_count, c.score, c.confidence), reverse=True)
        lead = ordered[0]
        source_keys = list(dict.fromkeys(k for c in ordered for k in (c.source_cluster_keys or [c.cluster_key]) if k))
        seen_urls, merged_ev = set(), []
        for c in ordered:
            for ev in c.evidence_links:
                if (u := canonical_url(ev.get("url") or ev.get("final_url") or "") or str(ev.get("title") or "")) and u not in seen_urls:
                    seen_urls.add(u)
                    merged_ev.append(dict(ev))
        merged_ev = merged_ev[:8]
        src_cnt, off_cnt, med_cnt, com_cnt = _recount_publishers(merged_ev)
        latest_dates = [c.latest_published_at for c in ordered if c.latest_published_at]
        risk = "green" if any(c.risk == "green" for c in ordered) else ("yellow" if any(c.risk == "yellow" for c in ordered) else lead.risk)
        merged_list.append(replace(
            lead, cluster_key=f"merged:{mkey.split('|', 1)[-1]}", risk=risk, confidence=max(c.confidence for c in ordered),
            selected=any(c.selected for c in ordered), source_count=src_cnt or max(c.source_count for c in ordered),
            official_count=off_cnt, media_count=med_cnt, community_count=com_cnt, first_seen_at=min(c.first_seen_at for c in ordered),
            latest_published_at=max(latest_dates) if latest_dates else None,
            key_facts=list(dict.fromkeys(clean_text(f) for c in ordered for f in c.key_facts if clean_text(f)))[:10],
            evidence_links=merged_ev, uncertainty=list(dict.fromkeys(clean_text(u) for c in ordered for u in c.uncertainty if clean_text(u)))[:8],
            score=max(c.score for c in ordered), source_cluster_keys=source_keys,
        ))
    return merged_list


def select_card_portfolio(
    cards: list[EvidenceCard],
    *,
    max_items: int = 0,
    min_items: int = 4,
    strict_auto: bool = True,
    story_history: dict[str, int] | None = None,
    excluded_keys: set[str] | None = None,
) -> dict[str, Any]:
    excluded, history = excluded_keys or set(), story_history or {}
    eligible, excluded_count = [], 0
    for card in cards:
        if {card.cluster_key, *(card.source_cluster_keys or [])} & excluded:
            excluded_count += 1
            continue
        publishable = is_publishable_card(card, strict_auto=strict_auto)
        if publishable or is_briefable_card(card, strict_auto=strict_auto):
            eligible.append(replace(card, editorial_tier="headline" if publishable else "brief", source_cluster_keys=list(card.source_cluster_keys or [card.cluster_key])))

    merged = merge_related_cards(eligible)
    meta: list[dict[str, Any]] = []
    for card in merged:
        spec: StorySpec = build_story_spec(card)
        hist_count = max((int(history.get(str(k), 0)) for k in [card.cluster_key, *(card.source_cluster_keys or [])]), default=0)
        base = selection_quality_score(card, spec=spec) + mainstream_priority_rank(card) * 15.0 + (10.0 if card.editorial_tier == "headline" else 0.0) + (3.0 if card.risk == "green" else 0.0) - min(36.0, max(0, hist_count) * 12.0)
        meta.append({
            "card": card, "base": round(base, 3), "hist": hist_count,
            "source": str((card.evidence_links[0] if card.evidence_links else {}).get("source") or "unknown"),
            "entity": clean_text(card.entity or spec.entity), "category": story_category(card),
            "is_paper": has_arxiv_evidence(card), "is_maint": _is_github_release(card),
            "story_key": spec.family_key or spec.topic_key or card.cluster_key,
        })

    target_limit, entity_cap = (max_items if max_items > 0 else len(meta)), (2 if 0 < max_items <= 6 else 3)
    selected_rows, used_idx, used_keys = [], set(), set()
    source_counts, entity_counts, category_counts = {}, {}, {}
    paper_count = maint_count = 0
    while len(selected_rows) < target_limit:
        best_idx, best_rank = -1, None
        for idx, row in enumerate(meta):
            if idx in used_idx or row["story_key"] in used_keys or (row["is_paper"] and paper_count >= 1) or (strict_auto and row["is_maint"] and maint_count >= 1):
                continue
            if max_items > 0 and (source_counts.get(row["source"], 0) >= (1 if row["is_paper"] else 2) or (row["entity"] and entity_counts.get(row["entity"], 0) >= entity_cap)):
                continue
            marginal = float(row["base"]) + (5.0 if row["source"] not in source_counts else -5.0 * source_counts[row["source"]]) + (7.0 if row["entity"] not in entity_counts else -6.0 * entity_counts[row["entity"]]) + (5.0 if row["category"] not in category_counts else -2.5 * category_counts[row["category"]])
            c_obj: EvidenceCard = row["card"]
            rank = (round(marginal, 4), row["base"], c_obj.official_count, c_obj.confidence, c_obj.cluster_key)
            if best_rank is None or rank > best_rank:
                best_rank, best_idx = rank, idx
        if best_idx < 0:
            break
        chosen = meta[best_idx]
        used_idx.add(best_idx)
        used_keys.add(chosen["story_key"])
        selected_rows.append(chosen)
        source_counts[chosen["source"]] = source_counts.get(chosen["source"], 0) + 1
        if chosen["entity"]:
            entity_counts[chosen["entity"]] = entity_counts.get(chosen["entity"], 0) + 1
        category_counts[chosen["category"]] = category_counts.get(chosen["category"], 0) + 1
        paper_count += int(chosen["is_paper"])
        maint_count += int(chosen["is_maint"])

    lead_idx = next((i for i, r in enumerate(selected_rows) if r["card"].editorial_tier == "headline" and not card_is_official_personnel_signal(r["card"]) and not card_is_community_signal(r["card"])), -1)
    order = [lead_idx, *(i for i in range(len(selected_rows)) if i != lead_idx)] if lead_idx >= 0 else list(range(len(selected_rows)))
    selected_cards = [replace(selected_rows[idx]["card"], editorial_tier="headline" if (lead_idx >= 0 and pos == 0) else "brief") for pos, idx in enumerate(order)]
    represented = {k for c in selected_cards for k in [c.cluster_key, *(c.source_cluster_keys or [])]}
    rejected_cards = [c for c in cards if not ({c.cluster_key, *(c.source_cluster_keys or [])} & represented)]
    metrics = {
        "mode": "greedy_diversity", "input_count": len(cards), "excluded_count": excluded_count, "eligible_count": len(eligible),
        "merged_count": len(merged), "selected_count": len(selected_cards), "rejected_count": len(rejected_cards),
        "min_items": min_items, "max_items": max_items, "below_min_items": len(selected_cards) < min_items,
        "tier_counts": {"headline": sum(1 for c in selected_cards if c.editorial_tier == "headline"), "brief": sum(1 for c in selected_cards if c.editorial_tier == "brief")},
        "category_counts": category_counts, "entity_counts": entity_counts, "source_counts": source_counts,
        "history_penalties": {r["card"].cluster_key: r["hist"] for r in selected_rows if r["hist"] > 0},
    }
    return {"selected": selected_cards, "rejected": rejected_cards, "metrics": metrics}
