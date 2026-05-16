from __future__ import annotations

import json
import logging
from typing import Any

from .api import add_labels_to_issue, create_label, get_labels

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3

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


async def _parse_label_data(result: str) -> dict:
    """解析 LLM 输出的标签 JSON。带重试提示的重试由调用方控制。"""
    stripped = result.strip()
    # 尝试提取 JSON（有些模型会在 JSON 外包 Markdown 代码块）
    for candidate in _extract_json_candidates(stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"无法从 LLM 输出中解析标签 JSON: {stripped[:200]}")


def _extract_json_candidates(text: str) -> list[str]:
    """从 LLM 输出中提取可能的 JSON 段落。"""
    candidates = [text]
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.find("```", start)
        if end > start:
            candidates.insert(0, text[start:end].strip())
    elif "```" in text:
        start = text.index("```") + 3
        end = text.find("```", start)
        if end > start:
            candidates.insert(0, text[start:end].strip())
    return candidates


async def auto_label_issue(
    issue_data: dict,
    repo_name: str,
    config: dict,
    engine_proxy: Any,
) -> list[str]:
    """使用 LLM 对 Issue 进行自动分类并返回建议标签列表。

    通过 EngineProxy.generate_raw(inject_persona=True) 调用，
    输出结构化 JSON 供程序解析和应用。
    失败时自动重试，不降级到关键词匹配。
    """
    prompt = f"""分析以下 GitHub Issue，输出 JSON 格式的标签建议。以你的角色身份进行分析，分析结果应符合该角色的认知视角。

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

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await engine_proxy.generate_raw(prompt, inject_persona=True, json_mode=True)
            label_data = await _parse_label_data(result)
            return _build_label_list(label_data)
        except Exception as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                logger.info(
                    "Issue #%d 标签分类第 %d/%d 次失败，重试中: %s",
                    issue_data.get("number", "?"), attempt, _MAX_RETRIES, exc,
                )
            else:
                logger.error(
                    "Issue #%d 标签分类 %d 次重试全部失败: %s",
                    issue_data.get("number", "?"), _MAX_RETRIES, exc,
                )

    raise RuntimeError(
        f"Issue #{issue_data.get('number', '?')} 标签分类失败（{_MAX_RETRIES}次重试）"
    ) from last_error


def _build_label_list(label_data: dict) -> list[str]:
    """从解析后的标签数据构建标签名列表。"""
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


async def adjust_labels_for_issue(
    issue_number: int,
    repo_name: str,
    title: str,
    conversation: list[dict],
    existing_labels: list[str],
    config: dict,
    engine_proxy: Any,
) -> list[str] | None:
    """信息收集过程中，根据对话进展重新评估所有标签。

    返回该 Issue 当前应拥有的完整标签列表，或者 None（保持现有标签不变）。
    调用方负责用 set_all_labels_to_issue 全量替换。
    """
    recent_msgs = conversation[-6:] if len(conversation) > 6 else conversation
    conv_text = "\n".join(
        f"[{'用户' if m['role'] == 'user' else 'AI'}]: {m['content'][:500]}"
        for m in recent_msgs
    )
    existing_str = ", ".join(existing_labels) if existing_labels else "（无）"

    prompt = f"""根据 Issue 对话进展，输出该 Issue 当前应有的完整标签列表。

Issue #{issue_number}: {title}

当前已有标签: {existing_str}

最近对话:
{conv_text}

可用标签: type:bug|feature|docs|question|refactor,
priority:critical|high|medium|low, difficulty:easy|medium|hard,
area:core|api|ui|docs|tests|config,
status:needs-triage|status:good-first-issue|status:help-wanted

规则：
1. 每个维度（type/priority/difficulty）最多1个标签
2. area 按需添加，不重复
3. 对话中澄清了问题类型时应更新 type 标签
4. 对话未提供新信息时保持原有标签不变
5. 必须包含 status:needs-triage（除非主动添加其他 status）

输出严格 JSON:
{{"labels": ["标签1", "标签2", ...], "reason": "简短理由"}}"""

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await engine_proxy.generate_raw(prompt, inject_persona=False, json_mode=True)
            data = json.loads(_extract_json_candidates(result.strip())[0])
            labels = data.get("labels", [])
            if isinstance(labels, list) and labels:
                logger.info("Issue #%d 标签建议: %s (当前: %s)", issue_number, labels, existing_labels)
                valid = {"type:bug", "type:feature", "type:docs", "type:question", "type:refactor",
                         "priority:critical", "priority:high", "priority:medium", "priority:low",
                         "difficulty:easy", "difficulty:medium", "difficulty:hard",
                         "area:core", "area:api", "area:ui", "area:docs", "area:tests", "area:config",
                         "status:needs-triage", "status:good-first-issue", "status:help-wanted"}
                return [l for l in labels if l in valid]
            return list(existing_labels)
        except Exception as exc:
            last_error = exc
            if attempt < _MAX_RETRIES:
                logger.debug("标签调整第 %d/%d 次失败: %s", attempt, _MAX_RETRIES, exc)
            else:
                logger.debug("标签调整 %d 次全部失败: %s", _MAX_RETRIES, exc)

    return list(existing_labels)
