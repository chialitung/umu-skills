# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Teacher 课程协同管理 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="manage_course_collaborators",
    description="管理课程协同者：列出、邀请、调整权限、删除、转让拥有者",
    required_capabilities=["course_management"],
    return_description="操作结果",
)
async def manage_course_collaborators(
    ctx: SkillContext,
    group_id: str,
    action: str,
    keyword: str | None = None,
    cooperation_info_id: str | None = None,
    role_type: str | None = None,
) -> dict[str, Any]:
    """管理课程协同者.

    Args:
        group_id: 课程 ID
        action: 操作类型：list / invite / update_role / remove / transfer_owner
        keyword: 搜索关键词，用于 invite / transfer_owner
        cooperation_info_id: 协同关系 ID，用于 update_role / remove
        role_type: 权限类型（editor/operator/viewer），用于 invite / update_role
    """
    action = action.lower()

    if action == "list":
        result = await ctx.call_capability_tool(
            capability="course_management",
            operation="list_course_collaborators",
            arguments={"group_id": group_id},
        )
    elif action == "invite":
        if not keyword or not role_type:
            return {
                "success": False,
                "data": None,
                "error_code": "MISSING_PARAMETERS",
                "error_message": "invite 操作需要提供 keyword 和 role_type",
                "suggested_action": "请提供被邀请者关键词和权限类型",
                "next_action": "needs_user_input",
            }
        result = await ctx.call_capability_tool(
            capability="course_management",
            operation="invite_course_collaborator",
            arguments={
                "group_id": group_id,
                "keyword": keyword,
                "role_type": role_type,
            },
        )
        # 邀请成功后，列出协同者以获取 cooperation_info_id，供后续更新/移除使用
        if result.get("success"):
            list_result = await ctx.call_capability_tool(
                capability="course_management",
                operation="list_course_collaborators",
                arguments={"group_id": group_id},
            )
            if list_result.get("success"):
                # invite_course_collaborator operation 返回的 teacher_id 对应列表中的 umu_id
                invited_account_id = result.get("data", {}).get("teacher_id") or result.get("data", {}).get("umu_id", "")
                invited_account = result.get("data", {}).get("account", "")
                for item in list_result.get("data", {}).get("collaborators", []):
                    matched = False
                    if invited_account_id and str(item.get("umu_id", "")) == str(invited_account_id):
                        matched = True
                    elif invited_account_id and str(item.get("teacher_id", "")) == str(invited_account_id):
                        matched = True
                    elif invited_account and item.get("teacher_email") == invited_account:
                        matched = True
                    if matched:
                        result["data"]["cooperation_info_id"] = item.get("cooperation_info_id")
                        break
    elif action == "update_role":
        if not cooperation_info_id or not role_type:
            return {
                "success": False,
                "data": None,
                "error_code": "MISSING_PARAMETERS",
                "error_message": "update_role 操作需要提供 cooperation_info_id 和 role_type",
                "suggested_action": "请提供协同关系 ID 和新权限类型",
                "next_action": "needs_user_input",
            }
        result = await ctx.call_capability_tool(
            capability="course_management",
            operation="update_collaborator_role",
            arguments={
                "group_id": group_id,
                "cooperation_info_id": cooperation_info_id,
                "role_type": role_type,
            },
        )
    elif action == "remove":
        if not cooperation_info_id:
            return {
                "success": False,
                "data": None,
                "error_code": "MISSING_PARAMETERS",
                "error_message": "remove 操作需要提供 cooperation_info_id",
                "suggested_action": "请提供协同关系 ID",
                "next_action": "needs_user_input",
            }
        result = await ctx.call_capability_tool(
            capability="course_management",
            operation="remove_course_collaborator",
            arguments={
                "group_id": group_id,
                "cooperation_info_id": cooperation_info_id,
            },
        )
    elif action == "transfer_owner":
        if not keyword:
            return {
                "success": False,
                "data": None,
                "error_code": "MISSING_PARAMETERS",
                "error_message": "transfer_owner 操作需要提供 keyword",
                "suggested_action": "请提供新拥有者关键词",
                "next_action": "needs_user_input",
            }
        result = await ctx.call_capability_tool(
            capability="course_management",
            operation="transfer_course_owner",
            arguments={
                "group_id": group_id,
                "keyword": keyword,
            },
        )
    else:
        return {
            "success": False,
            "data": None,
            "error_code": "INVALID_ACTION",
            "error_message": f"不支持的 action: {action}",
            "suggested_action": "请选择 list / invite / update_role / remove / transfer_owner 之一",
            "next_action": "needs_user_input",
        }

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "MANAGE_COLLABORATORS_FAILED",
            "error_message": result.get("error_message") or "课程协同管理操作失败",
            "suggested_action": result.get("suggested_action", ""),
            "next_action": result.get("next_action", "retry"),
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": result.get("suggested_action", ""),
        "next_action": "proceed",
    }


__all__ = ["manage_course_collaborators"]
