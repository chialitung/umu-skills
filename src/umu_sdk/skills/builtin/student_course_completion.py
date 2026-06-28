# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Student 课程完成相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill
from .learning_flow import enroll_course


@skill(
    name="complete_entire_course",
    description="自动完成整门课程，包含前置报名（含复杂报名表单）和完成进度验证",
    required_servers=["student"],
    return_description="课程完成结果，包含报名、完成、验证三个阶段信息",
)
async def complete_entire_course(
    ctx: SkillContext,
    course_identifier: str,
    skip_exam: bool = True,
    skip_questionnaire: bool = True,
    questionnaire_answers: str | None = None,
    exam_answers: str | None = None,
    contact_answers: dict[str, str] | None = None,
    section_answers: list[dict[str, Any]] | None = None,
    verify_progress: bool = True,
) -> dict[str, Any]:
    """完成整门课程.

    Args:
        course_identifier: 课程访问码、短域名或 URL。
        skip_exam: 是否跳过考试小节。
        skip_questionnaire: 是否跳过问卷小节。
        questionnaire_answers: 问卷答案配置（可选）。
        exam_answers: 考试答案配置（可选）。
        contact_answers: 报名联系信息答案（可选，复杂报名表单需要）。
        section_answers: 报名问题答案（可选，复杂报名表单需要）。
        verify_progress: 完成后是否调用学习进度接口验证真实完成率。
    """
    ctx.logger.info("[complete_entire_course] 开始学习课程: %s", course_identifier)

    # 1. 获取课程结构，确认报名状态
    structure_result = await ctx.call_tool(
        server="student",
        tool="stu_get_course_structure",
        arguments={"course_identifier": course_identifier},
    )
    if not structure_result.get("success"):
        return {
            "success": False,
            "data": structure_result.get("data"),
            "error_code": structure_result.get("error_code") or "GET_STRUCTURE_FAILED",
            "error_message": structure_result.get("error_message") or "获取课程结构失败",
            "suggested_action": "请确认课程标识正确且学员已登录",
            "next_action": "retry",
        }

    structure_data = structure_result.get("data", {})
    needs_enrollment = structure_data.get("needs_enrollment", False)
    enroll_id = structure_data.get("enroll_id", "")

    enrollment_info: dict[str, Any] = {
        "needed": needs_enrollment,
        "success": True,
        "data": None,
    }

    # 2. 如果需要报名，先完成报名（支持复杂报名表单）
    if needs_enrollment:
        ctx.logger.info("[complete_entire_course] 课程需要报名，先执行报名流程")
        enroll_kwargs: dict[str, Any] = {
            "course_identifier": course_identifier,
            "contact_answers": contact_answers,
            "section_answers": section_answers,
        }
        if enroll_id:
            enroll_kwargs["enroll_id"] = enroll_id

        enroll_result = await enroll_course(ctx, **enroll_kwargs)
        enrollment_info["success"] = enroll_result.get("success", False)
        enrollment_info["data"] = enroll_result.get("data")
        enrollment_info["error_code"] = enroll_result.get("error_code", "")
        enrollment_info["error_message"] = enroll_result.get("error_message", "")

        if not enroll_result.get("success"):
            # 报名需要用户输入时，把表单结构向上传递
            return {
                "success": False,
                "data": {
                    "enrollment": enrollment_info,
                    "completion": None,
                    "verification": None,
                },
                "error_code": enroll_result.get("error_code") or "ENROLLMENT_FAILED",
                "error_message": enroll_result.get("error_message") or "课程报名失败",
                "suggested_action": enroll_result.get("suggested_action")
                or "请检查报名信息后重试",
                "next_action": enroll_result.get("next_action") or "needs_user_input",
            }

    # 3. 完成课程小节
    arguments: dict[str, Any] = {
        "course_identifier": course_identifier,
        "skip_exam": skip_exam,
        "skip_questionnaire": skip_questionnaire,
    }
    if questionnaire_answers:
        arguments["questionnaire_answers"] = questionnaire_answers
    if exam_answers:
        arguments["exam_answers"] = exam_answers

    ctx.logger.info("[complete_entire_course] 调用 stu_complete_course 完成小节")
    result = await ctx.call_tool(
        server="student",
        tool="stu_complete_course",
        arguments=arguments,
    )

    completion_info: dict[str, Any] = {
        "success": result.get("success", False),
        "data": result.get("data"),
        "error_code": result.get("error_code", ""),
        "error_message": result.get("error_message", ""),
    }

    if not result["success"]:
        return {
            "success": False,
            "data": {
                "enrollment": enrollment_info,
                "completion": completion_info,
                "verification": None,
            },
            "error_code": result.get("error_code") or "COMPLETE_COURSE_FAILED",
            "error_message": result.get("error_message") or "课程完成失败",
            "suggested_action": result.get("suggested_action")
            or "请确认课程标识正确且学员已报名",
            "next_action": result.get("next_action") or "needs_enrollment",
        }

    # 4. 验证真实学习进度
    verification_info: dict[str, Any] = {"success": True, "data": None}
    if verify_progress:
        ctx.logger.info("[complete_entire_course] 验证课程完成进度")
        progress_result = await ctx.call_tool(
            server="student",
            tool="stu_get_learning_progress",
            arguments={"course_identifier": course_identifier},
        )
        verification_info["success"] = progress_result.get("success", False)
        verification_info["data"] = progress_result.get("data")
        verification_info["error_code"] = progress_result.get("error_code", "")
        verification_info["error_message"] = progress_result.get("error_message", "")

    completion_data = result.get("data", {})
    verified_rate = None
    if verification_info.get("data"):
        verified_rate = verification_info["data"].get("complete_rate")

    return {
        "success": True,
        "data": {
            "enrollment": enrollment_info,
            "completion": completion_info,
            "verification": verification_info,
            "completed_lessons": completion_data.get("completed_lessons", 0),
            "total_lessons": completion_data.get("total_lessons", 0),
            "progress_percentage": completion_data.get("progress_percentage", 0),
            "verified_complete_rate": verified_rate,
        },
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "lesson_completed",
    }


