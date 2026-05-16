"""LLM 驱动的信息收集与分析。

analyze_and_gather() 检查 Issue 对话历史，判断信息充分度。
支持三类决策：ask（追问）、ready（就绪）、close（关单）。
支持请求查看指定文件以辅助分析。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


async def analyze_and_gather(
    state: Any,
    engine_proxy: Any,
    code_context: dict[str, str] | None = None,
    model: str = "",
) -> dict[str, Any]:
    """分析 Issue 对话，判定下一步动作。

    Args:
        state: IssueState 实例
        engine_proxy: LLM 引擎代理
        code_context: {path: content} 额外代码上下文

    Returns:
        {
            "action": "ask" | "ready" | "close",
            "understanding": str,
            "approach": str,
            "question": str,
            "close_reason": str,
            "look_at_files": [str],
        }
    """
    prompt = _build_gather_prompt(state, code_context)
    logger.info("gather: Issue #%d 分析中 (conv=%d, q=%d, code_ctx=%d files)",
                 state.issue_number, len(state.conversation), state.questions_asked,
                 len(code_context) if code_context else 0)
    result_text = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result_text = await engine_proxy.generate_raw(prompt, inject_persona=False, model=model, json_mode=True)
            result_text = result_text.strip()
            break
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                logger.info("gather 第 %d/%d 次失败: %s", attempt, _MAX_RETRIES, exc)
            else:
                logger.error("gather %d 次全部失败", _MAX_RETRIES)

    if not result_text:
        return _fallback_result(state)

    parsed = _parse_gather_result(result_text)
    if parsed is None:
        return _fallback_result(state)
    return parsed


def _build_gather_prompt(state: Any, code_context: dict[str, str] | None = None) -> str:
    conv_lines: list[str] = []
    for m in state.conversation:
        role_label = "用户" if m["role"] == "user" else "AI"
        conv_lines.append(f"[{role_label}] {m['content'][:800]}")
    conv_text = "\n\n".join(conv_lines) if conv_lines else "（暂无对话）"

    code_section = ""
    if code_context:
        code_parts = []
        for path, content in code_context.items():
            code_parts.append(f"```\n# {path}\n{content[:3000]}\n```")
        code_section = "\n\n已查看的代码文件:\n" + "\n\n".join(code_parts)

    return f"""你正在管理一个 GitHub Issue。请判断下一步应该做什么。

Issue #{state.issue_number}: {state.title}

对话历史（最近 10 条）:
{conv_text}

已追问次数: {state.questions_asked}
{code_section}

请选择以下 action 之一：
- "ask": 信息不足，需要追问用户
- "ready": 信息充足，可以开始修复
- "close": 应关闭此 Issue（已解决、重复、无效、用户放弃等）

关闭判定标准：
1. 用户在对话中明确表示问题已解决或无需求
2. Issue 本身是重复的，已在其他 Issue 中处理
3. 用户多次不回复且信息始终不足（已追问 >= 2 次）
4. Issue 明确属于项目范围外且无法转为有效需求
5. 纯垃圾/无效内容（注意：closer.py 已经处理了明显的垃圾，
   这里主要关注对话过程中发现的应关闭情形）

如果需要查看特定文件来辅助理解问题，设置 look_at_files。
每轮最多请求 3 个文件，路径相对于仓库根目录（如 "src/main.py"）。

请以严格的 JSON 格式输出：
{{
    "action": "ask" | "ready" | "close",
    "understanding": "你对问题的理解（1-2句中文）",
    "approach": "如果 action=ready，简述修复方案；否则留空",
    "question": "如果 action=ask，生成追问（80-150字，友好）；否则留空",
    "close_reason": "如果 action=close，关闭理由（50字内）；否则留空",
    "look_at_files": ["path/to/file.py", ...]
}}"""


def _parse_gather_result(text: str) -> dict[str, Any] | None:
    import json as _json

    stripped = text.strip()

    # 1. 尝试提取 markdown 代码块中的 JSON
    if "```json" in stripped:
        start = stripped.index("```json") + 7
        end = stripped.find("```", start)
        if end > start:
            stripped = stripped[start:end].strip()
    elif "```" in stripped:
        start = stripped.index("```") + 3
        end = stripped.find("```", start)
        if end > start:
            stripped = stripped[start:end].strip()

    # 2. 尝试将整个文本解析为 JSON（多行 JSON 对象）
    if stripped.startswith("{"):
        # 找到最外层闭合的 }
        depth = 0
        end_idx = -1
        for i, ch in enumerate(stripped):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break
        if end_idx > 0:
            candidate = stripped[:end_idx]
            try:
                data = _json.loads(candidate)
                if isinstance(data, dict) and "action" in data:
                    return {
                        "action": str(data.get("action", "ask")),
                        "understanding": str(data.get("understanding", "")),
                        "approach": str(data.get("approach", "")),
                        "question": str(data.get("question", "")),
                        "close_reason": str(data.get("close_reason", "")),
                        "look_at_files": _parse_file_list(data.get("look_at_files", [])),
                    }
            except (_json.JSONDecodeError, ValueError):
                pass

    # 3. 逐行回退（单行 JSON）
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = _json.loads(line)
                if isinstance(data, dict) and "action" in data:
                    return {
                        "action": str(data.get("action", "ask")),
                        "understanding": str(data.get("understanding", "")),
                        "approach": str(data.get("approach", "")),
                        "question": str(data.get("question", "")),
                        "close_reason": str(data.get("close_reason", "")),
                        "look_at_files": _parse_file_list(data.get("look_at_files", [])),
                    }
            except (_json.JSONDecodeError, ValueError):
                continue
    logger.warning("gather JSON 解析失败: %s", text[:200])
    return None


def _parse_file_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lstrip("/") for v in value if str(v).strip()][:3]
    return []


def _fallback_result(state: Any) -> dict[str, Any]:
    if state.questions_asked >= 1:
        return {
            "action": "ready",
            "understanding": state.title,
            "approach": "请评估 Issue 内容后决定修复方案",
            "question": "",
            "close_reason": "",
            "look_at_files": [],
        }
    return {
        "action": "ask",
        "understanding": "",
        "approach": "",
        "question": "感谢提交 Issue！为了更好地理解问题，请问可以提供更详细的描述或复现步骤吗？",
        "close_reason": "",
        "look_at_files": [],
    }
