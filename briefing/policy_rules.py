from __future__ import annotations

from collections.abc import Iterable
import re

from .util import clean_text


AI_POLICY_PUBLIC_SERVICE_TERMS = (
    "行动方案",
    "指导意见",
    "管理办法",
    "实施方案",
    "监管规则",
    "国家标准",
)

AI_POLICY_PUBLIC_OWNER_PATTERNS = (
    r"(?:国务院|最高人民法院|最高人民检察院)",
    r"(?:中国|国家|中央)[\u4e00-\u9fff]{0,16}(?:委员会|局|办公室|法院|检察院|监管机构)",
    r"(?:科技|教育|工业和信息化|工信|公安|民政|司法|财政|自然资源|生态环境|交通运输|水利|农业农村|商务|应急管理|审计)部",
    r"(?:中央宣传部|中央组织部|中央统战部|中央社会工作部)",
    r"[\u4e00-\u9fff]{2,12}(?:省|市|自治区|自治州|县|区)(?:人民)?政府",
)

AI_POLICY_PERIOD_PATTERN = re.compile(
    r"20\d{2}\s*[—–-]\s*20\d{2}|未来\s*[一二三四五六七八九十百\d]+\s*年|"
    r"(?:到|至|截至)\s*20\d{2}\s*年"
)
AI_POLICY_TASK_PATTERN = re.compile(
    r"[一二三四五六七八九十百\d]+\s*(?:项|方面)\s*(?:重点|主要|具体)?任务|"
    r"(?:重点|主要|具体)?任务\s*(?:包括|涵盖|围绕|聚焦)"
)
AI_POLICY_RELEASE_TERMS = ("发布", "印发", "出台", "施行")


def is_concrete_public_ai_policy(title: str, supporting_texts: Iterable[str] = ()) -> bool:
    """识别具备公共主体、执行周期和具体任务的 AI 政策。"""
    text = clean_text(" ".join([title, *supporting_texts])).lower()
    has_ai = any(term in text for term in ["人工智能", "ai+", "ai +", "大模型", "智能监测", "智能预警"])
    has_policy = any(term in text for term in AI_POLICY_PUBLIC_SERVICE_TERMS)
    has_period = bool(AI_POLICY_PERIOD_PATTERN.search(text))
    has_tasks = bool(AI_POLICY_TASK_PATTERN.search(text))
    release_pattern = "|".join(re.escape(term) for term in AI_POLICY_RELEASE_TERMS)
    # 公共机构必须是发布动作的执行者，正文仅提到合作机构不算政策发布。
    has_public_release = any(
        re.search(rf"(?:{owner})[^。；;]{{0,24}}(?:{release_pattern})", text)
        for owner in AI_POLICY_PUBLIC_OWNER_PATTERNS
    )
    return has_ai and has_policy and has_period and has_tasks and has_public_release
