from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .models import EvidenceCard
from .presentation import card_display_entity
from .social_signals import card_is_community_signal
from .util import clean_text, stable_hash


@dataclass(slots=True)
class FactClaim:
    claim_id: str
    text: str
    claim_type: str
    specificity: int
    renderable: bool
    verifiable: bool
    evidence_urls: list[str] = field(default_factory=list)
    evidence_reliability: list[str] = field(default_factory=list)
    first_party_supported: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StorySpec:
    story_id: str
    entity: str
    kind: str
    headline: str
    subject: str
    action: str
    object: str
    topic_key: str
    family_key: str
    source_status: str
    first_party: bool
    evidence_count: int
    claims: list[FactClaim] = field(default_factory=list)
    audience: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def best_claim(self) -> FactClaim | None:
        return self.claims[0] if self.claims else None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_PREFIXES = (
    "首要来源发布时间",
    "首要来源",
    "来源摘要",
    "变更摘要",
    "修复/维护线索",
)

META_PHRASES = [
    "相关事件",
    "模型仓库更新",
    "模型页更新",
    "真正影响还要看",
    "后续复现",
    "需要人工确认",
    "性能榜单要看测试口径",
    "影响主要落在",
    "影响更偏向",
]

# Publisher names alone are not semantic evidence for a capability, price or
# availability claim.  Product/model terms remain useful anchors; this list is
# intentionally limited to organization-level names.
SOURCE_BRAND_TERMS = {
    "openai",
    "anthropic",
    "google",
    "deepmind",
    "nvidia",
    "microsoft",
    "meta",
    "xai",
    "alibaba",
    "mistral",
    "minimax",
    "moonshot",
    "腾讯",
    "阿里",
    "百度",
    "字节",
    "智谱",
    "英伟达",
    "huggingface",
    "modelscope",
    "github",
}

ACTION_TERMS: list[tuple[str, str]] = [
    ("正式发布", "发布"),
    ("发布", "发布"),
    ("推出", "推出"),
    ("上线", "上线"),
    ("接入", "接入"),
    ("新增", "新增"),
    ("开放", "开放"),
    ("开源", "开源"),
    ("重写", "重写"),
    ("迁移", "迁移"),
    ("修复", "修复"),
    ("升级", "升级"),
    ("更新", "更新"),
    ("暂停", "暂停"),
    ("调整", "调整"),
    ("拆分", "调整"),
    ("离职", "离职"),
    ("辞职", "辞职"),
    ("卸任", "卸任"),
    ("任命", "任命"),
    ("接任", "接任"),
    ("重组", "重组"),
    ("并入", "并入"),
    ("调任", "调任"),
    ("available", "接入"),
    ("launch", "发布"),
    ("release", "发布"),
    ("adds", "新增"),
    ("introduces", "推出"),
]

ORGANIZATION_ACTION_TERMS = {
    "离职", "辞职", "卸任", "离开", "任命", "接任", "就任", "重组", "并入", "调任",
    "resign", "depart", "step down", "appoint", "reorgan", "merge into",
}


def _organization_claim_priority(claim: FactClaim) -> tuple[int, int]:
    lowered = clean_text(claim.text).lower()
    if any(term in lowered for term in ["离职", "辞职", "卸任", "离开", "resign", "depart", "step down"]):
        action = 4
    elif any(term in lowered for term in ["任命", "接任", "就任", "调任", "appoint"]):
        action = 3
    elif any(term in lowered for term in ["重组", "并入", "reorgan", "merge into"]):
        action = 2
    else:
        action = 1
    return action, claim.specificity


def _dominant_organization_claim(claims: list[FactClaim]) -> FactClaim | None:
    verified = [claim for claim in claims if claim.renderable and claim.verifiable]
    organization = [claim for claim in verified if claim.claim_type == "organization"]
    if not organization:
        return None
    strongest = max(organization, key=_organization_claim_priority)
    # A named senior-personnel action can be the actual event even when a
    # clickbait discovery title also mentions a freshly released model. With
    # only one adjacent product claim, do not misclassify that story as a model
    # launch and merge the two unrelated events.
    named_personnel = (
        len(verified) <= 2
        and _organization_claim_priority(strongest)[0] >= 4
        and bool(re.search(r"\b[A-Z][A-Za-z.'-]+\s+[A-Z][A-Za-z.'-]+\b", strongest.text))
    )
    if len(organization) * 5 < len(verified) * 3 and not named_personnel:
        return None
    return strongest

PRODUCT_TERMS = [
    "chatgpt", "codex", "copilot", "claude", "gemini", "qwen", "deepseek",
    "kimi", "minimax", "mistral", "llama", "grok", "vllm", "ollama", "bun",
]

RELEVANCE_DOMAIN_TERMS = [
    "agent", "agentic", "智能体", "编程", "代码", "自动驾驶", "辅助驾驶", "世界模型",
    "智能驾驶", "物理ai", "机器人", "多模态", "安全系统", "安全团队", "数据中心",
    "上下文", "推理", "训练",
]

SENSATIONAL_CLAIM_PATTERNS = [
    r"跑路",
    r"狂喜",
    r"炸裂",
    r"刚刚",
    r"重磅",
    r"突发",
    r"消失.{0,12}(?:后|发帖)",
    r"只为(?:它|他|她|这)",
    r"最强\s*(?:agent|智能体)",
    r"显而易见",
    r"稳居第一梯队",
    r"[?？]{2,}",
]


def _contains_sensational_wording(text: str) -> bool:
    return any(re.search(pattern, clean_text(text), flags=re.I) for pattern in SENSATIONAL_CLAIM_PATTERNS)


