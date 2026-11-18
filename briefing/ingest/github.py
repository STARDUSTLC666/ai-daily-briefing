from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

import requests

from ..core import EvidenceCard, FeedItem, clause_truncate, clean_text, has_cjk, url_host

GITHUB_API = "https://api.github.com"
DEFAULT_USER_AGENT = "DailyBilibiliBriefing/0.1 (+local)"

_MAINTENANCE_MARKERS: tuple[str, ...] = (
    "修复",
    "测试",
    "兼容",
    "校验",
    "补齐",
    "schema",
    "协议",
    "插件",
    "文档",
    "维护",
)


def github_release_parts(url: str) -> tuple[str, str, str] | None:
    if url_host(url) != "github.com":
        return None
    parts = [part for part in urlparse(str(url or "")).path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] != "releases" or parts[3] != "tag":
        return None
    owner, repo = parts[0], parts[1]
    tag = "/".join(parts[4:])
    if not owner or not repo or not tag:
        return None
    return owner, repo, tag


def github_compare_parts(url: str) -> tuple[str, str, str] | None:
    if url_host(url) != "github.com":
        return None
    parts = [part for part in urlparse(str(url or "")).path.strip("/").split("/") if part]
    if len(parts) < 4 or parts[2] != "compare":
        return None
    owner, repo = parts[0], parts[1]
    comparison = "/".join(parts[3:])
    if not owner or not repo or not comparison:
        return None
    return owner, repo, comparison


def is_sparse_release_summary(summary: str, title: str = "") -> bool:
    text = clean_text(summary)
    if not text:
        return True
    lowered = text.lower().strip()
    title_lowered = clean_text(title).lower().strip()
    if title_lowered and lowered in {title_lowered, f"release {title_lowered}", f"发布 {title_lowered}"}:
        return True
    if re.match(r"^release\s+\S+\s*·\s*[\w.-]+/[\w.-]+", lowered):
        return True
    return bool(re.fullmatch(r"release\s+v?[a-z0-9_.-]+(?:-[a-z0-9_.-]+)?", lowered))


def previous_numeric_tag(tag: str) -> str:
    match = re.search(r"(\d+)(?!.*\d)", tag)
    if not match:
        return ""
    value = int(match.group(1))
    if value <= 0:
        return ""
    return f"{tag[: match.start()]}{value - 1}{tag[match.end():]}"


def _api_url(owner: str, repo: str, suffix: str) -> str:
    return f"{GITHUB_API}/repos/{quote(owner)}/{quote(repo)}/{suffix}"


def _headers(user_agent: str | None = None) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
    }


def _get_json(url: str, headers: dict[str, str], timeout: int) -> Any:
    response = requests.get(url, headers=headers, timeout=max(3, int(timeout)))
    response.raise_for_status()
    return response.json()


def _release_body_note(body: str, tag: str) -> str:
    text = clean_text(body, 900)
    if not text or is_sparse_release_summary(text, tag):
        return ""
    lines: list[str] = []
    for raw_line in re.split(r"[。；;\n]+", text):
        cleaned = clean_text(raw_line)
        if not cleaned or cleaned.startswith("#"):
            continue
        lines.append(cleaned.strip("-* "))
        if len(lines) >= 2:
            break
    return "；".join(lines)


def _find_previous_tag(releases: Any, current_tag: str) -> str:
    if not isinstance(releases, list):
        return ""
    for idx, row in enumerate(releases):
        if not isinstance(row, dict) or str(row.get("tag_name") or "") != current_tag:
            continue
        for candidate in releases[idx + 1 :]:
            if isinstance(candidate, dict) and candidate.get("tag_name"):
                return str(candidate["tag_name"])
        return ""
    for row in releases:
        if isinstance(row, dict) and row.get("tag_name") and str(row["tag_name"]) != current_tag:
            return str(row["tag_name"])
    return ""


def _clean_commit_title(message: str) -> str:
    first_line = str(message or "").splitlines()[0] if message else ""
    title = clean_text(first_line)
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    title = re.sub(r"\s*\(#\d+\)\s*$", "", title)
    return title.strip()


