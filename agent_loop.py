from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .api import create_pr, fork_repo, sync_fork, _headers
from .config import GithubAgentConfig
from .skills import (
    ToolDef,
    ToolRegistry,
    build_default_registry,
    set_workspace_root,
)
from .stream_writer import StreamWriter

logger = logging.getLogger(__name__)

_VIEWER_SCRIPT = "console_viewer.py"


def _launch_console_viewer(stream_file: Path, keep_open: bool = False) -> subprocess.Popen | None:
    """在独立 CMD 窗口中启动 console_viewer.py。仅 Windows。"""
    if sys.platform != "win32":
        return None

    viewer_script = Path(__file__).resolve().parent / _VIEWER_SCRIPT
    if not viewer_script.exists():
        return None

    try:
        args = [
            "cmd", "/c", "start", "Sirius GitHub Agent",
            "python", str(viewer_script), str(stream_file),
        ]
        if keep_open:
            args.append("--keep-open")
        proc = subprocess.Popen(args)
        return proc
    except Exception as exc:
        logger.warning("无法启动 console viewer: %s", exc)
        return None


async def prepare_workspace(repo_name: str, issue_number: int, config: dict) -> Path:
    """准备本地工作区：Fork → Sync → Clone → 创建分支。"""
    workspace_root = Path(config["workspace_dir"])
    task_dir = workspace_root / f"task_{issue_number}"
    task_dir.mkdir(parents=True, exist_ok=True)

    from git import Repo

    username = config.get("github_username", "")

    # 1. Fork（幂等）
    await fork_repo(repo_name, config)

    # 2. Sync upstream
    await sync_fork(repo_name, config)

    # 3. Clone
    pat = config.get("github_pat", "")
    fork_url = f"https://{pat}@github.com/{username}/{repo_name.split('/')[-1]}.git"
    if not (task_dir / ".git").exists():
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", fork_url, str(task_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

    # 4. 创建修复分支
    repo = Repo(task_dir)
    fix_branch = f"fix-issue-{issue_number}"
    try:
        repo.git.checkout("-b", fix_branch)
    except Exception:
        repo.git.checkout(fix_branch)

    return task_dir


def build_system_prompt(tool_registry: ToolRegistry, workspace_dir: Path, config: dict | None = None) -> str:
    """构建 Agent 的系统 Prompt。包含角色定义和可用工具说明。"""
    persona = (config or {}).get("persona_info", {})
    persona_section = ""
    if persona.get("name"):
        persona_section = f"\n你当前的角色身份是「{persona['name']}」，请以 {persona['name']} 的身份和风格来完成修复。"
        if persona.get("persona_summary"):
            persona_section += f"\n角色简介：{persona['persona_summary']}"
        if persona.get("personality_traits"):
            traits = "、".join(persona["personality_traits"]) if isinstance(persona["personality_traits"], list) else persona["personality_traits"]
            persona_section += f"\n性格特征：{traits}"
        if persona.get("communication_style"):
            persona_section += f"\n沟通风格：{persona['communication_style']}"
        persona_section += "\n\n你的代码修改风格和问题分析方式应体现该角色的特点。PR 描述、commit message 的措辞也要符合角色风格。\n"

    return f"""你是一名资深软件工程师，正在通过 tool calling 修复一个 GitHub Issue。{persona_section}

工作区路径：{workspace_dir}

你可以使用以下工具：
- search_content(keyword, directory)：全局搜索关键词
- read_file_chunk(file_path, start_line, end_line)：按行读取文件
- search_and_replace_block(file_path, old_block, new_block)：精确替换代码块
- run_local_test(test_command)：运行测试（如 pytest）

工作流程：
1. 先用 search_content 定位相关代码
2. 用 read_file_chunk 查看上下文
3. 用 search_and_replace_block 进行修改
4. 修改完毕后先运行 run_local_test("flake8 .") 做静态检查
5. 静态检查通过后运行 run_local_test("pytest") 做单元测试
6. 如果任何检查失败，分析错误并继续修改"""


async def call_llm_with_tools(
    messages: list[dict],
    tool_registry: ToolRegistry,
    engine_proxy: Any,
) -> Any:
    """调用 LLM，附带工具定义。等待工具调用结果后返回。"""
    tools = tool_registry.get_schema_list()
    prompt = messages[-1]["content"] if messages else ""

    if not tools:
        return await engine_proxy.generate_text(prompt)

    result_text = await engine_proxy.generate_text(prompt)

    class MockResponse:
        def __init__(self, text: str):
            self.content = text
            self.thinking = ""
            self.tool_calls = []

    return MockResponse(result_text)


