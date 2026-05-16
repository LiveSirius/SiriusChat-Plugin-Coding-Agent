from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .api import _headers

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


async def auto_review_pr(
    pr_data: dict,
    repo_full_name: str,
    engine_proxy: Any,
    config: dict,
    review_mode: str = "quick",
) -> dict:
    """对 PR 进行自动代码审阅。

    Args:
        pr_data: Webhook 中的 pull_request 对象
        repo_full_name: 仓库全名 (owner/repo)
        engine_proxy: 引擎代理，用于 LLM 调用
        config: 插件配置
        review_mode: "quick" | "deep" | "incremental"

    Returns:
        {"review_id": int, "comments": int, "summary": str, "verdict": str}
    """
    pr_number = pr_data["number"]
    pr_title = pr_data["title"]
    pr_body = pr_data.get("body", "")

    headers = _headers(config)
    diff_headers = {**headers, "Accept": "application/vnd.github.v3.diff"}

    async with httpx.AsyncClient(headers=diff_headers, timeout=30.0) as client:
        diff_resp = await client.get(f"{_BASE}/repos/{repo_full_name}/pulls/{pr_number}")
        diff_content = diff_resp.text if diff_resp.status_code == 200 else ""

        files_resp = await client.get(
            f"{_BASE}/repos/{repo_full_name}/pulls/{pr_number}/files",
            params={"per_page": 100},
        )
        files_data = files_resp.json() if files_resp.status_code == 200 else []

    if not diff_content.strip():
        logger.info("PR #%d diff 为空，跳过审阅", pr_number)
        return {"verdict": "skipped", "reason": "无可审阅的文本 diff"}

    files_summary = "\n".join(
        f"- {f['filename']} ({f['changes']} 行变更, +{f['additions']}/-{f['deletions']})"
        for f in files_data[:30]
    )

    diff_truncated = diff_content[:8000]
    if len(diff_content) > 8000:
        diff_truncated += f"\n... (diff 被截断，原始大小 {len(diff_content)} 字符)"

    persona = config.get("persona_info", {})
    persona_section = ""
    if persona.get("name"):
        persona_section = f"\n你当前的角色身份是「{persona['name']}」，请以 {persona['name']} 的视角和风格进行代码审阅。"
        if persona.get("persona_summary"):
            persona_section += f"\n角色简介：{persona['persona_summary']}"
        if persona.get("personality_traits"):
            traits = "、".join(persona["personality_traits"]) if isinstance(persona["personality_traits"], list) else persona["personality_traits"]
            persona_section += f"\n性格特征：{traits}"
        if persona.get("communication_style"):
            persona_section += f"\n沟通风格：{persona['communication_style']}"

    system_prompt = f"""你是一名资深代码审阅者，正在以指定角色身份进行审阅。{persona_section}

审阅规则：
1. 按维度分类问题：正确性 (correctness)、安全性 (security)、风格 (style)、测试 (testing)、性能 (performance)
2. 每条问题给出：严重程度 (critical/warning/suggestion)、涉及文件、行号、问题描述、修改建议
3. 不要对无关紧要的风格差异吹毛求疵
4. 如果 diff 中没有明显问题，输出 "未发现明显问题"
5. 输出严格的 JSON 格式

PR 信息：
- 标题: {pr_title}
- 描述: {pr_body[:2000]}

变更文件列表:
{files_summary}

DIFF:
{diff_truncated}

请输出 JSON（不要 Markdown 代码块包裹）:
{{
    "verdict": "approve|comment|request_changes",
    "summary": "审阅摘要（1-3句中文）",
    "issues": [
        {{
            "severity": "critical|warning|suggestion",
            "category": "correctness|security|style|testing|performance|dependency",
            "file": "文件路径",
            "line": 行号或null,
            "title": "问题简述",
            "description": "详细描述",
            "suggestion": "修改建议"
        }}
    ]
}}"""

    result_text = await engine_proxy.generate_raw(system_prompt)

    try:
        review_result = json.loads(result_text.strip())
    except json.JSONDecodeError:
        logger.warning("PR 审阅 JSON 解析失败，降级为纯文本评论")
        review_result = {
            "verdict": "comment",
            "summary": "（自动审阅生成，原始输出非 JSON 格式）",
            "issues": [],
        }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        body_lines = [f"**自动代码审阅**\n\n{review_result.get('summary', '')}\n"]

        issues = review_result.get("issues", [])
        if issues:
            body_lines.append(f"发现 {len(issues)} 个问题：\n")
            severity_emoji = {"critical": "CRITICAL", "warning": "WARNING", "suggestion": "SUGGESTION"}
            category_labels = {
                "correctness": "正确性", "security": "安全性", "style": "风格",
                "testing": "测试", "performance": "性能", "dependency": "依赖",
            }
            for i, issue in enumerate(issues, 1):
                sev = severity_emoji.get(issue.get("severity", "suggestion"), "INFO")
                cat = category_labels.get(issue.get("category", ""), issue.get("category", ""))
                body_lines.append(
                    f"\n**{i}. [{sev}] [{cat}] {issue.get('title', '')}**\n"
                    f"- 文件: `{issue.get('file', 'N/A')}`"
                )
                if issue.get("line"):
                    body_lines[-1] += f" (L{issue['line']})"
                body_lines.append(f"- 描述: {issue.get('description', '')}")
                if issue.get("suggestion"):
                    body_lines.append(f"- 建议: {issue.get('suggestion', '')}")
        else:
            body_lines.append("\n未发现明显问题。")

        verdict = review_result.get("verdict", "comment")
        event_map = {"approve": "APPROVE", "comment": "COMMENT", "request_changes": "REQUEST_CHANGES"}
        review_event = event_map.get(verdict, "COMMENT")
        review_body = "\n".join(body_lines)

        review_resp = await client.post(
            f"{_BASE}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            json={"body": review_body, "event": review_event},
        )
        if review_resp.status_code in (200, 201):
            review_data = review_resp.json()
            logger.info("PR #%d 审阅完成: verdict=%s, issues=%d", pr_number, verdict, len(issues))
            return {
                "review_id": review_data["id"],
                "verdict": verdict,
                "issues_count": len(issues),
                "summary": review_result.get("summary", ""),
            }
        else:
            logger.error("提交 PR Review 失败: %d %s", review_resp.status_code, review_resp.text)
            return {"error": f"提交 Review 失败: {review_resp.status_code}"}


