from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import web

from .commenter import generate_issue_comment, post_comment
from .labeler import apply_labels_to_issue, auto_label_issue
from .review import auto_review_pr, has_existing_review

logger = logging.getLogger(__name__)


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """验证 GitHub Webhook HMAC-SHA256 签名。"""
    if not secret:
        return True
    expected = "sha256=" + hmac.new(secret.encode(), payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)


async def start_webhook_server(
    host: str,
    port: int,
    config: dict[str, Any],
    adapter: Any,
    engine_proxy: Any,
    data_store: Any,
) -> tuple[web.AppRunner, int]:
    """启动 Webhook HTTP 服务。

    返回 (runner, actual_port)，port=0 时自动分配空闲端口。
    """
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        return await webhook_handler(request, config, adapter, engine_proxy, data_store)

    app.router.add_post("/webhook/github", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    # 获取实际端口（port=0 自动分配时）
    actual_port = site._server.sockets[0].getsockname()[1] if port == 0 else port
    logger.info("Webhook HTTP 服务已启动: http://%s:%s/webhook/github", host, actual_port)
    return runner, actual_port


async def webhook_handler(
    request: web.Request,
    config: dict[str, Any],
    adapter: Any,
    engine_proxy: Any,
    data_store: Any,
) -> web.Response:
    """GitHub Webhook 统一入口。

    按 X-GitHub-Event 头分发到不同流程：
    - issues → Issue 自动修复流（标签 + 回复 + 通知）
    - pull_request → PR 自动审阅流
    """
    # 签名验证
    body_bytes = await request.read()
    sig = request.headers.get("X-Hub-Signature-256", "")
    secret = config.get("webhook_secret", "")
    if not verify_signature(body_bytes, sig, secret):
        logger.warning("Webhook 签名验证失败")
        return web.json_response({"error": "signature mismatch"}, status=401)

    event_type = request.headers.get("X-GitHub-Event", "")
    body = await request.json()

    repo_name = body.get("repository", {}).get("full_name", "")
    repos = config.get("repos", [])

    # 校验仓库是否在绑定列表中
    if repos and repo_name not in repos:
        logger.debug("Webhook 仓库 %s 不在绑定列表中，忽略", repo_name)
        return web.json_response({"status": "ignored", "reason": "repo not in bindings"})

    # ── Issue 事件 ──
    if event_type == "issues" and body.get("action") == "opened":
        await _handle_issue_opened(body, config, adapter, engine_proxy, data_store)
        return web.json_response({"status": "ok", "event": "issue_opened"})

    # ── PR 事件（审阅流） ──
    if event_type == "pull_request":
        action = body.get("action", "")
        if action in ("opened", "synchronize"):
            asyncio.create_task(
                _handle_pr_event(body, config, adapter, engine_proxy)
            )
            return web.json_response({"status": "ok", "event": "pr_review_triggered"})

    return web.json_response({"status": "ignored"})


async def _handle_issue_opened(
    body: dict,
    config: dict,
    adapter: Any,
    engine_proxy: Any,
    data_store: Any,
) -> None:
    """处理 Issue 打开事件：自动标签 → 智能回复 → 通知管理员。"""
    issue_data = body["issue"]
    repo_name = body["repository"]["full_name"]
    admin_id = config.get("admin_user_id", "")

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


async def _handle_pr_event(
    body: dict,
    config: dict,
    adapter: Any,
    engine_proxy: Any,
) -> None:
    """处理 PR 事件：自动代码审阅。"""
    pr_data = body["pull_request"]
    repo_name = body["repository"]["full_name"]
    pr_number = pr_data["number"]
    action = body.get("action", "")

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

        admin_id = config.get("admin_user_id", "")
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
