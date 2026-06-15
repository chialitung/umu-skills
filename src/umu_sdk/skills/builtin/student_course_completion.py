# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Student 课程完成相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="complete_entire_course",
    description="自动完成整门课程（按原子工具能力）",
    required_servers=["student"],
    return_description="课程完成结果",
)
async def complete_entire_course(
    ctx: SkillContext,
    course_identifier: str,
    skip_exam: bool = True,
    skip_questionnaire: bool = True,
    questionnaire_answers: str | None = None,
    exam_answers: str | None = None,
) -> dict[str, Any]:
    """完成整门课程.

    Args:
        course_identifier: 课程访问码、短域名或 URL。
        skip_exam: 是否跳过考试小节。
        skip_questionnaire: 是否跳过问卷小节。
        questionnaire_answers: 问卷答案配置（可选）。
        exam_answers: 考试答案配置（可选）。
    """
    arguments: dict[str, Any] = {
        "course_identifier": course_identifier,
        "skip_exam": skip_exam,
        "skip_questionnaire": skip_questionnaire,
    }
    if questionnaire_answers:
        arguments["questionnaire_answers"] = questionnaire_answers
    if exam_answers:
        arguments["exam_answers"] = exam_answers

    result = await ctx.call_tool(
        server="student",
        tool="stu_complete_course",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "COMPLETE_COURSE_FAILED",
            "error_message": result.get("error_message") or "课程完成失败",
            "suggested_action": "请确认课程标识正确且学员已报名",
            "next_action": "needs_enrollment",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


__all__ = ["complete_entire_course"]
