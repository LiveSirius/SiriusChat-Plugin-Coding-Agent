from __future__ import annotations

import asyncio
import io
import logging
import sys
import traceback
from typing import Any

from sirius_chat.github import GitHubWebhookServer
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
    _plugin_description = "GitHub Issue/PR 自动化管理 + Python 代码执行（仓库由 github_monitor 统一管理）"
    _plugin_version = "2.0.0"
    _plugin_author = "SiriusChat"

    _plugin_parameters = [
        {"name": "webhook_port", "type": "int", "description": "Webhook 监听端口（0=自动分配）", "default": 0},
        {"name": "webhook_public_url", "type": "string", "description": "Webhook 公网地址（如 ngrok URL）"},
        {"name": "github_write_token", "type": "string", "description": "GitHub 写操作 PAT（fork/PR/标签/评论），留空则复用 monitor 读 Token"},
        {"name": "model", "type": "string", "description": "自定义 LLM 模型名（空=使用路由）"},
        {"name": "max_retries", "type": "int", "description": "最大重试次数", "default": 3},
        {"name": "test_command", "type": "string", "description": "测试命令", "default": "pytest"},
        {"name": "auto_label", "type": "boolean", "description": "启用 Issue 自动标签", "default": True},
        {"name": "auto_comment", "type": "boolean", "description": "启用 Issue 智能回复", "default": True},
        {"name": "auto_review", "type": "boolean", "description": "启用 PR 自动审阅", "default": True},
        {"name": "review_mode", "type": "string", "description": "PR 审阅深度: quick|deep", "default": "quick"},
        {"name": "console_viewer_enabled", "type": "boolean", "description": "弹出实时控制台窗口", "default": True},
        {"name": "console_viewer_keep_open", "type": "boolean", "description": "修复完成后保持窗口打开", "default": False},
        {"name": "poll_interval_seconds", "type": "int", "description": "API 轮询间隔（秒，0=仅用Webhook，默认60）", "default": 60},
    ]

    def __init__(self) -> None:
        self._gh_config: GithubAgentConfig | None = None
        self._monitor: MonitorConfig = MonitorConfig()
        self._webhook_server: GitHubWebhookServer | None = None
        self._webhook_port: int = 0
        self._poll_task: asyncio.Task | None = None

    async def on_load(self) -> None:
        """加载配置并启动服务。

        仓库列表和 token 从 github_monitor 的 SkillDataStore 读取，
        用户无需重复配置。插件自身设置通过 ctx.config（WebUI）管理。
        """
        self._gh_config = GithubAgentConfig.from_dict(self.ctx.config)

        # 从 github_monitor 读取仓库和 per-repo token
        if self.ctx.data_store:
            self._monitor = MonitorConfig.load(self.ctx.data_store)

        if not self._monitor.repo_names:
            logger.info("github_monitor 中未配置任何仓库，等待配置后重新加载")
            return

        config_dict = self._build_config_dict()

        # ── Webhook 模式（收到 GitHub push 时实时触发）──
        try:
            self._webhook_server = GitHubWebhookServer(
                secret=self._monitor.webhook_secret,
                host="127.0.0.1",
                port=self._gh_config.webhook_port,
            )
            self._webhook_server.set_repo_filter(
                lambda r: r in self._monitor.repo_names
            )

            adapter = self.ctx.adapter
            engine_proxy = self.ctx.engine_proxy
            data_store = self.ctx.data_store

            async def _on_issue(event_type: str, body: dict[str, Any]) -> None:
                if body.get("action") == "opened":
                    await handle_issue_opened(body, config_dict, adapter, engine_proxy, data_store)

            self._webhook_server.add_handler("issues", _on_issue)

            async def _on_pr(event_type: str, body: dict[str, Any]) -> None:
                action = body.get("action", "")
                if action in ("opened", "synchronize"):
                    asyncio.create_task(
                        handle_pr_event(body, config_dict, adapter, engine_proxy)
                    )

            self._webhook_server.add_handler("pull_request", _on_pr)

            self._webhook_port = await self._webhook_server.start()
            logger.info("GitHub Webhook 服务已启动: 127.0.0.1:%s", self._webhook_port)
        except Exception as exc:
            logger.warning("Webhook 服务启动失败（不影响插件运行）: %s", exc)

        # ── API 轮询（不需要公网 IP）──
        poll_interval = self._gh_config.poll_interval_seconds
        if poll_interval > 0:
            from .poller import start_polling_loop

            self._poll_task = await start_polling_loop(
                config=config_dict,
                adapter=self.ctx.adapter,
                engine_proxy=self.ctx.engine_proxy,
                data_store=self.ctx.data_store,
            )
            logger.info("API 轮询已启动（间隔 %d 秒）", poll_interval)

    async def on_unload(self) -> None:
        """停止所有后台任务。"""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            logger.info("API 轮询已停止")
        if self._webhook_server:
            try:
                await self._webhook_server.stop()
                logger.info("Webhook 服务已停止")
            except Exception as exc:
                logger.warning("停止 Webhook 服务时出错: %s", exc)

    def _build_config_dict(self) -> dict[str, Any]:
        """构建传递给子模块的配置字典。

        仓库列表和 per-repo token 来自 MonitorConfig，
        github_username 从 per-repo 信息推断或留空（仅用于 git commit author）。
        """
        if self._gh_config is None:
            return {}
        return {
            "repos": self._monitor.repo_names,
            "_monitor": self._monitor,
            "github_write_token": self._gh_config.github_write_token,
            "admin_user_id": self._resolve_admin_id(),
            "model": self._gh_config.model,
            "webhook_secret": self._monitor.webhook_secret,
            "auto_label": self._gh_config.auto_label,
            "auto_comment": self._gh_config.auto_comment,
            "auto_review": self._gh_config.auto_review,
            "review_mode": self._gh_config.review_mode,
            "workspace_dir": str(self._gh_config.workspace_dir),
            "webhook_public_url": self._gh_config.webhook_public_url,
            "console_viewer_enabled": self._gh_config.console_viewer_enabled,
            "console_viewer_keep_open": self._gh_config.console_viewer_keep_open,
            "poll_interval_seconds": self._gh_config.poll_interval_seconds,
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
