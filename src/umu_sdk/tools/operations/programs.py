# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习项目相关业务操作.

本模块提供学习项目的增删改查等无状态业务函数，可被 Teacher/Admin MCP server
复用。函数不处理角色相关文案，仅负责调用 UMU API 并返回结构化结果或抛出异常。
"""

from __future__ import annotations

import time
from typing import Any

from ...adapters.mcp.program_builder import ProgramBuilder
from ...adapters.mcp.program_student_manager import ProgramStudentManager
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation
from ..shared.progress import report_pagination_progress


def _program_list_url_and_params(
    scope: str,
    keywords: str,
    page: int,
    page_size: int,
) -> tuple[str, dict[str, str]]:
    """根据 scope 返回学习项目列表的端点和参数."""
    base_params: dict[str, str] = {
        "t": str(int(time.time() * 1000)),
        "page": str(page),
        "size": str(page_size),
    }
    if scope == "owned":
        url = "/api/program/getlist"
        base_params["owner"] = "1"
        base_params["type"] = "1"
    elif scope == "cooperated":
        url = "/api/program/getcooperateprogramlist"
    elif scope == "enrolled":
        url = "/api/program/getmyparticipatedprogramlist"
    else:
        raise UMUError(f"不支持的 scope: {scope}", code="INVALID_SCOPE")

    if keywords:
        base_params["keywords"] = keywords

    return url, base_params


def _format_program_list_item(item: dict[str, Any]) -> dict[str, Any]:
    """统一格式化 /api/program/getlist 返回的项目字段."""
    creator = item.get("creator", {}) or {}
    return {
        "program_id": str(item.get("program_id", "")),
        "program_title": item.get("program_title", ""),
        "desc": item.get("desc", ""),
        "access_code": item.get("access_code", ""),
        "share_url": item.get("share_url", ""),
        "share_pc_url": item.get("share_pc_url", ""),
        "head_img": item.get("head_img", ""),
        "bg_img": item.get("setup", {}).get("bg_img", ""),
        "create_time": item.get("create_time", ""),
        "update_time": item.get("update_time", ""),
        "creator_umu_id": str(creator.get("umu_id", "")),
        "creator_name": creator.get("user_name", item.get("creater_name", "")),
        "group_num": item.get("group_num", 0),
        "module_num": item.get("module_num", 0),
        "is_creator": item.get("is_creator", 0),
    }


@umu_operation(
    name="delete_learning_program",
    description="删除学习项目",
    roles=["teacher", "admin"],
    parameter_docs={"program_id": "学习项目 ID"},
)
async def delete_learning_program(client: UMUClient, program_id: str) -> dict[str, Any]:
    """删除学习项目.

    触发条件：需要删除学习项目时调用。
    副作用：将学习项目移至平台回收站。

    Args:
        client: 已登录的 UMUClient 实例。
        program_id: 学习项目 ID。

    Returns:
        {"program_id": "...", "deleted": True}

    Raises:
        ValueError: program_id 为空。
        RuntimeError: UMU 接口返回业务失败。
    """
    if not program_id or str(program_id) in ("0", ""):
        raise ValueError("program_id 不能为空")

    resp = client.post(
        client.desktop_url("/api/program/deleteprogram"),
        data={"program_id": program_id},
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {"program_id": program_id, "deleted": True}

    raise RuntimeError(resp.get("error", "删除学习项目失败"))


async def _list_personal_learning_programs_impl(
    client: UMUClient,
    scope: str,
    keywords: str | None = None,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询个人视角学习项目清单的共享实现."""
    url, base_params = _program_list_url_and_params(
        scope, keywords or "", page, page_size
    )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params = {**base_params, "page": str(p), "size": str(sz)}
        resp = client.get(client.desktop_url(url), params=params)
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习项目列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        program_list = data.get("list", [])
        total_all = int(page_info.get("list_total_num", 0) or 0)
        return [_format_program_list_item(item) for item in program_list], total_all

    if fetch_all:
        all_items: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1
        batch_size = 20

        while True:
            items, total_all = _fetch_page(current_page, batch_size)
            all_items.extend(items)

            report_pagination_progress(
                "list_personal_learning_programs",
                current_page,
                len(all_items),
                total_all,
                batch_size,
                is_complete=len(all_items) >= total_all or not items,
            )

            if len(all_items) >= total_all or not items:
                break
            current_page += 1
            if current_page > 50:
                report_pagination_progress(
                    "list_personal_learning_programs",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                break

        return {
            "scope": scope,
            "programs": all_items,
            "total": len(all_items),
            "pagination": {
                "total_all": total_all,
                "current_page": 1,
                "page_size": len(all_items) if all_items else 0,
            },
        }

    items, total_all = _fetch_page(page, page_size)
    return {
        "scope": scope,
        "programs": items,
        "total": len(items),
        "pagination": {
            "total_all": total_all,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="list_learning_programs",
    description="查询当前讲师的学习项目清单",
    roles=["teacher"],
    parameter_docs={
        "scope": "列表视角：owned=我拥有的, cooperated=协同给我的, enrolled=我报名的",
        "keywords": "按标题/访问码模糊搜索",
        "page": "页码",
        "page_size": "每页数量",
        "fetch_all": "是否自动获取全量数据",
    },
)
async def list_learning_programs(
    client: UMUClient,
    scope: str,
    keywords: str | None = None,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询当前讲师的学习项目清单."""
    return await _list_personal_learning_programs_impl(
        client, scope, keywords, page, page_size, fetch_all
    )


@umu_operation(
    name="list_personal_learning_programs",
    description="查询当前管理员作为普通用户的学习项目清单",
    roles=["admin"],
    parameter_docs={
        "scope": "列表视角：owned=我拥有的, cooperated=协同给我的, enrolled=我报名的",
        "keywords": "按标题/访问码模糊搜索",
        "page": "页码",
        "page_size": "每页数量",
        "fetch_all": "是否自动获取全量数据",
    },
)
async def list_personal_learning_programs(
    client: UMUClient,
    scope: str,
    keywords: str | None = None,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询当前管理员作为普通用户的学习项目清单."""
    return await _list_personal_learning_programs_impl(
        client, scope, keywords, page, page_size, fetch_all
    )


@umu_operation(
    name="create_learning_program",
    description="创建学习项目（不含课程）",
    roles=["teacher"],
    parameter_docs={
        "title": "学习项目标题",
        "desc_plain": "纯文本介绍",
        "desc_richtext": "富文本介绍（HTML）",
        "cover_image_path": "本地封面图路径",
        "bg_image_path": "本地背景图路径",
        "cover_image_url": "封面图 URL，与 cover_image_path 二选一，URL 优先",
        "bg_image_url": "背景图 URL，与 bg_image_path 二选一，URL 优先",
        "tags": "标签列表",
        "category_ids": "分类 ID 列表",
        "category_names": "分类名称列表，与 category_ids 二选一，名称优先",
        "start_time": "开始时间戳字符串",
        "end_time": "结束时间戳字符串",
        "skin_id": "皮肤 ID",
        "pc_skin_id": "PC 皮肤 ID",
        "show_banner": "是否显示 banner",
        "unlock_type": "解锁类型",
        "show_type": "显示类型",
        "open_module": "开放模块",
        "sort": "排序方式",
        "enable_certificate": "是否启用证书",
    },
)
async def create_learning_program(
    client: UMUClient,
    title: str,
    desc_plain: str = "",
    desc_richtext: str = "",
    cover_image_path: str | None = None,
    bg_image_path: str | None = None,
    cover_image_url: str | None = None,
    bg_image_url: str | None = None,
    tags: list[str] | None = None,
    category_ids: list[str] | None = None,
    category_names: list[str] | None = None,
    start_time: str = "",
    end_time: str = "",
    skin_id: int = 1,
    pc_skin_id: int = 1,
    show_banner: bool = True,
    unlock_type: int = 1,
    show_type: int = 1,
    open_module: int = 1,
    sort: str = "asc",
    enable_certificate: bool = False,
) -> dict[str, Any]:
    """创建学习项目.

    返回包含 program_id 的字典，可用于后续添加课程。
    """
    builder = ProgramBuilder(client, client.base_url)
    return builder.create_program(
        title=title,
        desc_plain=desc_plain,
        desc_richtext=desc_richtext,
        cover_path=cover_image_path or "",
        bg_path=bg_image_path or "",
        cover_image_url=cover_image_url or "",
        bg_image_url=bg_image_url or "",
        tags=tags,
        category_ids=category_ids,
        category_names=category_names,
        start_time=start_time,
        end_time=end_time,
        skin_id=skin_id,
        pc_skin_id=pc_skin_id,
        show_banner=show_banner,
        unlock_type=unlock_type,
        show_type=show_type,
        open_module=open_module,
        sort=sort,
        enable_certificate=enable_certificate,
    )


@umu_operation(
    name="get_learning_program",
    description="获取学习项目详情",
    roles=["teacher"],
    parameter_docs={"program_id": "学习项目 ID"},
)
async def get_learning_program(client: UMUClient, program_id: str) -> dict[str, Any]:
    """获取学习项目详情.

    返回项目基本信息、模块列表及课程关系，可用于修改前查看当前结构。
    """
    builder = ProgramBuilder(client, client.base_url)
    return builder.get_program(program_id)


@umu_operation(
    name="update_learning_program",
    description="修改学习项目基本信息",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "title": "学习项目标题",
        "desc_plain": "纯文本介绍",
        "desc_richtext": "富文本介绍（HTML）",
        "cover_image_path": "本地封面图路径，传空字符串表示清除",
        "bg_image_path": "本地背景图路径，传空字符串表示清除",
        "cover_image_url": "封面图 URL，与 cover_image_path 二选一，URL 优先",
        "bg_image_url": "背景图 URL，与 bg_image_path 二选一，URL 优先",
        "tags": "标签列表",
        "category_ids": "分类 ID 列表",
        "category_names": "分类名称列表，与 category_ids 二选一，名称优先",
        "skin_id": "皮肤 ID",
        "pc_skin_id": "PC 皮肤 ID",
        "show_banner": "是否显示 banner",
        "unlock_type": "解锁类型",
        "show_type": "显示类型",
        "open_module": "开放模块",
        "sort": "排序方式",
        "enable_certificate": "是否启用证书",
    },
)
async def update_learning_program(
    client: UMUClient,
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
) -> dict[str, Any]:
    """修改学习项目基本信息.

    未提供的字段保持原值；需要修改的字段显式传入。
    """
    builder = ProgramBuilder(client, client.base_url)
    return builder.update_program(
        program_id=program_id,
        title=title,
        desc_plain=desc_plain,
        desc_richtext=desc_richtext,
        cover_path=cover_image_path,
        bg_path=bg_image_path,
        cover_image_url=cover_image_url,
        bg_image_url=bg_image_url,
        tags=tags,
        category_ids=category_ids,
        category_names=category_names,
        skin_id=skin_id,
        pc_skin_id=pc_skin_id,
        show_banner=show_banner,
        unlock_type=unlock_type,
        show_type=show_type,
        open_module=open_module,
        sort=sort,
        enable_certificate=enable_certificate,
    )


@umu_operation(
    name="add_courses_to_learning_program",
    description="将课程按模块添加到学习项目",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "modules": '模块列表，每项包含 module_title 与 course_ids，例如 [{"module_title": "阶段一", "course_ids": ["7329920"]}]',
    },
)
async def add_courses_to_learning_program(
    client: UMUClient,
    program_id: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    """将课程按模块添加到学习项目.

    若 module_id 为空且提供 module_title，则自动创建新模块。
    """
    builder = ProgramBuilder(client, client.base_url)
    return builder.add_courses(program_id=program_id, modules=modules)


@umu_operation(
    name="remove_courses_from_learning_program",
    description="从学习项目中移除课程",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "module_group_ids": "模块课程关系 ID 列表（来自 group_list 中的 id）",
    },
)
async def remove_courses_from_learning_program(
    client: UMUClient,
    program_id: str,
    module_group_ids: list[str],
) -> dict[str, Any]:
    """从学习项目中移除课程."""
    builder = ProgramBuilder(client, client.base_url)
    return builder.remove_courses(program_id=program_id, module_group_ids=module_group_ids)


@umu_operation(
    name="update_learning_program_modules",
    description="修改学习项目的模块信息",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "modules": '模块列表，每项包含 module_id 与可修改字段，例如 [{"module_id": "197797", "module_title": "新标题", "module_desc_richtext": "<p>描述</p>", "group_list": [...]}]',
    },
)
async def update_learning_program_modules(
    client: UMUClient,
    program_id: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    """修改学习项目的模块信息.

    可修改模块标题、模块描述、模块富文本描述、模块内课程顺序及是否必修。
    group_list 中的 id 为模块课程关系 ID（module_group_id）。
    """
    builder = ProgramBuilder(client, client.base_url)
    return builder.update_modules(program_id=program_id, modules=modules)


@umu_operation(
    name="configure_program_certificate",
    description="配置学习项目证书",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "theme_id": "证书模板 ID，不填则使用第一个可用模板",
        "text": "证书正文",
        "teacher_name": "讲师姓名",
    },
)
async def configure_program_certificate(
    client: UMUClient,
    program_id: str,
    theme_id: str = "",
    text: str = "",
    teacher_name: str = "",
) -> dict[str, Any]:
    """配置学习项目证书."""
    builder = ProgramBuilder(client, client.base_url)
    return builder.configure_certificate(program_id, theme_id, text, teacher_name)