async def agentic_loop(
    issue_data: dict,
    workspace_dir: Path,
    tool_registry: ToolRegistry,
    engine_proxy: Any,
    config: dict,
    stream: StreamWriter | None = None,
) -> str:
    """核心自治修复循环。返回状态码。"""
    max_retries = config.get("max_retries", 3)

    system_prompt = build_system_prompt(tool_registry, workspace_dir, config)
    user_message = f"Issue #{issue_data.get('number', '?')}: {issue_data.get('title', '')}\n\n{issue_data.get('body', '')}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for attempt in range(1, max_retries + 1):
        # 调用 LLM
        response = await call_llm_with_tools(messages, tool_registry, engine_proxy)

        if stream:
            stream.think(response.content)
            stream.phase("VALIDATION", f"第 {attempt} 轮验证")

        # 无工具调用 → 验证阶段
        lint_result = await _run_local_test_wrapper("flake8 .", workspace_dir)
        if stream:
            stream.test_run("flake8 .", lint_result["success"], lint_result.get("stdout", ""), lint_result.get("stderr", ""))

        if not lint_result["success"]:
            if attempt < max_retries:
                messages.append({
                    "role": "user",
                    "content": f"静态检查失败（第{attempt}次）：\n{lint_result['stderr']}\n请修复代码风格/语法问题。",
                })
                if stream:
                    stream.retry(attempt, max_retries, lint_result.get("stderr", ""))
                continue
            else:
                if stream:
                    stream.error("静态检查未通过，已达重试上限")
                    stream.done(success=False, summary="flake8 检查未通过")
                return "MAX_RETRIES_EXCEEDED"

        test_result = await _run_local_test_wrapper(config.get("test_command", "pytest"), workspace_dir)
        if stream:
            stream.test_run(config["test_command"], test_result["success"], test_result.get("stdout", ""), test_result.get("stderr", ""))

        if test_result["success"]:
            if stream:
                stream.phase("COMMIT", "测试通过，准备提交...")
            return "TESTS_PASSED"

        if attempt < max_retries:
            messages.append({
                "role": "user",
                "content": f"测试失败（第{attempt}次）：\n{test_result['stderr']}\n请分析错误并修复。",
            })
            if stream:
                stream.retry(attempt, max_retries, test_result.get("stderr", ""))
        else:
            if stream:
                stream.error(f"达到最大重试次数 ({max_retries})，修复失败")
                stream.done(success=False, summary="测试未通过，已达重试上限")
            return "MAX_RETRIES_EXCEEDED"

    return "MAX_RETRIES_EXCEEDED"


