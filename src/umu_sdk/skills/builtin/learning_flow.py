# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学员学习流程相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


def _is_fully_enrolled(enroll_data: dict[str, Any] | None) -> bool:
    """判断报名数据是否表示已真正报名成功.

    UMU 平台中，stu_enroll_course 可能返回中间状态：
    - is_enrolled == 2 且 pay_status == "success" 表示真正报名成功
    - is_enrolled == 1 或 pay_status == "pay" 仅表示预报名，可能还需填写报名表单
    """
    if not enroll_data:
        return False
    is_enrolled = enroll_data.get("is_enrolled")
    pay_status = enroll_data.get("pay_status")
    return is_enrolled == 2 and pay_status == "success"


def _build_enroll_form_payload(
    course_identifier: str,
    contact_answers: dict[str, str] | None,
    section_answers: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """构造 stu_submit_enroll_form 所需参数."""
    payload: dict[str, Any] = {"course_identifier": course_identifier}
    if contact_answers:
        payload["contact_answers"] = contact_answers
    if section_answers:
        payload["section_answers"] = section_answers
    return payload


@skill(
    name="enroll_course",
    description="为当前学员报名课程，支持需要填写联系信息/单选/多选/开放题的特殊报名表单",
    required_capabilities=["learning"],
    return_description="报名结果，复杂表单需要用户输入时会返回表单结构",
)
async def enroll_course(
    ctx: SkillContext,
    enroll_id: str = "",
    course_identifier: str = "",
    contact_answers: dict[str, str] | None = None,
    section_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """为当前登录学员报名课程.

    Args:
        enroll_id: 报名 ID，来自 stu_get_course_structure 返回的 enroll_id。
        course_identifier: 课程访问码、短域名或完整 URL。若未提供 enroll_id，
            Skill 会自动解析课程标识并获取 enroll_id。
        contact_answers: 联系信息答案，如 {"username": "张三", "mobile": "13800138000"}。
        section_answers: 报名问题答案列表，格式见 stu_submit_enroll_form 说明。
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

    resolved_identifier = course_identifier
    if not enroll_id and course_identifier:
        ctx.logger.info(
            "[enroll_course] 未提供 enroll_id，尝试从课程标识解析: %s", course_identifier
        )
        resolve_result = await ctx.call_capability_tool(
            capability="learning",
            operation="resolve_course_url",
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
        resolved_identifier = course_identifier

        structure_result = await ctx.call_capability_tool(
            capability="learning",
            operation="get_course_structure",
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

    result = await ctx.call_capability_tool(
        capability="learning",
        operation="enroll_course",
        arguments={"enroll_id": enroll_id},
    )
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code", "ENROLL_FAILED"),
            "error_message": result.get("error_message", "报名失败"),
            "suggested_action": "请确认 enroll_id 正确，且学员未已报名",
            "next_action": "retry",
        }

    enroll_data = result.get("data", {})
    if _is_fully_enrolled(enroll_data):
        return {
            "success": True,
            "data": enroll_data,
            "error_code": "",
            "error_message": "",
            "suggested_action": "报名成功，可继续完成课程小节",
            "next_action": "proceed",
        }

    # 预报名或中间状态，检查是否需要填写报名表单
    ctx.logger.info("[enroll_course] 报名状态为中间态，检查是否需要填写报名表单")
    form_result = await ctx.call_capability_tool(
        capability="learning",
        operation="get_enroll_form",
        arguments={"course_identifier": resolved_identifier or enroll_id},
    )
    if not form_result.get("success"):
        # 无法获取表单时，直接返回当前报名结果，由调用方决定
        return {
            "success": True,
            "data": enroll_data,
            "error_code": "ENROLL_INCOMPLETE",
            "error_message": "报名尚未完成，但无法获取报名表单",
            "suggested_action": "请确认课程是否需要填写报名信息，或手动完成报名",
            "next_action": "needs_user_input",
        }

    form_data = form_result.get("data", {})
    contact_fields = form_data.get("contact_fields", [])
    section_questions = form_data.get("section_questions", [])

    # 判断是否存在需要用户填写的必填项
    has_required_contact = any(
        field.get("selected") and field.get("required") for field in contact_fields
    )
    has_required_question = any(
        question.get("required") and question.get("type") != "paragraph"
        for question in section_questions
    )

    if not has_required_contact and not has_required_question:
        # 表单中没有必填项，尝试再次调用 stu_enroll_course 确认
        retry_result = await ctx.call_capability_tool(
            capability="learning",
            operation="enroll_course",
            arguments={"enroll_id": enroll_id},
        )
        if retry_result.get("success"):
            retry_data = retry_result.get("data", {})
            if _is_fully_enrolled(retry_data):
                return {
                    "success": True,
                    "data": retry_data,
                    "error_code": "",
                    "error_message": "",
                    "suggested_action": "报名成功，可继续完成课程小节",
                    "next_action": "proceed",
                }
        return {
            "success": True,
            "data": enroll_data,
            "error_code": "ENROLL_INCOMPLETE",
            "error_message": "报名尚未完成，但报名表单无必填项",
            "suggested_action": "请手动确认报名状态",
            "next_action": "needs_user_input",
        }

    # 需要填写表单
    if contact_answers is None and section_answers is None:
        return {
            "success": False,
            "data": form_data,
            "error_code": "ENROLL_FORM_REQUIRED",
            "error_message": "课程报名需要填写联系信息和/或报名问题",
            "suggested_action": "请提供 contact_answers 和 section_answers 后重新调用",
            "next_action": "needs_user_input",
        }

    ctx.logger.info("[enroll_course] 提交报名表单")
    submit_result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_enroll_form",
        arguments=_build_enroll_form_payload(
            resolved_identifier or enroll_id,
            contact_answers,
            section_answers,
        ),
    )
    if not submit_result.get("success"):
        return {
            "success": False,
            "data": submit_result.get("data"),
            "error_code": submit_result.get("error_code") or "SUBMIT_ENROLL_FORM_FAILED",
            "error_message": submit_result.get("error_message") or "报名表单提交失败",
            "suggested_action": "请检查 contact_answers 和 section_answers 格式",
            "next_action": "needs_user_input",
        }

    # 表单提交后再次确认报名状态
    final_result = await ctx.call_capability_tool(
        capability="learning",
        operation="enroll_course",
        arguments={"enroll_id": enroll_id},
    )
    if not final_result.get("success"):
        return {
            "success": False,
            "data": final_result.get("data"),
            "error_code": final_result.get("error_code") or "FINAL_ENROLL_FAILED",
            "error_message": final_result.get("error_message") or "报名表单提交后确认报名状态失败",
            "suggested_action": "请手动检查报名状态",
            "next_action": "retry",
        }

    final_data = final_result.get("data", {})
    if _is_fully_enrolled(final_data):
        return {
            "success": True,
            "data": final_data,
            "error_code": "",
            "error_message": "",
            "suggested_action": "报名成功，可继续完成课程小节",
            "next_action": "proceed",
        }

    return {
        "success": False,
        "data": final_data,
        "error_code": "ENROLL_INCOMPLETE_AFTER_FORM",
        "error_message": "已提交报名表单，但报名状态仍未完成",
        "suggested_action": "请检查表单答案是否满足要求，或手动完成报名",
        "next_action": "needs_user_input",
    }


@skill(
    name="get_course_enroll_form",
    description="获取课程的复杂报名表单结构（联系信息 + 报名问题）",
    required_capabilities=["learning"],
    return_description="报名表单结构",
)
async def get_course_enroll_form(
    ctx: SkillContext,
    course_identifier: str,
) -> dict[str, Any]:
    """获取课程报名表单.

    Args:
        course_identifier: 课程访问码、短域名或完整 URL。
    """
    ctx.logger.info("[get_course_enroll_form] 获取报名表单: %s", course_identifier)

    result = await ctx.call_capability_tool(
        capability="learning",
        operation="get_enroll_form",
        arguments={"course_identifier": course_identifier},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_ENROLL_FORM_FAILED",
            "error_message": result.get("error_message") or "获取报名表单失败",
            "suggested_action": "请确认课程标识正确，且课程需要报名",
            "next_action": "retry",
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
    name="submit_course_enroll_form",
    description="提交课程的复杂报名表单",
    required_capabilities=["learning"],
    return_description="表单提交结果",
)
async def submit_course_enroll_form(
    ctx: SkillContext,
    course_identifier: str,
    contact_answers: dict[str, str] | None = None,
    section_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """提交课程报名表单.

    Args:
        course_identifier: 课程访问码、短域名或完整 URL。
        contact_answers: 联系信息答案。
        section_answers: 报名问题答案列表。
    """
    ctx.logger.info("[submit_course_enroll_form] 提交报名表单: %s", course_identifier)

    payload = _build_enroll_form_payload(course_identifier, contact_answers, section_answers)
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="submit_enroll_form",
        arguments=payload,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_ENROLL_FORM_FAILED",
            "error_message": result.get("error_message") or "报名表单提交失败",
            "suggested_action": "请检查 contact_answers 和 section_answers 格式",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "表单提交成功，可继续完成课程报名或学习",
        "next_action": "proceed",
    }


@skill(
    name="get_course_progress",
    description="获取当前学员在指定课程的学习进度和结构",
    required_capabilities=["learning"],
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
    resolve_result = await ctx.call_capability_tool(
        capability="learning",
        operation="resolve_course_url",
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
    result = await ctx.call_capability_tool(
        capability="learning",
        operation="get_course_structure",
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


__all__ = [
    "enroll_course",
    "get_course_enroll_form",
    "submit_course_enroll_form",
    "get_course_progress",
]
