"""github_monitor 配置读取器。

从 github_monitor SKILL 的 SkillDataStore 读取仓库列表和 per-repo token，
供 coding_agent 插件复用，无需重复配置仓库。

路径: {work_path}/skill_data/github_monitor.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MonitorRepo:
    """一个来自 github_monitor 的仓库条目。"""

    full_name: str          # "owner/repo"
    owner: str
    repo: str
    mode: str = "poll"      # "poll" | "webhook"
    token: str = ""         # per-repo PAT（优先于插件全局 PAT）
    groups: list[str] = field(default_factory=list)


@dataclass
class MonitorConfig:
    """从 github_monitor SkillDataStore 读取的配置快照。"""

    repos: list[MonitorRepo] = field(default_factory=list)
    repo_names: list[str] = field(default_factory=list)    # ["owner/repo", ...]
    webhook_secret: str = ""

    @classmethod
    def load(cls, data_store: Any) -> MonitorConfig:
        """从 PluginDataStore 的内部路径推导 skill_data 目录，加载 github_monitor 配置。"""
        # PluginDataStore 的 _data_dir = {work_path}/plugin_data
        data_dir = getattr(data_store, "_data_dir", None)
        if data_dir is None:
            logger.warning("无法获取 plugin data 路径，跳过 github_monitor 配置读取")
            return cls()

        work_path = Path(data_dir).parent
        monitor_path = work_path / "skill_data" / "github_monitor.json"
        if not monitor_path.exists():
            logger.info("github_monitor 配置文件不存在: %s", monitor_path)
            return cls()

        try:
            raw = json.loads(monitor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("读取 github_monitor 配置失败: %s", exc)
            return cls()

        repos_data: list[dict[str, Any]] = raw.get("repos", [])
        if not isinstance(repos_data, list):
            return cls()

        repos: list[MonitorRepo] = []
        repo_names: list[str] = []
        for r in repos_data:
            owner = str(r.get("owner", "")).strip()
            repo = str(r.get("repo", "")).strip()
            if not owner or not repo:
                continue
            full_name = f"{owner}/{repo}"
            repos.append(MonitorRepo(
                full_name=full_name,
                owner=owner,
                repo=repo,
                mode=str(r.get("mode", "poll")),
                token=str(r.get("github_token", "")).strip(),
                groups=list(r.get("groups", [])),
            ))
            repo_names.append(full_name)

        webhook_secret = str(raw.get("webhook_secret", ""))

        logger.info("从 github_monitor 加载了 %d 个仓库: %s", len(repos), ", ".join(repo_names))
        return cls(repos=repos, repo_names=repo_names, webhook_secret=webhook_secret)

    def get_token(self, repo_full_name: str) -> str:
        """获取指定仓库的 per-repo token，无配置时返回空字符串。"""
        for r in self.repos:
            if r.full_name == repo_full_name:
                return r.token
        return ""