@skill(
    name="learn_course",
    description="端到端学习课程：报名（含复杂报名表单）+ 完成所有小节 + 验证进度",
    required_servers=["student"],
    return_description="完整学习报告",
)
async def learn_course(
    ctx: SkillContext,
    course_identifier: str,
    skip_exam: bool = True,
    skip_questionnaire: bool = True,
    questionnaire_answers: str | None = None,
    exam_answers: str | None = None,
    contact_answers: dict[str, str] | None = None,
    section_answers: list[dict[str, Any]] | None = None,
    verify_progress: bool = True,
) -> dict[str, Any]:
    """一键完成报名并学习整门课程.

    Args:
        course_identifier: 课程访问码、短域名或 URL。
        skip_exam: 是否跳过考试小节。
        skip_questionnaire: 是否跳过问卷小节。
        questionnaire_answers: 问卷答案配置（可选）。
        exam_answers: 考试答案配置（可选）。
        contact_answers: 报名联系信息答案（可选）。
        section_answers: 报名问题答案（可选）。
        verify_progress: 完成后是否验证真实进度。
    """
    ctx.logger.info("[learn_course] 端到端学习课程: %s", course_identifier)

    # 直接复用 complete_entire_course 的完整流程
    result = await complete_entire_course(
        ctx,
        course_identifier=course_identifier,
        skip_exam=skip_exam,
        skip_questionnaire=skip_questionnaire,
        questionnaire_answers=questionnaire_answers,
        exam_answers=exam_answers,
        contact_answers=contact_answers,
        section_answers=section_answers,
        verify_progress=verify_progress,
    )

    # 如果成功，在 data 上增加一个顶层标记便于识别
    if result.get("success") and isinstance(result.get("data"), dict):
        result["data"]["course_identifier"] = course_identifier

    return result


__all__ = ["complete_entire_course", "learn_course"]
