"""Admin 组织架构相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_departments",
    description="列出企业部门",
    required_servers=["admin"],
    return_description="部门列表",
)
async def list_departments(
    ctx: SkillContext,
) -> dict[str, Any]:
    """列出部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_departments",
        arguments={},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_DEPARTMENTS_FAILED",
            "error_message": result.get("error_message") or "部门列表获取失败",
            "suggested_action": "请确认管理员已登录",
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
    name="list_groups",
    description="列出企业分组",
    required_servers=["admin"],
    return_description="分组列表及分页信息",
)
async def list_groups(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """列出分组."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_groups",
        arguments={"page": page, "page_size": page_size},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_GROUPS_FAILED",
            "error_message": result.get("error_message") or "分组列表获取失败",
            "suggested_action": "请确认管理员已登录",
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
    name="list_classes",
    description="列出企业班级",
    required_servers=["admin"],
    return_description="班级列表及分页信息",
)
async def list_classes(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出班级."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_classes",
        arguments={"page": page, "page_size": page_size, "fetch_all": fetch_all},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_CLASSES_FAILED",
            "error_message": result.get("error_message") or "班级列表获取失败",
            "suggested_action": "请确认管理员已登录",
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


__all__ = [
    "list_departments",
    "get_department_tree",
    "get_department",
    "get_child_departments",
    "list_department_members",
    "search_department_members",
    "create_department",
    "update_department",
    "sort_departments",
    "add_department_members",
    "move_department_members",
    "remove_department_members",
    "delete_departments",
    "list_groups",
    "list_classes",
]


@skill(
    name="get_department_tree",
    description="获取企业部门树",
    required_servers=["admin"],
    return_description="完整部门树，包含子部门",
)
async def get_department_tree(
    ctx: SkillContext,
    fetch_all: bool = True,
) -> dict[str, Any]:
    """获取部门树."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_get_department_tree",
        arguments={"fetch_all": fetch_all},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_DEPARTMENT_TREE_FAILED",
            "error_message": result.get("error_message") or "部门树获取失败",
            "suggested_action": "请确认管理员已登录",
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
    name="get_department",
    description="获取部门详情",
    required_servers=["admin"],
    return_description="部门详情，包括上级路径和负责人",
)
async def get_department(
    ctx: SkillContext,
    department_id: str,
) -> dict[str, Any]:
    """获取部门详情."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_get_department",
        arguments={"department_id": department_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_DEPARTMENT_FAILED",
            "error_message": result.get("error_message") or "部门详情获取失败",
            "suggested_action": "请确认 department_id 正确",
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
    name="get_child_departments",
    description="获取子部门列表",
    required_servers=["admin"],
    return_description="子部门列表",
)
async def get_child_departments(
    ctx: SkillContext,
    department_id: str = "0",
) -> dict[str, Any]:
    """获取子部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_get_child_departments",
        arguments={"department_id": department_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_CHILD_DEPARTMENTS_FAILED",
            "error_message": result.get("error_message") or "子部门获取失败",
            "suggested_action": "请确认 department_id 正确",
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
    name="list_department_members",
    description="列出部门成员",
    required_servers=["admin"],
    return_description="部门成员列表及分页信息",
)
async def list_department_members(
    ctx: SkillContext,
    department_id: str,
    keywords: str | None = None,
    page: int = 1,
    page_size: int = 15,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出部门成员."""
    arguments: dict[str, Any] = {
        "department_id": department_id,
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if keywords:
        arguments["keywords"] = keywords

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_department_members",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_DEPARTMENT_MEMBERS_FAILED",
            "error_message": result.get("error_message") or "部门成员获取失败",
            "suggested_action": "请确认管理员已登录且 department_id 正确",
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
    name="search_department_members",
    description="搜索可加入部门的成员",
    required_servers=["admin"],
    return_description="可加入部门的成员列表",
)
async def search_department_members(
    ctx: SkillContext,
    department_id: str | None = None,
    keywords: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """搜索可加入部门的成员."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
    }
    if department_id:
        arguments["department_id"] = department_id
    if keywords:
        arguments["keywords"] = keywords

    result = await ctx.call_tool(
        server="admin",
        tool="adm_search_department_members",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SEARCH_DEPARTMENT_MEMBERS_FAILED",
            "error_message": result.get("error_message") or "成员搜索失败",
            "suggested_action": "请确认管理员已登录",
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
    name="create_department",
    description="创建部门",
    required_servers=["admin"],
    return_description="新创建部门的 ID",
)
async def create_department(
    ctx: SkillContext,
    department_name: str,
    parent_department_id: str = "0",
) -> dict[str, Any]:
    """创建部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_create_department",
        arguments={
            "department_name": department_name,
            "parent_department_id": parent_department_id,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CREATE_DEPARTMENT_FAILED",
            "error_message": result.get("error_message") or "部门创建失败",
            "suggested_action": "请确认父部门 ID 正确且名称不重复",
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
    name="update_department",
    description="更新部门信息",
    required_servers=["admin"],
    return_description="更新结果",
)
async def update_department(
    ctx: SkillContext,
    department_id: str,
    department_name: str | None = None,
    parent_department_id: str | None = None,
    manager_umu_ids: str | None = None,
) -> dict[str, Any]:
    """更新部门."""
    arguments: dict[str, Any] = {"department_id": department_id}
    if department_name:
        arguments["department_name"] = department_name
    if parent_department_id is not None:
        arguments["parent_department_id"] = parent_department_id
    if manager_umu_ids is not None:
        arguments["manager_umu_ids"] = manager_umu_ids

    result = await ctx.call_tool(
        server="admin",
        tool="adm_update_department",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "UPDATE_DEPARTMENT_FAILED",
            "error_message": result.get("error_message") or "部门更新失败",
            "suggested_action": "请确认 department_id 和父部门 ID 正确",
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
    name="sort_departments",
    description="调整部门排序",
    required_servers=["admin"],
    return_description="排序结果",
)
async def sort_departments(
    ctx: SkillContext,
    department_orders: str,
) -> dict[str, Any]:
    """调整部门排序."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_sort_departments",
        arguments={"department_orders": department_orders},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SORT_DEPARTMENTS_FAILED",
            "error_message": result.get("error_message") or "部门排序失败",
            "suggested_action": "请确认排序 JSON 格式正确",
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
    name="add_department_members",
    description="添加成员到部门",
    required_servers=["admin"],
    return_description="添加结果",
)
async def add_department_members(
    ctx: SkillContext,
    department_id: str,
    umu_ids: str,
) -> dict[str, Any]:
    """添加成员到部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_add_department_members",
        arguments={"department_id": department_id, "umu_ids": umu_ids},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "ADD_DEPARTMENT_MEMBERS_FAILED",
            "error_message": result.get("error_message") or "添加部门成员失败",
            "suggested_action": "请确认 umu_id 正确",
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
    name="move_department_members",
    description="调整成员所属部门",
    required_servers=["admin"],
    return_description="调整结果",
)
async def move_department_members(
    ctx: SkillContext,
    umu_ids: str,
    department_ids: str,
) -> dict[str, Any]:
    """调整成员部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_move_department_members",
        arguments={"umu_ids": umu_ids, "department_ids": department_ids},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "MOVE_DEPARTMENT_MEMBERS_FAILED",
            "error_message": result.get("error_message") or "调整成员部门失败",
            "suggested_action": "请确认 umu_id 和 department_id 正确",
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
    name="remove_department_members",
    description="从部门移除成员",
    required_servers=["admin"],
    return_description="移除结果",
)
async def remove_department_members(
    ctx: SkillContext,
    member_ids: str,
) -> dict[str, Any]:
    """移除部门成员."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_remove_department_members",
        arguments={"member_ids": member_ids},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "REMOVE_DEPARTMENT_MEMBERS_FAILED",
            "error_message": result.get("error_message") or "移除部门成员失败",
            "suggested_action": "请确认 member_id 正确",
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
    name="delete_departments",
    description="删除部门",
    required_servers=["admin"],
    return_description="删除结果",
)
async def delete_departments(
    ctx: SkillContext,
    department_ids: str,
) -> dict[str, Any]:
    """删除部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_delete_departments",
        arguments={"department_ids": department_ids},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "DELETE_DEPARTMENTS_FAILED",
            "error_message": result.get("error_message") or "删除部门失败",
            "suggested_action": "请确认部门下无成员和子部门",
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


__all__ = [
    "list_departments",
    "get_department_tree",
    "get_department",
    "get_child_departments",
    "list_department_members",
    "search_department_members",
    "create_department",
    "update_department",
    "sort_departments",
    "add_department_members",
    "move_department_members",
    "remove_department_members",
    "delete_departments",
    "list_groups",
    "list_classes",
]
