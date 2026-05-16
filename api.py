"""Coding Agent GitHub API 封装。

基于 sirius_chat.github.client 提供的 GitHubClient 与 github_headers，
封装 Issue/PR/Label/Fork 等高级操作的快捷函数。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from sirius_chat.github.client import GitHubClient, github_headers

logger = logging.getLogger(__name__)


def _token_for_repo(config: dict[str, Any], repo: str = "") -> str:
    """获取 API token。

    优先级：per-repo token（MonitorConfig）> 全局 github_pat（旧兼容）。
    """
    monitor = config.get("_monitor")
    if monitor is not None and repo:
        token = monitor.get_token(repo)
        if token:
            return token
    return config.get("github_pat", "")


def _token(config: dict[str, Any]) -> str:
    return config.get("github_pat", "")


# ═══════════════════════════════════════════════════════════════════════
# Issue / PR 查询
# ═══════════════════════════════════════════════════════════════════════


async def get_issue(repo: str, issue_number: int, config: dict[str, Any]) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        result = await client.get_json(f"/repos/{repo}/issues/{issue_number}")
        if result is None:
            logger.error("获取 Issue #%d 失败", issue_number)
        return result


async def get_pr(repo: str, pr_number: int, config: dict[str, Any]) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        result = await client.get_json(f"/repos/{repo}/pulls/{pr_number}")
        if result is None:
            logger.error("获取 PR #%d 失败", pr_number)
        return result


async def get_pr_diff(repo: str, pr_number: int, config: dict[str, Any]) -> str:
    token = _token_for_repo(config, repo)
    headers = github_headers(token, extra_accept="application/vnd.github.v3.diff")
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as cli:
        resp = await cli.get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
        if resp.status_code == 200:
            return resp.text
        logger.error("获取 PR #%d diff 失败: %d", pr_number, resp.status_code)
        return ""


async def get_pr_files(repo: str, pr_number: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.get(f"/repos/{repo}/pulls/{pr_number}/files", params={"per_page": 100})
        if resp.status_code == 200:
            return resp.json()
        return []


async def get_pr_reviews(repo: str, pr_number: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.get(f"/repos/{repo}/pulls/{pr_number}/reviews", params={"per_page": 100})
        if resp.status_code == 200:
            return resp.json()
        return []


# ═══════════════════════════════════════════════════════════════════════
# 仓库操作
# ═══════════════════════════════════════════════════════════════════════


async def fork_repo(repo: str, config: dict[str, Any]) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(f"/repos/{repo}/forks")
        if resp.status_code in (200, 201, 202):
            return resp.json()
        if resp.status_code == 422:
            logger.info("已 Fork 过 %s，跳过", repo)
            return None
        logger.error("Fork 失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return None


async def sync_fork(repo: str, config: dict[str, Any]) -> bool:
    username = repo.split("/")[0] if "/" in repo else config.get("github_username", "")
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(
            f"/repos/{username}/{repo.split('/')[-1]}/merge-upstream",
            json={"branch": "main"},
        )
        if resp.status_code == 200:
            return True
        logger.warning("同步 Fork 失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return False


# ═══════════════════════════════════════════════════════════════════════
# PR 操作
# ═══════════════════════════════════════════════════════════════════════


async def create_pr(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        result = await client.post_json(
            f"/repos/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if result is None:
            logger.error("创建 PR 失败: %s", repo)
        return result


async def create_review(
    repo: str,
    pr_number: int,
    body: str,
    event: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        result = await client.post_json(
            f"/repos/{repo}/pulls/{pr_number}/reviews",
            json={"body": body, "event": event},
        )
        if result is None:
            logger.error("提交 Review 失败: PR #%d", pr_number)
        return result


async def create_inline_comment(
    repo: str,
    pr_number: int,
    commit_id: str,
    path: str,
    line: int,
    body: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(
            f"/repos/{repo}/pulls/{pr_number}/comments",
            json={"body": body, "commit_id": commit_id, "path": path, "line": line, "side": "RIGHT"},
        )
        if resp.status_code == 201:
            return resp.json()
        logger.warning("行内评论失败: %d %s", resp.status_code, resp.text[:200])
        return None


# ═══════════════════════════════════════════════════════════════════════
# 标签操作
# ═══════════════════════════════════════════════════════════════════════


async def get_labels(repo: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.get(f"/repos/{repo}/labels", params={"per_page": 100})
        if resp.status_code == 200:
            return resp.json()
        return []


async def create_label(
    repo: str,
    name: str,
    color: str,
    description: str,
    config: dict[str, Any],
) -> bool:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(
            f"/repos/{repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        return resp.status_code in (200, 201)


async def add_labels_to_issue(
    repo: str,
    issue_number: int,
    labels: list[str],
    config: dict[str, Any],
) -> bool:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(
            f"/repos/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        return resp.status_code == 200


async def post_issue_comment(
    repo: str,
    issue_number: int,
    body: str,
    config: dict[str, Any],
) -> bool:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.post(
            f"/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        if resp.status_code in (200, 201):
            return True
        logger.error("发表 Issue 评论失败: %d %s", resp.status_code, resp.text[:200])
        return False


# ═══════════════════════════════════════════════════════════════════════
# 列表轮询
# ═══════════════════════════════════════════════════════════════════════


async def list_repo_issues(
    repo: str,
    config: dict[str, Any],
    *,
    since: str | None = None,
    state: str = "open",
    per_page: int = 30,
) -> list[dict[str, Any]]:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        params: dict[str, Any] = {"state": state, "sort": "created", "direction": "desc", "per_page": per_page}
        if since:
            params["since"] = since
        resp = await client.get(f"/repos/{repo}/issues", params=params)
        if resp.status_code == 200:
            return [item for item in resp.json() if "pull_request" not in item]
        logger.error("获取 Issue 列表失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return []


async def list_repo_pulls(
    repo: str,
    config: dict[str, Any],
    *,
    state: str = "open",
    per_page: int = 30,
) -> list[dict[str, Any]]:
    async with GitHubClient(_token_for_repo(config, repo)) as client:
        resp = await client.get(
            f"/repos/{repo}/pulls",
            params={"state": state, "sort": "updated", "direction": "desc", "per_page": per_page},
        )
        if resp.status_code == 200:
            return resp.json()
        logger.error("获取 PR 列表失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return []