def _summarize_commit_title(message: str) -> str:
    raw = _clean_commit_title(message)
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("release ") or re.fullmatch(r"v?\d+(?:\.\d+)+(?:[-._a-z0-9]+)?", lowered):
        return ""
    if has_cjk(raw):
        return clause_truncate(raw, 64)
    if "schema" in lowered or "protocol" in lowered:
        return "更新配置或协议 schema"
    if any(tok in lowered for tok in ("login", "auth", "oauth", "token")):
        return "调整登录与鉴权流程"
    if re.search(r"\btests?\b", lowered):
        return "补充自动化测试"
    if re.search(r"\b(?:docs?|readme)\b", lowered):
        return "更新项目文档"
    if lowered.startswith(("fix ", "fix:", "fix(", "fixed ", "bugfix", "hotfix")):
        return "修复一项代码问题"
    if lowered.startswith(("feat ", "feat:", "feat(", "add ", "adds ", "added ")):
        return "新增一项代码能力"
    if lowered.startswith(("perf ", "perf:", "perf(", "optim")):
        return "优化运行性能"
    if lowered.startswith(("refactor", "update ", "updates ", "updated ")):
        return "更新一项代码逻辑"
    if lowered.startswith(("remove ", "removes ", "removed ", "deprecat")):
        return "移除一项旧代码逻辑"
    return "代码维护调整"


def _commit_summaries(compare_data: Any, limit: int = 3) -> list[str]:
    commits = compare_data.get("commits") if isinstance(compare_data, dict) else []
    result: list[str] = []
    seen: set[str] = set()
    for row in commits or []:
        if not isinstance(row, dict):
            continue
        commit = row.get("commit")
        if not isinstance(commit, dict):
            continue
        summary = _summarize_commit_title(str(commit.get("message") or ""))
        if not summary or summary in seen:
            continue
        seen.add(summary)
        result.append(summary)
        if len(result) >= limit:
            break
    return result


def _maintenance_summaries(compare_data: Any, limit: int = 2) -> list[str]:
    result: list[str] = []
    for summary in _commit_summaries(compare_data, limit=8):
        lowered = summary.lower()
        if not summary.startswith("修复") and not any(marker in lowered for marker in _MAINTENANCE_MARKERS):
            continue
        if summary not in result:
            result.append(summary)
        if len(result) >= limit:
            break
    return result


def build_release_delta_summary(tag: str, previous_tag: str, release_body: str, compare_data: Any) -> str:
    body_note = _release_body_note(release_body, tag)
    bits: list[str] = []
    if previous_tag and isinstance(compare_data, dict):
        commit_count = int(compare_data.get("total_commits") or len(compare_data.get("commits") or []))
        file_count = len(compare_data.get("files") or [])
        if commit_count or file_count:
            bits.append(f"版本对比：较 {previous_tag}，GitHub compare 显示 {commit_count} 个提交、{file_count} 个文件变更")
        summaries = _commit_summaries(compare_data)
        if summaries:
            bits.append("变更摘要：" + "；".join(summaries))
        maintenance = _maintenance_summaries(compare_data)
        if maintenance:
            bits.append("修复/维护线索：" + "；".join(maintenance))
        elif summaries:
            bits.append("修复/维护线索：compare 摘要里没有明确 bugfix，先按普通更新处理")
        compare_url = str(compare_data.get("html_url") or "")
        if compare_url:
            bits.append(f"变更来源：{compare_url}")
    if body_note:
        bits.append("Release 说明：" + body_note)
    if bits:
        return "；".join(bits)
    return "Release 页面未写明变更说明；需要查看 GitHub compare 或 commit 记录。"


def _fetch_release_delta(
    owner: str,
    repo: str,
    tag: str,
    *,
    timeout: int,
    user_agent: str | None = None,
) -> dict[str, Any]:
    headers = _headers(user_agent)
    release_body = ""
    release_missing = False
    try:
        release = _get_json(_api_url(owner, repo, f"releases/tags/{quote(tag, safe='')}"), headers, timeout)
        if isinstance(release, dict):
            release_body = str(release.get("body") or "")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        release_missing = status in {404, 410}
    except Exception:
        release_body = ""

    if release_missing:
        return {
            "missing": True,
            "previous_tag": "",
            "compare_verified": False,
            "compare_url": "",
            "summary": "GitHub Release 公开页面返回 404；如果来源 feed 已记录，只按官方发布线索保留，细节待确认。",
        }

    previous_tag = ""
    try:
        releases = _get_json(_api_url(owner, repo, "releases?per_page=20"), headers, timeout)
        previous_tag = _find_previous_tag(releases, tag)
    except Exception:
        previous_tag = ""
    previous_tag = previous_tag or previous_numeric_tag(tag)

    compare_data: Any = {}
    compare_url = ""
    compare_verified = False
    if previous_tag:
        try:
            candidate_compare = _get_json(
                _api_url(owner, repo, f"compare/{quote(previous_tag, safe='')}...{quote(tag, safe='')}"),
                headers,
                timeout,
            )
            candidate_url = str(candidate_compare.get("html_url") or "") if isinstance(candidate_compare, dict) else ""
            candidate_parts = github_compare_parts(candidate_url)
            if candidate_parts and (candidate_parts[0].lower(), candidate_parts[1].lower()) == (owner.lower(), repo.lower()):
                compare_data = candidate_compare
                compare_url = candidate_url
                compare_verified = True
        except Exception:
            compare_data = {}

    summary = clean_text(build_release_delta_summary(tag, previous_tag, release_body, compare_data), 1200)
    return {
        "missing": False,
        "previous_tag": previous_tag,
        "compare_verified": compare_verified,
        "compare_url": compare_url,
        "summary": summary,
    }


