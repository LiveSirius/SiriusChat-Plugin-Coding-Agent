from __future__ import annotations

import io
import sys
import traceback

from sirius_chat.plugins import PluginBase, PluginResponse
from sirius_chat.plugins.decorators import command


class CodingAgentPlugin(PluginBase):
    _plugin_name = "coding_agent"
    _plugin_display_name = "编码助手"
    _plugin_description = "执行 Python 代码片段并返回结果"
    _plugin_version = "0.1.0"
    _plugin_author = "SiriusChat"

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
