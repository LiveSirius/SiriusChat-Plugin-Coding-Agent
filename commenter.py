from __future__ import annotations

import logging
from typing import Any

from .api import post_issue_comment

logger = logging.getLogger(__name__)


async def generate_issue_comment(
    issue_data: dict,
    labels: list[str],
    repo_full_name: str,
    engine_proxy: Any,
    config: dict | None = None,
) -> str:
    """使用 LLM 生成 Issue 智能回复评论，回复风格符合当前人格属性。"""
    label_display = " ".join(f"`{l}`" for l in labels) if labels else "（待人工分类）"

    persona = (config or {}).get("persona_info", {})
    persona_section = ""
    if persona.get("name"):
        persona_section = f"\n你的角色身份是「{persona['name']}」。"
        if persona.get("persona_summary"):
            persona_section += f"\n角色简介：{persona['persona_summary']}"
        if persona.get("personality_traits"):
            traits = "、".join(persona["personality_traits"]) if isinstance(persona["personality_traits"], list) else persona["personality_traits"]
            persona_section += f"\n性格特征：{traits}"
        if persona.get("communication_style"):
            persona_section += f"\n沟通风格：{persona['communication_style']}\n"
            persona_section += "\n请严格按照沟通风格的要求来撰写回复，回复的口吻和表达方式必须与角色一致。评论末尾署名使用角色名称。"
        persona_section += "\n请严格按照该角色的身份、性格和沟通风格撰写回复，让回复看起来就是该角色本人写的。"

    prompt = f"""你正在以指定角色身份回复一个 GitHub Issue。回复内容将在 Issue 下公开发表，面向 Issue 提交者。{persona_section}

Issue #{issue_data.get('number', '?')}: {issue_data.get('title', '')}

Issue 内容:
{issue_data.get('body', '')[:3000]}

已自动分析并应用的标签: {label_display}

回复要求：
1. 开头感谢用户提交 Issue
2. 简要复述你理解的问题
3. 如果 issue 描述不够清晰，提出 1-2 个追问帮助澄清
4. 如果 issue 包含了复现步骤/错误日志，肯定用户的详细描述
5. 结尾告知后续流程：标签已自动分析，管理员将评估是否启动自动修复
6. 整体语气必须与你的角色身份一致
7. 长度控制在 100-200 字，不要过长
8. 输出纯文本（Markdown 格式，但不要代码块包裹）

请直接输出评论正文，不要包含任何前缀说明。"""
    try:
        result = await engine_proxy.generate_text_analysis(prompt)
        return result.strip()
    except Exception:
        issue_title = issue_data.get("title", "未知")
        issue_number = issue_data.get("number", "?")
        return (
            f"感谢提交 Issue #{issue_number}：{issue_title}！\n\n"
            f"已自动分析并应用标签：{label_display}\n\n"
            f"管理员将尽快评估此 Issue，届时可能启动自动修复流程。感谢你的反馈！"
        )


async def post_comment(
    repo_full_name: str,
    issue_number: int,
    comment_body: str,
    config: dict,
) -> bool:
    """在 Issue 下发表评论。"""
    return await post_issue_comment(repo_full_name, issue_number, comment_body, config)