async def _run_local_test_wrapper(test_command: str, workspace_dir: Path) -> dict:
    """运行测试命令的封装。"""
    proc = await asyncio.create_subprocess_exec(
        *test_command.split(),
        cwd=str(workspace_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return {"success": False, "stdout": "", "stderr": "测试超时（>60秒）"}
    return {
        "success": proc.returncode == 0,
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
    }


async def generate_changelog(diff: str, issue_data: dict, engine_proxy: Any) -> str:
    """使用 LLM 根据 git diff 生成人类可读的 Changelog。"""
    if not diff.strip():
        return "无文本变更（可能仅修改了二进制文件）。"

    prompt = f"""你是一个技术文档撰写者。请根据以下 git diff 生成一份简洁的中文 Changelog。

Issue: #{issue_data.get('number', '?')} - {issue_data.get('title', '')}

要求：
1. 以要点列表形式列出每项变更（3-6 条为宜）
2. 每条包含：修改的文件（取 basename）、修改原因、影响
3. 使用 Markdown 格式（每行以 - 开头）
4. 不需要评价代码质量，只描述事实
5. 禁止输出 JSON，直接输出 Markdown 要点

Git Diff:
{diff[:6000]}"""
    try:
        result = await engine_proxy.generate_text_analysis(prompt)
        return result.strip()
    except Exception:
        lines = []
        for line in diff.split("\n")[:20]:
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 3:
                    lines.append(f"- {parts[2]}")
        return "\n".join(lines) if lines else "（自动生成失败，请查看文件变更统计）"


async def finalize_and_create_pr(
    workspace_dir: Path,
    repo_name: str,
    issue_number: int,
    config: dict,
    engine_proxy: Any,
    issue_data: dict,
    adapter: Any | None = None,
    admin_user_id: str = "",
) -> str:
    """提交代码并创建 Pull Request。返回 PR URL。"""
    from git import Repo

    repo = Repo(workspace_dir)
    repo.git.add(".")

    issue_title = issue_data.get("title", f"Fix issue #{issue_number}")
    repo.index.commit(f"Auto-fix issue #{issue_number}: {issue_title[:60]}")

    username = config.get("github_username", "")
    fork_repo_name = repo_name.split("/")[-1]
    repo.git.push("origin", f"fix-issue-{issue_number}")

    pr_title = f"Fix #{issue_number}: {issue_title[:72]}"
    diff_stat = repo.git.diff("main", "--stat")
    diff_full = repo.git.diff("main")
    changelog = await generate_changelog(diff_full[:6000], issue_data, engine_proxy)
    pr_body = (
        f"## 自动修复\n\n"
        f"### 变更摘要\n{changelog}\n\n"
        f"### 文件变更统计\n```\n{diff_stat}\n```\n\n"
        f"Closes #{issue_number}"
    )

    pr_result = await create_pr(
        repo_name,
        pr_title,
        pr_body,
        f"{username}:fix-issue-{issue_number}",
        "main",
        config,
    )

    pr_url = pr_result.get("html_url", "") if pr_result else ""

    if adapter and admin_user_id:
        await adapter.send_private_message(
            admin_user_id,
            f"修复完成，PR 已创建：{pr_url}",
        )

    shutil.rmtree(workspace_dir, ignore_errors=True)
    return pr_url


async def run_agent_loop(
    task_data: dict,
    config: dict,
    engine_proxy: Any,
    adapter: Any | None = None,
    admin_user_id: str = "",
) -> str:
    """完整的 agent 修复管线，带实时控制台输出。

    Returns:
        状态码: "SUCCESS" | "MAX_RETRIES_EXCEEDED" | "ERROR"
    """
    task_id = task_data["task_id"]

    workspace_root = Path(config.get("workspace_dir", "data/github_workspace"))
    workspace_root.mkdir(parents=True, exist_ok=True)
    stream_file = workspace_root / "logs" / f"agent_{task_id}.stream"
    stream = StreamWriter(stream_file)

    viewer_process = None
    if config.get("console_viewer_enabled", True):
        viewer_process = _launch_console_viewer(stream_file, config.get("console_viewer_keep_open", False))

    try:
        stream.phase("PREPARATION", f"Issue #{task_data['issue_number']}: {task_data['issue_title']}")
        workspace_dir = await prepare_workspace(task_data["repo"], task_data["issue_number"], config)
        set_workspace_root(workspace_dir)

        tool_registry = build_default_registry()

        issue_data = {
            "number": task_data["issue_number"],
            "title": task_data["issue_title"],
            "body": task_data.get("issue_body", ""),
        }

        stream.phase("ANALYSIS", "开始代码检索与定位...")

        result = await agentic_loop(
            issue_data=issue_data,
            workspace_dir=workspace_dir,
            tool_registry=tool_registry,
            engine_proxy=engine_proxy,
            config=config,
            stream=stream,
        )

        if result != "TESTS_PASSED":
            stream.done(success=False, summary=f"修复失败: {result}")
            return result

        stream.phase("COMMIT", "测试通过，开始提交与 PR...")
        pr_url = await finalize_and_create_pr(
            workspace_dir=workspace_dir,
            repo_name=task_data["repo"],
            issue_number=task_data["issue_number"],
            config=config,
            engine_proxy=engine_proxy,
            issue_data=issue_data,
            adapter=adapter,
            admin_user_id=admin_user_id,
        )
        stream.done(success=True, summary="PR 已创建", pr_url=pr_url)
        return "SUCCESS"

    except Exception as exc:
        logger.exception("Agent loop 异常")
        stream.error(str(exc))
        stream.done(success=False, summary=f"异常终止: {exc}")
        return "ERROR"

    finally:
        stream.close()
        if viewer_process:
            try:
                viewer_process.wait(timeout=2)
            except Exception:
                pass
