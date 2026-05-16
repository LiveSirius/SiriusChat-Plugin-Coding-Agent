from __future__ import annotations

import json
import logging
from typing import Any

from .api import add_labels_to_issue, create_label, get_labels

logger = logging.getLogger(__name__)

# 标签元数据：颜色和描述
_LABEL_META: dict[str, tuple[str, str]] = {
    "type:bug":              ("d73a4a", "Something isn't working"),
    "type:feature":          ("a2eeef", "New feature or request"),
    "type:docs":             ("0075ca", "Improvements or additions to documentation"),
    "type:question":         ("d876e3", "Further information is requested"),
    "type:refactor":         ("fbca04", "Code refactoring without feature change"),
    "priority:critical":     ("b60205", "Must be resolved ASAP"),
    "priority:high":         ("d93f0b", "High priority"),
    "priority:medium":       ("fbca04", "Medium priority"),
    "priority:low":          ("0e8a16", "Low priority"),
    "difficulty:easy":       ("0e8a16", "Good for newcomers"),
    "difficulty:medium":     ("fbca04", "Some experience required"),
    "difficulty:hard":       ("b60205", "Requires deep expertise"),
    "status:needs-triage":   ("ededed", "Awaiting triage"),
    "status:good-first-issue": ("7057ff", "Good for newcomers"),
    "status:help-wanted":    ("008672", "Extra attention is needed"),
    "area:core":             ("0052cc", "Core engine / runtime"),
    "area:api":              ("5319e7", "API / endpoints"),
    "area:ui":               ("d4c5f9", "User interface"),
    "area:docs":             ("0075ca", "Documentation"),
    "area:tests":            ("006b75", "Testing infrastructure"),
    "area:config":           ("bfdadc", "Configuration"),
}


def _label_metadata(label_name: str) -> tuple[str, str]:
    return _LABEL_META.get(label_name, ("cccccc", ""))


async def auto_label_issue(
    issue_data: dict,
    repo_name: str,
    config: dict,
    engine_proxy: Any,
) -> list[str]:
    """使用 LLM 对 Issue 进行自动分类并返回建议标签列表。

    通过 EngineProxy.generate_text_analysis() 调用轻量分析模型，
    输出结构化 JSON 供程序解析和应用。
    """
    persona = config.get("persona_info", {})
    persona_section = ""
    if persona.get("name"):
        persona_section = (
            f"\n你当前的角色身份是「{persona['name']}」，请以 {persona['name']} 的视角来分析这个 Issue。"
        )
        if persona.get("personality_traits"):
            traits = "、".join(persona["personality_traits"]) if isinstance(persona["personality_traits"], list) else persona["personality_traits"]
            persona_section += f"\n{persona['name']}的性格特征：{traits}"
        if persona.get("communication_style"):
            persona_section += f"\n{persona['name']}的沟通风格：{persona['communication_style']}"

    prompt = f"""你是一个 Issue 分类助手，正在以指定角色身份分析 Issue。分析结果应符合该角色的认知视角。
{persona_section}

严格遵守以下标签命名规范：
- 类型标签: type:bug / type:feature / type:docs / type:question / type:refactor
- 优先级标签: priority:critical / priority:high / priority:medium / priority:low
- 难度标签: difficulty:easy / difficulty:medium / difficulty:hard
- 模块标签: area:core / area:api / area:ui / area:docs / area:tests / area:config

Issue 标题: {issue_data.get('title', '')}
Issue 内容:
{issue_data.get('body', '')[:3000]}

请输出严格 JSON（不要 Markdown 代码块包裹）:
{{
    "type": "bug|feature|docs|question|refactor",
    "priority": "critical|high|medium|low",
    "difficulty": "easy|medium|hard",
    "areas": ["area:xxx", ...],
    "auto_apply": true,
    "reason_brief": "一句话理由"
}}"""
    try:
        result = await engine_proxy.generate_raw(prompt)
        label_data = json.loads(result.strip())
    except (json.JSONDecodeError, Exception):
        return _fallback_label_by_keywords(issue_data)

    labels: list[str] = []

    type_map = {
        "bug": "type:bug", "feature": "type:feature",
        "docs": "type:docs", "question": "type:question",
        "refactor": "type:refactor",
    }
    if label_data.get("type") in type_map:
        labels.append(type_map[label_data["type"]])

    priority_map = {
        "critical": "priority:critical", "high": "priority:high",
        "medium": "priority:medium", "low": "priority:low",
    }
    if label_data.get("priority") in priority_map:
        labels.append(priority_map[label_data["priority"]])

    difficulty_map = {
        "easy": "difficulty:easy", "medium": "difficulty:medium",
        "hard": "difficulty:hard",
    }
    if label_data.get("difficulty") in difficulty_map:
        labels.append(difficulty_map[label_data["difficulty"]])

    valid_areas = {"area:core", "area:api", "area:ui", "area:docs", "area:tests", "area:config"}
    for area in label_data.get("areas", []):
        if area in valid_areas:
            labels.append(area)

    labels.append("status:needs-triage")

    if (label_data.get("difficulty") == "easy" and
            label_data.get("type") in ("bug", "feature")):
        labels.append("status:good-first-issue")

    return labels


def _fallback_label_by_keywords(issue_data: dict) -> list[str]:
    """LLM 分类失败时的关键词降级方案。"""
    text = f"{issue_data.get('title', '')} {issue_data.get('body', '')}".lower()
    labels = ["status:needs-triage"]

    if any(kw in text for kw in ["bug", "报错", "错误", "crash", "崩溃", "异常"]):
        labels.append("type:bug")
    elif any(kw in text for kw in ["feature", "功能", "建议", "希望", "新增"]):
        labels.append("type:feature")
    elif any(kw in text for kw in ["doc", "文档", "说明", "readme"]):
        labels.append("type:docs")
    else:
        labels.append("type:question")

    if any(kw in text for kw in ["紧急", "urgent", "critical", "严重", "线上"]):
        labels.append("priority:critical")
    elif any(kw in text for kw in ["重要", "high", "核心"]):
        labels.append("priority:high")

    return labels


async def apply_labels_to_issue(
    repo_full_name: str,
    issue_number: int,
    labels: list[str],
    config: dict,
) -> bool:
    """通过 GitHub REST API 将标签应用到 Issue。

    先检查仓库是否存在该标签，若不存在则创建后再应用。
    """
    existing_labels = await get_labels(repo_full_name, config)
    existing_names: set[str] = {lb["name"] for lb in existing_labels}

    for label_name in labels:
        if label_name not in existing_names:
            color, description = _label_metadata(label_name)
            await create_label(repo_full_name, label_name, color, description, config)
            logger.info("创建新标签: %s (%s)", label_name, repo_full_name)

    return await add_labels_to_issue(repo_full_name, issue_number, labels, config)
