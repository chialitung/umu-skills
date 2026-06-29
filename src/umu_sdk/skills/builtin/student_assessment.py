# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Student 问卷/考试相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_questionnaire",
    description="获取问卷小节的题目",
    required_capabilities=["learning"],
    return_description="问卷题目列表",
)
async def get_questionnaire(
    ctx: SkillContext,
    element_id: str,
) -> dict[str, Any]:
    """获取问卷题目."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="get_questionnaire_questions",
        arguments={"element_id": element_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_QUESTIONNAIRE_FAILED",
            "error_message": result.get("error_message") or "问卷题目获取失败",
            "suggested_action": "请确认 element_id 对应问卷小节",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


@skill(
    name="submit_questionnaire",
    description="提交问卷小节答案（JSON 格式）",
    required_capabilities=["learning"],
    return_description="提交结果",
)
async def submit_questionnaire(
    ctx: SkillContext,
    element_id: str,
    answers_json: str,
) -> dict[str, Any]:
    """提交问卷."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_questionnaire",
        arguments={"element_id": element_id, "answers_json": answers_json},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_QUESTIONNAIRE_FAILED",
            "error_message": result.get("error_message") or "问卷提交失败",
            "suggested_action": "请检查 answers_json 格式",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


@skill(
    name="submit_questionnaire_simple",
    description="使用简化配置提交问卷小节答案",
    required_capabilities=["learning"],
    return_description="提交结果",
)
async def submit_questionnaire_simple(
    ctx: SkillContext,
    element_id: str,
    answers_config: str,
) -> dict[str, Any]:
    """使用配置格式提交问卷."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_questionnaire_with_config",
        arguments={"element_id": element_id, "answers_config": answers_config},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_QUESTIONNAIRE_FAILED",
            "error_message": result.get("error_message") or "问卷提交失败",
            "suggested_action": "请检查 answers_config 格式（如 A;BCD;开放答案）",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


@skill(
    name="start_exam",
    description="开始考试小节",
    required_capabilities=["learning"],
    return_description="考试提交 ID 等信息",
)
async def start_exam(
    ctx: SkillContext,
    element_id: str,
) -> dict[str, Any]:
    """开始考试."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="start_exam",
        arguments={"element_id": element_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "START_EXAM_FAILED",
            "error_message": result.get("error_message") or "考试开始失败",
            "suggested_action": "请确认 element_id 对应考试小节",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


@skill(
    name="submit_exam",
    description="提交考试答案（JSON 格式）",
    required_capabilities=["learning"],
    return_description="提交结果",
)
async def submit_exam(
    ctx: SkillContext,
    element_id: str,
    exam_submit_id: str,
    answers_json: str = "{}",
) -> dict[str, Any]:
    """提交考试."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_exam",
        arguments={
            "element_id": element_id,
            "exam_submit_id": exam_submit_id,
            "answers_json": answers_json,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_EXAM_FAILED",
            "error_message": result.get("error_message") or "考试提交失败",
            "suggested_action": "请检查 answers_json 格式",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


@skill(
    name="submit_exam_simple",
    description="使用简化配置提交考试答案",
    required_capabilities=["learning"],
    return_description="提交结果",
)
async def submit_exam_simple(
    ctx: SkillContext,
    element_id: str,
    answers_config: str,
) -> dict[str, Any]:
    """使用配置格式提交考试."""
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_exam_with_config",
        arguments={"element_id": element_id, "answers_config": answers_config},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_EXAM_FAILED",
            "error_message": result.get("error_message") or "考试提交失败",
            "suggested_action": "请检查 answers_config 格式（如 A;BCD;开放答案）",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


__all__ = [
    "get_questionnaire",
    "submit_questionnaire",
    "submit_questionnaire_simple",
    "start_exam",
    "submit_exam",
    "submit_exam_simple",
]