def has_affirmed_action(text: str, action: str) -> bool:
    """按分句判断动作是否成立，覆盖前置否定和“传闻不实”后置否定。"""
    lowered = clean_text(text).lower()
    for match in re.finditer(re.escape(action.lower()), lowered):
        separators = ["，", ",", "。", "；", ";", "！", "!", "？", "?"]
        clause_start = max(lowered.rfind(mark, 0, match.start()) for mark in separators)
        clause_ends = [lowered.find(mark, match.end()) for mark in separators]
        clause_end = min((value for value in clause_ends if value >= 0), default=len(lowered))
        prefix = lowered[clause_start + 1 : match.start()]
        suffix = lowered[match.end() : clause_end]
        contrasts = list(re.finditer(r"(?:但|不过|然而|而是|but|however)\s*", prefix, flags=re.I))
        if contrasts:
            prefix = prefix[contrasts[-1].end() :]
        if re.search(
            r"(?:不会|不再|不打算|并非|不是|并未|尚未|未曾|没有|从未|"
            r"否认|无(?:需|须)|will\s+not|(?:does?|did)\s+not|not|never)",
            prefix,
            flags=re.I,
        ):
            continue
        if re.search(
            r"^[^，,。；;]{0,24}(?:传闻|消息|说法|rumou?r)[^，,。；;]{0,12}"
            r"(?:不实|错误|被否认|false|incorrect|denied)",
            suffix,
            flags=re.I,
        ):
            continue
        return True
    return False


def _dominant_release_claim(claims: list[FactClaim]) -> FactClaim | None:
    candidates = [
        claim
        for claim in claims
        if claim.renderable
        and claim.verifiable
        and not _contains_sensational_wording(claim.text)
        and any(
            has_affirmed_action(claim.text, term)
            for term in ["正式发布", "发布", "推出", "上线", "开源", "更新", "升级"]
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda claim: (
            bool(re.search(r"\b[A-Z][A-Za-z0-9.-]+(?:\s+[A-Z][A-Za-z0-9.-]+){1,2}\b", claim.text)),
            claim.specificity,
            -len(claim.text),
        ),
    )


def _is_subscription_change_claim(claim: FactClaim) -> bool:
    """识别暂停订阅、会员拆分等当前运营变化。"""
    lowered = clean_text(claim.text).lower()
    subscription = any(term in lowered for term in ["订阅", "会员", "subscription", "membership"])
    change = any(
        has_affirmed_action(lowered, term)
        for term in ["暂停", "停止", "拆分", "调整", "分为", "pause", "suspend", "split"]
    )
    return claim.renderable and claim.verifiable and subscription and change


def _dominant_subscription_change_claim(claims: list[FactClaim]) -> FactClaim | None:
    """优先选择直接影响订阅可用性的运营事实。"""
    candidates = [claim for claim in claims if _is_subscription_change_claim(claim)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda claim: (
            any(term in claim.text.lower() for term in ["暂停", "停止", "pause", "suspend"]),
            claim.specificity,
        ),
    )


def _headline_model_release_is_supported(headline: str, claims: list[FactClaim]) -> bool:
    """确认标题里的模型发布是否有同模型的可核验事实支撑。"""
    headline_roots = {_model_root(anchor) for anchor in _model_anchors_in_text(headline)}
    if not headline_roots:
        return False
    for claim in claims:
        if not claim.renderable or not claim.verifiable:
            continue
        lowered = claim.text.lower()
        if not any(
            has_affirmed_action(lowered, term)
            for term in ["发布", "推出", "上线", "开源", "release", "launch"]
        ):
            continue
        if headline_roots & {_model_root(anchor) for anchor in _model_anchors_in_text(claim.text)}:
            return True
    return False

MODEL_PATTERN = re.compile(
    # (?<![A-Za-z0-9]) instead of \b: Chinese headlines glue model names to CJK text
    # ("阿里开源Qwen3.6"), where \b never matches between a CJK char and a Latin letter.
    r"(?<![A-Za-z0-9])(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan)"
    r"[-‐‑–—_\s]?[a-z]*\d[A-Za-z0-9_.‐‑–—-]*(?=$|[^A-Za-z0-9_.‐‑–—-])",
    flags=re.I,
)

VERSIONED_PRODUCT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+(?![A-Za-z0-9])",
    flags=re.I,
)

CJK_MODEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<alias>千问)\s*(?P<version>\d+(?:\.\d+)*)"
    r"(?P<suffix>(?:[-‐‑–—_][A-Za-z0-9.]+)*)",
    flags=re.I,
)

MODEL_ALIAS_PREFIXES = {"千问": "qwen"}
MODEL_ROOT_PATTERN = re.compile(
    r"^(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan)"
    r"(?:-?[a-z]+)?-?\d+(?:\.\d+)*",
    flags=re.I,
)
MODEL_LIFECYCLE_SUFFIX_PATTERN = re.compile(
    r"(?:-(?:preview|alpha|beta|rc)(?:[.-]?\d+)*|-(?:nightly|experimental))+$",
    flags=re.I,
)
MODEL_SPACE_BRANCH_PATTERN = re.compile(
    r"^\s+(?P<branch>max|coder|vl|vision|omni|audio|code|instruct|reasoning|chat)"
    r"(?=$|[^A-Za-z0-9])(?P<lifecycle>(?:[\s_-]+(?:preview|alpha|beta|rc)(?:[.\s_-]?\d+)*)?)",
    flags=re.I,
)

