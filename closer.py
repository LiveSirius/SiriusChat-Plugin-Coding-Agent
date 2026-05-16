"""垃圾 Issue/PR 检测与自动关闭。

用 LLM 分析新提交的 Issue/PR，判断是否属于无意义或垃圾提交，
若判定为垃圾则自动附带关闭理由并关闭。
"""

from __future__ import annotations

import logging
from typing import Any

from .api import close_issue, close_pr

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


async def try_close_garbage_issue(
    issue_data: dict[str, Any],
    repo_name: str,
    engine_proxy: Any,
    config: dict[str, Any],
) -> bool:
    """分析 Issue 是否为垃圾内容，若是则关闭。

    Returns True 如果已关闭（即判定为垃圾），False 表示保留。
    """
    prompt = _build_issue_garbage_prompt(issue_data)

    result_text = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result_text = await engine_proxy.generate_raw(prompt, inject_persona=True)
            result_text = result_text.strip()
            break
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                logger.info("Issue #%d 垃圾检测第 %d/%d 次失败，重试中: %s",
                            issue_data.get("number", "?"), attempt, _MAX_RETRIES, exc)
            else:
                logger.error("Issue #%d 垃圾检测 %d 次全部失败，跳过关闭",
                             issue_data.get("number", "?"), _MAX_RETRIES)

    if not result_text:
        return False

    parsed = _parse_garbage_result(result_text)
    if not parsed["is_garbage"]:
        return False

    close_msg = parsed["reason"] or "此 Issue 经自动分析判定为无意义提交，已自动关闭。如有疑问请联系管理员。"
    await close_issue(repo_name, issue_data["number"], close_msg, config)
    logger.info("Issue #%d 已自动关闭（垃圾判定: %s）", issue_data["number"], parsed["reason"])
    return True


async def try_close_garbage_pr(
    pr_data: dict[str, Any],
    repo_name: str,
    engine_proxy: Any,
    config: dict[str, Any],
) -> bool:
    """分析 PR 是否为垃圾内容，若是则关闭。

    Returns True 如果已关闭，False 表示保留。
    """
    prompt = _build_pr_garbage_prompt(pr_data)

    result_text = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result_text = await engine_proxy.generate_raw(prompt, inject_persona=True)
            result_text = result_text.strip()
            break
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                logger.info("PR #%d 垃圾检测第 %d/%d 次失败，重试中: %s",
                            pr_data.get("number", "?"), attempt, _MAX_RETRIES, exc)
            else:
                logger.error("PR #%d 垃圾检测 %d 次全部失败，跳过关闭",
                             pr_data.get("number", "?"), _MAX_RETRIES)

    if not result_text:
        return False

    parsed = _parse_garbage_result(result_text)
    if not parsed["is_garbage"]:
        return False

    close_msg = parsed["reason"] or "此 PR 经自动分析判定为无意义提交，已自动关闭。如有疑问请联系管理员。"
    await close_pr(repo_name, pr_data["number"], close_msg, config)
    logger.info("PR #%d 已自动关闭（垃圾判定: %s）", pr_data["number"], parsed["reason"])
    return True


def _build_issue_garbage_prompt(issue_data: dict[str, Any]) -> str:
    return f"""你正在审核一个 GitHub Issue，判断它是否属于垃圾/无意义提交。

判定为垃圾的标准（满足任意一条即可）：
1. 内容为纯广告、推广链接
2. 内容完全与项目无关（如随机字符、测试、空白内容）
3. 标题和内容均为无意义的占位符（如 "test"、"..."、"asdf"）
4. 明确是恶意或滥用性质的提交

注意：如果 Issue 仅仅描述不够清晰但仍有合理诉求，则判定为正常提交。

Issue #{issue_data.get('number', '?')}: {issue_data.get('title', '')}

Issue 内容:
{issue_data.get('body', '')[:3000] or '（无内容）'}

请以严格的 JSON 格式输出，不要包含其他内容：
{{"is_garbage": true或false, "reason": "若判定为垃圾，给出简短中文关闭理由（不超过80字）；否则留空"}}"""


def _build_pr_garbage_prompt(pr_data: dict[str, Any]) -> str:
    return f"""你正在审核一个 GitHub Pull Request，判断它是否属于垃圾/无意义提交。

判定为垃圾的标准（满足任意一条即可）：
1. 内容为纯广告、推广链接
2. 内容完全与项目无关（如随机字符、测试、空白内容）
3. 标题和内容均为无意义的占位符（如 "test"、"..."、"asdf"）
4. 没有任何实质代码变更或仅包含故意破坏性修改
5. 明确是恶意或滥用性质的提交

注意：如果 PR 包含合理的代码改动哪怕很小，则判定为正常提交。

PR #{pr_data.get('number', '?')}: {pr_data.get('title', '')}

PR 内容:
{pr_data.get('body', '')[:3000] or '（无内容）'}

请以严格的 JSON 格式输出，不要包含其他内容：
{{"is_garbage": true或false, "reason": "若判定为垃圾，给出简短中文关闭理由（不超过80字）；否则留空"}}"""


def _parse_garbage_result(text: str) -> dict[str, Any]:
    import json as _json

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = _json.loads(line)
                if isinstance(data, dict) and "is_garbage" in data:
                    return {
                        "is_garbage": bool(data.get("is_garbage", False)),
                        "reason": str(data.get("reason", "")).strip()[:200],
                    }
            except (_json.JSONDecodeError, ValueError):
                continue
    logger.warning("垃圾判定 JSON 解析失败，保留 Issue/PR: %s", text[:200])
    return {"is_garbage": False, "reason": ""}