@umu_operation(
    name="set_program_points_status",
    description="开启或关闭学习项目积分",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "enabled": "是否开启积分",
    },
)
async def set_program_points_status(
    client: UMUClient,
    program_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """开启或关闭学习项目积分."""
    builder = ProgramBuilder(client, client.base_url)
    return builder.set_points_status(program_id, enabled)


@umu_operation(
    name="search_courses_for_program",
    description="搜索可加入学习项目的课程",
    roles=["teacher"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "keywords": "搜索关键词",
        "creater_name": "按创建人筛选",
        "page": "页码",
        "page_size": "每页数量",
    },
)
async def search_courses_for_program(
    client: UMUClient,
    program_id: str,
    keywords: str = "",
    creater_name: str = "",
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """搜索可加入学习项目的课程."""
    builder = ProgramBuilder(client, client.base_url)
    items, total = builder.search_courses(
        program_id, keywords, creater_name, page, page_size
    )
    return {
        "courses": items,
        "total": total,
        "pagination": {"current_page": page, "page_size": page_size},
    }


@umu_operation(
    name="list_program_participants",
    description="查询学习项目的学员名单",
    roles=["teacher", "admin"],
    capabilities=["program_management"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "status_filter": "学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        "include_disabled": "是否包含已禁用账号，默认包含",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_program_participants(
    client: UMUClient,
    program_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习项目的学员名单.

    返回学习项目下所有学员的完成状态，支持按完成状态筛选和是否包含已禁用账号。
    结果中的 modules / courses 字段已根据 table_head 动态列深度格式化。
    讲师及以上权限角色可调用。
    """
    manager = ProgramStudentManager(client, client.base_url)
    return manager.list_participants(
        program_id=program_id,
        status_filter=status_filter,
        include_disabled=include_disabled,
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )


@umu_operation(
    name="list_program_learning_tasks",
    description="查询学习项目的学习任务学员名单",
    roles=["teacher", "admin"],
    capabilities=["program_management"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "status_filter": "学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        "include_disabled": "是否包含已禁用账号，默认包含",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_program_learning_tasks(
    client: UMUClient,
    program_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习项目的学习任务学员名单.

    返回被分配给该学习项目作为学习任务的学员列表，支持按完成状态筛选和是否显示禁用账号。
    结果中的 modules / courses 字段已根据 table_head 动态列深度格式化。
    讲师及以上权限角色可调用。
    """
    manager = ProgramStudentManager(client, client.base_url)
    return manager.list_learning_tasks(
        program_id=program_id,
        status_filter=status_filter,
        include_disabled=include_disabled,
        page=page,
        page_size=page_size,
        fetch_all=fetch_all,
    )
