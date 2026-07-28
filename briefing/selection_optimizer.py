from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CandidateProfile(Generic[T]):
    item: T
    key: str
    source: str
    entity: str
    category: str
    topic: str
    kind: str
    base_score: float
    is_paper: bool = False
    is_maintenance: bool = False
    first_party: bool = False
    evidence_count: int = 0
    public_claims: int = 0
    audience: tuple[str, ...] = ()
    history_count: int = 0


@dataclass(slots=True)
class PortfolioResult(Generic[T]):
    items: list[T]
    score: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _State(Generic[T]):
    profiles: tuple[CandidateProfile[T], ...] = ()
    score: float = 0.0
    sources: dict[str, int] = field(default_factory=dict)
    entities: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    topics: dict[str, int] = field(default_factory=dict)
    audiences: dict[str, int] = field(default_factory=dict)
    papers: int = 0
    maintenance: int = 0


def _increment(values: dict[str, int], key: str) -> dict[str, int]:
    result = dict(values)
    if key:
        result[key] = result.get(key, 0) + 1
    return result


def _increment_many(values: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    result = dict(values)
    for key in keys:
        if key:
            result[key] = result.get(key, 0) + 1
    return result


def _marginal(profile: CandidateProfile[Any], state: _State[Any]) -> float:
    score = profile.base_score
    if profile.source and profile.source not in state.sources:
        score += 5.0
    else:
        score -= 4.0 * state.sources.get(profile.source, 0)
    if profile.entity and profile.entity not in state.entities:
        score += 7.0
    else:
        score -= 6.0 * state.entities.get(profile.entity, 0)
    if profile.category and profile.category not in state.categories:
        score += 5.0
    if profile.audience:
        score += min(6.0, 2.0 * sum(1 for value in profile.audience if value not in state.audiences))
    score += min(8.0, profile.public_claims * 1.5)
    score += min(5.0, profile.evidence_count * 1.25)
    if profile.first_party:
        score += 3.0
    # Repeated coverage remains eligible when the day is genuinely sparse, but
    # an equally useful fresh story should win. Repetition is therefore a soft
    # objective penalty rather than a hard filter.
    score -= min(36.0, max(0, profile.history_count) * 12.0)
    return score


def _allowed(
    profile: CandidateProfile[Any],
    state: _State[Any],
    *,
    max_items: int,
    category_limits: dict[str, int],
    strict_auto: bool,
) -> tuple[bool, str]:
    if any(existing.key == profile.key for existing in state.profiles):
        return False, "duplicate_story"
    if profile.is_paper and state.papers >= 1:
        return False, "paper_limit"
    if state.sources.get(profile.source, 0) >= (1 if profile.is_paper else 2):
        return False, "source_limit"
    entity_limit = 2 if max_items <= 6 else 3
    if profile.entity and state.entities.get(profile.entity, 0) >= entity_limit:
        return False, "entity_limit"
    if max_items <= 6 and profile.topic and state.topics.get(profile.topic, 0) >= 1:
        return False, "topic_overlap"
    limit = category_limits.get(profile.category, max_items)
    if limit > 0 and state.categories.get(profile.category, 0) >= limit:
        return False, "category_limit"
    if strict_auto and profile.is_maintenance and state.maintenance >= 1:
        return False, "maintenance_limit"
    return True, ""


def optimize_portfolio(
    profiles: list[CandidateProfile[T]],
    *,
    max_items: int,
    category_limits: dict[str, int] | None = None,
    strict_auto: bool = False,
    beam_width: int = 256,
) -> PortfolioResult[T]:
    if max_items <= 0:
        # Unbounded mode keeps every distinct story (no volume caps), but airing the
        # SAME story twice in one edition is always an accuracy bug — dedup by key,
        # keeping the highest-scored profile.
        ordered_unbounded = sorted(profiles, key=lambda profile: (profile.base_score, profile.evidence_count, profile.public_claims), reverse=True)
        seen_keys: set[str] = set()
        unique: list[CandidateProfile[T]] = []
        duplicates = 0
        for profile in ordered_unbounded:
            if profile.key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(profile.key)
            unique.append(profile)
        return PortfolioResult(
            items=[profile.item for profile in unique],
            score=sum(profile.base_score for profile in unique),
            diagnostics={"mode": "unbounded", "duplicate_stories_dropped": duplicates},
        )
    limits = category_limits or {}
    ordered = sorted(profiles, key=lambda profile: (profile.base_score, profile.evidence_count, profile.public_claims), reverse=True)
    states: list[_State[T]] = [_State()]
    rejection_counts: dict[str, int] = {}
    for profile in ordered:
        expanded = list(states)
        for state in states:
            if len(state.profiles) >= max_items:
                continue
            allowed, reason = _allowed(profile, state, max_items=max_items, category_limits=limits, strict_auto=strict_auto)
            if not allowed:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                continue
            expanded.append(
                _State(
                    profiles=(*state.profiles, profile),
                    score=state.score + _marginal(profile, state),
                    sources=_increment(state.sources, profile.source),
                    entities=_increment(state.entities, profile.entity),
                    categories=_increment(state.categories, profile.category),
                    topics=_increment(state.topics, profile.topic),
                    audiences=_increment_many(state.audiences, profile.audience),
                    papers=state.papers + int(profile.is_paper),
                    maintenance=state.maintenance + int(profile.is_maintenance),
                )
            )
        deduped: dict[tuple[str, ...], _State[T]] = {}
        for state in expanded:
            signature = tuple(sorted(candidate.key for candidate in state.profiles))
            current = deduped.get(signature)
            if current is None or (len(state.profiles), state.score) > (len(current.profiles), current.score):
                deduped[signature] = state
        states = sorted(deduped.values(), key=lambda state: (len(state.profiles), state.score), reverse=True)[: max(16, beam_width)]
    best = max(states, key=lambda state: (len(state.profiles), state.score))
    selected_profiles = sorted(best.profiles, key=lambda profile: profile.base_score, reverse=True)
    return PortfolioResult(
        items=[profile.item for profile in selected_profiles],
        score=round(best.score, 2),
        diagnostics={
            "mode": "portfolio_beam_search",
            "candidate_count": len(profiles),
            "selected_count": len(selected_profiles),
            "objective_score": round(best.score, 2),
            "beam_width": max(16, beam_width),
            "selected_keys": [profile.key for profile in selected_profiles],
            "selected_categories": best.categories,
            "selected_entities": best.entities,
            "selected_sources": best.sources,
            "audience_coverage": sorted(best.audiences),
            "history_penalty_items": {
                profile.key: profile.history_count
                for profile in selected_profiles
                if profile.history_count > 0
            },
            "selected_utility": {
                profile.key: round(profile.base_score - min(36.0, max(0, profile.history_count) * 12.0), 2)
                for profile in selected_profiles
            },
            "constraint_rejections_explored": rejection_counts,
        },
    )
