from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


def _headers(config: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.get('github_pat', '')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _client(config: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=_headers(config), timeout=30.0)


async def get_issue(repo: str, issue_number: int, config: dict[str, Any]) -> dict[str, Any] | None:
    """获取 Issue 详情。"""
    async with _client(config) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/issues/{issue_number}")
        if resp.status_code == 200:
            return resp.json()
        logger.error("获取 Issue #%d 失败: %d %s", issue_number, resp.status_code, resp.text[:200])
        return None


async def get_pr(repo: str, pr_number: int, config: dict[str, Any]) -> dict[str, Any] | None:
    """获取 PR 详情。"""
    async with _client(config) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/pulls/{pr_number}")
        if resp.status_code == 200:
            return resp.json()
        logger.error("获取 PR #%d 失败: %d %s", pr_number, resp.status_code, resp.text[:200])
        return None


async def get_pr_diff(repo: str, pr_number: int, config: dict[str, Any]) -> str:
    """获取 PR 的 diff 文本。"""
    headers = _headers(config)
    headers["Accept"] = "application/vnd.github.v3.diff"
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/pulls/{pr_number}")
        if resp.status_code == 200:
            return resp.text
        logger.error("获取 PR #%d diff 失败: %d", pr_number, resp.status_code)
        return ""


async def get_pr_files(repo: str, pr_number: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    """获取 PR 的文件变更列表。"""
    async with _client(config) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/pulls/{pr_number}/files", params={"per_page": 100})
        if resp.status_code == 200:
            return resp.json()
        return []


async def get_pr_reviews(repo: str, pr_number: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    """获取 PR 的 Review 列表。"""
    async with _client(config) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/pulls/{pr_number}/reviews", params={"per_page": 100})
        if resp.status_code == 200:
            return resp.json()
        return []


async def fork_repo(repo: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """Fork 仓库（幂等：已 Fork 则返回现有 Fork）。"""
    async with _client(config) as cli:
        resp = await cli.post(f"{_BASE}/repos/{repo}/forks")
        if resp.status_code in (200, 201, 202):
            return resp.json()
        if resp.status_code == 422:
            logger.info("已 Fork 过 %s，跳过", repo)
            return None
        logger.error("Fork 失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return None


async def sync_fork(repo: str, config: dict[str, Any]) -> bool:
    """将 Fork 与上游同步。"""
    username = config.get("github_username", "")
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{username}/{repo.split('/')[-1]}/merge-upstream",
            json={"branch": "main"},
        )
        if resp.status_code == 200:
            return True
        logger.warning("同步 Fork 失败 %s: %d %s", repo, resp.status_code, resp.text[:200])
        return False


async def create_pr(
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """创建 Pull Request。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error("创建 PR 失败: %d %s", resp.status_code, resp.text[:300])
        return None


async def create_review(
    repo: str,
    pr_number: int,
    body: str,
    event: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """提交 PR Review。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/pulls/{pr_number}/reviews",
            json={"body": body, "event": event},
        )
        if resp.status_code in (200, 201):
            return resp.json()
        logger.error("提交 Review 失败: %d %s", resp.status_code, resp.text[:200])
        return None


async def create_inline_comment(
    repo: str,
    pr_number: int,
    commit_id: str,
    path: str,
    line: int,
    body: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """在 PR 的指定行发表行内评论。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/pulls/{pr_number}/comments",
            json={"body": body, "commit_id": commit_id, "path": path, "line": line, "side": "RIGHT"},
        )
        if resp.status_code == 201:
            return resp.json()
        logger.warning("行内评论失败: %d %s", resp.status_code, resp.text[:200])
        return None


async def get_labels(repo: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """获取仓库的所有标签。"""
    async with _client(config) as cli:
        resp = await cli.get(f"{_BASE}/repos/{repo}/labels", params={"per_page": 100})
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
    """在仓库中创建标签。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        return resp.status_code in (200, 201)


async def add_labels_to_issue(
    repo: str,
    issue_number: int,
    labels: list[str],
    config: dict[str, Any],
) -> bool:
    """为 Issue 添加标签。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        return resp.status_code == 200


async def post_issue_comment(
    repo: str,
    issue_number: int,
    body: str,
    config: dict[str, Any],
) -> bool:
    """在 Issue 下发表评论。"""
    async with _client(config) as cli:
        resp = await cli.post(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        if resp.status_code in (200, 201):
            return True
        logger.error("发表 Issue 评论失败: %d %s", resp.status_code, resp.text[:200])
        return False
