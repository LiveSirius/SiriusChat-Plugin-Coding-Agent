from __future__ import annotations

import asyncio
import io
import logging
import sys
import traceback
from typing import Any

from sirius_chat.github.event_bridge import register_issue_handler, register_pr_handler
from sirius_chat.plugins import PluginBase, PluginResponse
from sirius_chat.plugins.decorators import command

from .commands import handle_gh_command
from .config import GithubAgentConfig
from .monitor_config import MonitorConfig
from .webhook import handle_issue_opened, handle_pr_event

logger = logging.getLogger(__name__)

_plugin_dependencies = ["httpx", "GitPython"]


class CodingAgentPlugin(PluginBase):
    _plugin_name = "coding_agent"
    _plugin_display_name = "编码助手"
    _plugin_description = "GitHub Issue/PR 自动化管理 + Python 代码执行（事件由 github_monitor 驱动）"
    _plugin_version = "2.1.0"
    _plugin_author = "SiriusChat"

    _plugin_parameters = [
        {"name": "github_write_token", "type": "string", "description": "GitHub 写操作 PAT（fork/PR/标签/评论），留空则复用 monitor 读 Token"},
        {"name": "active_repos", "type": "list", "description": "生效仓库（owner/repo，每行一个，留空=monitor中全部仓库生效）"},
        {"name": "model", "type": "string", "description": "自定义 LLM 模型名（空=使用路由）"},
        {"name": "max_retries", "type": "int", "description": "最大重试次数", "default": 3},
        {"name": "test_command", "type": "string", "description": "测试命令", "default": "pytest"},
        {"name": "auto_label", "type": "boolean", "description": "启用 Issue 自动标签", "default": True},
        {"name": "auto_comment", "type": "boolean", "description": "启用 Issue 智能回复", "default": True},
        {"name": "auto_review", "type": "boolean", "description": "启用 PR 自动审阅", "default": True},
        {"name": "review_mode", "type": "string", "description": "PR 审阅深度: quick|deep", "default": "quick"},
        {"name": "console_viewer_enabled", "type": "boolean", "description": "弹出实时控制台窗口", "default": True},
        {"name": "console_viewer_keep_open", "type": "boolean", "description": "修复完成后保持窗口打开", "default": False},
    ]

    def __init__(self) -> None:
        self._gh_config: GithubAgentConfig | None = None
        self._monitor: MonitorConfig = MonitorConfig()
        self._effective_repos: list[str] = []

    async def on_load(self) -> None:
        """加载配置并注册事件桥接。

        不自行启动 webhook 或轮询，所有事件由 github_monitor SKILL
        通过 event_bridge 推送。
        """
        self._gh_config = GithubAgentConfig.from_dict(self.ctx.config)

        if self.ctx.data_store:
            self._monitor = MonitorConfig.load(self.ctx.data_store)

        if not self._monitor.repo_names:
            logger.info("github_monitor 中未配置任何仓库，等待配置后重载")
            return

        # 过滤生效仓库
        active = self._gh_config.active_repos
        if active:
            active_set = set(active)
            self._effective_repos = [r for r in self._monitor.repo_names if r in active_set]
            logger.info("生效仓库过滤: %d/%d (%s)", len(self._effective_repos),
                        len(self._monitor.repo_names), ", ".join(self._effective_repos) if self._effective_repos else "无")
        else:
            self._effective_repos = list(self._monitor.repo_names)

        if not self._effective_repos:
            logger.info("active_repos 过滤后无生效仓库，跳过事件注册")
            return

        config_dict = self._build_config_dict()

        # 注册到 event_bridge（github_monitor 检测到事件时会回调）
        async def _on_issue_opened(body: dict[str, Any], repo_name: str) -> None:
            if repo_name not in self._effective_repos:
                return
            await handle_issue_opened(body, config_dict, self.ctx.adapter,
                                       self.ctx.engine_proxy, self.ctx.data_store)

        async def _on_pr_event(body: dict[str, Any], repo_name: str, action: str) -> None:
            if repo_name not in self._effective_repos:
                return
            asyncio.create_task(
                handle_pr_event(body, config_dict, self.ctx.adapter, self.ctx.engine_proxy)
            )

        register_issue_handler(_on_issue_opened)
        register_pr_handler(_on_pr_event)
        logger.info("已注册 event_bridge 处理器，等待 github_monitor 事件推送")

    async def on_unload(self) -> None:
        """停止所有后台任务。"""
        pass

    def _build_config_dict(self) -> dict[str, Any]:
        if self._gh_config is None:
            return {}
        return {
            "repos": self._effective_repos,
            "_monitor": self._monitor,
            "active_repos": self._effective_repos,
            "github_write_token": self._gh_config.github_write_token,
            "admin_user_id": self._resolve_admin_id(),
            "model": self._gh_config.model,
            "webhook_secret": self._monitor.webhook_secret,
            "auto_label": self._gh_config.auto_label,
            "auto_comment": self._gh_config.auto_comment,
            "auto_review": self._gh_config.auto_review,
            "review_mode": self._gh_config.review_mode,
            "workspace_dir": str(self._gh_config.workspace_dir),
            "console_viewer_enabled": self._gh_config.console_viewer_enabled,
            "console_viewer_keep_open": self._gh_config.console_viewer_keep_open,
        }

    def _resolve_admin_id(self) -> str:
        adapter = getattr(self.ctx, "adapter", None)
        if adapter is None:
            return ""
        plugin_config = getattr(adapter, "plugin_config", None)
        if isinstance(plugin_config, dict):
            return str(plugin_config.get("root", "")).strip()
        return ""

    @command(
        "py",
        prefix="/",
        patterns=["py", "python", "python3"],
        pattern_type="keyword",
        render_mode="direct",
        description="执行一行 Python 代码并返回结果",
        examples=["/py print('Hello World')", "/py 1+1"],
    )
    async def execute_python(self, code: str) -> PluginResponse:
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            exec(code)
            output = captured.getvalue().strip()
            return PluginResponse.ok(text=output or "代码执行完成（无输出）")
        except Exception:
            error = traceback.format_exc(limit=0).strip()
            return PluginResponse.fail(f"执行出错:\n{error}")
        finally:
            sys.stdout = old_stdout

    @command(
        "gh",
        prefix="/",
        patterns=["/gh"],
        render_mode="direct",
        description="GitHub Agent 指令：管理 Issue 修复、PR 审阅",
        examples=["/gh <task_id> auto", "/gh review <repo_index> <pr_number> [quick|deep]"],
    )
    async def github_agent(self, command_args: str = "") -> PluginResponse:
        if not self._monitor.repo_names:
            return PluginResponse.fail("未在 github_monitor 中配置仓库，请在 WebUI 的 SKILL 设置中配置")

        config_dict = {
            **self._build_config_dict(),
            "max_retries": self._gh_config.max_retries if self._gh_config else 3,
            "test_command": self._gh_config.test_command if self._gh_config else "pytest",
        }

        result = await handle_gh_command(
            ctx=self.ctx,
            command_args=command_args,
            config=config_dict,
            engine_proxy=self.ctx.engine_proxy,
            data_store=self.ctx.data_store,
        )

        if result.startswith("权限不足"):
            return PluginResponse.fail(result)
        return PluginResponse.ok(text=result)