INTEGRATION_TARGET_PATTERNS = (
    (r"\bgithub\s+copilot\b", "github-copilot"),
    (r"\bmicrosoft\s+365\s+copilot\b", "microsoft-365-copilot"),
    (r"\bdatabricks\s+agent\s+bricks\b", "databricks-agent-bricks"),
    (r"\bamazon\s+bedrock\b", "amazon-bedrock"),
    (r"\bazure\s+ai\s+foundry\b", "azure-ai-foundry"),
    (r"\bvertex\s+ai\b", "vertex-ai"),
    (r"\bopenrouter\b", "openrouter"),
    (r"\bhugging\s*face\b", "hugging-face"),
    (r"\bmodelscope\b|魔搭", "modelscope"),
)

MEASURE_PATTERN = re.compile(
    # b(?![a-z0-9]) instead of b\b: "7b参数" has no \b between "b" and a CJK char.
    r"\d+(?:\.\d+)?\s*(?:%|倍|万行|亿个|次提交|个工作流|个\s*claude|ms|秒|gb|mb|"
    r"token|tokens|美元|元|分|b(?![a-z0-9])|k(?![a-z0-9]))",
    flags=re.I,
)


def _normalize_model_anchor(value: str) -> str:
    """归一模型名和生命周期后缀，同时保留 Max、Coder、VL 等产品分支。"""
    normalized = clean_text(value).lower()
    normalized = normalized.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[_\s]+", "-", normalized).strip("-")
    for alias, canonical in MODEL_ALIAS_PREFIXES.items():
        if normalized.startswith(alias):
            normalized = canonical + normalized[len(alias):]
            break
    # Qwen 的官方紧凑写法是 Qwen3.8；仅归一这一已验证别名，保留 GPT-5.6 等品牌惯用连字符。
    normalized = re.sub(r"^qwen-(?=\d)", "qwen", normalized, flags=re.I)
    normalized = MODEL_LIFECYCLE_SUFFIX_PATTERN.sub("", normalized)
    return re.sub(r"-{2,}", "-", normalized).strip("-")


def _model_anchors_in_text(value: str) -> list[str]:
    """从中英文文本提取可比较的版本化模型锚点。"""
    text = clean_text(value)
    anchors: list[str] = []
    for match in MODEL_PATTERN.finditer(text):
        raw = match.group(0)
        branch = MODEL_SPACE_BRANCH_PATTERN.match(text[match.end() :])
        if branch:
            raw = f"{raw}-{branch.group('branch')}{branch.group('lifecycle') or ''}"
        anchors.append(_normalize_model_anchor(raw))
    for match in CJK_MODEL_PATTERN.finditer(text):
        raw = f"{match.group('alias')}{match.group('version')}{match.group('suffix') or ''}"
        branch = MODEL_SPACE_BRANCH_PATTERN.match(text[match.end() :])
        if branch:
            raw = f"{raw}-{branch.group('branch')}{branch.group('lifecycle') or ''}"
        anchors.append(_normalize_model_anchor(raw))
    return [anchor for anchor in anchors if anchor]


def _model_root(anchor: str) -> str:
    """提取模型的版本根，用于判断同一版本下的产品分支。"""
    match = MODEL_ROOT_PATTERN.match(anchor)
    return match.group(0).lower() if match else anchor.lower()


def _story_model_anchor(headline: str, claims: list[FactClaim]) -> str:
    """选择故事的模型分支；单次正文噪声不得覆盖标题或重复证据。"""
    headline_anchors = _model_anchors_in_text(headline)
    claim_anchors: list[str] = []
    first_party_anchors: set[str] = set()
    for claim in claims:
        if not claim.renderable or not claim.verifiable:
            continue
        anchors = _model_anchors_in_text(claim.text)
        claim_anchors.extend(anchors)
        if claim.first_party_supported:
            first_party_anchors.update(anchors)
    counts = Counter([*headline_anchors, *claim_anchors])
    headline_roots = list(dict.fromkeys(_model_root(anchor) for anchor in headline_anchors))
    if headline_roots:
        target_root = headline_roots[0]
    else:
        supported = [
            anchor
            for anchor, count in counts.items()
            if count >= 2 or anchor in first_party_anchors
        ]
        if not supported:
            return ""
        root_scores = Counter()
        for anchor in supported:
            root_scores[_model_root(anchor)] += counts[anchor] + (2 if anchor in first_party_anchors else 0)
        target_root = max(root_scores, key=lambda root: (root_scores[root], len(root)))

    qualified = {
        anchor
        for anchor, count in counts.items()
        if _model_root(anchor) == target_root
        and (anchor in headline_anchors or anchor in first_party_anchors or count >= 2)
    }
    branches = sorted((anchor for anchor in qualified if anchor != target_root), key=lambda anchor: (len(anchor), anchor))
    if len(branches) == 1:
        return branches[0]
    if branches and all(branch == branches[0] or branch.startswith(branches[0] + "-") for branch in branches):
        return branches[-1]
    return target_root


def _integration_target_anchor(headline: str) -> str:
    """提取模型接入的目标平台；未知平台宁可分开，也不跨平台误并。"""
    lowered = clean_text(headline).lower()
    named_targets = sorted(
        {target for pattern, target in INTEGRATION_TARGET_PATTERNS if re.search(pattern, lowered, flags=re.I)}
    )
    if named_targets:
        return "+".join(named_targets)
    match = re.search(r"\bavailable\s+(?:in|on|through)\s+([^,，。；;|]{2,64})", lowered, flags=re.I)
    if match:
        target = match.group(1)
    else:
        match = re.search(r"(?:接入|集成到|集成至)\s*([^,，。；;|]{2,64})", lowered)
        target = match.group(1) if match else ""
    if target and not _model_anchors_in_text(target):
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", target).strip("-")
        if slug:
            return slug[:64]
    return f"event-{stable_hash([lowered])[:12]}"


