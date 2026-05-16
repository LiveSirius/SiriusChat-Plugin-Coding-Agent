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

    # 绑定的仓库列表（支持多仓库绑定，每项格式 owner/repo）
    repos: list[str] = field(default_factory=list)

    # Webhook
    webhook_port: int = 0  # 0 = 自动分配空闲端口
    webhook_secret: str = ""  # HMAC-SHA256 签名密钥（可选）
    webhook_public_url: str = ""  # 公网可达的 URL（如 ngrok 地址），用于 GitHub Webhook 配置指引

    # 工作区
    workspace_dir: Path = Path("data/github_workspace")

    # Agent 循环
    max_retries: int = 3
    test_command: str = "pytest"
    model: str = ""  # 空 = 使用 generate_raw 的 task_name 路由

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
            "repos": self.repos,
            "webhook_port": self.webhook_port,
            "webhook_secret": _mask_secret(self.webhook_secret),
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GithubAgentConfig:
        repos = data.get("repos", [])
        if isinstance(repos, str):
            repos = [r.strip() for r in repos.split(",") if r.strip()]
        return cls(
            github_pat=data.get("github_pat", ""),
            github_username=data.get("github_username", ""),
            admin_user_id=data.get("admin_user_id", ""),
            repos=list(repos),
            webhook_port=int(data.get("webhook_port", 0)),
            webhook_secret=data.get("webhook_secret", ""),
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
        )


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    if isinstance(value, int):
        return bool(value)
    return False


def _mask_secret(value: str) -> str:
    if not value or len(value) < 8:
        return value or ""
    return value[:4] + "****"
