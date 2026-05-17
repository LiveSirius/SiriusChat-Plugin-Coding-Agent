from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class ToolDef:
    """工具定义。"""
    name: str
    description: str
    parameters: dict  # JSON Schema 格式
    handler: Callable[..., Awaitable[Any]]


class ToolRegistry:
    """Agent Loop 内部工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get_schema_list(self) -> list[dict]:
        """生成 OpenAI function calling 格式的工具列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def call(self, name: str, **kwargs: Any) -> str:
        """调用工具并返回结果字符串。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"未知工具: {name}"
        try:
            result = await tool.handler(**kwargs)
            return str(result)
        except Exception as e:
            return f"工具执行失败: {e}"


# ── 工作区路径安全校验 ──

_workspace_root: Path | None = None


def set_workspace_root(root: Path) -> None:
    global _workspace_root
    _workspace_root = root.resolve()


def _validate_path(file_path: str) -> None:
    """确保文件路径在 workspace 内，防止路径穿越攻击。"""
    resolved = Path(file_path).resolve()
    if _workspace_root is None:
        raise RuntimeError("工作区未初始化")
    if not str(resolved).startswith(str(_workspace_root)):
        raise PermissionError(f"禁止访问工作区外的文件: {file_path}")


# ── 4 个工具实现 ──


async def search_content(keyword: str, directory: str = ".") -> str:
    """搜索关键词，返回文件路径与行号。使用 Python 原生实现，无外部依赖。"""
    base = Path(directory).resolve()
    if not base.exists():
        return f"搜索目录不存在: {directory}"
    results: list[str] = []
    noise_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".mypy_cache", ".pytest_cache"}
    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue
        parts = set(file_path.parts) & noise_dirs
        if parts:
            continue
        try:
            if file_path.suffix and file_path.stat().st_size > 256_000:
                continue
        except OSError:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if keyword.lower() in line.lower():
                rel = file_path.relative_to(base)
                results.append(f"{rel}:{line_no}:{line[:200].strip()}")
                if len(results) >= 50:
                    return "\n".join(results) + "\n（结果已截断至 50 条）"
    return "\n".join(results) if results else "未找到匹配结果"


async def read_file_chunk(file_path: str, start_line: int, end_line: int) -> str:
    """按行读取文件片段，防止超大文件撑爆 Token。"""
    _validate_path(file_path)
    lines: list[str] = []
    try:
        async with asyncio.to_thread(_read_lines, file_path, start_line, end_line) as result:
            lines = result
    except Exception as e:
        return f"读取文件失败: {e}"
    return "\n".join(lines)


def _read_lines(file_path: str, start_line: int, end_line: int) -> list[str]:
    result: list[str] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if i > end_line:
                break
            if i >= start_line:
                result.append(f"{i}:{line.rstrip()}")
    return result


async def search_and_replace_block(file_path: str, old_block: str, new_block: str) -> str:
    """精确字符串替换，校验唯一性。"""
    _validate_path(file_path)
    content = Path(file_path).read_text(encoding="utf-8")
    count = content.count(old_block)
    if count == 0:
        return f"错误：未在 {file_path} 中找到目标代码块"
    if count > 1:
        # 列出所有匹配位置便于 LLM 调整搜索块
        positions = []
        idx = 0
        while True:
            pos = content.find(old_block, idx)
            if pos == -1:
                break
            line_no = content[:pos].count("\n") + 1
            positions.append(f"L{line_no}")
            idx = pos + 1
        return f"错误：目标代码块在 {file_path} 中出现 {count} 次（不唯一）。匹配位置：{', '.join(positions)}。请提供更多上下文使匹配唯一。"
    new_content = content.replace(old_block, new_block, 1)
    Path(file_path).write_text(new_content, encoding="utf-8")
    return f"成功替换 {file_path} 中的代码块"


async def run_local_test(test_command: str) -> dict:
    """在沙盒中运行测试命令。"""
    proc = await asyncio.create_subprocess_exec(
        *test_command.split(),
        cwd=str(_workspace_root) if _workspace_root else None,
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


async def tool_done() -> str:
    """声明工作完成，触发验证流程。"""
    return "DONE"


def build_default_registry() -> ToolRegistry:
    """创建并注册 4 个默认工具的 ToolRegistry。"""
    registry = ToolRegistry()

    registry.register(ToolDef(
        name="search_content",
        description="在指定目录中搜索关键词，返回匹配的文件路径与行号",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "directory": {"type": "string", "description": "搜索目录，默认当前目录"},
            },
            "required": ["keyword"],
        },
        handler=search_content,
    ))

    registry.register(ToolDef(
        name="read_file_chunk",
        description="按行读取文件指定片段，防止超大文件撑爆 Token",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行号（从1开始）"},
                "end_line": {"type": "integer", "description": "结束行号"},
            },
            "required": ["file_path", "start_line", "end_line"],
        },
        handler=read_file_chunk,
    ))

    registry.register(ToolDef(
        name="search_and_replace_block",
        description="在文件中精确替换代码块，要求被替换的代码块在文件中唯一",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "old_block": {"type": "string", "description": "要被替换的旧代码块"},
                "new_block": {"type": "string", "description": "替换后的新代码块"},
            },
            "required": ["file_path", "old_block", "new_block"],
        },
        handler=search_and_replace_block,
    ))

    registry.register(ToolDef(
        name="run_local_test",
        description="在工作区中运行测试命令（如 pytest/flake8）并返回结果",
        parameters={
            "type": "object",
            "properties": {
                "test_command": {"type": "string", "description": "测试命令，如 pytest 或 flake8 ."},
            },
            "required": ["test_command"],
        },
        handler=run_local_test,
    ))

    registry.register(ToolDef(
        name="done",
        description="确认所有修改已完成，触发代码提交和 PR 创建流程",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=tool_done,
    ))

    return registry
