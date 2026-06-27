# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学员学习流程相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="enroll_course",
    description="使用报名 ID 或课程标识为当前学员报名指定课程",
    required_servers=["student"],
    return_description="报名结果",
)
async def enroll_course(
    ctx: SkillContext,
    enroll_id: str = "",
    course_identifier: str = "",
) -> dict[str, Any]:
    """为当前登录学员报名课程.

    Args:
        enroll_id: 报名 ID，来自 stu_get_course_structure 返回的 enroll_id。
        course_identifier: 课程访问码、短域名或完整 URL。若未提供 enroll_id，
            Skill 会自动解析课程标识并获取 enroll_id。
    """
    if not enroll_id and not course_identifier:
        return {
            "success": False,
            "data": None,
            "error_code": "MISSING_PARAMETERS",
            "error_message": "需要提供 enroll_id 或 course_identifier 之一",
            "suggested_action": "请提供 enroll_id 或课程访问码/链接",
            "next_action": "needs_user_input",
        }

    if not enroll_id and course_identifier:
        ctx.logger.info("[enroll_course] 未提供 enroll_id，尝试从课程标识解析: %s", course_identifier)
        resolve_result = await ctx.call_tool(
            server="student",
            tool="stu_resolve_course_url",
            arguments={"course_identifier": course_identifier},
        )
        if not resolve_result.get("success"):
            return {
                "success": False,
                "data": resolve_result.get("data"),
                "error_code": resolve_result.get("error_code") or "RESOLVE_COURSE_FAILED",
                "error_message": resolve_result.get("error_message") or "解析课程标识失败",
                "suggested_action": "请确认课程访问码/链接正确",
                "next_action": "needs_user_input",
            }
        resolved = resolve_result.get("data", {})
        group_id = resolved.get("group_id", "")

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
                "suggested_action": "请确认学员已登录且有权限访问该课程",
                "next_action": "retry",
            }
        enroll_id = structure_result.get("data", {}).get("enroll_id", "")
        if not enroll_id and group_id:
            # 兜底：使用 group_id 作为 enroll_id 尝试
            enroll_id = group_id

    if not enroll_id:
        return {
            "success": False,
            "data": None,
            "error_code": "MISSING_ENROLL_ID",
            "error_message": "无法从课程标识中提取报名 ID",
            "suggested_action": "请直接提供 enroll_id",
            "next_action": "needs_user_input",
        }

    ctx.logger.info("[enroll_course] 报名课程，enroll_id: %s", enroll_id)

    result = await ctx.call_tool(
        server="student",
        tool="stu_enroll_course",
        arguments={"enroll_id": enroll_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": None,
            "error_code": result.get("error_code", "ENROLL_FAILED"),
            "error_message": result.get("error_message", "报名失败"),
            "suggested_action": "请确认 enroll_id 正确，且学员未已报名",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "报名成功，可继续完成课程小节",
        "next_action": "proceed",
    }


@skill(
    name="get_course_progress",
    description="获取当前学员在指定课程的学习进度和结构",
    required_servers=["student"],
    return_description="课程结构及完成状态",
)
async def get_course_progress(
    ctx: SkillContext,
    course_identifier: str,
    include_question_preview: bool = False,
) -> dict[str, Any]:
    """获取学员在指定课程的学习进度和结构.

    Args:
        course_identifier: 课程访问码、短域名或完整 URL。
        include_question_preview: 是否包含问卷/考试小节的题目预览。
    """
    ctx.logger.info("[get_course_progress] 查询课程进度: %s", course_identifier)

    # 1. 解析课程标识
    resolve_result = await ctx.call_tool(
        server="student",
        tool="stu_resolve_course_url",
        arguments={"course_identifier": course_identifier},
    )
    if not resolve_result["success"]:
        return {
            "success": False,
            "data": None,
            "error_code": resolve_result.get("error_code", "RESOLVE_FAILED"),
            "error_message": resolve_result.get("error_message", "课程标识解析失败"),
            "suggested_action": "请确认课程访问码/短域名/URL 正确",
            "next_action": "needs_user_input",
        }

    # 2. 获取课程结构和进度
    result = await ctx.call_tool(
        server="student",
        tool="stu_get_course_structure",
        arguments={
            "course_identifier": course_identifier,
            "include_question_preview": include_question_preview,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": None,
            "error_code": result.get("error_code", "GET_PROGRESS_FAILED"),
            "error_message": result.get("error_message", "获取课程进度失败"),
            "suggested_action": "请先确认学员已报名该课程",
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


__all__ = ["enroll_course", "get_course_progress"]