async def post_inline_review_comments(
    repo_full_name: str,
    pr_number: int,
    commit_id: str,
    issues: list[dict],
    config: dict,
) -> int:
    """对 PR 的特定代码行发布行内评论。深度审阅模式使用。"""
    posted = 0
    headers = _headers(config)
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        for issue in issues:
            if not issue.get("file") or not issue.get("line"):
                continue
            body = f"**{issue.get('title', '自动审阅')}**\n\n{issue.get('description', '')}"
            if issue.get("suggestion"):
                body += f"\n\n建议: {issue['suggestion']}"
            try:
                resp = await client.post(
                    f"{_BASE}/repos/{repo_full_name}/pulls/{pr_number}/comments",
                    json={
                        "body": body,
                        "commit_id": commit_id,
                        "path": issue["file"],
                        "line": issue["line"],
                        "side": "RIGHT",
                    },
                )
                if resp.status_code == 201:
                    posted += 1
            except Exception as exc:
                logger.warning("发布行内评论失败: %s", exc)
    return posted


async def has_existing_review(
    repo_full_name: str,
    pr_number: int,
    config: dict,
) -> bool:
    """检查 agent 是否已经对该 PR 提交过审阅。

    用于 synchronize 事件时判断避免重复。
    """
    headers = _headers(config)
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        resp = await client.get(
            f"{_BASE}/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            params={"per_page": 100},
        )
        if resp.status_code != 200:
            return False
        for review in resp.json():
            if "自动代码审阅" in review.get("body", ""):
                return True
    return False
