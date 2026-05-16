"""LLM 驱动的信息收集与分析。

analyze_and_gather() 检查 Issue 的对话历史，判断信息是否充分，
不足则生成追问，就绪则输出理解和方案。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


async def analyze_and_gather(
    state: Any,
    engine_proxy: Any,
) -> dict[str, Any]:
    """分析 Issue 对话，判定信息就绪度。

    Returns:
        {
            "ready": bool,
            "understanding": str,     # 模型对问题的理解
            "approach": str,          # 提议的修复方案（仅 ready=true）
            "question": str,          # 追问内容（仅 ready=false）
        }
    """
    prompt = _build_gather_prompt(state)
    result_text = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result_text = await engine_proxy.generate_raw(prompt, inject_persona=False)
            result_text = result_text.strip()
            break
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                logger.info("gather 第 %d/%d 次失败: %s", attempt, _MAX_RETRIES, exc)
            else:
                logger.error("gather %d 次全部失败", _MAX_RETRIES)

    if not result_text:
        return _fallback_ready(state)

    parsed = _parse_gather_result(result_text)
    if parsed is None:
        return _fallback_ready(state)
    return parsed


def _build_gather_prompt(state: Any) -> str:
    conv_lines: list[str] = []
    for m in state.conversation[-10:]:
        role_label = "用户" if m["role"] == "user" else "AI"
        conv_lines.append(f"[{role_label}] {m['content'][:800]}")
    conv_text = "\n\n".join(conv_lines) if conv_lines else "（暂无对话）"

    return f"""你正在分析一个 GitHub Issue，判断目前掌握的信息是否足以开始修复。

判断标准：
1. 问题描述是否足够清晰（症状、期望行为、复现步骤）
2. 如果涉及代码错误，是否有错误日志/堆栈
3. 如果涉及功能变更，是否有明确的需求边界
4. 交互了几轮后是否已经澄清了核心疑问
5. 如果 issue 打开后没有任何额外互动且描述非常模糊，需要先追问

Issue #{state.issue_number}: {state.title}

对话历史（最近 10 条）:
{conv_text}

已追问次数: {state.questions_asked}

请以严格的 JSON 格式输出，不要包含其他内容：
{{
    "ready": true或false,
    "understanding": "你对问题的理解（1-2句，中文）",
    "approach": "如果 ready=true，简述修复方案（1-2句）；否则留空",
    "question": "如果 ready=false，生成一条将在 Issue 下公开发表的追问（80-150字，友好，一次只问1-2个关键点）；否则留空"
}}"""


def _parse_gather_result(text: str) -> dict[str, Any] | None:
    import json as _json
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = _json.loads(line)
                if isinstance(data, dict) and "ready" in data:
                    return {
                        "ready": bool(data.get("ready", False)),
                        "understanding": str(data.get("understanding", "")),
                        "approach": str(data.get("approach", "")),
                        "question": str(data.get("question", "")),
                    }
            except (_json.JSONDecodeError, ValueError):
                continue
    logger.warning("gather JSON 解析失败: %s", text[:200])
    return None


def _fallback_ready(state: Any) -> dict[str, Any]:
    """LLM 调用失败时，如果已经有对话交互则判定为就绪，否则要求更多信息。"""
    if state.questions_asked >= 1:
        return {
            "ready": True,
            "understanding": state.title,
            "approach": "请评估 Issue 内容后决定修复方案",
            "question": "",
        }
    return {
        "ready": False,
        "understanding": "",
        "approach": "",
        "question": "感谢提交 Issue！为了更好地理解问题，请问可以提供更详细的描述或复现步骤吗？",
    }