def _model_event_scope(kind: str, action: str, headline: str) -> str:
    """给模型族附加事件范围，避免把发布、评测、接入和人事变化混为一条。"""
    lowered = clean_text(headline).lower()
    if kind == "benchmark" or any(term in lowered for term in ["benchmark", "评测", "榜单", "跑分", "得分"]):
        return "benchmark"
    if action == "接入" or any(
        has_affirmed_action(lowered, term) for term in ["接入", "integration", "available"]
    ):
        return f"integration|{_integration_target_anchor(headline)}"
    if kind == "industry" or any(term in lowered for term in ORGANIZATION_ACTION_TERMS):
        return "organization"
    if kind == "model_repository":
        return "repository"
    if kind == "model_update":
        return "update"
    if kind in {"media_report", "community_signal", "official_update", "other"} and any(
        term in lowered for term in ["测试入口", "预览版", "preview", "early access"]
    ):
        return "release"
    if kind == "model_release" or action in {"发布", "推出", "上线", "开源", "开放"}:
        return "release"
    return kind or "other"


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_raw_english(text: str) -> bool:
    text = clean_text(text)
    if len(text) < 30 or _has_cjk(text):
        return False
    letters = sum(ch.isascii() and ch.isalpha() for ch in text)
    visible = sum(not ch.isspace() for ch in text)
    return bool(visible) and letters / visible > 0.58


def _clean_claim(raw: str) -> str:
    text = clean_text(raw)
    text = re.sub(r"https?://\S+", "", text)
    for prefix in SOURCE_PREFIXES:
        text = re.sub(rf"^{re.escape(prefix)}\s*[：:]\s*", "", text, flags=re.I)
    text = re.sub(r"^.{1,40}\s+相关事件\s*[：:]\s*", "", text)
    return text.strip(" ：:；;，,。")


def _atomic_claim_parts(raw: str) -> list[str]:
    """Split scraped prose into auditable facts instead of page-sized blobs."""
    primary = re.split(r"(?<=[。！？!?；;])\s*|[\n]+", clean_text(raw))
    parts: list[str] = []
    boundary = re.compile(
        r"\s+(?=(?:"
        r"而|同时|此外|其中|按照|据|根据|针对|部分|另有|据了解|除了|第二|第三|"
        r"翻到|没有让|峰值时期|方法[:：]|但是|本次|目前|今天起|近日|日前|今年|去年|本月|"
        r"\d{4}年[,，]?|他(?:在|还|也|曾|称)|她(?:在|还|也|曾|称)|"
        r"在(?:感知|视觉|音频|博客|产品|模型|测试|评测|此次|本次|\s*AI\s*负责人)|"
        r"这(?:是|已经|次|意味着)|小扎[“\"']|"
        r"[A-Z][A-Za-z.'-]+\s*(?:离职|辞职|卸任|将离职|已离职)|"
        r"GPT[-A-Za-z0-9.]*\s*(?:上线|发布)"
        r"))"
    )
    for chunk in primary:
        for piece in boundary.split(chunk):
            text = _clean_claim(piece)
            if 12 <= len(text) <= 280:
                parts.append(text)
    return parts


def _claim_type(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["起诉", "提起诉讼", "指控", "法院", "案件编号", "诉状"]):
        return "legal"
    if any(term in lowered for term in ORGANIZATION_ACTION_TERMS):
        return "organization"
    if MEASURE_PATTERN.search(text) or any(term in lowered for term in ["benchmark", "评测", "得分", "延迟", "吞吐"]):
        return "metric"
    if (
        any(term in lowered for term in ["订阅", "会员", "subscription", "membership"])
        and any(
            has_affirmed_action(lowered, term)
            for term in ["暂停", "停止", "拆分", "调整", "分为", "pause", "suspend", "split"]
        )
    ):
        return "availability"
    if any(term in lowered for term in ["定价", "美元", "免费", "cost"]) or (
        "价格" in lowered and bool(re.search(r"\d", text))
    ):
        return "pricing"
    if any(term in lowered for term in ["可用", "开放", "上线", "接入", "入口", "available", "rollout"]):
        return "availability"
    if any(term in lowered for term in ["新增", "支持", "修复", "重写", "迁移", "调用", "能力", "feature"]):
        return "change"
    if any(term in lowered for term in ["方法", "提出", "实验", "数据集", "论文", "研究"]):
        return "research"
    return "statement"


