from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GithubAgentConfig:
    """GitHub Agent 插件配置模型。

    仓库列表和事件检测由 github_monitor SKILL 统一管理，
    插件通过 event_bridge 接收事件 + monitor_config 读取配置。
    """

    # ── 插件自身设置 ──
    workspace_dir: Path = Path("data/github_workspace")

    # ── 生效仓库过滤（空=全部）──
    active_repos: list[str] = field(default_factory=list)

    # ── 写操作 Token（与 github_monitor 的读 token 分离）──
    github_write_token: str = ""
    github_username: str = ""
    github_email: str = ""

    # ── Agent 循环 ──
    max_retries: int = 3
    test_command: str = "pytest"
    model: str = ""
    max_questions: int = 12

    # ── 功能开关 ──
    auto_label: bool = True
    auto_comment: bool = True
    auto_review: bool = True
    auto_close_garbage: bool = True
    review_mode: str = "quick"

    # ── 控制台可视化 ──
    console_viewer_enabled: bool = True
    console_viewer_keep_open: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_repos": self.active_repos,
            "github_write_token": _mask_secret(self.github_write_token),
            "github_username": self.github_username,
            "github_email": self.github_email,
            "workspace_dir": str(self.workspace_dir),
            "max_retries": self.max_retries,
            "test_command": self.test_command,
            "model": self.model,
            "max_questions": self.max_questions,
            "auto_label": self.auto_label,
            "auto_comment": self.auto_comment,
            "auto_review": self.auto_review,
            "auto_close_garbage": self.auto_close_garbage,
            "review_mode": self.review_mode,
            "console_viewer_enabled": self.console_viewer_enabled,
            "console_viewer_keep_open": self.console_viewer_keep_open,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GithubAgentConfig:
        return cls(
            active_repos=_parse_list(data.get("active_repos", [])),
            github_write_token=data.get("github_write_token", ""),
            github_username=data.get("github_username", ""),
            github_email=data.get("github_email", ""),
            workspace_dir=Path(data.get("workspace_dir", "data/github_workspace")),
            max_retries=int(data.get("max_retries", 3)),
            test_command=data.get("test_command", "pytest"),
            model=data.get("model", ""),
            max_questions=int(data.get("max_questions", 12)),
            auto_label=_parse_bool(data.get("auto_label", True)),
            auto_comment=_parse_bool(data.get("auto_comment", True)),
            auto_review=_parse_bool(data.get("auto_review", True)),
            auto_close_garbage=_parse_bool(data.get("auto_close_garbage", True)),
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


def _parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []
