from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .api import create_pr, fork_repo, sync_fork
from .skills import (
    ToolRegistry,
    build_default_registry,
    set_workspace_root,
)
from .stream_writer import StreamWriter

logger = logging.getLogger(__name__)

_VIEWER_SCRIPT = "console_viewer.py"
_CHANGELOG_RETRIES = 3


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
        logger.info("分支 %s 已存在，切换到该分支", fix_branch)
        repo.git.checkout(fix_branch)

    return task_dir


def _tool_schema_text(tool_registry: ToolRegistry) -> str:
    """将工具注册表转为纯文本描述，嵌入 System Prompt。"""
    lines = []
    for schema in tool_registry.get_schema_list():
        func = schema["function"]
        name = func["name"]
        desc = func["description"]
        params = func["parameters"]
        props = params.get("properties", {})
        required = params.get("required", [])

        lines.append(f"  - {name}: {desc}")
        for p_name, p_info in props.items():
            p_type = p_info.get("type", "any")
            p_desc = p_info.get("description", "")
            req_mark = " [必填]" if p_name in required else ""
            lines.append(f"      参数 {p_name} ({p_type}){req_mark}: {p_desc}")
    return "\n".join(lines)


def build_system_prompt(tool_registry: ToolRegistry, workspace_dir: Path) -> str:
    """构建 Agent 的系统 Prompt。人格属性由 generate_raw(inject_persona=True) 自动注入。"""
    tool_schema = _tool_schema_text(tool_registry)

    return f"""你是一名资深软件工程师，正在通过 tool calling 修复一个 GitHub Issue。请以你的角色身份和沟通风格来完成以下工作。

工作区路径：{workspace_dir}

## 可用工具

{tool_schema}

## 工具调用规则

当你需要执行操作时，请输出严格的 JSON 格式工具调用，每行一个：
```json
{{"tool": "工具名", "args": {{"参数1": "值1", ...}}}}
```

然后我会执行工具并返回结果给你。你可以连续输出多个工具调用。

当你完成所有修改、确认不再需要调用工具时，输出：
```json
{{"status": "done"}}
```

## 工作流程
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
    stream: StreamWriter | None = None,
    config: dict | None = None,
) -> list[dict]:
    """调用 LLM（prompt 中已嵌入工具定义），执行工具调用循环直到 LLM 输出 done。

    返回增强后的 messages 列表。
    """
    max_tool_rounds = 15
    model = (config or {}).get("model", "") or None

    for _round in range(max_tool_rounds):
        system_prompt = messages[0]["content"] if messages else ""
        existing = messages[1:-1]  # system 之后、最后一条 user 之前的会话历史
        user_prompt = messages[-1]["content"] if messages else ""

        result_text = await engine_proxy.generate_raw(
            prompt=user_prompt,
            system_prompt=system_prompt,
            messages=existing,
            inject_persona=True,
            model=model,
            task_name="plugin_raw",
        )

        if stream:
            stream.think(result_text)

        # 尝试解析 JSON 工具调用
        tool_call = _parse_tool_call(result_text)
        if tool_call is None:
            # 没有工具调用 → 视为 LLM 在思考/分析，进入验证阶段
            messages.append({"role": "assistant", "content": result_text})
            break

        if tool_call.get("status") == "done":
            # LLM 宣布工作完成
            messages.append({"role": "assistant", "content": result_text})
            break

        # 执行工具
        tool_name = tool_call.get("tool", "")
        tool_args = tool_call.get("args", {})
        if stream:
            stream.tool_call(tool_name, tool_args)

        result_str = await tool_registry.call(tool_name, **tool_args)
        if stream:
            stream.tool_result(tool_name, result_str)

        # 将工具调用和结果加入消息历史
        messages.append({
            "role": "assistant",
            "content": f"调用工具 {tool_name}，参数：{json.dumps(tool_args, ensure_ascii=False)}",
        })
        messages.append({
            "role": "user",
            "content": f"工具 {tool_name} 返回：\n{result_str}\n\n请根据结果继续分析或进行下一步操作。如果工作完成，输出 {{\"status\": \"done\"}}。",
        })

    else:
        logger.warning("工具调用轮数达到上限 %d，强制终止", max_tool_rounds)

    return messages


def _parse_tool_call(text: str) -> dict | None:
    """从 LLM 输出中解析第一条 JSON 工具调用。"""
    lines = text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if "tool" in obj or "status" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
    return None


async def generate_changelog(diff: str, issue_data: dict, engine_proxy: Any) -> str:
    """使用 LLM 根据 git diff 生成人类可读的 Changelog。"""
    if not diff.strip():
        return "无文本变更（可能仅修改了二进制文件）。"
    prompt = (
        f"你是一个技术文档撰写者。请根据以下 git diff 生成一份简洁的中文 Changelog。\n\n"
        f"Issue: #{issue_data.get('number', '?')} - {issue_data.get('title', '')}\n\n"
        f"要求：\n"
        f"1. 以要点列表形式列出每项变更（3-6 条为宜）\n"
        f"2. 每条包含：修改的文件（取 basename）、修改原因、影响\n"
        f"3. 使用 Markdown 格式（每行以 - 开头）\n"
        f"4. 不需要评价代码质量，只描述事实\n"
        f"5. 禁止输出 JSON，直接输出 Markdown 要点\n\n"
        f"Git Diff:\n{diff[:6000]}"
    )
    last_error = None
    for attempt in range(1, _CHANGELOG_RETRIES + 1):
        try:
            result = await engine_proxy.generate_raw(prompt, inject_persona=True)
            return result.strip()
        except Exception as exc:
            last_error = exc
            if attempt < _CHANGELOG_RETRIES:
                logger.info(
                    "Changelog 生成第 %d/%d 次失败，重试中: %s",
                    attempt, _CHANGELOG_RETRIES, exc,
                )
            else:
                logger.error(
                    "Changelog 生成 %d 次重试全部失败: %s",
                    _CHANGELOG_RETRIES, exc,
                )

    raise RuntimeError(
        f"Issue #{issue_data.get('number', '?')} Changelog 生成失败（{_CHANGELOG_RETRIES}次重试）"
    ) from last_error


async def run_agent_loop(
    task_data: dict,
    config: dict,
    engine_proxy: Any,
    adapter: Any | None = None,
) -> str:
    """完整的 agent 修复管线：工作区 → 代码分析 → 修改 → 测试 → PR。

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
        system_prompt = build_system_prompt(tool_registry, workspace_dir)
        user_message = f"Issue #{issue_data['number']}: {issue_data['title']}\n\n{issue_data.get('body', '')}"

        stream.phase("ANALYSIS", "开始代码检索与定位...")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        messages = await call_llm_with_tools(messages, tool_registry, engine_proxy, stream, config)

        # 验证阶段：flake8 → pytest，失败则重试
        max_retries = config.get("max_retries", 3)
        tests_passed = False

        for attempt in range(1, max_retries + 1):
            stream.phase("VALIDATION", f"第 {attempt} 轮验证")

            lint_result = await _run_test_cmd("flake8 .", workspace_dir)
            stream.test_run("flake8 .", lint_result["success"], lint_result.get("stdout", ""), lint_result.get("stderr", ""))

            if not lint_result["success"]:
                if attempt < max_retries:
                    stream.retry(attempt, max_retries, lint_result.get("stderr", ""))
                    messages.append({
                        "role": "user",
                        "content": f"静态检查失败（第{attempt}次）:\n{lint_result['stderr']}\n请修复代码风格/语法问题。",
                    })
                    messages = await call_llm_with_tools(messages, tool_registry, engine_proxy, stream, config)
                    continue
                stream.error("静态检查未通过，已达重试上限")
                stream.done(success=False, summary="flake8 检查未通过")
                return "MAX_RETRIES_EXCEEDED"

            test_result = await _run_test_cmd(config.get("test_command", "pytest"), workspace_dir)
            stream.test_run(config["test_command"], test_result["success"], test_result.get("stdout", ""), test_result.get("stderr", ""))

            if test_result["success"]:
                tests_passed = True
                break

            if attempt < max_retries:
                stream.retry(attempt, max_retries, test_result.get("stderr", ""))
                messages.append({
                    "role": "user",
                    "content": f"测试失败（第{attempt}次）:\n{test_result['stderr']}\n请分析错误并修复。",
                })
                messages = await call_llm_with_tools(messages, tool_registry, engine_proxy, stream, config)
            else:
                stream.error(f"达到最大重试次数 ({max_retries})，修复失败")
                stream.done(success=False, summary="测试未通过，已达重试上限")
                return "MAX_RETRIES_EXCEEDED"

        if not tests_passed:
            stream.done(success=False, summary="修复失败")
            return "FAILED"

        # 测试通过 → 提交 PR
        stream.phase("COMMIT", "测试通过，开始提交与 PR...")
        pr_url = await _finalize_and_create_pr(
            workspace_dir=workspace_dir,
            repo_name=task_data["repo"],
            issue_number=task_data["issue_number"],
            config=config,
            engine_proxy=engine_proxy,
            issue_data=issue_data,
            adapter=adapter,
        )
        stream.done(success=True, summary="PR 已创建", pr_url=pr_url)
        return "SUCCESS"

    except Exception as exc:
        logger.exception("Agent loop 异常")
        if "stream" in locals():
            stream.error(str(exc))
            stream.done(success=False, summary=f"异常终止: {exc}")
        return "ERROR"

    finally:
        if "stream" in locals():
            stream.close()
        if viewer_process:
            try:
                viewer_process.wait(timeout=2)
            except Exception:
                pass


