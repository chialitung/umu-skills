"""Admin 学习任务明细相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_user_tasks",
    description="查询企业学习任务明细，支持任务类型、完成状态、到期状态、部门、分组、班级、分配者、学员、学习任务名称、课程关键词等多条件组合筛选",
    required_servers=["admin"],
    return_description="任务明细列表及分页信息",
)
async def get_user_tasks(
    ctx: SkillContext,
    task_types: str = "",
    learn_status: str = "",
    due_status: str = "",
    department_ids: str = "",
    department_names: str = "",
    group_ids: str = "",
    group_names: str = "",
    class_ids: str = "",
    class_names: str = "",
    assigner_umu_ids: str = "",
    assigner_keywords: str = "",
    student_umu_ids: str = "",
    student_keywords: str = "",
    task_name: str = "",
    course_keywords: str = "",
    assign_start_day: str = "",
    assign_end_day: str = "",
    due_start_day: str = "",
    due_end_day: str = "",
    page: int = 1,
    page_size: int = 500,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习任务明细."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }

    for key, value in {
        "task_types": task_types,
        "learn_status": learn_status,
        "due_status": due_status,
        "department_ids": department_ids,
        "department_names": department_names,
        "group_ids": group_ids,
        "group_names": group_names,
        "class_ids": class_ids,
        "class_names": class_names,
        "assigner_umu_ids": assigner_umu_ids,
        "assigner_keywords": assigner_keywords,
        "student_umu_ids": student_umu_ids,
        "student_keywords": student_keywords,
        "task_name": task_name,
        "course_keywords": course_keywords,
        "assign_start_day": assign_start_day,
        "assign_end_day": assign_end_day,
        "due_start_day": due_start_day,
        "due_end_day": due_end_day,
    }.items():
        if value:
            arguments[key] = value

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_user_tasks",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_USER_TASKS_FAILED",
            "error_message": result.get("error_message") or "任务明细获取失败",
            "suggested_action": result.get("suggested_action") or "请确认管理员已登录",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": result.get("suggested_action", ""),
        "next_action": "proceed",
    }


__all__ = ["get_user_tasks"]
