from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GithubAgentConfig:
    """GitHub Agent 插件配置模型。"""

    # GitHub 认证
    github_pat: str = ""
    github_username: str = ""

    # 管理员
    admin_user_id: str = ""

    # 绑定的仓库（用于自动修复和审阅的目标仓库）
    repo: str = ""

    # Webhook
    webhook_port: int = 0  # 0 = 自动分配空闲端口
    webhook_secret: str = ""  # HMAC-SHA256 签名密钥（可选）
    webhook_public_url: str = ""  # 公网可达的 URL（如 ngrok 地址），用于 GitHub Webhook 配置指引

    # 工作区
    workspace_dir: Path = Path("data/github_workspace")

    # Agent 循环
    max_retries: int = 3
    test_command: str = "pytest"

    # 功能开关
    auto_label: bool = True
    auto_comment: bool = True
    auto_review: bool = True
    review_mode: str = "quick"  # quick | deep

    # 控制台可视化
    console_viewer_enabled: bool = True
    console_viewer_keep_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "github_pat": _mask_secret(self.github_pat),
            "github_username": self.github_username,
            "admin_user_id": self.admin_user_id,
            "repo": self.repo,
            "webhook_port": self.webhook_port,
            "webhook_secret": _mask_secret(self.webhook_secret),
            "webhook_public_url": self.webhook_public_url,
            "workspace_dir": str(self.workspace_dir),
            "max_retries": self.max_retries,
            "test_command": self.test_command,
            "auto_label": self.auto_label,
            "auto_comment": self.auto_comment,
            "auto_review": self.auto_review,
            "review_mode": self.review_mode,
            "console_viewer_enabled": self.console_viewer_enabled,
            "console_viewer_keep_open": self.console_viewer_keep_open,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GithubAgentConfig:
        return cls(
            github_pat=data.get("github_pat", ""),
            github_username=data.get("github_username", ""),
            admin_user_id=data.get("admin_user_id", ""),
            repo=data.get("repo", ""),
            webhook_port=data.get("webhook_port", 0),
            webhook_secret=data.get("webhook_secret", ""),
            webhook_public_url=data.get("webhook_public_url", ""),
            workspace_dir=Path(data.get("workspace_dir", "data/github_workspace")),
            max_retries=data.get("max_retries", 3),
            test_command=data.get("test_command", "pytest"),
            auto_label=data.get("auto_label", True),
            auto_comment=data.get("auto_comment", True),
            auto_review=data.get("auto_review", True),
            review_mode=data.get("review_mode", "quick"),
            console_viewer_enabled=data.get("console_viewer_enabled", True),
            console_viewer_keep_open=data.get("console_viewer_keep_open", False),
        )


def _mask_secret(value: str) -> str:
    if not value or len(value) < 8:
        return value or ""
    return value[:4] + "****"
