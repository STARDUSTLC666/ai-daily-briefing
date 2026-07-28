from __future__ import annotations

import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote, urlparse

import requests

from .models import EvidenceCard, FeedItem
from .util import clean_text


GITHUB_API = "https://api.github.com"
RELEASE_MAINTENANCE_MARKERS = (
    "修复",
    "测试",
    "安装脚本",
    "元数据",
    "兼容",
    "校验",
    "补齐",
    "schema",
    "协议",
    "plugin",
    "插件",
    "asset",
    "checksum",
    "digest",
)


def github_release_parts(url: str) -> tuple[str, str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 5 or parts[2] != "releases" or parts[3] != "tag":
        return None
    owner, repo = parts[0], parts[1]
    tag = "/".join(parts[4:])
    if not owner or not repo or not tag:
        return None
    return owner, repo, tag


def github_compare_parts(url: str) -> tuple[str, str, str] | None:
    """解析 GitHub compare URL，并保留版本范围用于证据标题。"""
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 4 or parts[2] != "compare":
        return None
    owner, repo = parts[0], parts[1]
    comparison = "/".join(parts[3:])
    if not owner or not repo or not comparison:
        return None
    return owner, repo, comparison


def _github_compare_evidence(card: EvidenceCard) -> tuple[dict[str, str], list[str]] | None:
    """仅从 GitHub API 成功标记恢复同仓 compare 证据。"""
    release_rows: list[tuple[dict[str, str], tuple[str, str, str]]] = []
    for row in card.evidence_links:
        release = github_release_parts(str(row.get("url") or row.get("final_url") or ""))
        if release:
            release_rows.append((row, release))
    if not release_rows:
        return None

    for release_row, release in release_rows:
        if str(release_row.get("github_compare_verified") or "").lower() != "true":
            continue
        compare_url = clean_text(str(release_row.get("github_compare_url") or ""))
        compare_summary = clean_text(str(release_row.get("github_compare_summary") or ""))
        compare = github_compare_parts(compare_url)
        if not compare or not compare_summary:
            continue
        if (release[0].lower(), release[1].lower()) != (compare[0].lower(), compare[1].lower()):
            continue

        # 只绑定 API compare 已结构化生成的三类事实；网页正文或 key_facts 中
        # 偶然出现的同仓 compare URL 没有内部成功标记，不能获得 A 级身份。
        clauses = [clean_text(value).strip("；; ") for value in re.split(r"[；;]+", compare_summary)]
        evidence_clauses = [
            clause
            for clause in clauses
            if re.match(r"^(?:版本对比|变更摘要|修复/维护线索)\s*[:：]", clause)
        ]
        if not evidence_clauses:
            continue
        source = clean_text(str(release_row.get("source") or "GitHub Release"))
        subject = re.sub(r"\s+GitHub\s+Releases?\s*$", "", source, flags=re.I).strip()
        subject = subject or f"{compare[0]}/{compare[1]}"
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
    return None


def attach_github_compare_evidence(cards: list[EvidenceCard]) -> int:
    """给发布卡补齐官方 compare 证据；重复调用保持幂等。"""
    attached = 0
    for card in cards:
        payload = _github_compare_evidence(card)
        if not payload:
            continue
        evidence, localized_facts = payload
        for fact in localized_facts:
            if fact not in card.key_facts:
                card.key_facts.append(fact)
        target_url = str(evidence["url"])
        if any(str(row.get("url") or row.get("final_url") or "") == target_url for row in card.evidence_links):
            continue
        card.evidence_links.append(evidence)
        attached += 1
    return attached


def is_sparse_release_summary(summary: str, title: str = "") -> bool:
    text = clean_text(summary)
    if not text:
        return True
    title_text = clean_text(title)
    lowered = text.lower().strip()
    title_lowered = title_text.lower().strip()
    if title_lowered and lowered in {title_lowered, f"release {title_lowered}", f"发布 {title_lowered}"}:
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
        "User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local)",
    }


def _get_json(url: str, headers: dict[str, str], timeout: int) -> Any:
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _release_body_note(body: str, tag: str) -> str:
    text = clean_text(body, 900)
    if not text or is_sparse_release_summary(text, tag):
        return ""
    lines = []
    for line in re.split(r"[。；;\n]+", text):
        cleaned = clean_text(line)
        if not cleaned:
            continue
        if cleaned.startswith("#"):
            continue
        lines.append(cleaned.strip("-* "))
        if len(lines) >= 2:
            break
    return "；".join(lines)


def _find_previous_tag(releases: Any, current_tag: str) -> str:
    if not isinstance(releases, list):
        return ""
    for idx, row in enumerate(releases):
        if not isinstance(row, dict):
            continue
        if str(row.get("tag_name") or "") != current_tag:
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
    title = clean_text(message).split("\n", 1)[0]
    title = re.sub(r"^\[[^\]]+\]\s*", "", title)
    title = re.sub(r"\s*\(#\d+\)\s*$", "", title)
    return title.strip()


