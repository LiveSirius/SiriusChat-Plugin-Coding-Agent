from __future__ import annotations

import io
import logging
import sys
import traceback
from typing import Any

from sirius_chat.plugins import PluginBase, PluginResponse
from sirius_chat.plugins.decorators import command

from .commands import handle_gh_command
from .config import GithubAgentConfig
from .webhook import start_webhook_server

logger = logging.getLogger(__name__)

_plugin_dependencies = ["httpx", "GitPython"]


class CodingAgentPlugin(PluginBase):
    _plugin_name = "coding_agent"
    _plugin_display_name = "编码助手"
    _plugin_description = "GitHub Issue/PR 自动化管理 + Python 代码执行"
    _plugin_version = "1.0.0"
    _plugin_author = "SiriusChat"

    def __init__(self) -> None:
        self._gh_config: GithubAgentConfig | None = None
        self._webhook_runner: Any = None
        self._webhook_port: int = 0

    async def on_load(self) -> None:
        """加载配置并启动 Webhook 服务。"""
        settings = self.ctx.data_store.get("github_agent_config")
        if settings:
            self._gh_config = GithubAgentConfig.from_dict(settings)
        else:
            self._gh_config = GithubAgentConfig()

        if self._gh_config.github_pat:
            config_dict = {
                "github_pat": self._gh_config.github_pat,
                "github_username": self._gh_config.github_username,
                "admin_user_id": self._gh_config.admin_user_id,
                "repo": self._gh_config.repo,
                "webhook_secret": self._gh_config.webhook_secret,
                "auto_label": self._gh_config.auto_label,
                "auto_comment": self._gh_config.auto_comment,
                "auto_review": self._gh_config.auto_review,
                "review_mode": self._gh_config.review_mode,
                "workspace_dir": str(self._gh_config.workspace_dir),
                "webhook_public_url": self._gh_config.webhook_public_url,
                "console_viewer_enabled": self._gh_config.console_viewer_enabled,
                "console_viewer_keep_open": self._gh_config.console_viewer_keep_open,
            }

            try:
                runner, port = await start_webhook_server(
                    host="127.0.0.1",
                    port=self._gh_config.webhook_port,
                    config=config_dict,
                    adapter=self.ctx.adapter,
                    engine_proxy=self.ctx.engine_proxy,
                    data_store=self.ctx.data_store,
                )
                self._webhook_runner = runner
                self._webhook_port = port
                logger.info("GitHub Webhook 服务已启动: 127.0.0.1:%s", port)
            except Exception as exc:
                logger.warning("Webhook 服务启动失败（不影响插件运行）: %s", exc)

    async def on_unload(self) -> None:
        """停止 Webhook 服务，释放资源。"""
        if self._webhook_runner:
            try:
                await self._webhook_runner.cleanup()
                logger.info("Webhook 服务已停止")
            except Exception as exc:
                logger.warning("停止 Webhook 服务时出错: %s", exc)

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
        """执行单行 Python 代码，捕获 stdout 并返回执行结果。"""
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
        examples=["/gh <task_id> auto", "/gh review 1 quick"],
    )
    async def github_agent(self, command_args: str = "") -> PluginResponse:
        """处理 GitHub Agent 相关指令。"""
        if not self._gh_config or not self._gh_config.github_pat:
            return PluginResponse.fail("GitHub Agent 未配置，请先设置 github_pat")

        config_dict = {
            "github_pat": self._gh_config.github_pat,
            "github_username": self._gh_config.github_username,
            "admin_user_id": self._gh_config.admin_user_id,
            "repo": self._gh_config.repo,
            "max_retries": self._gh_config.max_retries,
            "test_command": self._gh_config.test_command,
            "workspace_dir": str(self._gh_config.workspace_dir),
            "webhook_secret": self._gh_config.webhook_secret,
            "console_viewer_enabled": self._gh_config.console_viewer_enabled,
            "console_viewer_keep_open": self._gh_config.console_viewer_keep_open,
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