def enrich_github_release_items(
    items: list[FeedItem],
    *,
    timeout: int = 10,
    user_agent: str | None = None,
    limit: int = 6,
) -> int:
    """Enrich sparse GitHub Release feed items in-place via Releases & Compare API."""
    enriched = 0
    for item in items:
        if limit > 0 and enriched >= limit:
            break
        parts = github_release_parts(item.link)
        if not parts:
            continue
        if item.summary and not is_sparse_release_summary(item.summary, item.title):
            continue
        owner, repo, tag = parts
        delta = _fetch_release_delta(owner, repo, tag, timeout=timeout, user_agent=user_agent)
        raw = dict(item.raw or {})
        raw["github_release_enriched"] = True
        if delta["missing"]:
            raw["github_release_missing"] = True
        else:
            raw["github_previous_tag"] = delta["previous_tag"]
            raw["github_compare_verified"] = bool(delta["compare_verified"])
            if delta["compare_verified"]:
                raw["github_compare_url"] = delta["compare_url"]
                raw["github_compare_summary"] = delta["summary"]
        item.summary = delta["summary"]
        item.raw = raw
        enriched += 1
    return enriched


def _compare_evidence_from_release_row(
    release_row: dict[str, str],
    release_parts: tuple[str, str, str],
) -> tuple[dict[str, str], list[str]] | None:
    if str(release_row.get("github_compare_verified") or "").lower() != "true":
        return None
    compare_url = clean_text(str(release_row.get("github_compare_url") or ""))
    compare_summary = clean_text(str(release_row.get("github_compare_summary") or ""))
    compare = github_compare_parts(compare_url)
    if not compare or not compare_summary:
        return None
    if (release_parts[0].lower(), release_parts[1].lower()) != (compare[0].lower(), compare[1].lower()):
        return None

    clauses = [clean_text(val).strip("；; ") for val in re.split(r"[；;]+", compare_summary)]
    evidence_clauses = [
        clause
        for clause in clauses
        if re.match(r"^(?:版本对比|变更摘要|修复/维护线索)\s*[:：]", clause)
    ]
    if not evidence_clauses:
        return None

    source = clean_text(str(release_row.get("source") or "GitHub Release"))
    subject = re.sub(r"\s+GitHub\s+Releases?\s*$", "", source, flags=re.I).strip() or f"{compare[0]}/{compare[1]}"
    localized_facts = [f"{subject} {clause}" for clause in evidence_clauses]
    return (
        {
            "source": f"{source} Compare",
            "tier": "A",
            "reliability": "official",
            "title": f"GitHub compare {compare[2]}",
            "url": compare_url,
            "published_at": str(release_row.get("published_at") or ""),
            "excerpt": "；".join(localized_facts),
            "derived_evidence": "github_api_compare",
            "screenshot_required": "false",
        },
        localized_facts,
    )


def attach_github_compare_evidence(cards: list[EvidenceCard], *, timeout: int = 12) -> int:
    """Attach verified same-repository GitHub compare evidence to release cards (idempotent)."""
    attached = 0
    for card in cards:
        payload: tuple[dict[str, str], list[str]] | None = None
        for row in card.evidence_links:
            url = str(row.get("url") or row.get("final_url") or "")
            release_parts = github_release_parts(url)
            if not release_parts:
                continue
            payload = _compare_evidence_from_release_row(row, release_parts)
            if (
                payload is None
                and "github_compare_verified" not in row
                and card.selected
                and timeout > 0
                and (str(row.get("tier") or "").upper() == "A" or str(row.get("reliability") or "") == "official")
                and is_sparse_release_summary(str(row.get("excerpt") or ""), str(row.get("title") or ""))
            ):
                delta = _fetch_release_delta(*release_parts, timeout=timeout)
                row["github_compare_verified"] = "true" if delta["compare_verified"] else "false"
                if delta["compare_verified"]:
                    row["github_compare_url"] = delta["compare_url"]
                    row["github_compare_summary"] = delta["summary"]
                    payload = _compare_evidence_from_release_row(row, release_parts)
            if payload is not None:
                break

        if payload is None:
            continue
        evidence, localized_facts = payload
        for fact in localized_facts:
            if fact not in card.key_facts:
                card.key_facts.append(fact)
        target_url = str(evidence["url"])
        if any(str(existing.get("url") or existing.get("final_url") or "") == target_url for existing in card.evidence_links):
            continue
        card.evidence_links.append(evidence)
        attached += 1
    return attached
