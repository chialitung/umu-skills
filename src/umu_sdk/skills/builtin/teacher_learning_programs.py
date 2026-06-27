# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Teacher 学习项目列表查询 Skill.

项目访问权限相关 Skill 已迁移至 program_permissions.py，以 Teacher 子 MCP
的 canonical 原子工具实现，供多角色复用。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_teacher_learning_programs",
    description="查询讲师视角的学习项目清单，支持我拥有的/协同给我的/我报名的三个视角",
    required_servers=["teacher"],
    return_description="学习项目列表",
)
async def list_teacher_learning_programs(
    ctx: SkillContext,
    scope: str,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询讲师视角的学习项目清单."""
    arguments: dict[str, Any] = {
        "scope": scope,
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if keywords:
        arguments["keywords"] = keywords

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_learning_programs",
        arguments=arguments,
    )
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_LEARNING_PROGRAMS_FAILED",
            "error_message": result.get("error_message") or "获取学习项目列表失败",
            "suggested_action": result.get("suggested_action") or "请确认讲师已登录",
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


async def _list_programs_by_scope(
    ctx: SkillContext,
    scope: str,
    keywords: str,
    page: int,
    page_size: int,
    fetch_all: bool,
) -> dict[str, Any]:
    """按 scope 调用底层 Skill 的通用封装."""
    return await list_teacher_learning_programs(
        ctx, scope=scope, keywords=keywords, page=page, page_size=page_size, fetch_all=fetch_all
    )


@skill(
    name="list_owned_learning_programs",
    description="查询我拥有的学习项目清单",
    required_servers=["teacher"],
    return_description="学习项目列表",
)
async def list_owned_learning_programs(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询我拥有的学习项目清单."""
    return await _list_programs_by_scope(ctx, "owned", keywords, page, page_size, fetch_all)


@skill(
    name="list_cooperated_learning_programs",
    description="查询协同给我的学习项目清单",
    required_servers=["teacher"],
    return_description="学习项目列表",
)
async def list_cooperated_learning_programs(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询协同给我的学习项目清单."""
    return await _list_programs_by_scope(ctx, "cooperated", keywords, page, page_size, fetch_all)


@skill(
    name="list_enrolled_learning_programs",
    description="查询我报名的学习项目清单",
    required_servers=["teacher"],
    return_description="学习项目列表",
)
async def list_enrolled_learning_programs(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询我报名的学习项目清单."""
    return await _list_programs_by_scope(ctx, "enrolled", keywords, page, page_size, fetch_all)


@skill(
    name="create_learning_program",
    description="创建学习项目并添加课程，可选配置证书与积分",
    required_servers=["teacher"],
    return_description="包含 program_id、添加结果等",
)
async def create_learning_program(
    ctx: SkillContext,
    title: str,
    desc_plain: str = "",
    desc_richtext: str = "",
    cover_image_path: str = "",
    bg_image_path: str = "",
    cover_image_url: str = "",
    bg_image_url: str = "",
    tags: list[str] | None = None,
    category_ids: list[str] | None = None,
    category_names: list[str] | None = None,
    modules: list[dict[str, Any]] | None = None,
    course_ids: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
    enable_certificate: bool = False,
    certificate_text: str = "",
    certificate_theme_id: str = "",
    enable_points: bool = False,
    skin_id: int = 1,
    pc_skin_id: int = 1,
    show_banner: bool = True,
    unlock_type: int = 1,
    show_type: int = 1,
    open_module: int = 1,
    sort: str = "asc",
) -> dict[str, Any]:
    """创建学习项目.

    若同时提供 modules 与 course_ids，优先使用 modules；
    仅提供 course_ids 时，自动归入默认模块。
    """
    modules = modules or []
    if course_ids and not modules:
        modules = [{"module_title": "必修课程", "course_ids": course_ids}]

    create_args: dict[str, Any] = {"title": title}
    if desc_plain:
        create_args["desc_plain"] = desc_plain
    if desc_richtext:
        create_args["desc_richtext"] = desc_richtext
    if cover_image_path:
        create_args["cover_image_path"] = cover_image_path
    if bg_image_path:
        create_args["bg_image_path"] = bg_image_path
    if cover_image_url:
        create_args["cover_image_url"] = cover_image_url
    if bg_image_url:
        create_args["bg_image_url"] = bg_image_url
    if tags:
        create_args["tags"] = tags
    if category_ids:
        create_args["category_ids"] = category_ids
    if category_names:
        create_args["category_names"] = category_names
    if start_time:
        create_args["start_time"] = start_time
    if end_time:
        create_args["end_time"] = end_time
    create_args["skin_id"] = skin_id
    create_args["pc_skin_id"] = pc_skin_id
    create_args["show_banner"] = show_banner
    create_args["unlock_type"] = unlock_type
    create_args["show_type"] = show_type
    create_args["open_module"] = open_module
    create_args["sort"] = sort
    create_args["enable_certificate"] = enable_certificate

    ctx.logger.info("[create_learning_program] 创建学习项目: %s", title)
    create_result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_learning_program",
        arguments=create_args,
    )
    if not create_result["success"]:
        return {
            "success": False,
            "data": create_result.get("data"),
            "error_code": create_result.get("error_code", "CREATE_LEARNING_PROGRAM_FAILED"),
            "error_message": create_result.get("error_message", "创建学习项目失败"),
            "suggested_action": "请检查讲师是否已登录",
            "next_action": "retry",
        }

    program_id = create_result.get("data", {}).get("program_id")
    if not program_id or str(program_id) in ("0", ""):
        return {
            "success": False,
            "data": create_result.get("data"),
            "error_code": "MISSING_PROGRAM_ID",
            "error_message": "创建学习项目成功，但响应中无有效 program_id",
            "suggested_action": "请检查 tch_create_learning_program 返回结构或 API 是否可用",
            "next_action": "needs_user_input",
        }

    result_data: dict[str, Any] = {"program_id": program_id}

    if modules:
        ctx.logger.info("[create_learning_program] 向项目 %s 添加课程", program_id)
        add_result = await ctx.call_tool(
            server="teacher",
            tool="tch_add_courses_to_learning_program",
            arguments={"program_id": program_id, "modules": modules},
        )
        result_data["add_courses_result"] = add_result.get("data", {})
        if not add_result["success"]:
            return {
                "success": False,
                "data": result_data,
                "error_code": add_result.get("error_code", "ADD_COURSES_FAILED"),
                "error_message": add_result.get("error_message", "添加课程失败"),
                "suggested_action": "部分课程可能未添加成功，请查看 add_courses_result",
                "next_action": "retry",
            }

    if enable_certificate:
        ctx.logger.info("[create_learning_program] 配置证书")
        cert_args: dict[str, Any] = {"program_id": program_id}
        if certificate_text:
            cert_args["text"] = certificate_text
        if certificate_theme_id:
            cert_args["theme_id"] = certificate_theme_id
        cert_result = await ctx.call_tool(
            server="teacher",
            tool="tch_configure_program_certificate",
            arguments=cert_args,
        )
        result_data["certificate_result"] = cert_result.get("data", {})
        if not cert_result["success"]:
            return {
                "success": False,
                "data": result_data,
                "error_code": cert_result.get("error_code", "CONFIGURE_CERTIFICATE_FAILED"),
                "error_message": cert_result.get("error_message", "配置证书失败"),
                "suggested_action": "可稍后手动配置证书",
                "next_action": "proceed",
            }

    if enable_points:
        ctx.logger.info("[create_learning_program] 开启积分")
        points_result = await ctx.call_tool(
            server="teacher",
            tool="tch_set_program_points_status",
            arguments={"program_id": program_id, "enabled": True},
        )
        result_data["points_result"] = points_result.get("data", {})
        if not points_result["success"]:
            return {
                "success": False,
                "data": result_data,
                "error_code": points_result.get("error_code", "SET_POINTS_FAILED"),
                "error_message": points_result.get("error_message", "开启积分失败"),
                "suggested_action": "可稍后手动开启积分",
                "next_action": "proceed",
            }

    return {
        "success": True,
        "data": result_data,
        "error_code": "",
        "error_message": "",
        "suggested_action": "学习项目已创建完成，可继续添加更多课程或配置权限",
        "next_action": "proceed",
    }


@skill(
    name="update_learning_program",
    description="修改学习项目基本信息、模块与课程关系，支持删除课程",
    required_servers=["teacher"],
    return_description="包含 program_id 与各子操作结果",
)
async def update_learning_program(
    ctx: SkillContext,
    program_id: str,
    title: str | None = None,
    desc_plain: str | None = None,
    desc_richtext: str | None = None,
    cover_image_path: str | None = None,
    bg_image_path: str | None = None,
    cover_image_url: str | None = None,
    bg_image_url: str | None = None,
    tags: list[str] | None = None,
    category_ids: list[str] | None = None,
    category_names: list[str] | None = None,
    skin_id: int | None = None,
    pc_skin_id: int | None = None,
    show_banner: bool | None = None,
    unlock_type: int | None = None,
    show_type: int | None = None,
    open_module: int | None = None,
    sort: str | None = None,
    enable_certificate: bool | None = None,
    modules: list[dict[str, Any]] | None = None,
    remove_module_group_ids: list[str] | None = None,
) -> dict[str, Any]:
    """修改学习项目.

    支持修改项目基本信息、模块信息、课程排序/必修状态，以及删除指定课程。
    """
    update_args: dict[str, Any] = {"program_id": program_id}
    if title is not None:
        update_args["title"] = title
    if desc_plain is not None:
        update_args["desc_plain"] = desc_plain
    if desc_richtext is not None:
        update_args["desc_richtext"] = desc_richtext
    if cover_image_path is not None:
        update_args["cover_image_path"] = cover_image_path
    if bg_image_path is not None:
        update_args["bg_image_path"] = bg_image_path
    if cover_image_url is not None:
        update_args["cover_image_url"] = cover_image_url
    if bg_image_url is not None:
        update_args["bg_image_url"] = bg_image_url
    if tags is not None:
        update_args["tags"] = tags
    if category_ids is not None:
        update_args["category_ids"] = category_ids
    if category_names is not None:
        update_args["category_names"] = category_names
    if skin_id is not None:
        update_args["skin_id"] = skin_id
    if pc_skin_id is not None:
        update_args["pc_skin_id"] = pc_skin_id
    if show_banner is not None:
        update_args["show_banner"] = show_banner
    if unlock_type is not None:
        update_args["unlock_type"] = unlock_type
    if show_type is not None:
        update_args["show_type"] = show_type
    if open_module is not None:
        update_args["open_module"] = open_module
    if sort is not None:
        update_args["sort"] = sort
    if enable_certificate is not None:
        update_args["enable_certificate"] = enable_certificate

    result_data: dict[str, Any] = {"program_id": program_id}

    # 先删除课程，再修改模块，避免顺序冲突
    if remove_module_group_ids:
        ctx.logger.info("[update_learning_program] 从项目 %s 删除课程", program_id)
        remove_result = await ctx.call_tool(
            server="teacher",
            tool="tch_remove_courses_from_learning_program",
            arguments={"program_id": program_id, "module_group_ids": remove_module_group_ids},
        )
        result_data["remove_courses_result"] = remove_result.get("data", {})
        if not remove_result["success"]:
            return {
                "success": False,
                "data": result_data,
                "error_code": remove_result.get("error_code", "REMOVE_COURSES_FAILED"),
                "error_message": remove_result.get("error_message", "删除课程失败"),
                "suggested_action": "失败课程可单独重试",
                "next_action": "retry",
            }

    ctx.logger.info("[update_learning_program] 修改项目 %s 基本信息", program_id)
    update_result = await ctx.call_tool(
        server="teacher",
        tool="tch_update_learning_program",
        arguments=update_args,
    )
    if not update_result["success"]:
        return {
            "success": False,
            "data": result_data,
            "error_code": update_result.get("error_code", "UPDATE_LEARNING_PROGRAM_FAILED"),
            "error_message": update_result.get("error_message", "修改学习项目失败"),
            "suggested_action": "请检查讲师是否已登录或项目是否存在",
            "next_action": "retry",
        }

    if modules:
        ctx.logger.info("[update_learning_program] 修改项目 %s 模块", program_id)
        module_result = await ctx.call_tool(
            server="teacher",
            tool="tch_update_learning_program_modules",
            arguments={"program_id": program_id, "modules": modules},
        )
        result_data["update_modules_result"] = module_result.get("data", {})
        if not module_result["success"]:
            return {
                "success": False,
                "data": result_data,
                "error_code": module_result.get("error_code", "UPDATE_MODULES_FAILED"),
                "error_message": module_result.get("error_message", "修改模块失败"),
                "suggested_action": "部分模块可能未修改成功，请查看 update_modules_result",
                "next_action": "retry",
            }

    return {
        "success": True,
        "data": result_data,
        "error_code": "",
        "error_message": "",
        "suggested_action": "学习项目已修改完成",
        "next_action": "proceed",
    }


@skill(
    name="list_program_participants",
    description="查询学习项目的学员名单，支持按完成状态筛选与是否包含禁用账号",
    required_servers=["teacher"],
    return_description="学员名单及 modules/courses 深度格式化结果",
)
async def list_program_participants(
    ctx: SkillContext,
    program_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习项目的学员名单."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_program_participants",
        arguments={
            "program_id": program_id,
            "status_filter": status_filter,
            "include_disabled": include_disabled,
            "page": page,
            "page_size": page_size,
            "fetch_all": fetch_all,
        },
    )
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_PROGRAM_PARTICIPANTS_FAILED",
            "error_message": result.get("error_message") or "查询学习项目学员名单失败",
            "suggested_action": result.get("suggested_action") or "请确认讲师已登录",
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
    name="list_program_learning_tasks",
    description="查询学习项目的学习任务学员名单，支持按完成状态筛选与是否包含禁用账号",
    required_servers=["teacher"],
    return_description="学习任务学员名单及 modules/courses 深度格式化结果",
)
async def list_program_learning_tasks(
    ctx: SkillContext,
    program_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习项目的学习任务学员名单."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_program_learning_tasks",
        arguments={
            "program_id": program_id,
            "status_filter": status_filter,
            "include_disabled": include_disabled,
            "page": page,
            "page_size": page_size,
            "fetch_all": fetch_all,
        },
    )
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_PROGRAM_LEARNING_TASKS_FAILED",
            "error_message": result.get("error_message") or "查询学习项目学习任务学员名单失败",
            "suggested_action": result.get("suggested_action") or "请确认讲师已登录",
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
    "list_teacher_learning_programs",
    "list_owned_learning_programs",
    "list_cooperated_learning_programs",
    "list_enrolled_learning_programs",
    "create_learning_program",
    "update_learning_program",
    "list_program_participants",
    "list_program_learning_tasks",
]