def _claim_score(text: str, claim_type: str) -> int:
    lowered = text.lower()
    score = 0
    if _has_cjk(text):
        score += 18
    if MODEL_PATTERN.search(text) or any(term in lowered for term in PRODUCT_TERMS):
        score += 12
    # Riemann-1.0 等新品牌尚未进入固定模型词表，版本化名字本身仍是具体产品证据。
    if VERSIONED_PRODUCT_PATTERN.search(text):
        score += 12
    score += min(24, len(MEASURE_PATTERN.findall(text)) * 8)
    if claim_type in {"metric", "pricing", "availability", "change", "legal", "organization"}:
        score += 12
    if any(action in lowered for action, _label in ACTION_TERMS):
        score += 8
    attributed = bool(
        re.search(r"(?:据.{0,24}(?:消息|报道|资料)|获悉|消息人士|(?:公司|官方|团队|发言人)?\s*(?:回应|表示|宣布))", text)
    )
    if len(text) >= 18 and attributed:
        # Attributed media claims and explicit company responses are useful
        # compact-news facts even when they do not contain a model version or
        # benchmark number. Provenance is still enforced separately below.
        score += 6
    if re.search(r"(?:项目|业务).{0,32}(?:由|隶属于).{0,36}(?:团队|部门|业务线).{0,12}负责", text):
        # A named project/team assignment is a concrete organizational fact,
        # even when the sentence has no model number or benchmark figure.
        score += 8
    if 24 <= len(text) <= 220:
        score += 8
    if any(meta.lower() in lowered for meta in META_PHRASES):
        score -= 24
    if any(noise in lowered for noise in ["utm_source=", "utm_medium=", "w=3840&q=", "加载更多", "点击查看原文", "免责声明"]):
        score -= 40
    return max(0, min(100, score))


def _artifact_claim(text: str) -> bool:
    lowered = clean_text(text).lower()
    concrete = [
        "license", "许可", "apache", "mit", "权重", "weights", "safetensors", "gguf", "mlx",
        "参数", "context", "上下文", "download", "下载", "text-generation", "image-text", "api", "调用入口",
    ]
    return any(term in lowered for term in concrete) and not lowered.startswith(("hugging face 模型仓库更新", "模型仓库更新", "模型页更新"))


def _claim_terms(text: str) -> set[str]:
    lowered = clean_text(text).lower().replace("‐", "-").replace("‑", "-")
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}|\d+(?:\.\d+)?", lowered)
        if token not in {"相关事件", "来源摘要", "首要来源", "发布", "推出", "上线", "更新", "新增", "模型", "官方"}
    }


def _supporting_evidence(text: str, evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    claim_terms = _claim_terms(text)
    numeric_claims = set(re.findall(r"\d+(?:\.\d+)?", text))
    matches: list[tuple[int, dict[str, str]]] = []
    for row in evidence:
        url = str(row.get("url") or row.get("final_url") or "")
        primary_excerpt = clean_text(str(row.get("excerpt") or ""))
        primary_provenance = clean_text(str(row.get("excerpt_provenance") or ""))
        source_summary = clean_text(str(row.get("source_summary") or ""))
        source_summary_provenance = clean_text(str(row.get("source_summary_provenance") or ""))
        evidence_parts: list[str] = []
        if primary_excerpt and (not numeric_claims or primary_provenance != "feed_summary"):
            evidence_parts.append(primary_excerpt)
        if source_summary and (not numeric_claims or source_summary_provenance != "feed_summary"):
            evidence_parts.append(source_summary)
        excerpt = clean_text("\n".join(evidence_parts))
        if not url or not excerpt:
            continue
        excerpt_terms = _claim_terms(excerpt)
        overlap_terms = claim_terms & excerpt_terms
        overlap = len(overlap_terms)
        numeric_excerpt = set(re.findall(r"\d+(?:\.\d+)?", excerpt))
        numeric_match = bool(numeric_claims) and numeric_claims.issubset(numeric_excerpt)
        # A quantitative claim must preserve its numbers in the excerpt; brand
        # overlap alone cannot support a price, score, speed or percentage.
        if numeric_claims:
            supported = numeric_match and overlap >= 1
        else:
            # A publisher name (or two brands) cannot by itself support an
            # action/availability claim.  Require a second shared token and at
            # least one non-brand anchor such as the product, feature or
            # action/object phrase.
            contextual_overlap = overlap_terms - SOURCE_BRAND_TERMS
            supported = overlap >= 2 and bool(contextual_overlap)
        if supported:
            matches.append((overlap + (4 if numeric_match else 0), row))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _score, row in matches[:3]]


def _is_first_party_evidence(row: dict[str, str]) -> bool:
    reliability = clean_text(str(row.get("reliability") or "")).lower()
    tier = clean_text(str(row.get("tier") or "")).upper()
    source = clean_text(str(row.get("source") or "")).lower()
    return reliability == "official" or (tier == "A" and not any(term in source for term in ["media", "infoq", "36氪", "爱范儿", "量子位"]))


def _repeated_versioned_anchors(text: str) -> set[str]:
    """从正文中识别重复出现的版本化产品名，修复标题省略模型名的媒体稿。"""
    counts: dict[str, int] = {}
    for token in VERSIONED_PRODUCT_PATTERN.findall(clean_text(text)):
        lowered = token.lower()
        if not any(char.isdigit() for char in lowered):
            continue
        counts[lowered] = counts.get(lowered, 0) + 1
    return {token for token, count in counts.items() if count >= 2}


