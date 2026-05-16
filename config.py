from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GithubAgentConfig:
    """GitHub Agent 插件配置模型。

    仓库列表和 per-repo token 由 github_monitor SKILL 统一管理，
    插件通过 monitor_config.MonitorConfig 自动读取，无需重复配置。
    """

    # ── 插件自身设置 ──
    webhook_port: int = 0
    webhook_public_url: str = ""
    workspace_dir: Path = Path("data/github_workspace")

    # ── Agent 循环 ──
    max_retries: int = 3
    test_command: str = "pytest"
    model: str = ""

    # ── 功能开关 ──
    auto_label: bool = True
    auto_comment: bool = True
    auto_review: bool = True
    review_mode: str = "quick"

    # ── 控制台可视化 ──
    console_viewer_enabled: bool = True
    console_viewer_keep_open: bool = False

    # ── 轮询 ──
    poll_interval_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "webhook_port": self.webhook_port,
            "webhook_public_url": self.webhook_public_url,
            "workspace_dir": str(self.workspace_dir),
            "max_retries": self.max_retries,
            "test_command": self.test_command,
            "model": self.model,
            "auto_label": self.auto_label,
            "auto_comment": self.auto_comment,
            "auto_review": self.auto_review,
            "review_mode": self.review_mode,
            "console_viewer_enabled": self.console_viewer_enabled,
            "console_viewer_keep_open": self.console_viewer_keep_open,
            "poll_interval_seconds": self.poll_interval_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GithubAgentConfig:
        return cls(
            webhook_port=int(data.get("webhook_port", 0)),
            webhook_public_url=data.get("webhook_public_url", ""),
            workspace_dir=Path(data.get("workspace_dir", "data/github_workspace")),
            max_retries=int(data.get("max_retries", 3)),
            test_command=data.get("test_command", "pytest"),
            model=data.get("model", ""),
            auto_label=_parse_bool(data.get("auto_label", True)),
            auto_comment=_parse_bool(data.get("auto_comment", True)),
            auto_review=_parse_bool(data.get("auto_review", True)),
            review_mode=data.get("review_mode", "quick"),
            console_viewer_enabled=_parse_bool(data.get("console_viewer_enabled", True)),
            console_viewer_keep_open=_parse_bool(data.get("console_viewer_keep_open", False)),
            poll_interval_seconds=int(data.get("poll_interval_seconds", 60)),
        )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    if isinstance(value, int):
        return bool(value)
    return False
