from __future__ import annotations

import asyncio
import logging
from typing import Any

from .api import get_pr
from .agent_loop import run_agent_loop

logger = logging.getLogger(__name__)


async def handle_gh_command(
    ctx: Any,
    command_args: str,
    config: dict,
    engine_proxy: Any,
    data_store: Any,
) -> str:
    """处理 /gh 指令的统一入口。

    指令格式:
        /gh <task_id> auto                  — 启动自动修复
        /gh <task_id> status                — 查询任务状态
        /gh <task_id> abort                 — 中止任务
        /gh review <pr_number> [quick|deep] — 手动触发 PR 审阅（单仓库）
        /gh review <repo_index> <pr_number> [quick|deep] — 手动触发 PR 审阅（多仓库）
    """
    # 权限校验
    admin_id = config.get("admin_user_id", "")
    if ctx.message.user_id != admin_id:
        return "权限不足"

    parts = command_args.strip().split()
    if not parts:
        return "用法: /gh <task_id> auto|status|abort 或 /gh review [<repo_index>] <pr_number> [quick|deep]"

    # ── /gh review [<repo_index>] <pr_number> [mode] ──
    if parts[0] == "review":
        return await _handle_review_command(parts, config, engine_proxy, ctx)

    task_id = parts[0]
    action = parts[1] if len(parts) > 1 else "auto"

    if action == "auto":
        return await _handle_auto_command(task_id, config, engine_proxy, data_store, ctx)
    elif action == "status":
        return await _handle_status_command(task_id, data_store)
    elif action == "abort":
        return await _handle_abort_command(task_id, data_store)
    else:
        return f"未知操作: {action}"


async def _handle_auto_command(
    task_id: str,
    config: dict,
    engine_proxy: Any,
    data_store: Any,
    ctx: Any,
) -> str:
    """启动自动修复。"""
    raw = data_store.get(f"task_{task_id}")
    if raw is None:
        return f"未找到任务 {task_id}"

    task_data = _ensure_dict(raw)
    task_data["status"] = "APPROVED"
    data_store.set(f"task_{task_id}", task_data)

    adapter = getattr(ctx, "adapter", None)
    asyncio.create_task(
        run_agent_loop(
            task_data=task_data,
            config=config,
            engine_proxy=engine_proxy,
            adapter=adapter,
            admin_user_id=config.get("admin_user_id", ""),
        )
    )
    return f"任务已启动：Issue #{task_data.get('issue_number', '?')}"


async def _handle_status_command(task_id: str, data_store: Any) -> str:
    """查询任务状态。"""
    raw = data_store.get(f"task_{task_id}")
    if raw is None:
        return f"未找到任务 {task_id}"
    task_data = _ensure_dict(raw)
    return (
        f"任务 {task_id} 状态:\n"
        f"Issue: #{task_data.get('issue_number', '?')} - {task_data.get('issue_title', '')}\n"
        f"状态: {task_data.get('status', 'UNKNOWN')}\n"
        f"仓库: {task_data.get('repo', 'N/A')}\n"
        f"标签: {' '.join(task_data.get('labels', [])) or '无'}"
    )


async def _handle_abort_command(task_id: str, data_store: Any) -> str:
    """中止任务。"""
    raw = data_store.get(f"task_{task_id}")
    if raw is None:
        return f"未找到任务 {task_id}"
    task_data = _ensure_dict(raw)
    task_data["status"] = "ABORTED"
    data_store.set(f"task_{task_id}", task_data)
    return f"任务 {task_id} 已中止"


async def _handle_review_command(
    parts: list[str],
    config: dict,
    engine_proxy: Any,
    ctx: Any,
) -> str:
    """处理 /gh review 指令，支持多仓库。

    用法：
        /gh review <pr_number> [quick|deep]           — 单仓库，自动选择
        /gh review <repo_index> <pr_number> [quick|deep] — 多仓库，指定索引
    """
    repos = config.get("repos", [])
    if not repos:
        return "未绑定仓库，请在 WebUI 插件设置中配置 repos"

    # 字数 2 → review <pr_number> 或 review <repo_index> <pr_number>
    if len(parts) < 2:
        return "用法: /gh review [<repo_index>] <pr_number> [quick|deep]"

    # 自动判断：第一部分是纯数字 → 可能是 pr_number 或 repo_index
    repo: str
    pr_number: int
    try:
        first_num = int(parts[1])
    except ValueError:
        return f"无效的 PR 编号或仓库索引: {parts[1]}"

    if len(parts) >= 3:
        try:
            third_num = int(parts[2])
        except ValueError:
            # third is a string like "quick"/"deep" → parts[1] is pr_number
            repo = _resolve_repo(repos, None)
            pr_number = first_num
        else:
            # parts[1] is repo_index, parts[2] is pr_number
            repo = _resolve_repo(repos, first_num)
            pr_number = third_num
    elif len(repos) == 1:
        repo = repos[0]
        pr_number = first_num
    else:
        return (
            f"检测到 {len(repos)} 个绑定仓库，请指定索引:\n"
            + "\n".join(f"  [{i}] {r}" for i, r in enumerate(repos))
            + f"\n\n用法: /gh review <索引> <pr_number> [quick|deep]"
        )

    if not repo:
        return "无法确定仓库，请检查绑定配置"

    # 审阅模式
    mode = "quick"
    if len(parts) >= 3:
        candidate = parts[-1]
        if candidate in ("quick", "deep"):
            mode = candidate

    pr_data = await get_pr(repo, pr_number, config)
    if pr_data is None:
        return f"未找到 PR #{pr_number}（仓库 {repo}）"

    from .review import auto_review_pr

    adapter = getattr(ctx, "adapter", None)

    async def _run_review():
        try:
            result = await auto_review_pr(pr_data, repo, engine_proxy, config, mode)
            if "error" in result:
                logger.error("PR #%d 审阅失败: %s", pr_number, result["error"])
                return
            admin_id = config.get("admin_user_id", "")
            if admin_id and adapter:
                pr_url = pr_data.get("html_url", "")
                await adapter.send_private_message(
                    admin_id,
                    f"PR #{pr_number} 自动审阅完成（{mode}模式）\n"
                    f"仓库: {repo}\n"
                    f"结论: {result.get('verdict', 'N/A')}（{result.get('issues_count', 0)} 个问题）\n"
                    f"摘要: {result.get('summary', '')}\n"
                    f"链接: {pr_url}",
                )
        except Exception as exc:
            logger.error("PR 审阅异常: %s", exc)

    asyncio.create_task(_run_review())
    return f"已启动 {mode} 模式审阅 PR #{pr_number}（仓库 {repo}）"


def _resolve_repo(repos: list[str], index: int | None) -> str:
    """根据索引解析仓库，None 时自动选唯一的。"""
    if index is not None and 0 <= index < len(repos):
        return repos[index]
    if len(repos) == 1:
        return repos[0]
    return ""


def _ensure_dict(raw: Any) -> dict:
    """确保 data_store 返回的数据为 dict 格式。"""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "to_dict"):
        return raw.to_dict()
    return {"data": str(raw)}