def _relevance_anchors(card: EvidenceCard) -> tuple[set[str], set[str]]:
    headline = clean_text(card.event_title).lower()
    anchors = set(_topic_anchors(card.event_title))
    anchors.update(term for term in RELEVANCE_DOMAIN_TERMS if term in headline)
    for evidence in card.evidence_links:
        url = str(evidence.get("url") or evidence.get("final_url") or "")
        match = re.search(r"(?:huggingface\.co|modelscope\.cn/models?)/([^/?#]+/[^/?#]+)", url, flags=re.I)
        if match:
            model_id = match.group(1).lower()
            anchors.add(model_id)
            anchors.add(model_id.split("/", 1)[-1])
    entity = clean_text(card.entity).lower()
    entity_anchors = {
        token
        for token in re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", entity)
        if token not in {"人工智能", "模型"}
    }
    anchors.update(entity_anchors)
    # Marketing headlines sometimes omit the actual product name. Promote a
    # product repeated in multiple evidence excerpts to an event anchor.
    excerpt_blob = " ".join(
        clean_text(" ".join([str(row.get("excerpt") or ""), str(row.get("source_summary") or "")])).lower()
        for row in card.evidence_links
    )
    for product in PRODUCT_TERMS:
        if excerpt_blob.count(product) >= 2:
            anchors.add(product)
            entity_anchors.add(product)
    # 媒体标题可能只写“机器人悟了”，但正文会反复出现 Riemann-1.0。
    # 这类版本化名字作为强事件锚点，仍需后续 claim-excerpt 数字一致性校验。
    anchors.update(_repeated_versioned_anchors(excerpt_blob))
    normalized = {anchor.lower() for anchor in anchors if anchor}
    if "自动驾驶" in normalized or "自动驾驶" in headline:
        normalized.update({"智能驾驶", "辅助驾驶", "物理ai"})
    if "智能驾驶" in normalized or "智能驾驶" in headline:
        normalized.update({"自动驾驶", "辅助驾驶", "物理ai"})
    weak = {anchor.lower() for anchor in entity_anchors if anchor}
    weak.update(anchor for anchor in normalized if anchor in SOURCE_BRAND_TERMS)
    # A product explicitly named in the headline is event-specific, not a
    # weak entity hint inferred from a long article body.
    weak.difference_update(product for product in PRODUCT_TERMS if product in headline)
    return normalized, weak


def _claim_relevance_level(text: str, anchors: set[str], weak_anchors: set[str], claim_type: str) -> int:
    lowered = clean_text(text).lower().replace("‐", "-").replace("‑", "-")
    if not anchors:
        return 2
    strong_anchors = anchors - weak_anchors
    if any(anchor in lowered for anchor in strong_anchors):
        return 2
    weak_matches = sum(1 for anchor in weak_anchors if anchor in lowered)
    if weak_matches >= 2 or (weak_matches >= 1 and claim_type in {"organization", "legal"}):
        return 2
    # A strongly quantitative sentence in a single-story card may omit the
    # brand while still describe the event. Navigation/roundup fragments are
    # filtered separately and should not get this exception.
    measurements = len(MEASURE_PATTERN.findall(text))
    engineering_terms = ["代码", "提交", "工作流", "延迟", "吞吐", "启动时间", "内存", "成本", "准确率"]
    if measurements >= 2 and any(term in lowered for term in engineering_terms):
        return 2
    # Keep a same-entity claim visible to diagnostics, but down-rank it below
    # the publication threshold. This prevents a background sibling product
    # from becoming the narrated fact merely because it shares the vendor.
    return 1 if weak_matches else 0