def _human_commit_summary(title: str) -> str:
    raw = _clean_commit_title(title)
    lowered = raw.lower()
    if not raw:
        return ""
    if lowered.startswith("release "):
        return ""
    if "multi-agent" in lowered and ("mode hint" in lowered or "hint text" in lowered):
        return "新增 multi-agent 模式提示配置"
    if "install" in lowered and ("metadata" in lowered or "release" in lowered or "asset" in lowered):
        return "调整安装脚本的 release 元数据获取"
    if "install" in lowered and "test" in lowered:
        return "补充安装脚本测试"
    if "plugin" in lowered and "version" in lowered:
        return "补齐插件版本字段"
    if "schema" in lowered or "protocol" in lowered:
        return "更新配置或协议 schema"
    if "login" in lowered or "auth" in lowered or "oauth" in lowered:
        return "调整登录与鉴权流程"
    if "code-mode" in lowered and any(marker in lowered for marker in ["fall back", "fallback", "runtime", "host"]):
        return "改进 code-mode 的运行时回退"
    if "code-mode" in lowered:
        return "调整 code-mode 运行逻辑"
    # 当前 Codex alpha 版本的具体修复项可由 compare commit 直接核对，保留信息量。
    if "quoted hook commands" in lowered and "windows" in lowered:
        return "修复 Windows 引号 Hook 命令解析"
    if lowered.startswith(("fix ", "fix:", "fixed ")):
        return "修复一项代码问题"
    if lowered.startswith(("add ", "adds ", "added ")):
        return "新增一项代码能力"
    if lowered.startswith(("update ", "updates ", "updated ")):
        return "更新一项代码逻辑"
    if lowered.startswith(("remove ", "removes ", "removed ")):
        return "移除一项旧代码逻辑"
    # Do not leak an untranslated commit subject into the Chinese video. A
    # generic maintenance label is safer than pretending to translate a detail
    # that the release notes did not explain.
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
        summary = _human_commit_summary(str(commit.get("message") or ""))
        if not summary or summary in seen:
            continue
        seen.add(summary)
        result.append(summary)
        if len(result) >= limit:
            break
    return result


def _release_maintenance_summaries(compare_data: Any, limit: int = 2) -> list[str]:
    result: list[str] = []
    for summary in _commit_summaries(compare_data, limit=8):
        lowered = summary.lower()
        if not summary.startswith("修复") and not any(marker in lowered for marker in RELEASE_MAINTENANCE_MARKERS):
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
        maintenance = _release_maintenance_summaries(compare_data)
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


def enrich_github_release_item(item: FeedItem, user_agent: str | None = None, timeout_seconds: int = 12) -> FeedItem:
    parts = github_release_parts(item.link)
    if not parts:
        return item
    if item.summary and not is_sparse_release_summary(item.summary, item.title):
        return item
    owner, repo, tag = parts
    headers = _headers(user_agent)
    release_body = ""
    release_missing = False
    previous_tag = ""
    compare_data: Any = {}
    compare_verified = False
    compare_url = ""
    try:
        release = _get_json(_api_url(owner, repo, f"releases/tags/{quote(tag, safe='')}"), headers, timeout_seconds)
        if isinstance(release, dict):
            release_body = str(release.get("body") or "")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        release_missing = status in {404, 410}
        release_body = ""
    except Exception:
        release_body = ""
    if release_missing:
        raw = {**item.raw, "github_release_enriched": True, "github_release_missing": True}
        summary = "GitHub Release 公开页面返回 404；如果来源 feed 已记录，只按官方发布线索保留，细节待确认。"
        return replace(item, summary=summary, raw=raw)
    try:
        releases = _get_json(_api_url(owner, repo, "releases?per_page=20"), headers, timeout_seconds)
        previous_tag = _find_previous_tag(releases, tag)
    except Exception:
        previous_tag = ""
    previous_tag = previous_tag or previous_numeric_tag(tag)
    if previous_tag:
        try:
            candidate_compare = _get_json(
                _api_url(owner, repo, f"compare/{quote(previous_tag, safe='')}...{quote(tag, safe='')}"),
                headers,
                timeout_seconds,
            )
            candidate_url = str(candidate_compare.get("html_url") or "") if isinstance(candidate_compare, dict) else ""
            candidate_parts = github_compare_parts(candidate_url)
            if candidate_parts and (candidate_parts[0].lower(), candidate_parts[1].lower()) == (owner.lower(), repo.lower()):
                compare_data = candidate_compare
                compare_url = candidate_url
                compare_verified = True
        except Exception:
            compare_data = {}
    summary = build_release_delta_summary(tag, previous_tag, release_body, compare_data)
    raw = {
        **item.raw,
        "github_release_enriched": True,
        "github_previous_tag": previous_tag,
        "github_compare_verified": compare_verified,
    }
    if compare_verified:
        raw["github_compare_url"] = compare_url
        raw["github_compare_summary"] = clean_text(summary, 1200)
    return replace(item, summary=clean_text(summary, 1200), raw=raw)


def enrich_github_release_items(items: list[FeedItem], user_agent: str | None = None, limit: int = 3) -> list[FeedItem]:
    result = list(items)
    enriched = 0
    for idx, item in enumerate(result):
        if enriched >= limit:
            break
        if not github_release_parts(item.link):
            continue
        updated = enrich_github_release_item(item, user_agent=user_agent)
        if updated is not item:
            enriched += 1
            result[idx] = updated
    return result
