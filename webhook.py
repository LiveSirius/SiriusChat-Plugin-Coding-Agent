"""Plugin webhook 事件处理器（业务逻辑层）。

复用框架 sirius_chat.github_webhook.GitHubWebhookServer 作为 HTTP 服务器基础设施，
本模块仅包含 Issue/PR 的具体处理逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .commenter import generate_issue_comment, post_comment
from .closer import try_close_garbage_issue, try_close_garbage_pr
from .labeler import apply_labels_to_issue, auto_label_issue
from .review import auto_review_pr, has_existing_review

logger = logging.getLogger(__name__)


async def handle_issue_opened(
    body: dict[str, Any],
    config: dict[str, Any],
    adapter: Any,
    engine_proxy: Any,
    data_store: Any,
) -> None:
    """处理 Issue 打开事件：垃圾检测 → 自动标签 → 智能回复 → 通知管理员。"""
    issue_data = body["issue"]
    repo_name = body["repository"]["full_name"]
    admin_id = _resolve_admin_id(adapter)

    # 0. 垃圾检测（优先执行，判定为垃圾则跳过后续流程）
    if config.get("auto_close_garbage", True):
        try:
            closed = await try_close_garbage_issue(issue_data, repo_name, engine_proxy, config)
            if closed:
                if admin_id:
                    await adapter.send_private_message(
                        admin_id,
                        f"Issue #{issue_data['number']}: {issue_data['title']} 已自动关闭（判定为垃圾）\n"
                        f"仓库: {repo_name}",
                    )
                return
        except Exception as exc:
            logger.error("Issue #%d 垃圾检测失败: %s", issue_data["number"], exc, exc_info=True)

    labels: list[str] = []

    # 1. 自动标签（带重试，失败时记录错误日志）
    if config.get("auto_label", True):
        try:
            labels = await auto_label_issue(issue_data, repo_name, config, engine_proxy)
            await apply_labels_to_issue(repo_name, issue_data["number"], labels, config)
            logger.info("Issue #%d 自动标签: %s", issue_data["number"], labels)
        except Exception as exc:
            logger.error("Issue #%d 自动标签失败: %s", issue_data["number"], exc, exc_info=True)

    # 2. 智能回复（带重试，失败时记录错误日志）
    if config.get("auto_comment", True):
        try:
            comment = await generate_issue_comment(issue_data, labels, repo_name, engine_proxy, config)
            await post_comment(repo_name, issue_data["number"], comment, config)
        except Exception as exc:
            logger.error("Issue #%d 智能回复失败: %s", issue_data["number"], exc, exc_info=True)

    # 3. 生成 TaskID → 持久化 → 通知管理员
    task_id = uuid.uuid4().hex[:12]
    task_data = {
        "task_id": task_id,
        "repo": repo_name,
        "issue_number": issue_data["number"],
        "issue_title": issue_data["title"],
        "issue_body": issue_data.get("body", ""),
        "labels": labels,
        "status": "PENDING_APPROVAL",
        "created_at": time.time(),
    }
    data_store.set(f"task_{task_id}", task_data)

    label_str = " ".join(f"[{l}]" for l in labels) if labels else "（未自动标签）"
    if admin_id:
        await adapter.send_private_message(
            admin_id,
            f"新 Issue #{issue_data['number']}: {issue_data['title']}\n"
            f"标签: {label_str}\n"
            f"仓库: {repo_name}\n"
            f"回复 /gh {task_id} auto 启动自动修复",
        )


async def handle_pr_event(
    body: dict[str, Any],
    config: dict[str, Any],
    adapter: Any,
    engine_proxy: Any,
) -> None:
    """处理 PR 事件：垃圾检测 → 自动代码审阅。"""
    pr_data = body["pull_request"]
    repo_name = body["repository"]["full_name"]
    pr_number = pr_data["number"]
    action = body.get("action", "")

    # 0. 垃圾检测（优先执行，判定为垃圾则跳过后续流程）
    if config.get("auto_close_garbage", True):
        try:
            closed = await try_close_garbage_pr(pr_data, repo_name, engine_proxy, config)
            if closed:
                admin_id = _resolve_admin_id(adapter)
                if admin_id:
                    await adapter.send_private_message(
                        admin_id,
                        f"PR #{pr_number}: {pr_data['title']} 已自动关闭（判定为垃圾）\n"
                        f"仓库: {repo_name}",
                    )
                return
        except Exception as exc:
            logger.error("PR #%d 垃圾检测失败: %s", pr_number, exc, exc_info=True)

    # 判定审阅模式
    if action == "synchronize":
        already_reviewed = await has_existing_review(repo_name, pr_number, config)
        review_mode = "incremental" if already_reviewed else "quick"
    else:
        review_mode = "quick"

    try:
        result = await auto_review_pr(pr_data, repo_name, engine_proxy, config, review_mode)
        if "error" in result:
            logger.error("PR #%d 审阅失败: %s", pr_number, result["error"])
            return

        admin_id = _resolve_admin_id(adapter)
        if admin_id:
            pr_url = pr_data["html_url"]
            verdict_emoji = {"approve": "OK", "comment": "COMMENT", "request_changes": "CHANGES"}
            emoji = verdict_emoji.get(result.get("verdict", ""), "BOT")
            await adapter.send_private_message(
                admin_id,
                f"[{emoji}] PR #{pr_number} 自动审阅完成\n"
                f"标题: {pr_data['title']}\n"
                f"结论: {result.get('verdict', 'N/A')}（{result.get('issues_count', 0)} 个问题）\n"
                f"摘要: {result.get('summary', '')}\n"
                f"链接: {pr_url}",
            )
    except Exception as exc:
        logger.error("PR #%d 审阅后台任务异常: %s", pr_number, exc, exc_info=True)


def _resolve_admin_id(adapter: Any) -> str:
    """从 adapter 读取 root 用户 ID。"""
    if adapter is None:
        return ""
    plugin_config = getattr(adapter, "plugin_config", None)
    if isinstance(plugin_config, dict):
        return str(plugin_config.get("root", "")).strip()
    return ""