def extract_claims(card: EvidenceCard, limit: int = 8) -> list[FactClaim]:
    candidates: list[str] = []
    raw_fact_sources = list(card.key_facts)
    # 正文摘录即使很短，也可能包含 feed 摘要没有的决定性评测或可用性事实；
    # 只有未标 provenance 的旧测试夹具继续沿用长度门槛。
    if (
        len(card.evidence_links) > 1
        or any(str(row.get("excerpt_provenance") or "") == "article_excerpts" for row in card.evidence_links)
        or any(len(str(row.get("excerpt") or "")) > 320 for row in card.evidence_links)
    ):
        raw_fact_sources.extend(str(row.get("excerpt") or "") for row in card.evidence_links if row.get("excerpt"))
    for raw in raw_fact_sources:
        candidates.extend(_atomic_claim_parts(raw))
    seen: set[str] = set()
    claims: list[FactClaim] = []
    anchors, weak_anchors = _relevance_anchors(card)
    headline_norm = re.sub(r"\W+", "", clean_text(card.event_title).lower())
    for text in candidates:
        norm = re.sub(r"\W+", "", text.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        kind = _claim_type(text)
        relevance_level = _claim_relevance_level(text, anchors, weak_anchors, kind)
        if relevance_level == 0:
            continue
        specificity = _claim_score(text, kind)
        if relevance_level == 1:
            specificity = max(0, specificity - 20)
        lowered_text = text.lower()
        if re.sub(r"\W+", "", lowered_text) == headline_norm and kind == "statement":
            specificity = min(specificity, 12)
        is_repository = any("huggingface.co/" in str(row.get("url") or "").lower() or "modelscope.cn/" in str(row.get("url") or "").lower() for row in card.evidence_links)
        if is_repository and not _artifact_claim(text):
            specificity = min(specificity, 12)
        elif is_repository and _artifact_claim(text):
            specificity = min(100, specificity + 8)
        headline_lower = card.event_title.lower()
        headline_models = {name.lower() for name in MODEL_PATTERN.findall(headline_lower)}
        claim_models = {name.lower() for name in MODEL_PATTERN.findall(lowered_text)}
        if headline_models and headline_models & claim_models:
            specificity = min(100, specificity + 12)
        elif headline_models and claim_models and not (headline_models & claim_models):
            specificity = max(0, specificity - 16)
        claim_products = {product for product in PRODUCT_TERMS if product in lowered_text}
        headline_products = {product for product in PRODUCT_TERMS if product in headline_lower}
        if claim_products - headline_products and headline_models:
            specificity = max(0, specificity - 14)
        if any(marker in lowered_text for marker in [
            "搜狐网", "新浪网", "appeared first on", "责任编辑", "文章来源", "utm_source=", "utm_medium=",
            "粤icp", "版权所有", "以商业目的使用", "早报|", "一键直达", "老乡鸡", "台风", "燃油车",
        ]):
            specificity = max(0, specificity - 60)
        if _contains_sensational_wording(text):
            # Discovery headlines are still retained in the audit trail, but
            # clickbait wording cannot become the narrated factual claim.
            specificity = max(0, specificity - 40)
        if lowered_text.count(" * ") >= 2 or lowered_text.count("经理") + lowered_text.count("负责人") >= 2:
            specificity = max(0, specificity - 12)
        renderable = _has_cjk(text) and not _looks_raw_english(text)
        supporting_evidence = _supporting_evidence(text, card.evidence_links)
        supporting_urls = [str(row.get("url") or row.get("final_url") or "") for row in supporting_evidence]
        # Backward-compatible fixtures may not yet carry excerpts. Production
        # cards created by verify.py always do, so only use the single-source
        # fallback when no excerpt fields exist at all.
        has_excerpts = any(clean_text(str(row.get("excerpt") or "")) for row in card.evidence_links)
        if not has_excerpts and len(card.evidence_links) == 1:
            fallback_url = str(card.evidence_links[0].get("url") or card.evidence_links[0].get("final_url") or "")
            supporting_urls = [fallback_url] if fallback_url else []
            supporting_evidence = list(card.evidence_links) if fallback_url else []
        claim_id = stable_hash([card.cluster_key, text, *supporting_urls])[:20]
        evidence_reliability = [clean_text(str(row.get("reliability") or row.get("tier") or "unknown")) for row in supporting_evidence]
        claims.append(
            FactClaim(
                claim_id=claim_id,
                text=text,
                claim_type=kind,
                specificity=specificity,
                renderable=renderable,
                verifiable=bool(supporting_urls) and specificity >= 28,
                evidence_urls=supporting_urls,
                evidence_reliability=evidence_reliability,
                first_party_supported=any(_is_first_party_evidence(row) for row in supporting_evidence),
            )
        )
    claims.sort(key=lambda claim: (claim.renderable, claim.verifiable, claim.specificity, len(claim.text)), reverse=True)
    return claims[:limit]


def _source_status(card: EvidenceCard) -> str:
    if card.official_count > 0 and card.risk == "green":
        return "official_confirmed"
    if card.official_count > 0:
        return "official_signal"
    if card.media_count >= 2:
        return "media_cross_checked"
    if card.media_count == 1:
        return "single_media"
    if card.community_count > 0:
        return "community"
    return "unverified"


def _source_flags(card: EvidenceCard) -> tuple[bool, bool, bool]:
    sources = [str(row.get("source") or "").lower() for row in card.evidence_links]
    urls = [str(row.get("url") or row.get("final_url") or "").lower() for row in card.evidence_links]
    is_paper = any(source.startswith("arxiv") for source in sources) or any("arxiv.org/" in url for url in urls)
    is_repository = any("hugging face" in source or "modelscope" in source for source in sources) or any(
        "huggingface.co/" in url or "modelscope.cn/" in url for url in urls
    )
    is_github_release = any("github release" in source for source in sources) or any("/releases/tag/" in url for url in urls)
    return is_paper, is_repository, is_github_release


def _classify_kind(card: EvidenceCard, claims: list[FactClaim], semantic_headline: str = "") -> str:
    headline = clean_text(semantic_headline or card.event_title).lower()
    is_paper, is_repository, is_github_release = _source_flags(card)
    if is_paper:
        return "research"
    if is_repository:
        return "model_repository"
    if is_github_release:
        return "developer_release"
    if card_is_community_signal(card):
        return "community_signal"
    if _dominant_organization_claim(claims) is not None:
        return "industry"
    if any(term in headline for term in ["benchmark", "performance", "评测", "榜单", "跑分", "提速", "性能"]):
        return "benchmark"
    if any(term in headline for term in ["重写", "迁移", "实战", "实践", "案例", "workflow", "case study"]):
        return "case_study"
    has_model = bool(MODEL_PATTERN.search(headline)) or "模型" in headline
    if has_model and (
        any(has_affirmed_action(headline, term) for term in ["发布", "推出", "上线", "release", "launch"])
    ):
        return "model_release"
    if has_model and any(has_affirmed_action(headline, term) for term in ["更新", "升级", "增强", "新增"]):
        return "model_update"
    if any(term in headline for term in ["融资", "收购", "监管", "禁用", "合作", "人事", "离职", "任命"]):
        return "industry"
    if any(term in headline for term in PRODUCT_TERMS) and (
        any(
            has_affirmed_action(headline, term)
            for term in ["发布", "推出", "上线", "接入", "新增", "更新", "available", "launch", "introduces"]
        )
        or any(claim.claim_type in {"change", "availability"} for claim in claims)
    ):
        return "product_update"
    if card.official_count > 0:
        return "official_update"
    if card.media_count > 0:
        return "media_report"
    return "other"


def _subject(card: EvidenceCard, headline: str, *, prefer_headline: bool = False) -> str:
    if prefer_headline:
        organization = re.search(
            r"(?:^|[,，]\s*)(OpenAI|Anthropic|Meta|Google|Microsoft|xAI|NVIDIA|GitHub|"
            r"字节跳动|字节|阿里|腾讯|百度|智谱|月之暗面|MiniMax)\s*"
            r"(?:正式)?(?:发布|推出|上线|开源|更新|升级)",
            headline,
            flags=re.I,
        )
        if organization:
            name = clean_text(organization.group(1))
            return "字节" if name == "字节跳动" else name
    display_entity = card_display_entity(card)
    if display_entity and display_entity != "AI":
        return display_entity
    model = MODEL_PATTERN.search(headline)
    if model:
        return model.group(0)
    for product in PRODUCT_TERMS:
        if product in headline.lower():
            return product.title()
    return "AI"


def _action(headline: str, claims: list[FactClaim]) -> str:
    lowered = headline.lower()
    for raw, label in ACTION_TERMS:
        if has_affirmed_action(lowered, raw):
            return label
    for claim in claims:
        lowered_claim = claim.text.lower()
        for raw, label in ACTION_TERMS:
            if has_affirmed_action(lowered_claim, raw):
                return label
    return "动态"


def _topic_anchors(headline: str) -> list[str]:
    normalized = headline.lower().replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    models = MODEL_PATTERN.findall(normalized)
    products = [term for term in PRODUCT_TERMS if term in normalized]
    if models or products:
        return (models[:2] or products[:2])
    released_product = re.search(
        r"(?:正式)?(?:发布|推出|上线|开源|更新|升级)(?:了)?(?:新版|新一代)?\s*"
        r"([A-Z][A-Za-z0-9.-]+(?:\s+[A-Z][A-Za-z0-9.-]+){0,2}(?:\s+\d+(?:\.\d+)*)?)",
        headline,
        flags=re.I,
    )
    if released_product:
        return [clean_text(released_product.group(1))]
    words = re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    stop = {"发布", "推出", "上线", "更新", "新增", "正式", "available", "release", "with", "from", "that", "this"}
    return [word for word in words if word not in stop][:3]


def _topic_key(entity: str, headline: str, kind: str) -> str:
    # Anchors from the released-product branch keep the headline's original casing;
    # lowercase them so "ERNIE Bot 5.0" and "Ernie Bot 5.0" produce one topic key.
    anchors = [anchor.lower() for anchor in _topic_anchors(headline)]
    raw = "|".join([entity.lower(), kind, *anchors])
    return re.sub(r"\s+", "-", raw)[:160]


def _family_key(
    entity: str,
    headline: str,
    *,
    claims: list[FactClaim] | None = None,
    kind: str = "",
    action: str = "",
) -> str:
    """生成带事件范围的故事族键，模型生命周期后缀不另起新闻。"""
    model_anchor = _story_model_anchor(headline, claims or [])
    if model_anchor:
        scope = _model_event_scope(kind, action, headline)
        return f"model|{scope}|{model_anchor}"[:140]
    anchors = _topic_anchors(headline)
    if not anchors:
        return ""
    # A named model/product is globally stable enough to group its launch wave
    # across vendor and integration entities. Generic product words remain
    # entity-scoped to avoid merging unrelated Copilot/Agent stories.
    primary = anchors[0].lower().replace(" ", "-")
    return f"{entity.lower()}|{primary}"[:120]


def _audience(kind: str, headline: str) -> list[str]:
    text = headline.lower()
    audience: list[str] = []
    if kind in {"developer_release", "case_study", "benchmark"} or any(term in text for term in ["api", "sdk", "github", "codex", "vllm"]):
        audience.append("developers")
    if kind in {"product_update", "model_release", "model_update"} or any(term in text for term in ["chatgpt", "copilot", "客户端"]):
        audience.append("users")
    if kind in {"industry", "research"}:
        audience.append("industry")
    return audience or ["general"]


def build_story_spec(card: EvidenceCard) -> StorySpec:
    headline = clean_text(card.event_title)
    claims = extract_claims(card)
    organization_claim = _dominant_organization_claim(claims)
    release_claim = _dominant_release_claim(claims) if _contains_sensational_wording(headline) else None
    subscription_claim = _dominant_subscription_change_claim(claims)
    unsupported_model_release = bool(
        subscription_claim
        and _model_anchors_in_text(headline)
        and not _headline_model_release_is_supported(headline, claims)
    )
    semantic_headline = (
        organization_claim.text
        if organization_claim is not None
        else subscription_claim.text
        if unsupported_model_release and subscription_claim is not None
        else release_claim.text
        if release_claim is not None
        else headline
    )
    kind = _classify_kind(card, claims, semantic_headline)
    prefer_headline = semantic_headline != headline
    subject = _subject(card, semantic_headline, prefer_headline=prefer_headline)
    semantic_entity = subject if prefer_headline and subject != "AI" else card.entity
    action = _action(semantic_headline, claims)
    best = next((claim.text for claim in claims if claim.renderable), "")
    topic_key = _topic_key(semantic_entity or subject, semantic_headline, kind)
    warnings = list(card.uncertainty)
    if not any(claim.renderable and claim.verifiable for claim in claims):
        warnings.append("no_renderable_verifiable_claim")
    return StorySpec(
        story_id=stable_hash([card.cluster_key, topic_key])[:20],
        entity=semantic_entity,
        kind=kind,
        headline=headline,
        subject=subject,
        action=action,
        object=best or headline,
        topic_key=topic_key,
        # Organization actions must not merge with a model launch merely
        # because the discovery headline used the launch as clickbait context.
        family_key="" if organization_claim is not None else _family_key(
            semantic_entity or subject,
            semantic_headline,
            claims=claims,
            kind=kind,
            action=action,
        ),
        source_status=_source_status(card),
        first_party=card.official_count > 0,
        evidence_count=len({str(row.get("url") or row.get("source") or "") for row in card.evidence_links}),
        claims=claims,
        audience=_audience(kind, headline),
        warnings=warnings,
    )