async def _run_test_cmd(test_command: str, workspace_dir: Path) -> dict:
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


async def _finalize_and_create_pr(
    workspace_dir: Path,
    repo_name: str,
    issue_number: int,
    config: dict,
    engine_proxy: Any,
    issue_data: dict,
    adapter: Any | None = None,
) -> str:
    """提交代码并创建 Pull Request。返回 PR URL。"""
    from git import Repo

    repo = Repo(workspace_dir)
    repo.git.add(".")

    # 设置仓库级 git 用户身份，确保 GitHub 将提交归因于 AI 账户而非本地用户
    username = config.get("github_username", "")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", username)
        cw.set_value("user", "email", f"{username}@users.noreply.github.com")

    issue_title = issue_data.get("title", f"Fix issue #{issue_number}")
    repo.index.commit(f"Auto-fix issue #{issue_number}: {issue_title[:60]}")

    repo.git.push("origin", f"fix-issue-{issue_number}")

    pr_title = f"Fix #{issue_number}: {issue_title[:72]}"
    diff_full = repo.git.diff("main")
    changelog = await generate_changelog(diff_full[:6000], issue_data, engine_proxy)
    pr_body = f"## 自动修复\n\n### 变更摘要\n{changelog}\n\nCloses #{issue_number}"

    pr_result = await create_pr(
        repo_name,
        pr_title,
        pr_body,
        f"{username}:fix-issue-{issue_number}",
        "main",
        config,
    )
    pr_url = pr_result.get("html_url", "") if pr_result else ""

    admin_id = _resolve_admin_id(adapter)
    if adapter and admin_id:
        await adapter.send_private_message(
            admin_id,
            f"修复完成，PR 已创建：{pr_url}",
        )

    shutil.rmtree(workspace_dir, ignore_errors=True)
    return pr_url


def _resolve_admin_id(adapter: Any | None) -> str:
    """从 adapter 读取 root 用户 ID。"""
    if adapter is None:
        return ""
    plugin_config = getattr(adapter, "plugin_config", None)
    if isinstance(plugin_config, dict):
        return str(plugin_config.get("root", "")).strip()
    return ""