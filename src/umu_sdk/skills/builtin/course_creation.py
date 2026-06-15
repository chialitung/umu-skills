# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程创建相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="create_course_with_scorm",
    description="创建空课程并添加 SCORM 小节（使用已有 SCORM 资源）",
    required_servers=["teacher"],
    return_description="包含 group_id、section_id 等创建结果",
)
async def create_course_with_scorm(
    ctx: SkillContext,
    title: str,
    scorm_resource_id: str,
    section_title: str = "SCORM 学习",
    category_names: list[str] | None = None,
) -> dict[str, Any]:
    """创建空课程并绑定一个已有的 SCORM 资源作为小节.

    流程：
    1. 获取课程分类（可选）
    2. 创建空课程
    3. 添加 SCORM 小节
    """
    category_names = category_names or []

    # 1. 创建空课程
    ctx.logger.info("[create_course_with_scorm] 创建课程: %s", title)
    create_args: dict[str, Any] = {"title": title}
    if category_names:
        create_args["category_names"] = category_names

    course_result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_course",
        arguments=create_args,
    )
    if not course_result["success"]:
        return {
            "success": False,
            "data": None,
            "error_code": course_result.get("error_code", "CREATE_COURSE_FAILED"),
            "error_message": course_result.get("error_message", "创建课程失败"),
            "suggested_action": "请检查讲师是否已登录，以及分类名称是否正确",
            "next_action": "retry",
        }

    group_id = _extract_group_id(course_result.get("data"))
    if not group_id:
        return {
            "success": False,
            "data": course_result.get("data"),
            "error_code": "MISSING_GROUP_ID",
            "error_message": "创建课程成功，但响应中未找到 group_id",
            "suggested_action": "请检查 tch_create_course 的返回结构",
            "next_action": "needs_user_input",
        }

    # 2. 添加 SCORM 小节
    ctx.logger.info("[create_course_with_scorm] 添加 SCORM 小节到课程 %s", group_id)
    section_result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_scorm_section",
        arguments={
            "group_id": group_id,
            "section_title": section_title,
            "scorm_resource_id": scorm_resource_id,
        },
    )
    if not section_result["success"]:
        return {
            "success": False,
            "data": {"group_id": group_id},
            "error_code": section_result.get("error_code", "CREATE_SECTION_FAILED"),
            "error_message": section_result.get("error_message", "添加小节失败"),
            "suggested_action": "请检查 scorm_resource_id 是否有效",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": {
            "group_id": group_id,
            "section_result": section_result.get("data"),
        },
        "error_code": "",
        "error_message": "",
        "suggested_action": "课程已创建完成，可继续添加更多小节或发布课程",
        "next_action": "proceed",
    }


def _extract_group_id(data: Any) -> str | None:
    """从 tch_create_course 的返回数据中提取 group_id."""
    if not isinstance(data, dict):
        return None
    group_id = data.get("group_id") or data.get("groupId") or data.get("id")
    return str(group_id) if group_id else None


__all__ = ["create_course_with_scorm"]
