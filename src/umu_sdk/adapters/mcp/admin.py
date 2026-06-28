# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU 管理员端 MCP Server.

将 UMU 平台的管理员操作暴露为 MCP Tools，供 AI 自主编排账号管理、
数据查询等后台运营流程。

Usage:
    # 启动 MCP Server
    python -m umu_sdk.adapters.mcp.admin

    # 或使用 CLI
    umu-skills-admin

Environment Variables:
    UMU_BASE_URL: UMU 基础 URL (默认: https://www.umu.cn)
    UMU_ADMIN_USERNAME: 管理员登录用户名
    UMU_ADMIN_PASSWORD: 管理员登录密码
    MCP_LOG_LEVEL: 日志级别 (DEBUG|INFO|WARNING|ERROR，默认: INFO)
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Annotated, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import load_credentials_with_source
from .utils import (
    format_login_summary,
    fuzzy_filter_items,
    get_login_identity,
    report_pagination_progress,
)
from ...core.admin_models import (
    AdminAccount,
    AdminAccountRaw,
    AdminClass,
    AdminClassRaw,
    AdminCourse,
    AdminCourseAuditRecord,
    AdminCourseAuditRecordRaw,
    AdminCourseBlacklistEntry,
    AdminCourseBlacklistEntryRaw,
    AdminCourseCategory,
    AdminCourseCategoryRaw,
    AdminCourseRaw,
    AdminLearningProgram,
    AdminLearningProgramRaw,
    format_timestamp_beijing,
    Instructor,
    InstructorRaw,
    LearningRecord,
    LearningRecordRaw,
    TeachingRecord,
    TeachingRecordAuditStatus,
    TeachingRecordRaw,
    UserTask,
    UserTaskRaw,
)
from .session import SessionManager
from .export_engine import ExportEngine
from .shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)
from . import prompts

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    """配置结构化日志."""
    import sys

    level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = os.getenv(
        "MCP_LOG_FORMAT",
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger("umu.mcp.admin")
    root.setLevel(level)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger("umu.mcp.admin")

# ---------------------------------------------------------------------------
# 全局实例（由 lifespan 管理）
# ---------------------------------------------------------------------------
_umu_client: UMUClient | None = None
_session_manager: SessionManager | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """应用生命周期管理.

    启动时初始化会话管理器并创建默认会话；关闭时释放所有会话资源.
    默认从 UMU_ADMIN_USERNAME / UMU_ADMIN_PASSWORD 读取管理员账号自动登录.
    未配置凭据时正常启动，提示手动调用 adm_login 登录.
    """
    global _umu_client, _session_manager

    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    # 每次启动都重新读取管理员账号凭据；优先级：显式参数/环境变量 > .env > 加密凭证
    username, password, source = load_credentials_with_source("admin")

    _session_manager = SessionManager(
        base_url=base_url,
    )

    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    if username and password:
        try:
            await _session_manager.login_session(
                default_session.session_id, username, password, credential_source=source.value
            )
            default_session.credential_source = source.value
            identity = get_login_identity(_umu_client)
            logger.info(
                "默认会话已自动登录: %s",
                format_login_summary(username, source.value, identity),
            )
        except Exception as e:
            logger.error("默认会话自动登录失败: %s", e)
    else:
        logger.info("未配置管理员账号凭据，请调用 adm_login 或 adm_create_session")

    logger.info(
        "UMU 管理员端服务已启动，目标: %s",
        base_url,
    )

    yield {"client": _umu_client, "session_manager": _session_manager}

    if _session_manager:
        _session_manager.close_all()
        _session_manager = None
    _umu_client = None
    logger.info("UMU 管理员端服务已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-admin",
    instructions="""UMU 学习平台管理员端 MCP 服务。

提供平台管理相关的原子化操作，包括：
- 账号管理：创建、查询、删除学员/讲师账号
- 部门管理：查询部门树/子部门/详情/成员、创建/更新/排序/删除部门、添加/移动/移除部门成员
- 分组管理：创建/重命名/删除分组、查询分组成员/管理员、添加/移除成员与管理员
- 课程管理：查询企业课程清单（支持按名称/标签/访问码/创建人/权限/审核状态等筛选）
- 学习数据查询：学员学习进度、课程统计数据
- 系统运营：批量操作、数据导出

AI 使用本服务时，应先确保管理员已登录，然后按需求调用对应工具。
""",
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_client(session_id: str | None = None) -> UMUClient:
    """获取客户端实例.

    Args:
        session_id: 会话 ID。如果提供，从会话池中获取对应客户端；
                   如果不提供，使用默认全局客户端（向后兼容）。

    Raises:
        RuntimeError: 客户端未初始化或会话不存在/已过期
    """
    if session_id:
        if _session_manager is None:
            raise RuntimeError("会话管理器未初始化")
        session = _session_manager.get_session_sync(session_id)
        if session is None:
            raise RuntimeError(f"会话不存在或已过期: {session_id}")
        return session.client

    # 向后兼容：使用默认全局客户端
    if _umu_client is None:
        raise RuntimeError("UMU 客户端未初始化，请先登录")
    return _umu_client


def _require_auth(client: UMUClient) -> str | None:
    """检查客户端认证状态.

    Returns:
        None 表示认证正常；否则返回错误信息字符串.
    """
    if not client.auth.is_authenticated():
        return "当前未登录或 Token 已过期，请先调用 adm_login 登录"
    return None


def _ok(
    data: Any = None,
    next_action: str = "proceed",
    suggested_action: str = "",
    **kwargs: Any,
) -> str:
    """构造成功返回结构."""
    result: dict[str, Any] = {
        "success": True,
        "data": data,
        "error_code": "",
        "error_message": "",
        "suggested_action": suggested_action,
        "next_action": next_action,
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


def _err(
    error_code: str,
    error_message: str,
    suggested_action: str = "",
    data: Any = None,
    **kwargs: Any,
) -> str:
    """构造失败返回结构."""
    result: dict[str, Any] = {
        "success": False,
        "data": data,
        "error_code": error_code,
        "error_message": error_message,
        "suggested_action": suggested_action,
        "next_action": "",
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


_ADMIN_SESSION_CONFIG = SessionToolConfig(
    role="adm",
    role_label="管理员",
    tool_domain_hint="管理员端相关 Tool",
    login_success_suffix="现在可以调用管理员端相关 Tool",
    check_auth_success_suffix="管理相关 Tool",
    create_session_suggested_action="保存 session_id，后续调用 tool 时传入此参数",
    create_session_with_password=True,
    include_is_authenticated_in_session=True,
)


# ---------------------------------------------------------------------------
# Tools: 认证
# ---------------------------------------------------------------------------


mcp.tool()(
    make_login_tool(
        _ADMIN_SESSION_CONFIG,
        get_client=_get_client,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_check_auth_tool(
        _ADMIN_SESSION_CONFIG,
        get_client=_get_client,
        ok=_ok,
        err=_err,
    )
)


@mcp.tool()
async def adm_get_user_info(
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取当前登录管理员的用户信息.

    触发条件：需要了解当前登录账号的基本信息时调用。
    前置依赖：需先调用 adm_login 完成登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        r = client.get(client.desktop_url("/uapi/v1/user/get"))
        data = r.get("data", {})
        return _ok(
            data=data,
            next_action="proceed",
        )
    except Exception as e:
        return _err(
            error_code="GET_USER_INFO_FAILED",
            error_message=str(e),
            suggested_action="检查网络连接或登录状态",
        )


# ---------------------------------------------------------------------------
# Tools: 会话管理
# ---------------------------------------------------------------------------


mcp.tool()(
    make_create_session_tool(
        _ADMIN_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_list_sessions_tool(
        _ADMIN_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_destroy_session_tool(
        _ADMIN_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)

# ---------------------------------------------------------------------------
# Tools: 账号管理
# ---------------------------------------------------------------------------

# 角色类型映射
_ROLE_TYPE_MAP = {
    1: "学员",
    2: "讲师",
    3: "学习负责人",
    4: "系统管理员",
    5: "子管理员",
}

# 账号状态文本映射
# 注意: 不同企业的 UMU 平台状态码映射可能不同。
# 观察到的常见映射: 1=已启用, 2=定时禁用, 3=已禁用(立即)
# 请以 adm_list_accounts 的 account_status 筛选结果为准确认实际映射。
_STATUS_TEXT_MAP = {
    0: "待加入",
    1: "已启用",
    2: "定时禁用",
    3: "已禁用",
}


def _get_status_text(status_code: int) -> str:
    """将状态码转换为人读文本."""
    return _STATUS_TEXT_MAP.get(status_code, f"未知状态({status_code})")


def _get_user_name_by_id(client: UMUClient, umu_id: str) -> str:
    """通过 umu_id 查询用户姓名.

    Args:
        client: UMUClient 实例
        umu_id: 用户 umu_id

    Returns:
        用户姓名，找不到返回空字符串。
    """
    try:
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getUserList"),
            params={
                "is_manager": "0",
                "page": "1",
                "size": "100",
                "group_operator": "intersection",
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            return ""

        user_list = resp.get("data", {}).get("list", [])
        for user in user_list:
            if str(user.get("umu_id", "")) == umu_id:
                return user.get("user_name", "") or ""
        return ""
    except Exception:
        return ""


def _parse_effective_time(time_str: str | None) -> int:
    """解析生效时间为 Unix 时间戳（东八区）.

    Args:
        time_str: 时间字符串。None/空/"immediate" 表示立即生效；
                 否则为 ISO 格式日期时间如 "2026-06-12T09:00"。

    Returns:
        Unix 时间戳（秒），0 表示立即生效。

    Raises:
        ValueError: 时间格式无法解析。
    """
    if not time_str or time_str.lower() == "immediate":
        return 0

    formats = (
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(time_str, fmt)
            # 视为东八区时间
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            return int(dt.timestamp())
        except ValueError:
            continue

    raise ValueError(f"无法解析时间格式: {time_str}，支持的格式: YYYY-MM-DDTHH:MM")


def _find_user_by_email(client: UMUClient, email: str) -> dict[str, Any] | None:
    """通过邮箱查找用户.

    Args:
        client: UMUClient 实例
        email: 用户邮箱

    Returns:
        用户信息字典，找不到返回 None。
    """
    try:
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getUserList"),
            params={
                "is_manager": "0",
                "page": "1",
                "size": "100",
                "group_operator": "intersection",
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            return None

        user_list = resp.get("data", {}).get("list", [])
        for user in user_list:
            if user.get("email", "").lower() == email.lower():
                return user
        return None
    except Exception:
        return None


def _build_learning_records_search_condition(
    start_day: str | None = None,
    end_day: str | None = None,
    uids: list[str] | None = None,
    course_title: str | None = None,
    department_ids: str | None = None,
    group_ids: str | None = None,
    class_ids: list[str] | None = None,
) -> dict[str, Any]:
    """构建学习记录查询的 search_condition JSON 对象.

    Args:
        start_day: 最后学习时间起始日期，YYYY-MM-DD
        end_day: 最后学习时间结束日期，YYYY-MM-DD
        uids: 学员 UMU ID 数组
        course_title: 课程名称模糊搜索关键词
        department_ids: 部门 ID 逗号分隔字符串
        group_ids: 企业分组 ID 逗号分隔字符串
        class_ids: 班级 ID 数组

    Returns:
        search_condition 字典，将被 JSON 序列化后作为查询参数。
    """
    condition: dict[str, Any] = {}

    if start_day:
        condition["start_date"] = start_day
    if end_day:
        condition["end_date"] = end_day
    if uids:
        condition["uids"] = uids
    if course_title:
        condition["group_title"] = course_title
    if department_ids:
        condition["department_ids"] = [d.strip() for d in department_ids.split(",") if d.strip()]
    if group_ids:
        condition["enterprise_group_ids"] = [g.strip() for g in group_ids.split(",") if g.strip()]
    if class_ids:
        condition["class_ids"] = class_ids

    return condition


async def _resolve_class_names(
    client: UMUClient,
    class_names: str,
) -> list[str] | None:
    """通过班级名称关键词搜索获取匹配的班级 IDs.

    Args:
        client: UMUClient 实例
        class_names: 班级名称关键词，多个用逗号分隔

    Returns:
        匹配的班级 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: class-list 接口返回错误。
    """
    keywords_list = [c.strip() for c in class_names.split(",") if c.strip()]
    if not keywords_list:
        return None

    matched_ids: list[str] = []
    for keyword in keywords_list:
        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/class-list"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "page": "1",
                "size": "100",
            },
        )

        if resp.get("error_code") != 0:
            msg = resp.get("error_message", "")
            raise RuntimeError(f"查询班级列表失败: {msg}" if msg else "查询班级列表失败")

        class_list = resp.get("data", {}).get("list", [])
        found = False
        for cls in class_list:
            if keyword.lower() in (cls.get("name", "") or "").lower():
                matched_ids.append(str(cls.get("id", "")))
                found = True

        if not found:
            return None

    return matched_ids


async def _resolve_student_keywords(
    client: UMUClient,
    keywords: str,
) -> list[str] | None:
    """通过学员关键词搜索获取匹配的 uids.

    Args:
        client: UMUClient 实例
        keywords: 学员姓名/邮箱/手机号/用户名关键词

    Returns:
        匹配的 uids 列表，无匹配返回 None。

    Raises:
        RuntimeError: user-list 接口返回错误。
    """
    resp = client.get(
        client.desktop_url("/uapi/v1/enterprise/user-list"),
        params={
            "t": str(int(datetime.now().timestamp() * 1000)),
            "keyword": keywords,
            "page": "1",
            "size": "50",
        },
    )

    if resp.get("error_code") != 0:
        msg = resp.get("error_message", "")
        raise RuntimeError(f"搜索学员失败: {msg}" if msg else "搜索学员失败")

    user_list = resp.get("data", {}).get("list", [])
    if not user_list:
        return None

    return [str(user.get("id", "")) for user in user_list if user.get("id")]


async def _resolve_department_names(
    client: UMUClient,
    names: str,
) -> list[str] | None:
    """通过部门名称关键词搜索获取匹配的部门 IDs.

    Args:
        client: UMUClient 实例
        names: 部门名称关键词，多个用逗号分隔

    Returns:
        匹配的部门 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: 部门接口返回错误。
    """
    keywords_list = [n.strip() for n in names.split(",") if n.strip()]
    if not keywords_list:
        return None

    resp = client.get(
        client.desktop_url("/uapi/v1/department/get-departments-by-managerid"),
        params={
            "t": str(int(datetime.now().timestamp() * 1000)),
            "type": "2",
        },
    )

    if resp.get("error_code") != 0:
        msg = resp.get("error_message", "")
        raise RuntimeError(f"查询部门列表失败: {msg}" if msg else "查询部门列表失败")

    department_list = resp.get("data", {}).get("department_list", [])

    def _walk_departments(depts: list[dict[str, Any]]) -> list[str]:
        matched: list[str] = []
        for dept in depts:
            dept_name = (dept.get("department_name", "") or "").lower()
            for kw in keywords_list:
                if kw.lower() in dept_name:
                    matched.append(str(dept.get("department_id", "")))
                    break
            # 递归检查子部门
            children = dept.get("child_path", []) or []
            if children:
                matched.extend(_walk_departments(children))
        return matched

    matched_ids = _walk_departments(department_list)
    if not matched_ids:
        return None

    # 去重并保持顺序
    seen: set[str] = set()
    unique_ids: list[str] = []
    for did in matched_ids:
        if did and did not in seen:
            seen.add(did)
            unique_ids.append(did)
    return unique_ids


async def _resolve_group_names(
    client: UMUClient,
    names: str,
) -> list[str] | None:
    """通过分组名称关键词搜索获取匹配的分组 IDs.

    Args:
        client: UMUClient 实例
        names: 分组名称关键词，多个用逗号分隔

    Returns:
        匹配的分组 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: 分组接口返回错误。
    """
    keywords_list = [n.strip() for n in names.split(",") if n.strip()]
    if not keywords_list:
        return None

    matched_ids: set[str] = set()
    page = 1
    max_pages = 50
    page_size = 100
    records_fetched = 0

    while page <= max_pages:
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getGroupList"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "page": str(page),
                "size": str(page_size),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            msg = resp.get("error", "")
            raise RuntimeError(f"查询分组列表失败: {msg}" if msg else "查询分组列表失败")

        group_list = resp.get("data", {}).get("list", [])
        if not group_list:
            break

        for group in group_list:
            group_name = (group.get("group_name", "") or "").lower()
            for kw in keywords_list:
                if kw.lower() in group_name:
                    matched_ids.add(str(group.get("id", "")))
                    break

        records_fetched += len(group_list)
        total = int(resp.get("data", {}).get("total", 0) or 0)

        report_pagination_progress(
            "_resolve_group_names",
            page,
            records_fetched,
            total,
            page_size,
            is_complete=total > 0 and records_fetched >= total,
        )

        if total > 0 and records_fetched >= total:
            break

        page += 1
        if page > max_pages:
            report_pagination_progress(
                "_resolve_group_names",
                page,
                records_fetched,
                total,
                page_size,
                is_safety_limit=True,
            )
            break

    if not matched_ids:
        return None
    return list(matched_ids)


async def _resolve_class_names_all(
    client: UMUClient,
    names: str,
) -> list[str] | None:
    """通过班级名称关键词搜索获取匹配的班级 IDs（全量翻页）.

    Args:
        client: UMUClient 实例
        names: 班级名称关键词，多个用逗号分隔

    Returns:
        匹配的班级 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: class-list 接口返回错误。
    """
    keywords_list = [n.strip() for n in names.split(",") if n.strip()]
    if not keywords_list:
        return None

    matched_ids: set[str] = set()
    page = 1
    max_pages = 50
    page_size = 100
    records_fetched = 0

    while page <= max_pages:
        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/class-list"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "page": str(page),
                "size": str(page_size),
            },
        )

        if resp.get("error_code") != 0:
            msg = resp.get("error_message", "")
            raise RuntimeError(f"查询班级列表失败: {msg}" if msg else "查询班级列表失败")

        class_list = resp.get("data", {}).get("list", [])
        if not class_list:
            break

        for cls in class_list:
            cls_name = (cls.get("name", "") or "").lower()
            for kw in keywords_list:
                if kw.lower() in cls_name:
                    matched_ids.add(str(cls.get("id", "")))
                    break

        records_fetched += len(class_list)
        total = int(resp.get("data", {}).get("total", 0) or 0)

        report_pagination_progress(
            "_resolve_class_names_all",
            page,
            records_fetched,
            total,
            page_size,
            is_complete=total > 0 and records_fetched >= total,
        )

        if total > 0 and records_fetched >= total:
            break

        page += 1
        if page > max_pages:
            report_pagination_progress(
                "_resolve_class_names_all",
                page,
                records_fetched,
                total,
                page_size,
                is_safety_limit=True,
            )
            break

    if not matched_ids:
        return None
    return list(matched_ids)


async def _resolve_user_keywords(
    client: UMUClient,
    keywords: str,
) -> list[str] | None:
    """通过用户关键词搜索获取匹配的 umu_id 列表.

    Args:
        client: UMUClient 实例
        keywords: 用户姓名/邮箱/手机号/用户名关键词（单个）

    Returns:
        匹配的 umu_id 列表，无匹配返回 None。

    Raises:
        RuntimeError: search-user 接口返回错误。
    """
    resp = client.get(
        client.desktop_url("/uapi/v1/enterprise/search-user"),
        params={
            "t": str(int(datetime.now().timestamp() * 1000)),
            "keyword": keywords,
            "condition": "",
            "page": "1",
            "size": "50",
        },
    )

    if resp.get("error_code") != 0:
        msg = resp.get("error_message", "")
        raise RuntimeError(f"搜索用户失败: {msg}" if msg else "搜索用户失败")

    user_list = resp.get("data", {}).get("list", [])
    if not user_list:
        return None

    return [str(user.get("umu_id", "")) for user in user_list if user.get("umu_id")]


async def _resolve_teacher_keywords(
    client: UMUClient,
    keywords: str,
) -> list[str] | None:
    """通过讲师关键词搜索获取匹配的 umu_id 列表.

    Args:
        client: UMUClient 实例
        keywords: 讲师邮箱/手机号/用户名/姓名关键词，多个用逗号分隔

    Returns:
        匹配的 umu_id 列表，无匹配返回 None。
    """
    parts = [p.strip() for p in keywords.split(",") if p.strip()]
    result: list[str] = []
    for part in parts:
        try:
            ids = await _resolve_user_keywords(client, part)
        except Exception:
            ids = None
        if ids:
            result.extend(ids)
    return list(dict.fromkeys(result)) if result else None


@mcp.tool()
async def adm_create_account(
    user_name: Annotated[str, Field(description="用户姓名（必填）")],
    accounts: Annotated[
        str,
        Field(description="邮箱地址，多个用逗号分隔，如 'user1@example.com,user2@example.com'"),
    ],
    role_type: Annotated[
        int,
        Field(
            description="角色类型：1=学员, 2=讲师, 3=学习负责人, 4=系统管理员",
            ge=1,
            le=4,
        ),
    ],
    number: Annotated[
        str | None,
        Field(default=None, description="员工编号（可选）"),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分组ID列表，多个用逗号分隔，如 '177124,177125'（可选）",
        ),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="部门ID列表，多个用逗号分隔，如 '251103,251104'（可选）",
        ),
    ] = None,
    platform_permission: Annotated[
        int,
        Field(default=1, description="平台权限，默认1"),
    ] = 1,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """创建新账号.

    触发条件：当需要创建学员/讲师/负责人/系统管理员账号时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：在 UMU 平台创建新用户账号。

    创建流程（三步验证）：
    1. **预检**：调用 /uapi/v1/enterprise/add-user-check 验证参数合法性
    2. **创建**：调用 /ajax/enterprise/addUser 创建用户
    3. **确认**：检查响应中的 exists/success 字段，确认实际创建结果
    4. （可选）如有 group_ids，将用户添加到对应分组
    5. （可选）如有 department_ids，将用户添加到对应部门

    分组/部门添加失败为**非致命**错误，账号本身创建成功即视为整体成功。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 解析邮箱列表
    account_list = [a.strip() for a in accounts.split(",") if a.strip()]
    if not account_list:
        return _err(
            error_code="INVALID_ACCOUNTS",
            error_message="邮箱地址不能为空",
            suggested_action="提供至少一个有效的邮箱地址",
        )

    # 解析可选的分组和部门
    group_id_list: list[str] = []
    dept_id_list: list[str] = []
    if group_ids:
        group_id_list = [g.strip() for g in group_ids.split(",") if g.strip()]
    if department_ids:
        dept_id_list = [d.strip() for d in department_ids.split(",") if d.strip()]

    # -----------------------------------------------------------------------
    # 步骤 1: 预检 — 验证参数是否合法
    # -----------------------------------------------------------------------
    try:
        precheck_params: dict[str, str] = {
            "user_name": user_name,
            "role_type": str(role_type),
            "platform_permission": str(platform_permission),
        }
        # 预检只传第一个邮箱（与前端行为一致）
        precheck_params["email"] = account_list[0]
        if number:
            precheck_params["number"] = number
        if group_id_list:
            precheck_params["add_enterprise_group_ids"] = group_id_list[0]
        if dept_id_list:
            precheck_params["add_department_ids"] = dept_id_list[0]

        precheck_resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/add-user-check"),
            params=precheck_params,
        )

        # 预检不通过
        if precheck_resp.get("error_code") != 0:
            return _err(
                error_code="PRECHECK_FAILED",
                error_message=precheck_resp.get("error_message", "预检未通过，参数可能不合法"),
                suggested_action="检查邮箱是否已存在、角色权限是否足够，或员工号是否重复",
            )
        # data 不为 True 也表示预检不通过
        if precheck_resp.get("data") is not True:
            return _err(
                error_code="PRECHECK_REJECTED",
                error_message="该用户信息无法添加，可能邮箱已被占用或超出账号配额",
                suggested_action="更换邮箱，或联系管理员确认企业账号配额",
            )
    except Exception as e:
        logger.warning("预检请求异常: %s", e)
        # 预检失败不阻断，继续尝试创建（容错）

    # -----------------------------------------------------------------------
    # 步骤 2: 创建用户
    # -----------------------------------------------------------------------
    create_data: dict[str, Any] = {
        "user_name": user_name,
        "role_type": str(role_type),
        "platform_permission": str(platform_permission),
    }
    if number:
        create_data["number"] = number
    for account in account_list:
        create_data.setdefault("accounts[]", []).append(account)

    try:
        resp = client.post(
            client.desktop_url("/ajax/enterprise/addUser"),
            data=create_data,
        )
    except Exception as e:
        return _err(
            error_code="CREATE_USER_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或管理员权限后重试",
        )

    # -----------------------------------------------------------------------
    # 步骤 3: 确认 — 解析创建结果
    # -----------------------------------------------------------------------
    resp_data = resp.get("data", {})
    success_count = resp_data.get("success", 0)
    exists_count = resp_data.get("exists", 0)
    total_count = resp_data.get("total", len(account_list))
    umu_ids = resp_data.get("umu_ids", [])

    # 全部已存在
    if exists_count == total_count and success_count == 0:
        return _err(
            error_code="ALL_ACCOUNTS_EXIST",
            error_message=f"{exists_count}/{total_count} 个邮箱已存在，未创建任何账号",
            suggested_action="更换邮箱后重试，或使用 adm_list_accounts 查询已有账号",
        )

    # 部分已存在、部分创建成功
    if exists_count > 0 and success_count > 0:
        # 继续执行分组/部门添加，但标记警告
        partial_warning = {
            "type": "partial_exists",
            "exists_count": exists_count,
            "success_count": success_count,
            "message": f"{exists_count} 个邮箱已存在（跳过），{success_count} 个账号创建成功",
        }
    else:
        partial_warning = None

    # 全部失败且没有已存在的
    if success_count == 0 and exists_count == 0:
        return _err(
            error_code="CREATE_USER_FAILED",
            error_message=resp.get("error", "创建用户失败"),
            suggested_action="检查参数格式或联系管理员确认权限",
        )

    # -----------------------------------------------------------------------
    # 步骤 4: 添加到分组（非致命）
    # -----------------------------------------------------------------------
    warnings: list[dict[str, Any]] = []
    if partial_warning:
        warnings.append(partial_warning)

    groups_added: list[str] = []
    if group_id_list and umu_ids:
        for gid in group_id_list:
            try:
                group_resp = client.post(
                    client.desktop_url("/ajax/enterprise/updateGroupUser"),
                    data={
                        "enterprise_group_id[]": gid,
                        "member_id": json.dumps(umu_ids),
                        "is_delete": "0",
                    },
                )
                if group_resp.get("status") is True or group_resp.get("error_code") == 0:
                    groups_added.append(gid)
                else:
                    warnings.append(
                        {
                            "type": "add_group_failed",
                            "group_id": gid,
                            "message": group_resp.get("error", "添加到分组失败"),
                        }
                    )
            except Exception as e:
                warnings.append(
                    {
                        "type": "add_group_error",
                        "group_id": gid,
                        "message": str(e),
                    }
                )
                logger.warning("添加到分组失败: group_id=%s, error=%s", gid, e)

    # -----------------------------------------------------------------------
    # 步骤 5: 添加到部门（非致命）
    # -----------------------------------------------------------------------
    departments_added: list[str] = []
    if dept_id_list and umu_ids:
        try:
            dept_resp = client.post(
                client.desktop_url("/uapi/v1/department/add-member"),
                data={
                    "umu_ids": json.dumps(umu_ids),
                    "add_department_ids": json.dumps(dept_id_list),
                },
            )
            if dept_resp.get("error_code") == 0:
                departments_added = dept_id_list
            else:
                warnings.append(
                    {
                        "type": "add_department_failed",
                        "message": dept_resp.get("error_message", "添加到部门失败"),
                    }
                )
        except Exception as e:
            warnings.append(
                {
                    "type": "add_department_error",
                    "message": str(e),
                }
            )
            logger.warning("添加到部门失败: error=%s", e)

    result_data: dict[str, Any] = {
        "umu_ids": umu_ids,
        "user_name": user_name,
        "role_type": role_type,
        "role_name": _ROLE_TYPE_MAP.get(role_type, "未知"),
        "accounts": account_list,
        "created_count": success_count,
        "exists_count": exists_count,
        "groups_added": groups_added,
        "departments_added": departments_added,
    }
    if warnings:
        result_data["warnings"] = warnings

    return _ok(
        data=result_data,
        next_action="proceed",
        suggested_action="账号创建成功，可以继续创建其他账号或进行其他管理操作",
    )


@mcp.tool()
async def adm_update_account(
    umu_id: Annotated[
        str | None,
        Field(default=None, description="用户 umu_id，与 email 二选一"),
    ] = None,
    email: Annotated[
        str | None,
        Field(
            default=None,
            description="用户当前邮箱，与 umu_id 二选一。用于定位账号",
        ),
    ] = None,
    user_name: Annotated[
        str | None,
        Field(default=None, description="新姓名"),
    ] = None,
    new_email: Annotated[
        str | None,
        Field(default=None, description="新邮箱地址"),
    ] = None,
    login_name: Annotated[
        str | None,
        Field(default=None, description="新用户名（登录名）"),
    ] = None,
    phone: Annotated[
        str | None,
        Field(default=None, description="新手机号"),
    ] = None,
    number: Annotated[
        str | None,
        Field(default=None, description="新员工编号"),
    ] = None,
    role_type: Annotated[
        int | None,
        Field(
            default=None,
            description="角色类型：1=学员, 2=讲师, 3=学习负责人, 4=系统管理员, 5=子管理员",
            ge=1,
            le=5,
        ),
    ] = None,
    platform_permission: Annotated[
        int | None,
        Field(default=None, description="平台权限，不提供则保持原值"),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="部门ID列表，多个用逗号分隔，如 '251103,251104'。覆盖写入",
        ),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分组ID列表（普通成员），多个用逗号分隔，如 '177124,177125'。覆盖写入",
        ),
    ] = None,
    manager_group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="管理分组ID列表，多个用逗号分隔，如 '177124,177125'。覆盖写入。仅角色为 3/4/5 时允许",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """编辑企业账号信息.

    触发条件：需要修改已有账号的姓名、邮箱、用户名、手机号、工号、
    角色权限、平台权限、所属部门或所属分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改 UMU 平台上的账号信息。

    注意：
    - department_ids / group_ids / manager_group_ids 均为覆盖写入。
    - manager_group_ids 仅在角色为学习负责人(3)、系统管理员(4)或子管理员(5)时允许设置。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    if not umu_id and not email:
        return _err(
            error_code="MISSING_IDENTIFIER",
            error_message="必须提供 umu_id 或 email 之一",
            suggested_action="调用 adm_list_accounts 查询账号信息",
        )

    # -----------------------------------------------------------------------
    # 定位 umu_id
    # -----------------------------------------------------------------------
    if not umu_id and email:
        user = _find_user_by_email(client, email)
        if user is None:
            return _err(
                error_code="USER_NOT_FOUND",
                error_message=f"找不到邮箱为 {email} 的用户",
                suggested_action="检查邮箱是否正确，或调用 adm_list_accounts 查询",
            )
        umu_id = str(user.get("umu_id", ""))

    # -----------------------------------------------------------------------
    # 获取当前信息作为默认值与旧值
    # -----------------------------------------------------------------------
    try:
        current_resp = client.get(
            client.desktop_url("/ajax/enterprise/getUserInfo"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "umu_id": umu_id,
            },
        )
        current = (
            current_resp.get("data", {})
            if current_resp.get("status") is True or current_resp.get("error_code") == 0
            else {}
        )
    except Exception:
        current = {}

    user_info = current.get("user_info", {}) or {}

    def _get_current(field: str, default: Any = "") -> Any:
        return user_info.get(field, default) if user_info else default

    # -----------------------------------------------------------------------
    # manager_group_ids 角色校验
    # -----------------------------------------------------------------------
    if manager_group_ids is not None:
        effective_role = (
            role_type if role_type is not None else int(_get_current("role_type", 0) or 0)
        )
        if effective_role not in (3, 4, 5):
            return _err(
                error_code="INVALID_MANAGER_ROLE",
                error_message="只有学习负责人、系统管理员或子管理员才能被设置为分组管理员",
                suggested_action="移除 manager_group_ids，或先将 role_type 改为 3/4/5",
            )

    # -----------------------------------------------------------------------
    # 解析列表字段
    # -----------------------------------------------------------------------
    dept_id_list: list[str] = []
    group_id_list: list[str] = []
    manager_group_id_list: list[str] = []
    if department_ids:
        dept_id_list = [d.strip() for d in department_ids.split(",") if d.strip()]
    if group_ids:
        group_id_list = [g.strip() for g in group_ids.split(",") if g.strip()]
    if manager_group_ids:
        manager_group_id_list = [g.strip() for g in manager_group_ids.split(",") if g.strip()]

    # -----------------------------------------------------------------------
    # 构造 profile 旧值对象
    # -----------------------------------------------------------------------
    old_info = {
        "user_name": _get_current("user_name", ""),
        "email": _get_current("email", ""),
        "login_name": _get_current("login_name", ""),
        "phone": _get_current("phone", ""),
        "number": _get_current("number", ""),
        "role_type": int(_get_current("role_type", 0) or 0),
        "platform_permission": int(_get_current("platform_permission", 1) or 1),
        "departments": [
            {"id": str(d.get("department_id", "")), "name": d.get("department_name", "")}
            for d in current.get("departments", [])
        ]
        if current.get("departments")
        else [],
        "groups": [
            {"id": str(g.get("id", "")), "name": g.get("group_name", "")}
            for g in current.get("members", [])
        ]
        if current.get("members")
        else [],
        "manager_groups": [
            {"id": str(g.get("id", "")), "name": g.get("group_name", "")}
            for g in current.get("managers", [])
        ]
        if current.get("managers")
        else [],
    }

    # -----------------------------------------------------------------------
    # 更新 profile 字段
    # -----------------------------------------------------------------------
    warnings: list[dict[str, Any]] = []
    profile_changed = any(
        f is not None
        for f in (
            user_name,
            new_email,
            login_name,
            phone,
            number,
            role_type,
            platform_permission,
        )
    )

    if profile_changed:
        profile_data: dict[str, Any] = {
            "umu_id": umu_id,
            "user_name": user_name if user_name is not None else old_info["user_name"],
            "email": new_email if new_email is not None else old_info["email"],
            "login_name": login_name if login_name is not None else old_info["login_name"],
            "phone": phone if phone is not None else old_info["phone"],
            "number": number if number is not None else old_info["number"],
            "role_type": str(role_type if role_type is not None else old_info["role_type"]),
            "platform_permission": str(
                platform_permission
                if platform_permission is not None
                else old_info["platform_permission"]
            ),
        }

        # 预检
        try:
            precheck_params: dict[str, Any] = {
                "user_name": profile_data["user_name"],
                "email": profile_data["email"],
                "login_name": profile_data["login_name"],
                "phone": profile_data["phone"],
                "number": profile_data["number"],
                "role_type": profile_data["role_type"],
                "platform_permission": profile_data["platform_permission"],
                "umu_id": umu_id,
            }
            if dept_id_list:
                precheck_params["add_department_ids"] = dept_id_list[0]
            if group_id_list:
                precheck_params["add_enterprise_group_ids"] = group_id_list[0]

            precheck_resp = client.get(
                client.desktop_url("/uapi/v1/enterprise/add-user-check"),
                params=precheck_params,
            )
            precheck_data = precheck_resp.get("data")
            precheck_failed = (
                precheck_resp.get("error_code") != 0
                or precheck_data is None
                or (
                    isinstance(precheck_data, dict)
                    and precheck_data.get("res_code") not in (0, None)
                )
            )
            if precheck_failed:
                return _err(
                    error_code="PRECHECK_FAILED",
                    error_message=precheck_resp.get(
                        "error_message", "预检未通过，参数可能不合法或邮箱已被占用"
                    ),
                    suggested_action="检查邮箱、用户名、工号是否重复，或角色权限是否足够",
                )
        except Exception as e:
            logger.warning("预检请求异常: %s", e)

        # 提交 profile 更新
        try:
            resp = client.post(
                client.desktop_url("/ajax/enterprise/updateUser"),
                data=profile_data,
            )
            if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
                return _err(
                    error_code="UPDATE_USER_FAILED",
                    error_message=resp.get("error", "更新用户信息失败"),
                    suggested_action="检查参数格式或管理员权限",
                )
        except Exception as e:
            return _err(
                error_code="UPDATE_USER_ERROR",
                error_message=str(e),
                suggested_action="检查网络连接或管理员权限后重试",
            )

    # -----------------------------------------------------------------------
    # 更新分组（覆盖写入）
    # -----------------------------------------------------------------------
    if group_ids is not None or manager_group_ids is not None:
        final_member_groups = group_id_list if group_ids is not None else old_info["groups"]
        final_manager_groups = (
            manager_group_id_list if manager_group_ids is not None else old_info["manager_groups"]
        )

        group_data: dict[str, Any] = {"umu_id": umu_id}
        if final_member_groups:
            group_data["member_group_id[]"] = [
                g["id"] if isinstance(g, dict) else g for g in final_member_groups
            ]
        if final_manager_groups:
            group_data["manager_group_id[]"] = [
                g["id"] if isinstance(g, dict) else g for g in final_manager_groups
            ]

        try:
            group_resp = client.post(
                client.desktop_url("/ajax/enterprise/updateUserGroup"),
                data=group_data,
            )
            if not (group_resp.get("status") is True or group_resp.get("error_code") == 0):
                warnings.append(
                    {
                        "type": "update_group_failed",
                        "message": group_resp.get("error", "更新分组失败"),
                    }
                )
        except Exception as e:
            warnings.append(
                {
                    "type": "update_group_error",
                    "message": str(e),
                }
            )
            logger.warning("更新分组失败: umu_id=%s, error=%s", umu_id, e)

    # -----------------------------------------------------------------------
    # 更新部门（覆盖写入）
    # -----------------------------------------------------------------------
    if department_ids is not None:
        try:
            dept_resp = client.post(
                client.desktop_url("/uapi/v1/department/change-member-department"),
                data={
                    "umu_ids": json.dumps([umu_id]),
                    "department_ids": json.dumps(dept_id_list),
                },
            )
            if dept_resp.get("error_code") != 0:
                warnings.append(
                    {
                        "type": "update_department_failed",
                        "message": dept_resp.get("error_message", "更新部门失败"),
                    }
                )
        except Exception as e:
            warnings.append(
                {
                    "type": "update_department_error",
                    "message": str(e),
                }
            )
            logger.warning("更新部门失败: umu_id=%s, error=%s", umu_id, e)

    # -----------------------------------------------------------------------
    # 重新获取最新信息
    # -----------------------------------------------------------------------
    try:
        new_resp = client.get(
            client.desktop_url("/ajax/enterprise/getUserInfo"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "umu_id": umu_id,
            },
        )
        new_data = (
            new_resp.get("data", {})
            if new_resp.get("status") is True or new_resp.get("error_code") == 0
            else {}
        )
    except Exception:
        new_data = {}

    new_user_info = new_data.get("user_info", {}) or {}

    def _get_new(field: str, default: Any = "") -> Any:
        return new_user_info.get(field, default) if new_user_info else default

    new_info = {
        "user_name": _get_new("user_name", old_info["user_name"]),
        "email": _get_new("email", old_info["email"]),
        "login_name": _get_new("login_name", old_info["login_name"]),
        "phone": _get_new("phone", old_info["phone"]),
        "number": _get_new("number", old_info["number"]),
        "role_type": int(_get_new("role_type", old_info["role_type"]) or old_info["role_type"]),
        "platform_permission": int(
            _get_new("platform_permission", old_info["platform_permission"])
            or old_info["platform_permission"]
        ),
        "departments": [
            {"id": str(d.get("department_id", "")), "name": d.get("department_name", "")}
            for d in new_data.get("departments", [])
        ]
        if new_data.get("departments")
        else old_info["departments"],
        "groups": [
            {"id": str(g.get("id", "")), "name": g.get("group_name", "")}
            for g in new_data.get("members", [])
        ]
        if new_data.get("members")
        else old_info["groups"],
        "manager_groups": [
            {"id": str(g.get("id", "")), "name": g.get("group_name", "")}
            for g in new_data.get("managers", [])
        ]
        if new_data.get("managers")
        else old_info["manager_groups"],
    }

    updated_fields = [
        field
        for field in (
            "user_name",
            "email",
            "login_name",
            "phone",
            "number",
            "role_type",
            "platform_permission",
        )
        if new_info[field] != old_info[field]
    ]
    if department_ids is not None:
        updated_fields.append("departments")
    if group_ids is not None:
        updated_fields.append("groups")
    if manager_group_ids is not None:
        updated_fields.append("manager_groups")

    result_data: dict[str, Any] = {
        "umu_id": umu_id,
        "old": old_info,
        "new": new_info,
        "updated_fields": updated_fields,
    }
    if warnings:
        result_data["warnings"] = warnings

    return _ok(
        data=result_data,
        next_action="proceed",
        suggested_action="账号信息已更新，可通过 adm_list_accounts 查看最新状态",
    )


@mcp.tool()
async def adm_list_departments(
    fuzzy_name: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的部门名称模糊匹配关键词。提供时会从全量部门中筛选"
            "最匹配的候选，并返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取部门列表.

    触发条件：在创建账号前需要查看可用的部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/department/get-departments-by-managerid"),
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="LIST_DEPARTMENTS_FAILED",
                error_message=resp.get("error_message", "获取部门列表失败"),
                suggested_action="检查管理员权限或稍后重试",
            )

        dept_list = resp.get("data", {}).get("department_list", [])
        departments = []
        for dept in dept_list:
            departments.append(
                {
                    "department_id": str(dept.get("department_id", "")),
                    "department_name": dept.get("department_name", ""),
                    "parent_department_id": str(dept.get("parent_department_id", "0")),
                    "level": int(dept.get("level", 1) or 1),
                    "member_count": int(dept.get("member_count", 0) or 0),
                }
            )

        if fuzzy_name and fuzzy_name.strip():
            departments = fuzzy_filter_items(
                departments,
                fuzzy_name,
                key="department_name",
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )

        return _ok(
            data={
                "departments": departments,
                "total": len(departments),
            },
            next_action="proceed",
            suggested_action="使用 department_id 在 adm_create_account 中指定部门",
        )
    except Exception as e:
        return _err(
            error_code="LIST_DEPARTMENTS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 部门管理
# ---------------------------------------------------------------------------


def _normalize_department(dept: dict[str, Any]) -> dict[str, Any]:
    """将 UMU 部门字典规整为统一输出格式."""
    return {
        "department_id": str(dept.get("department_id", "")),
        "enterprise_id": str(dept.get("enterprise_id", "")),
        "parent_department_id": str(dept.get("parent_department_id", "0")),
        "department_name": dept.get("department_name", ""),
        "level": int(dept.get("level", 1) or 1),
        "show_index": int(dept.get("show_index", 0) or 0),
        "member_count": int(dept.get("member_count", 0) or 0),
        "managers": dept.get("managers", []) or [],
        "manage_permission": int(dept.get("manage_permission", 0) or 0),
        "parent_path": dept.get("parent_path", []) or [],
        "child_path": dept.get("child_path", []) or [],
    }


def _build_department_tree(
    client: UMUClient,
    parent_id: str,
    depth: int = 0,
    max_depth: int = 20,
) -> list[dict[str, Any]]:
    """递归获取部门子树.

    Args:
        client: UMUClient 实例
        parent_id: 父部门 ID
        depth: 当前递归深度
        max_depth: 最大递归深度，防止异常循环

    Returns:
        子部门列表，每个部门包含 children 字段。
    """
    if depth >= max_depth:
        return []

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/department/get-childdepartments-byid"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "department_id": parent_id,
                "type": "2",
            },
        )
    except Exception:
        return []

    if resp.get("error_code") != 0:
        return []

    dept_list = resp.get("data", {}).get("department_list", [])
    result: list[dict[str, Any]] = []
    for dept in dept_list:
        normalized = _normalize_department(dept)
        children = _build_department_tree(
            client,
            normalized["department_id"],
            depth + 1,
            max_depth,
        )
        normalized["children"] = children
        result.append(normalized)
    return result


@mcp.tool()
async def adm_get_department_tree(
    fetch_all: Annotated[
        bool,
        Field(
            default=True,
            description="是否递归获取完整子部门树。默认 True。",
        ),
    ] = True,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取企业部门树.

    触发条件：需要查看完整组织架构、按层级浏览部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    返回每个部门的 department_id、department_name、parent_department_id、
    level、show_index、member_count、managers 及 children 子树。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/department/get-departments-by-managerid"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "type": "2",
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="GET_DEPARTMENT_TREE_FAILED",
                error_message=resp.get("error_message", "获取部门树失败"),
                suggested_action="检查管理员权限或稍后重试",
            )

        dept_list = resp.get("data", {}).get("department_list", [])
        tree: list[dict[str, Any]] = []
        for dept in dept_list:
            normalized = _normalize_department(dept)
            if fetch_all:
                children = _build_department_tree(
                    client,
                    normalized["department_id"],
                )
                normalized["children"] = children
            tree.append(normalized)

        return _ok(
            data={
                "departments": tree,
                "total": len(tree),
            },
            next_action="proceed",
            suggested_action="使用 department_id 调用 adm_get_department 或 adm_list_department_members",
        )
    except Exception as e:
        return _err(
            error_code="GET_DEPARTMENT_TREE_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_get_department(
    department_id: Annotated[str, Field(description="部门 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取部门详情.

    触发条件：需要查看某个部门的基本信息、上级路径、负责人时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    返回字段包括 parent_path（上级路径）、managers（负责人列表）等。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/department/get-by-departmentid"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "department_id": department_id,
                "type": "2",
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="GET_DEPARTMENT_FAILED",
                error_message=resp.get("error_message", "获取部门详情失败"),
                suggested_action="检查 department_id 是否正确",
            )

        dept = resp.get("data", {})
        return _ok(
            data=_normalize_department(dept),
            next_action="proceed",
            suggested_action="使用 department_id 调用其他部门管理工具",
        )
    except Exception as e:
        return _err(
            error_code="GET_DEPARTMENT_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 department_id 后重试",
        )


@mcp.tool()
async def adm_get_child_departments(
    department_id: Annotated[
        str,
        Field(default="0", description="父部门 ID，默认 0 表示获取顶层部门"),
    ] = "0",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取子部门列表.

    触发条件：需要查看某个部门下的直接子部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/department/get-childdepartments-byid"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "department_id": department_id,
                "type": "2",
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="GET_CHILD_DEPARTMENTS_FAILED",
                error_message=resp.get("error_message", "获取子部门失败"),
                suggested_action="检查 department_id 是否正确",
            )

        dept_list = resp.get("data", {}).get("department_list", [])
        return _ok(
            data={
                "departments": [_normalize_department(d) for d in dept_list],
                "total": len(dept_list),
                "parent_department_id": department_id,
            },
            next_action="proceed",
            suggested_action="使用 department_id 调用 adm_get_department 查看详情",
        )
    except Exception as e:
        return _err(
            error_code="GET_CHILD_DEPARTMENTS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 department_id 后重试",
        )


@mcp.tool()
async def adm_list_department_members(
    department_id: Annotated[str, Field(description="部门 ID")],
    keywords: Annotated[
        str | None,
        Field(default=None, description="按姓名模糊搜索成员（可选）"),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=15, ge=1, le=100, description="每页数量（1-100），默认15"),
    ] = 15,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取部门成员列表.

    触发条件：需要查看某个部门下的成员时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    返回字段包括 umu_id、user_name、email、role_type、number、member_id 等。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "department_id": department_id,
            "umu_ids": "",
            "page": str(p),
            "size": str(sz),
        }
        resp = client.get(
            client.desktop_url("/uapi/v1/department/member-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取部门成员失败"))

        member_list = resp.get("data", {}).get("list", [])
        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)

        members: list[dict[str, Any]] = []
        for item in member_list:
            user_info = item.get("user_info", {}) or {}
            members.append(
                {
                    "umu_id": str(user_info.get("umu_id", "")),
                    "user_name": user_info.get("user_name", ""),
                    "email": user_info.get("email", ""),
                    "phone": user_info.get("phone", ""),
                    "number": item.get("number", "") or user_info.get("number", ""),
                    "role_type": int(item.get("role_type", 0) or 0),
                    "on_job_status": item.get("on_job_status", ""),
                    "member_id": item.get("member_id", 0),
                    "department_id": item.get("department_id", 0),
                    "user_department_name": item.get("user_department_name", ""),
                }
            )

        # 客户端关键词过滤（API 不支持服务端搜索时兜底）
        if keywords:
            kw = keywords.lower()
            members = [
                m
                for m in members
                if kw in (m.get("user_name", "") or "").lower()
                or kw in (m.get("email", "") or "").lower()
                or kw in (m.get("number", "") or "").lower()
            ]

        return members, total_all

    try:
        if fetch_all:
            all_members: list[dict[str, Any]] = []
            current_page = 1
            batch_size = page_size
            total_all = 0

            while True:
                members, total_all = _fetch_page(current_page, batch_size)
                all_members.extend(members)

                report_pagination_progress(
                    "adm_list_department_members",
                    current_page,
                    len(all_members),
                    total_all,
                    page_size,
                    is_complete=len(all_members) >= total_all or not members,
                )

                if len(all_members) >= total_all or not members:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_department_members",
                        current_page,
                        len(all_members),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "members": all_members,
                    "total": len(all_members),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_members) if all_members else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_remove_department_members 或 adm_move_department_members",
            )
        else:
            members, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "members": members,
                    "total": len(members),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_remove_department_members 或 adm_move_department_members",
            )
    except Exception as e:
        return _err(
            error_code="LIST_DEPARTMENT_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 department_id 后重试",
        )


@mcp.tool()
async def adm_search_department_members(
    department_id: Annotated[
        str | None,
        Field(
            default=None,
            description="目标部门 ID。提供时搜索可加入该部门的成员；不提供时全企业搜索。",
        ),
    ] = None,
    keywords: Annotated[
        str | None,
        Field(default=None, description="姓名/邮箱/手机号关键词"),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """搜索可加入部门的成员.

    触发条件：需要为部门添加成员但不知道 umu_id 时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    优先使用 /uapi/v1/department/sug-member 接口；如不可用则回退到
    /uapi/v1/department/users-not-in-department。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 优先尝试 sug-member 接口
    if keywords:
        try:
            resp = client.get(
                client.desktop_url("/uapi/v1/department/sug-member"),
                params={
                    "t": str(int(datetime.now().timestamp() * 1000)),
                    "keywords": keywords,
                    "department_id": department_id or "0",
                    "is_root": "0",
                },
            )
            if resp.get("error_code") == 0:
                sug_users = resp.get("data", []) or []
                return _ok(
                    data={
                        "users": [
                            {
                                "umu_id": str(u.get("umu_id", "")),
                                "user_name": u.get("user_name", ""),
                                "email": u.get("email", ""),
                                "phone": u.get("phone", ""),
                                "number": u.get("number", ""),
                                "role_type": int(u.get("role_type", 0) or 0),
                            }
                            for u in sug_users
                        ],
                        "total": len(sug_users),
                    },
                    next_action="proceed",
                    suggested_action="使用 umu_id 调用 adm_add_department_members",
                )
        except Exception:
            pass

    # 回退到 users-not-in-department
    try:
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "department": department_id or "0",
            "page": str(page),
            "size": str(page_size),
        }
        resp = client.get(
            client.desktop_url("/uapi/v1/department/users-not-in-department"),
            params=params,
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="SEARCH_DEPARTMENT_MEMBERS_FAILED",
                error_message=resp.get("error_message", "搜索成员失败"),
                suggested_action="检查 department_id 或稍后重试",
            )

        user_list = resp.get("data", {}).get("list", [])
        page_info = resp.get("data", {}).get("page_info", {})

        users: list[dict[str, Any]] = []
        for item in user_list:
            user_info = item.get("user_info", {}) or {}
            users.append(
                {
                    "umu_id": str(user_info.get("umu_id", "")),
                    "user_name": user_info.get("user_name", ""),
                    "email": user_info.get("email", ""),
                    "phone": user_info.get("phone", ""),
                    "number": item.get("number", ""),
                    "role_type": int(item.get("role_type", 0) or 0),
                    "on_job_status": item.get("on_job_status", ""),
                }
            )

        # 客户端关键词过滤
        if keywords:
            kw = keywords.lower()
            users = [
                u
                for u in users
                if kw in (u.get("user_name", "") or "").lower()
                or kw in (u.get("email", "") or "").lower()
                or kw in (u.get("number", "") or "").lower()
            ]

        return _ok(
            data={
                "users": users,
                "total": len(users),
                "pagination": {
                    "total_all": int(page_info.get("list_total_num", 0) or 0),
                    "current_page": int(page_info.get("current_page", page) or page),
                    "page_size": int(page_info.get("size", page_size) or page_size),
                },
            },
            next_action="proceed",
            suggested_action="使用 umu_id 调用 adm_add_department_members",
        )
    except Exception as e:
        return _err(
            error_code="SEARCH_DEPARTMENT_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_create_department(
    department_name: Annotated[str, Field(description="部门名称")],
    parent_department_id: Annotated[
        str,
        Field(default="0", description="父部门 ID，默认 0 表示顶层部门"),
    ] = "0",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """创建部门.

    触发条件：需要新增企业部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：在 UMU 平台创建新部门。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    if not department_name.strip():
        return _err(
            error_code="INVALID_DEPARTMENT_NAME",
            error_message="部门名称不能为空",
            suggested_action="提供有效的部门名称",
        )

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/department/add"),
            data={
                "department_name": department_name.strip(),
                "parent_department_id": parent_department_id,
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="CREATE_DEPARTMENT_FAILED",
                error_message=resp.get("error_message", "创建部门失败"),
                suggested_action="检查父部门 ID 或部门名称是否重复",
            )

        data = resp.get("data", {})
        return _ok(
            data={
                "department_id": str(data.get("department_id", "")),
                "status": data.get("status"),
                "desc": data.get("desc"),
            },
            next_action="proceed",
            suggested_action="使用 department_id 继续添加成员或创建子部门",
        )
    except Exception as e:
        return _err(
            error_code="CREATE_DEPARTMENT_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_update_department(
    department_id: Annotated[str, Field(description="部门 ID")],
    department_name: Annotated[
        str | None,
        Field(default=None, description="新的部门名称，不提供则不修改"),
    ] = None,
    parent_department_id: Annotated[
        str | None,
        Field(default=None, description="新的父部门 ID，不提供则不修改"),
    ] = None,
    manager_umu_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="负责人 umu_id 列表，多个用逗号分隔，如 '20458616'。不提供则不修改。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """更新部门信息.

    触发条件：需要重命名部门、调整上级部门或设置部门负责人时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改 UMU 平台上的部门信息。

    注意： department_name、parent_department_id、manager_umu_ids 至少提供一项。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 获取当前部门信息作为默认值
    try:
        current_resp = client.get(
            client.desktop_url("/uapi/v1/department/get-by-departmentid"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "department_id": department_id,
                "type": "2",
            },
        )
        current = current_resp.get("data", {}) if current_resp.get("error_code") == 0 else {}
    except Exception:
        current = {}

    if not department_name and not parent_department_id and not manager_umu_ids:
        return _err(
            error_code="NO_UPDATE_FIELDS",
            error_message="至少提供一项要修改的字段",
            suggested_action="提供 department_name、parent_department_id 或 manager_umu_ids",
        )

    update_data: dict[str, Any] = {
        "department_id": department_id,
        "department_name": department_name
        if department_name
        else current.get("department_name", ""),
        "parent_department_id": (
            parent_department_id
            if parent_department_id is not None
            else current.get("parent_department_id", "0")
        ),
    }
    if manager_umu_ids is not None:
        manager_list = [m.strip() for m in manager_umu_ids.split(",") if m.strip()]
        update_data["manager_umu_ids"] = json.dumps(manager_list)
    else:
        managers = current.get("managers", []) or []
        manager_list = [str(m.get("umu_id", "")) for m in managers if m.get("umu_id")]
        update_data["manager_umu_ids"] = json.dumps(manager_list)

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/department/edit"),
            data=update_data,
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="UPDATE_DEPARTMENT_FAILED",
                error_message=resp.get("error_message", "更新部门失败"),
                suggested_action="检查 department_id 或父部门 ID 是否正确",
            )

        return _ok(
            data={
                "department_id": department_id,
                "updated_fields": {
                    "department_name": department_name,
                    "parent_department_id": parent_department_id,
                    "manager_umu_ids": manager_umu_ids,
                },
            },
            next_action="proceed",
            suggested_action="部门信息已更新",
        )
    except Exception as e:
        return _err(
            error_code="UPDATE_DEPARTMENT_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_sort_departments(
    department_orders: Annotated[
        str,
        Field(
            description="部门排序列表，JSON 数组格式，如 "
            '\'[{"department_id":"297494","index":1},{"department_id":"297481","index":2}]\''
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """调整部门排序.

    触发条件：需要调整同级部门的显示顺序时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改 UMU 平台上同级部门的 show_index。

    department_orders 为 JSON 数组，每个元素包含 department_id 和 index（从 1 开始）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        order_list = json.loads(department_orders)
        if not isinstance(order_list, list) or not order_list:
            raise ValueError("department_orders 必须是 JSON 数组")
    except Exception as e:
        return _err(
            error_code="INVALID_SORT_FORMAT",
            error_message=f"排序参数格式错误: {e}",
            suggested_action='使用 JSON 数组格式，如 \'[{"department_id":"1","index":1}]\'',
        )

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/department/sort"),
            data={"department_list": json.dumps(order_list)},
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="SORT_DEPARTMENTS_FAILED",
                error_message=resp.get("error_message", "排序调整失败"),
                suggested_action="检查 department_id 列表是否正确",
            )

        return _ok(
            data={"department_orders": order_list},
            next_action="proceed",
            suggested_action="部门排序已更新",
        )
    except Exception as e:
        return _err(
            error_code="SORT_DEPARTMENTS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_add_department_members(
    department_id: Annotated[str, Field(description="目标部门 ID")],
    umu_ids: Annotated[
        str,
        Field(description="要添加的成员 umu_id 列表，多个用逗号分隔，如 '20439812,20439813'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """添加成员到部门.

    触发条件：需要将现有用户加入部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定用户添加到目标部门（保留原部门关系）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    id_list = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    if not id_list:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/department/add-member"),
            data={
                "umu_ids": json.dumps(id_list),
                "add_department_ids": json.dumps([department_id]),
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="ADD_DEPARTMENT_MEMBERS_FAILED",
                error_message=resp.get("error_message", "添加成员失败"),
                suggested_action="检查 umu_id 是否已在部门中",
            )

        return _ok(
            data={
                "department_id": department_id,
                "umu_ids": id_list,
                "added_count": len(id_list),
            },
            next_action="proceed",
            suggested_action="成员已添加，可调用 adm_list_department_members 查看",
        )
    except Exception as e:
        return _err(
            error_code="ADD_DEPARTMENT_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_move_department_members(
    umu_ids: Annotated[
        str,
        Field(description="要调整的成员 umu_id 列表，多个用逗号分隔"),
    ],
    department_ids: Annotated[
        str,
        Field(description="目标部门 ID 列表，多个用逗号分隔"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """调整成员所属部门.

    触发条件：需要将成员从当前部门迁移到其他部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改成员的部门归属关系。

    注意：该操作会覆盖成员原有的部门关系，请谨慎使用。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    uid_list = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    dept_list = [did.strip() for did in department_ids.split(",") if did.strip()]

    if not uid_list:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )
    if not dept_list:
        return _err(
            error_code="EMPTY_DEPARTMENT_IDS",
            error_message="department_ids 不能为空",
            suggested_action="提供至少一个 department_id",
        )

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/department/change-member-department"),
            data={
                "umu_ids": json.dumps(uid_list),
                "department_ids": json.dumps(dept_list),
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="MOVE_DEPARTMENT_MEMBERS_FAILED",
                error_message=resp.get("error_message", "调整成员部门失败"),
                suggested_action="检查 umu_id 和 department_id 是否正确",
            )

        return _ok(
            data={
                "umu_ids": uid_list,
                "department_ids": dept_list,
                "moved_count": len(uid_list),
            },
            next_action="proceed",
            suggested_action="成员部门已调整",
        )
    except Exception as e:
        return _err(
            error_code="MOVE_DEPARTMENT_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_remove_department_members(
    member_ids: Annotated[
        str,
        Field(
            description="要移除的成员 member_id 列表，多个用逗号分隔。"
            "member_id 可通过 adm_list_department_members 获取。",
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """移除部门成员.

    触发条件：需要将成员从部门中移除时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定成员从部门中删除。

    注意：需要提供 member_id（部门成员关系 ID），而非 umu_id。
    member_id 可通过 adm_list_department_members 的返回字段获取。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    id_list = [mid.strip() for mid in member_ids.split(",") if mid.strip()]
    if not id_list:
        return _err(
            error_code="EMPTY_MEMBER_IDS",
            error_message="member_ids 不能为空",
            suggested_action="提供至少一个 member_id",
        )

    try:
        # member_ids 参数为 JSON 数组格式的整数
        member_id_ints = [int(mid) for mid in id_list]
        resp = client.get(
            client.desktop_url("/uapi/v1/department/batch-delete-member"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "member_ids": json.dumps(member_id_ints),
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="REMOVE_DEPARTMENT_MEMBERS_FAILED",
                error_message=resp.get("error_message", "移除成员失败"),
                suggested_action="检查 member_id 是否正确",
            )

        return _ok(
            data={
                "member_ids": member_id_ints,
                "removed_count": len(member_id_ints),
            },
            next_action="proceed",
            suggested_action="成员已从部门移除",
        )
    except Exception as e:
        return _err(
            error_code="REMOVE_DEPARTMENT_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 member_id 格式后重试",
        )


def _classify_delete_error(error_message: str) -> str:
    """根据错误信息分类删除失败原因."""
    msg = (error_message or "").lower()
    if any(k in msg for k in ("不存在", "not found", "找不到", "无效")):
        return "not_found"
    if any(k in msg for k in ("成员", "子部门", "child", "sub", "包含", "下级")):
        return "has_members_or_children"
    return "api_error"


@mcp.tool()
async def adm_delete_departments(
    department_ids: Annotated[
        str,
        Field(description="要删除的部门 ID 列表，多个用逗号分隔"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除部门.

    触发条件：需要删除空部门或不再使用的部门时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：在 UMU 平台删除指定部门。

    本工具会先查询每个部门的层级信息，按从深到浅的顺序逐个删除，
    避免父部门先于子部门被删除导致失败。
    调用前请确保目标部门下无成员，否则可能删除失败。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    dept_list = [did.strip() for did in department_ids.split(",") if did.strip()]
    if not dept_list:
        return _err(
            error_code="EMPTY_DEPARTMENT_IDS",
            error_message="department_ids 不能为空",
            suggested_action="提供至少一个 department_id",
        )

    # -----------------------------------------------------------------------
    # 步骤 1: 获取每个部门的层级，用于按从深到浅排序
    # -----------------------------------------------------------------------
    dept_levels: list[tuple[str, int]] = []
    for dept_id in dept_list:
        try:
            resp = client.get(
                client.desktop_url("/uapi/v1/department/get-by-departmentid"),
                params={
                    "t": str(int(datetime.now().timestamp() * 1000)),
                    "department_id": dept_id,
                    "type": "2",
                },
            )
            if resp.get("error_code") == 0:
                level = int(resp.get("data", {}).get("level", 1) or 1)
            else:
                # 查询失败时默认 level=1，让其在最后尝试删除
                level = 1
            dept_levels.append((dept_id, level))
        except Exception:
            dept_levels.append((dept_id, 1))

    # 按层级降序排序：先删除深层子部门，再删除浅层父部门
    dept_levels.sort(key=lambda x: x[1], reverse=True)

    # -----------------------------------------------------------------------
    # 步骤 2: 逐个删除部门
    # -----------------------------------------------------------------------
    successful: list[str] = []
    failed: list[dict[str, Any]] = []

    for dept_id, _ in dept_levels:
        try:
            resp = client.get(
                client.desktop_url("/uapi/v1/department/delete"),
                params={
                    "t": str(int(datetime.now().timestamp() * 1000)),
                    "department_id": dept_id,
                },
            )

            if resp.get("error_code") == 0:
                successful.append(dept_id)
                continue

            error_message = resp.get("error_message", "删除部门失败")
            failed.append(
                {
                    "department_id": dept_id,
                    "reason": _classify_delete_error(error_message),
                    "error_message": error_message,
                }
            )
        except Exception as e:
            failed.append(
                {
                    "department_id": dept_id,
                    "reason": "api_error",
                    "error_message": str(e),
                }
            )

    # -----------------------------------------------------------------------
    # 步骤 3: 汇总结果
    # -----------------------------------------------------------------------
    result_data: dict[str, Any] = {
        "deleted_count": len(successful),
        "successful_department_ids": successful,
        "failed_departments": failed,
    }

    if not successful:
        return _err(
            error_code="DELETE_DEPARTMENTS_FAILED",
            error_message=f"全部 {len(failed)} 个部门删除失败",
            suggested_action="检查部门下是否还有成员或子部门，或稍后重试",
            data=result_data,
        )

    if failed:
        return _ok(
            data=result_data,
            next_action="proceed",
            suggested_action=f"已删除 {len(successful)} 个部门，{len(failed)} 个失败，请检查 failed_departments",
        )

    return _ok(
        data=result_data,
        next_action="proceed",
        suggested_action="部门已删除",
    )


@mcp.tool()
async def adm_list_groups(
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    fuzzy_name: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的分组名称模糊匹配关键词。提供时会自动获取全量列表"
            "并筛选最匹配的候选，返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取企业分组列表.

    触发条件：在创建账号前需要查看可用的分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    effective_fetch_all = fetch_all or bool(fuzzy_name and fuzzy_name.strip())

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], dict]:
        """获取单页数据，返回(分组列表, 分页信息)."""
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getGroupList"),
            params={
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取分组列表失败"))

        group_list = resp.get("data", {}).get("list", [])
        groups = []
        for group in group_list:
            groups.append(
                {
                    "id": str(group.get("id", "")),
                    "group_name": group.get("group_name", ""),
                    "member_count": int(group.get("member_count", 0) or 0),
                }
            )

        page_info = resp.get("data", {}).get("page_info", {})
        return groups, page_info

    try:
        if effective_fetch_all:
            all_groups: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                groups, page_info = _fetch_page(current_page, batch_size)
                all_groups.extend(groups)
                total_all = int(page_info.get("list_total_num", 0) or total_all)

                report_pagination_progress(
                    "adm_list_groups",
                    current_page,
                    len(all_groups),
                    total_all,
                    20,
                    is_complete=len(all_groups) >= total_all or not groups,
                )

                if len(all_groups) >= total_all or not groups:
                    break
                current_page += 1
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_groups",
                        current_page,
                        len(all_groups),
                        total_all,
                        20,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            result_groups = all_groups
            if fuzzy_name and fuzzy_name.strip():
                result_groups = fuzzy_filter_items(
                    all_groups,
                    fuzzy_name,
                    key="group_name",
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )

            return _ok(
                data={
                    "groups": result_groups,
                    "total": len(result_groups),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(result_groups) if result_groups else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 在 adm_create_account 中指定分组",
            )
        else:
            groups, page_info = _fetch_page(page, page_size)
            return _ok(
                data={
                    "groups": groups,
                    "pagination": {
                        "total": int(page_info.get("list_total_num", 0) or 0),
                        "current_page": int(page_info.get("current_page", page) or page),
                        "page_size": int(page_info.get("size", page_size) or page_size),
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 在 adm_create_account 中指定分组",
            )
    except Exception as e:
        return _err(
            error_code="LIST_GROUPS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


# ---------------------------------------------------------------------------
# Helpers: 分组管理
# ---------------------------------------------------------------------------


def _normalize_group(group: dict[str, Any]) -> dict[str, Any]:
    """将 UMU 分组字典规整为统一输出格式."""
    creator = group.get("creator", {}) or {}
    managers = group.get("managers", []) or []
    return {
        "id": str(group.get("id", "")),
        "group_name": group.get("group_name", ""),
        "group_name_letter": group.get("group_name_letter", ""),
        "member_count": int(group.get("member_count", 0) or 0),
        "creator_umu_id": str(group.get("umu_id", "")),
        "create_time": int(group.get("create_time", 0) or 0),
        "creator": {
            "umu_id": str(creator.get("umu_id", "")),
            "user_name": creator.get("user_name", ""),
            "manage_permission": int(creator.get("manage_permission", 0) or 0),
        },
        "managers": [
            {
                "user_name": m.get("user_name", ""),
                "email": m.get("email", ""),
            }
            for m in managers
        ],
    }


def _normalize_group_user(user: dict[str, Any]) -> dict[str, Any]:
    """将 UMU 分组用户字典规整为统一输出格式."""
    return {
        "umu_id": str(user.get("umu_id", "")),
        "user_name": user.get("user_name", ""),
        "user_name_letter": user.get("user_name_letter", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "area_code": user.get("area_code", ""),
        "login_name": user.get("login_name", ""),
        "role_type": int(user.get("role_type", 0) or 0),
        "role_name": _ROLE_TYPE_MAP.get(int(user.get("role_type", 0) or 0), "未知"),
        "manage_permission": int(user.get("manage_permission", 0) or 0),
    }


def _fetch_group_users(
    client: UMUClient,
    group_id: str,
    is_manager: int,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """获取分组成员或管理员单页数据.

    Args:
        client: UMUClient 实例
        group_id: 分组 ID
        is_manager: 0=成员, 1=管理员
        page: 页码
        page_size: 每页数量

    Returns:
        (用户列表, 总数量)
    """
    # 成员默认按姓名升序，管理员默认按加入时间/权限排序
    sort = "0" if is_manager else "1"
    resp = client.get(
        client.desktop_url("/uapi/v1/enterprise/enterprise-group-user-list"),
        params={
            "t": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            "enterprise_group_id": group_id,
            "is_manager": str(is_manager),
            "type": "user_list",
            "sort": sort,
            "page": str(page),
            "size": str(page_size),
        },
    )

    if resp.get("error_code") != 0:
        raise RuntimeError(resp.get("error_message", "获取分组成员失败"))

    data = resp.get("data", {})
    users = [_normalize_group_user(u) for u in data.get("list", [])]
    total_all = int(data.get("page_info", {}).get("list_total_num", 0) or 0)
    return users, total_all


def _get_all_group_users(
    client: UMUClient,
    group_id: str,
    is_manager: int,
) -> list[dict[str, Any]]:
    """获取分组全部成员或管理员."""
    all_users: list[dict[str, Any]] = []
    current_page = 1
    page_size = 1000
    total_all = 0

    while True:
        users, total_all = _fetch_group_users(client, group_id, is_manager, current_page, page_size)
        all_users.extend(users)

        report_pagination_progress(
            "_get_all_group_users",
            current_page,
            len(all_users),
            total_all,
            page_size,
            is_complete=len(all_users) >= total_all or not users,
        )

        if len(all_users) >= total_all or not users:
            break
        current_page += 1
        if current_page > 50:
            report_pagination_progress(
                "_get_all_group_users",
                current_page,
                len(all_users),
                total_all,
                page_size,
                is_safety_limit=True,
            )
            logger.warning("获取分组成员达到 50 页安全上限")
            break

    return all_users


def _update_group_membership(
    client: UMUClient,
    group_id: str,
    member_ids: list[str],
    manager_ids: list[str],
) -> None:
    """调用 updateGroupUser 覆盖设置分组成员与管理员.

    使用 is_delete=2 表示将成员/管理员设置为传入列表的精确值（幂等的覆盖模式）。
    """
    resp = client.post(
        client.desktop_url("/ajax/enterprise/updateGroupUser"),
        data={
            "enterprise_group_id[]": group_id,
            "member_id": json.dumps(member_ids),
            "manager_id": json.dumps(manager_ids),
            "is_delete": "2",
        },
    )

    if not (resp.get("status") is True or resp.get("error_code") == 0):
        raise RuntimeError(resp.get("error", "更新分组成员失败"))


# ---------------------------------------------------------------------------
# Tools: 分组管理
# ---------------------------------------------------------------------------


@mcp.tool()
async def adm_create_group(
    group_name: Annotated[str, Field(description="分组名称")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """创建企业分组.

    触发条件：需要新增分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：在 UMU 平台创建新分组。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    if not group_name.strip():
        return _err(
            error_code="INVALID_GROUP_NAME",
            error_message="分组名称不能为空",
            suggested_action="提供有效的分组名称",
        )

    try:
        resp = client.post(
            client.desktop_url("/ajax/enterprise/updateGroup"),
            data={"group_name": group_name.strip()},
        )

        if not (resp.get("status") is True or resp.get("error_code") == 0):
            return _err(
                error_code="CREATE_GROUP_FAILED",
                error_message=resp.get("error", "创建分组失败"),
                suggested_action="检查分组名称是否重复或管理员权限",
            )

        group_id = str(resp.get("data", {}).get("enterprise_group_id", ""))
        return _ok(
            data={"group_id": group_id, "group_name": group_name.strip()},
            next_action="proceed",
            suggested_action="使用 group_id 添加成员或管理员",
        )
    except Exception as e:
        return _err(
            error_code="CREATE_GROUP_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_update_group(
    group_id: Annotated[str, Field(description="分组 ID")],
    group_name: Annotated[str, Field(description="新的分组名称")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """修改分组名称.

    触发条件：需要重命名分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改 UMU 平台上的分组名称。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    if not group_name.strip():
        return _err(
            error_code="INVALID_GROUP_NAME",
            error_message="分组名称不能为空",
            suggested_action="提供有效的分组名称",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/update-group-name"),
            params={
                "t": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                "group_id": group_id,
                "new_group_name": group_name.strip(),
            },
        )

        if resp.get("error_code") != 0:
            return _err(
                error_code="UPDATE_GROUP_FAILED",
                error_message=resp.get("error_message", "修改分组名称失败"),
                suggested_action="检查 group_id 是否正确或名称是否重复",
            )

        status = resp.get("data", {}).get("status")
        if status != 1:
            return _err(
                error_code="UPDATE_GROUP_FAILED",
                error_message="修改分组名称未生效",
                suggested_action="检查 group_id 是否正确或名称是否重复",
            )

        return _ok(
            data={"group_id": group_id, "group_name": group_name.strip()},
            next_action="proceed",
            suggested_action="分组名称已更新",
        )
    except Exception as e:
        return _err(
            error_code="UPDATE_GROUP_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_delete_groups(
    group_ids: Annotated[
        str,
        Field(description="要删除的分组 ID 列表，多个用逗号分隔，如 '177155,177156'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除企业分组.

    触发条件：需要删除不再使用的分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：在 UMU 平台删除指定分组。

    注意：删除前请确保分组下已无成员，否则可能删除失败。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    id_list = [gid.strip() for gid in group_ids.split(",") if gid.strip()]
    if not id_list:
        return _err(
            error_code="EMPTY_GROUP_IDS",
            error_message="group_ids 不能为空",
            suggested_action="提供至少一个 group_id",
        )

    successful: list[str] = []
    failed: list[dict[str, Any]] = []

    for gid in id_list:
        try:
            resp = client.post(
                client.desktop_url("/ajax/enterprise/deleteGroup"),
                data={"enterprise_group_id[]": gid},
            )

            if resp.get("status") is True or resp.get("error_code") == 0:
                successful.append(gid)
                continue

            failed.append(
                {
                    "group_id": gid,
                    "error_message": resp.get("error", "删除分组失败"),
                }
            )
        except Exception as e:
            failed.append({"group_id": gid, "error_message": str(e)})

    result_data: dict[str, Any] = {
        "deleted_count": len(successful),
        "successful_group_ids": successful,
        "failed_groups": failed,
    }

    if not successful:
        return _err(
            error_code="DELETE_GROUPS_FAILED",
            error_message=f"全部 {len(failed)} 个分组删除失败",
            suggested_action="检查分组下是否还有成员或管理员，或稍后重试",
            data=result_data,
        )

    if failed:
        return _ok(
            data=result_data,
            next_action="proceed",
            suggested_action=f"已删除 {len(successful)} 个分组，{len(failed)} 个失败，请检查 failed_groups",
        )

    return _ok(
        data=result_data,
        next_action="proceed",
        suggested_action="分组已删除",
    )


@mcp.tool()
async def adm_delete_learning_program(
    program_id: Annotated[str, Field(description="学习项目 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """删除学习项目.

    触发条件：管理员需要删除指定学习项目时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将学习项目移至平台回收站。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
            next_action="retry",
        )

    if not program_id or str(program_id) in ("0", ""):
        return _err(
            error_code="EMPTY_PROGRAM_ID",
            error_message="program_id 不能为空",
            suggested_action="提供有效的学习项目 ID",
        )

    try:
        resp = client.post(
            client.desktop_url("/api/program/deleteprogram"),
            data={"program_id": program_id},
        )

        if resp.get("status") is True or resp.get("error_code") == 0:
            return _ok(
                data={"program_id": program_id, "deleted": True},
                next_action="proceed",
                suggested_action="学习项目已删除",
            )

        return _err(
            error_code="DELETE_LEARNING_PROGRAM_FAILED",
            error_message=resp.get("error", "删除学习项目失败"),
            suggested_action="请确认管理员对该学习项目具有删除权限",
        )
    except Exception as e:
        logger.exception("删除学习项目失败")
        return _err(
            error_code="DELETE_LEARNING_PROGRAM_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接和参数后重试",
        )


@mcp.tool()
async def adm_get_group(
    group_id: Annotated[str, Field(description="分组 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取分组详情.

    触发条件：需要查看分组基本信息、成员数、创建者、管理员时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getGroupList"),
            params={
                "t": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                "enterprise_group_id": group_id,
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            return _err(
                error_code="GET_GROUP_FAILED",
                error_message=resp.get("error", "获取分组详情失败"),
                suggested_action="检查 group_id 是否正确",
            )

        group_list = resp.get("data", {}).get("list", [])
        if not group_list:
            return _err(
                error_code="GROUP_NOT_FOUND",
                error_message="找不到指定分组",
                suggested_action="检查 group_id 是否正确",
            )

        return _ok(
            data=_normalize_group(group_list[0]),
            next_action="proceed",
            suggested_action="使用 group_id 调用成员/管理员管理工具",
        )
    except Exception as e:
        return _err(
            error_code="GET_GROUP_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 group_id 后重试",
        )


@mcp.tool()
async def adm_list_group_members(
    group_id: Annotated[str, Field(description="分组 ID")],
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=1000, description="每页数量（1-1000），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取分组成员列表.

    触发条件：需要查看某个分组下的普通成员时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        if fetch_all:
            members = _get_all_group_users(client, group_id, is_manager=0)
            return _ok(
                data={
                    "members": members,
                    "total": len(members),
                    "pagination": {
                        "total_all": len(members),
                        "current_page": 1,
                        "page_size": len(members),
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_remove_group_members 或 adm_add_group_managers",
            )

        members, total_all = _fetch_group_users(client, group_id, 0, page, page_size)
        return _ok(
            data={
                "members": members,
                "total": len(members),
                "pagination": {
                    "total_all": total_all,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="使用 umu_id 调用 adm_remove_group_members 或 adm_add_group_managers",
        )
    except Exception as e:
        return _err(
            error_code="LIST_GROUP_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 group_id 后重试",
        )


@mcp.tool()
async def adm_list_group_managers(
    group_id: Annotated[str, Field(description="分组 ID")],
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=1000, description="每页数量（1-1000），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取分组管理员列表.

    触发条件：需要查看某个分组的管理员时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        if fetch_all:
            managers = _get_all_group_users(client, group_id, is_manager=1)
            return _ok(
                data={
                    "managers": managers,
                    "total": len(managers),
                    "pagination": {
                        "total_all": len(managers),
                        "current_page": 1,
                        "page_size": len(managers),
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_remove_group_managers",
            )

        managers, total_all = _fetch_group_users(client, group_id, 1, page, page_size)
        return _ok(
            data={
                "managers": managers,
                "total": len(managers),
                "pagination": {
                    "total_all": total_all,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="使用 umu_id 调用 adm_remove_group_managers",
        )
    except Exception as e:
        return _err(
            error_code="LIST_GROUP_MANAGERS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或 group_id 后重试",
        )


@mcp.tool()
async def adm_add_group_members(
    group_id: Annotated[str, Field(description="分组 ID")],
    umu_ids: Annotated[
        str,
        Field(description="要添加的成员 umu_id 列表，多个用逗号分隔，如 '20439812,20439813'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """添加成员到分组.

    触发条件：需要将现有用户加入分组时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定用户添加到目标分组。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    add_ids = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    if not add_ids:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    try:
        current_members = _get_all_group_users(client, group_id, is_manager=0)
        current_managers = _get_all_group_users(client, group_id, is_manager=1)

        member_ids = [m["umu_id"] for m in current_members]
        manager_ids = [m["umu_id"] for m in current_managers]

        # 合并新成员，保持原有管理员不变
        new_member_ids = list(dict.fromkeys(member_ids + add_ids))

        _update_group_membership(client, group_id, new_member_ids, manager_ids)

        # 校验后端是否按预期生效（updateGroupUser 遇到不合法角色时会静默忽略）
        updated_members = _get_all_group_users(client, group_id, is_manager=0)
        updated_managers = _get_all_group_users(client, group_id, is_manager=1)
        actual_member_ids = {m["umu_id"] for m in updated_members}
        actual_manager_ids = {m["umu_id"] for m in updated_managers}

        failed_adds = [uid for uid in add_ids if uid not in actual_member_ids]
        dropped_managers = [uid for uid in manager_ids if uid not in actual_manager_ids]

        data: dict[str, Any] = {
            "group_id": group_id,
            "added_member_ids": [uid for uid in add_ids if uid in actual_member_ids],
            "member_count": len(actual_member_ids),
            "manager_count": len(actual_manager_ids),
        }
        if failed_adds:
            data["failed_member_ids"] = failed_adds
        if dropped_managers:
            data["dropped_manager_ids"] = dropped_managers

        if failed_adds or dropped_managers:
            return _err(
                error_code="PARTIAL_UPDATE",
                error_message=(
                    f"部分操作未生效：未成功添加成员 {failed_adds}；"
                    f"原有管理员被移除 {dropped_managers}"
                ).strip("；"),
                data=data,
                suggested_action="检查账号角色权限或 umu_id 是否正确，"
                "必要时单独修复被误移除的管理员",
            )

        return _ok(
            data=data,
            next_action="proceed",
            suggested_action="成员已添加，可调用 adm_list_group_members 查看",
        )
    except Exception as e:
        return _err(
            error_code="ADD_GROUP_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查 umu_id 或 group_id 是否正确",
        )


@mcp.tool()
async def adm_remove_group_members(
    group_id: Annotated[str, Field(description="分组 ID")],
    umu_ids: Annotated[
        str,
        Field(description="要移除的成员 umu_id 列表，多个用逗号分隔，如 '20439812,20439813'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """从分组移除成员.

    触发条件：需要将用户从分组中移除时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定用户从分组成员列表中移除。

    注意：如果用户同时是分组管理员，移除后仍保留管理员身份；如需完全移除，
    请同时调用 adm_remove_group_managers。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    remove_ids = {uid.strip() for uid in umu_ids.split(",") if uid.strip()}
    if not remove_ids:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    try:
        current_members = _get_all_group_users(client, group_id, is_manager=0)
        current_managers = _get_all_group_users(client, group_id, is_manager=1)

        member_ids = [m["umu_id"] for m in current_members if m["umu_id"] not in remove_ids]
        manager_ids = [m["umu_id"] for m in current_managers]

        _update_group_membership(client, group_id, member_ids, manager_ids)

        # 校验后端是否按预期生效
        updated_members = _get_all_group_users(client, group_id, is_manager=0)
        updated_managers = _get_all_group_users(client, group_id, is_manager=1)
        actual_member_ids = {m["umu_id"] for m in updated_members}
        actual_manager_ids = {m["umu_id"] for m in updated_managers}

        still_present = remove_ids & actual_member_ids
        dropped_managers = [uid for uid in manager_ids if uid not in actual_manager_ids]

        data: dict[str, Any] = {
            "group_id": group_id,
            "removed_member_ids": sorted(remove_ids - still_present),
            "member_count": len(actual_member_ids),
            "manager_count": len(actual_manager_ids),
        }
        if still_present:
            data["not_removed_member_ids"] = sorted(still_present)
        if dropped_managers:
            data["dropped_manager_ids"] = dropped_managers

        if still_present or dropped_managers:
            return _err(
                error_code="PARTIAL_UPDATE",
                error_message=(
                    f"部分操作未生效：未能移除成员 {sorted(still_present)}；"
                    f"原有管理员被移除 {dropped_managers}"
                ).strip("；"),
                data=data,
                suggested_action="检查账号是否仍在分组中，必要时单独修复被误移除的管理员",
            )

        return _ok(
            data=data,
            next_action="proceed",
            suggested_action="成员已移除，可调用 adm_list_group_members 查看",
        )
    except Exception as e:
        return _err(
            error_code="REMOVE_GROUP_MEMBERS_ERROR",
            error_message=str(e),
            suggested_action="检查 umu_id 或 group_id 是否正确",
        )


@mcp.tool()
async def adm_add_group_managers(
    group_id: Annotated[str, Field(description="分组 ID")],
    umu_ids: Annotated[
        str,
        Field(description="要添加的管理员 umu_id 列表，多个用逗号分隔，如 '20458620,17580402'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """添加管理员到分组.

    触发条件：需要为分组设置管理员时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定用户添加到目标分组的管理员列表。

    注意：若用户还不是分组成员，调用后同时会成为成员（因为成员列表保持不变）。
    若希望仅设为管理员而不保留成员身份，请先调用 adm_remove_group_members。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    add_ids = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    if not add_ids:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    try:
        current_members = _get_all_group_users(client, group_id, is_manager=0)
        current_managers = _get_all_group_users(client, group_id, is_manager=1)

        member_ids = [m["umu_id"] for m in current_members]
        manager_ids = [m["umu_id"] for m in current_managers]

        # 合并新管理员，保持原有成员不变
        new_manager_ids = list(dict.fromkeys(manager_ids + add_ids))

        _update_group_membership(client, group_id, member_ids, new_manager_ids)

        # 校验后端是否按预期生效（updateGroupUser 遇到不合法角色时会静默忽略）
        updated_members = _get_all_group_users(client, group_id, is_manager=0)
        updated_managers = _get_all_group_users(client, group_id, is_manager=1)
        actual_member_ids = {m["umu_id"] for m in updated_members}
        actual_manager_ids = {m["umu_id"] for m in updated_managers}

        failed_adds = [uid for uid in add_ids if uid not in actual_manager_ids]
        dropped_members = [uid for uid in member_ids if uid not in actual_member_ids]

        data: dict[str, Any] = {
            "group_id": group_id,
            "added_manager_ids": [uid for uid in add_ids if uid in actual_manager_ids],
            "manager_count": len(actual_manager_ids),
            "member_count": len(actual_member_ids),
        }
        if failed_adds:
            data["failed_manager_ids"] = failed_adds
        if dropped_members:
            data["dropped_member_ids"] = dropped_members

        if failed_adds or dropped_members:
            return _err(
                error_code="PARTIAL_UPDATE",
                error_message=(
                    f"部分操作未生效：未成功添加管理员 {failed_adds}；"
                    f"原有成员被移除 {dropped_members}"
                ).strip("；"),
                data=data,
                suggested_action="检查账号角色权限或 umu_id 是否正确，必要时单独修复被误移除的成员",
            )

        return _ok(
            data=data,
            next_action="proceed",
            suggested_action="管理员已添加，可调用 adm_list_group_managers 查看",
        )
    except Exception as e:
        return _err(
            error_code="ADD_GROUP_MANAGERS_ERROR",
            error_message=str(e),
            suggested_action="检查 umu_id 或 group_id 是否正确",
        )


@mcp.tool()
async def adm_remove_group_managers(
    group_id: Annotated[str, Field(description="分组 ID")],
    umu_ids: Annotated[
        str,
        Field(description="要移除的管理员 umu_id 列表，多个用逗号分隔，如 '20458620,17580402'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """从分组移除管理员.

    触发条件：需要取消分组管理员权限时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：将指定用户从分组管理员列表中移除。

    注意：移除管理员后，该用户仍保留分组成员身份；如需完全移除，
    请同时调用 adm_remove_group_members。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    remove_ids = {uid.strip() for uid in umu_ids.split(",") if uid.strip()}
    if not remove_ids:
        return _err(
            error_code="EMPTY_UMU_IDS",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    try:
        current_members = _get_all_group_users(client, group_id, is_manager=0)
        current_managers = _get_all_group_users(client, group_id, is_manager=1)

        member_ids = [m["umu_id"] for m in current_members]
        manager_ids = [m["umu_id"] for m in current_managers if m["umu_id"] not in remove_ids]

        _update_group_membership(client, group_id, member_ids, manager_ids)

        # 校验后端是否按预期生效
        updated_members = _get_all_group_users(client, group_id, is_manager=0)
        updated_managers = _get_all_group_users(client, group_id, is_manager=1)
        actual_member_ids = {m["umu_id"] for m in updated_members}
        actual_manager_ids = {m["umu_id"] for m in updated_managers}

        still_present = remove_ids & actual_manager_ids
        dropped_members = [uid for uid in member_ids if uid not in actual_member_ids]

        data: dict[str, Any] = {
            "group_id": group_id,
            "removed_manager_ids": sorted(remove_ids - still_present),
            "manager_count": len(actual_manager_ids),
            "member_count": len(actual_member_ids),
        }
        if still_present:
            data["not_removed_manager_ids"] = sorted(still_present)
        if dropped_members:
            data["dropped_member_ids"] = dropped_members

        if still_present or dropped_members:
            return _err(
                error_code="PARTIAL_UPDATE",
                error_message=(
                    f"部分操作未生效：未能移除管理员 {sorted(still_present)}；"
                    f"原有成员被移除 {dropped_members}"
                ).strip("；"),
                data=data,
                suggested_action="检查账号是否仍在管理员列表中，必要时单独修复被误移除的成员",
            )

        return _ok(
            data=data,
            next_action="proceed",
            suggested_action="管理员已移除，可调用 adm_list_group_managers 查看",
        )
    except Exception as e:
        return _err(
            error_code="REMOVE_GROUP_MANAGERS_ERROR",
            error_message=str(e),
            suggested_action="检查 umu_id 或 group_id 是否正确",
        )


@mcp.tool()
async def adm_list_accounts(
    keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="搜索关键词（姓名、邮箱、手机号、用户名），服务端模糊匹配。",
        ),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分组ID列表，多个用逗号分隔，如 '177124,177125'。不提供则不按分组筛选。",
        ),
    ] = None,
    group_operator: Annotated[
        str,
        Field(
            default="intersection",
            description='多分组关系："intersection"=交集（同时属于所有勾选的分组），'
            '"union"=并集（属于所勾选的任意一个分组）。',
        ),
    ] = "intersection",
    role_type: Annotated[
        int | None,
        Field(
            default=None,
            description="角色筛选：1=学员, 2=讲师, 3=学习负责人, 4=系统管理员, 5=子管理员。不提供则不筛选。",
        ),
    ] = None,
    account_status: Annotated[
        int | None,
        Field(
            default=None,
            description="状态筛选：0=待加入, 1=已启用, 2=已禁用, 3=定时禁用。"
            "注意：不同企业平台状态码映射可能不同，请以筛选结果为准。",
        ),
    ] = None,
    is_manager: Annotated[
        int,
        Field(default=0, description="0=返回全部账号（不限制角色）, 1=仅返回管理视角账号"),
    ] = 0,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=500, ge=1, le=500, description="每页数量（1-500），默认500"),
    ] = 500,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业账号列表（支持多条件组合搜索）.

    触发条件：需要查找用户、查看账号状态、按条件筛选账号时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    所有参数均可组合使用，例如：
    - 搜索B分组中已禁用的讲师：
      group_ids="251104", role_type=2, account_status=2
    - 搜索同时属于分组一和分组二的学员：
      group_ids="177124,177125", group_operator="intersection", role_type=1
    - 搜索在分组一或分组二中的已启用账号：
      group_ids="177124,177125", group_operator="union", account_status=1

    返回字段说明：
    - account_status: 状态码（数字），不同企业平台映射可能不同
    - status_text: 状态人读文本（基于常见映射，仅供参考）
    - is_active: "1"=活跃
    - role_type: 1=学员, 2=讲师, 3=学习负责人, 4=系统管理员, 5=子管理员
    - account_joining_time / first_login_time / last_login_time: Unix 时间戳（秒）
    - *_readable: 对应时间戳的北京时间字符串（%Y-%m-%d %H:%M:%S）
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], int]:
        """获取单页数据，返回(账号列表, 总数量)."""
        params: dict[str, str] = {
            "is_manager": str(is_manager),
            "page": str(p),
            "size": str(sz),
            "group_operator": group_operator,
        }
        if keywords:
            params["keywords"] = keywords
        if group_ids:
            params["group_ids"] = group_ids
        if role_type is not None:
            params["role_type"] = str(role_type)
        if account_status is not None:
            params["account_status"] = str(account_status)

        resp = client.get(
            client.desktop_url("/ajax/enterprise/getUserList"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取账号列表失败"))

        user_list = resp.get("data", {}).get("list", [])
        accounts = []
        for user in user_list:
            try:
                raw = AdminAccountRaw(**user)
                accounts.append(AdminAccount.from_raw(raw).model_dump())
            except Exception:
                # 如果个别账号字段异常，回退到原始字典构造逻辑，保证列表不中断
                status_code = int(user.get("account_status", 0) or 0)
                accounts.append(
                    {
                        "umu_id": str(user.get("umu_id", "")),
                        "user_name": user.get("user_name", ""),
                        "email": user.get("email", ""),
                        "phone": user.get("phone", ""),
                        "login_name": user.get("login_name", ""),
                        "number": user.get("number", ""),
                        "account_status": status_code,
                        "status_text": _get_status_text(status_code),
                        "is_active": user.get("is_active", ""),
                        "role_type": int(user.get("role_type", 0) or 0),
                        "role_name": _ROLE_TYPE_MAP.get(int(user.get("role_type", 0) or 0), "未知"),
                        "departments": user.get("departments", ""),
                        "account_joining_time": user.get("account_joining_time", 0),
                        "account_joining_time_readable": format_timestamp_beijing(
                            user.get("account_joining_time", 0) or 0
                        ),
                        "first_login_time": user.get("first_login_time", 0),
                        "first_login_time_readable": format_timestamp_beijing(
                            user.get("first_login_time", 0) or 0
                        ),
                        "last_login_time": user.get("last_login_time", 0),
                        "last_login_time_readable": format_timestamp_beijing(
                            user.get("last_login_time", 0) or 0
                        ),
                    }
                )

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return accounts, total_all

    try:
        if fetch_all:
            # 自动获取全量数据
            all_accounts: list[dict] = []
            current_page = 1
            batch_size = 500
            total_all = 0

            while True:
                accounts, total_all = _fetch_page(current_page, batch_size)
                all_accounts.extend(accounts)

                # 控制台进度提示（输出到 stderr，避免干扰 MCP stdio 协议）
                report_pagination_progress(
                    "adm_list_accounts",
                    current_page,
                    len(all_accounts),
                    total_all,
                    500,
                    is_complete=len(all_accounts) >= total_all or not accounts,
                )

                if len(all_accounts) >= total_all or not accounts:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_accounts",
                        current_page,
                        len(all_accounts),
                        total_all,
                        500,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "accounts": all_accounts,
                    "total": len(all_accounts),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_accounts) if all_accounts else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 或 email 调用 adm_disable_account / adm_enable_account",
            )
        else:
            # 单页模式
            accounts, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "accounts": accounts,
                    "total": len(accounts),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 或 email 调用 adm_disable_account / adm_enable_account",
            )
    except Exception as e:
        return _err(
            error_code="LIST_ACCOUNTS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_list_learning_records(
    start_day: Annotated[
        str | None,
        Field(
            default=None,
            description="最后学习时间的起始日期，格式 YYYY-MM-DD。与 end_day 配合使用。",
        ),
    ] = None,
    end_day: Annotated[
        str | None,
        Field(
            default=None,
            description="最后学习时间的结束日期，格式 YYYY-MM-DD。与 start_day 配合使用。",
        ),
    ] = None,
    student_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="学员搜索关键词（姓名、邮箱、手机号、用户名）。"
            "提供时工具内部会自动调用 user-list 接口获取 uids 进行精确筛选。",
        ),
    ] = None,
    course_title: Annotated[
        str | None,
        Field(
            default=None,
            description="课程名称模糊搜索关键词。",
        ),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="部门ID列表，多个用逗号分隔，如 '251103,251104'。不提供则不按部门筛选。",
        ),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="企业分组ID列表，多个用逗号分隔，如 '177124,177125'。不提供则不按分组筛选。",
        ),
    ] = None,
    class_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="班级ID列表，多个用逗号分隔，如 '442992,442993'。不提供则不按班级筛选。",
        ),
    ] = None,
    class_names: Annotated[
        str | None,
        Field(
            default=None,
            description="班级名称关键词，多个用逗号分隔。提供时工具内部会自动调用 class-list 接口"
            "获取班级 IDs 进行精确筛选。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业账号的课程学习明细.

    触发条件：需要查看学员课程学习进度、完成率、学习时长等明细数据时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持筛选条件：
    - 最后学习时间范围：start_day / end_day
    - 学员关键词：student_keywords（姓名/邮箱/手机号/用户名）
    - 课程名称：course_title（模糊搜索）
    - 部门：department_ids（逗号分隔）
    - 企业分组：group_ids（逗号分隔）
    - 班级：class_ids（逗号分隔）或 class_names（自动解析）

    返回字段说明：
    - first_learning_time / last_learning_time: Unix 时间戳（秒）
    - *_readable: 对应时间戳的北京时间字符串
    - group_completion_rate / group_overall_completion_rate: 完成率（0-1）
    - group_required_session_total_count / group_required_session_finished_count: 必修小节数/完成数
    - sum_learning_time / vlt: 学习时长
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 如果提供了学员关键词，先解析为 uids
    uids: list[str] | None = None
    if student_keywords:
        try:
            uids = await _resolve_student_keywords(client, student_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_STUDENT_ERROR",
                error_message=str(e),
                suggested_action="检查学员关键词或网络连接后重试",
            )
        if uids is None:
            return _err(
                error_code="STUDENT_NOT_FOUND",
                error_message=f"找不到匹配的学员: {student_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询学员信息",
            )

    # 如果提供了班级名称，先解析为 class_ids
    resolved_class_ids: list[str] | None = None
    if class_names:
        try:
            resolved_class_ids = await _resolve_class_names(client, class_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_CLASS_ERROR",
                error_message=str(e),
                suggested_action="检查班级名称或网络连接后重试",
            )
        if resolved_class_ids is None:
            return _err(
                error_code="CLASS_NOT_FOUND",
                error_message=f"找不到匹配的班级: {class_names}",
                suggested_action="检查班级名称拼写，或调用 adm_list_classes 查询班级信息",
            )

    # 合并直接传入的 class_ids 和从名称解析的 class_ids
    final_class_ids: list[str] | None = None
    if class_ids:
        final_class_ids = [c.strip() for c in class_ids.split(",") if c.strip()]
    if resolved_class_ids:
        if final_class_ids:
            final_class_ids = list(set(final_class_ids + resolved_class_ids))
        else:
            final_class_ids = resolved_class_ids

    search_condition = _build_learning_records_search_condition(
        start_day=start_day,
        end_day=end_day,
        uids=uids,
        course_title=course_title,
        department_ids=department_ids,
        group_ids=group_ids,
        class_ids=final_class_ids,
    )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], int]:
        """获取单页数据，返回(学习记录列表, 总数量)."""
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
            "search_condition": json.dumps(search_condition, ensure_ascii=False),
        }
        if start_day:
            params["start_day"] = start_day
        if end_day:
            params["end_day"] = end_day
        if department_ids:
            params["department_ids"] = department_ids
        if group_ids:
            params["enterprise_group_ids"] = group_ids
        if final_class_ids:
            params["class_ids"] = ",".join(final_class_ids)

        resp = client.get(
            client.desktop_url("/uapi/v1/dashboard/learning-group-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取学习记录失败"))

        record_list = resp.get("data", {}).get("list", [])
        records = []
        for item in record_list:
            try:
                raw = LearningRecordRaw(**item)
                records.append(LearningRecord.from_raw(raw).model_dump())
            except Exception:
                # 如果个别记录字段异常，回退到原始字典构造逻辑，保证列表不中断
                records.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return records, total_all

    try:
        if fetch_all:
            all_records: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                records, total_all = _fetch_page(current_page, batch_size)
                all_records.extend(records)

                report_pagination_progress(
                    "adm_list_learning_records",
                    current_page,
                    len(all_records),
                    total_all,
                    20,
                    is_complete=len(all_records) >= total_all or not records,
                )

                if len(all_records) >= total_all or not records:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_learning_records",
                        current_page,
                        len(all_records),
                        total_all,
                        20,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "records": all_records,
                    "total": len(all_records),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_records) if all_records else 0,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
        else:
            records, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "records": records,
                    "total": len(records),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
    except Exception as e:
        return _err(
            error_code="LIST_LEARNING_RECORDS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


def _day_to_timestamp(day: str, end_of_day: bool = False) -> int:
    """将 YYYY-MM-DD 转换为 Unix 时间戳（Asia/Shanghai +08:00）.

    Args:
        day: 日期字符串，格式 YYYY-MM-DD
        end_of_day: 是否转换为当天 23:59:59；否则为 00:00:00

    Returns:
        Unix 时间戳（秒）
    """
    dt = datetime.strptime(day, "%Y-%m-%d")
    tz = timezone(timedelta(hours=8))
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    else:
        dt = dt.replace(hour=0, minute=0, second=0)
    return int(dt.replace(tzinfo=tz).timestamp())


def _build_user_task_search_condition(
    task_types: list[str] | None = None,
    learn_status: list[str] | None = None,
    due_status: list[str] | None = None,
    department_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    class_ids: list[str] | None = None,
    from_umu_ids: list[str] | None = None,
    assign_umu_ids: list[str] | None = None,
    task_name: str | None = None,
    course_keywords: str | None = None,
    assign_start_ts: int | None = None,
    assign_stop_ts: int | None = None,
    due_start_ts: int | None = None,
    due_stop_ts: int | None = None,
) -> dict[str, Any]:
    """构建任务明细查询的 search_condition JSON 对象.

    对应 GET /uapi/v1/dashboard/user-task-list 的 search_condition 参数。

    Args:
        task_types: 任务类型列表，"1"=小节, "2"=课程, "3"=学习项目
        learn_status: 完成状态列表，"0"=待学习, "1"=学习中, "2"=按时完成, "3"=逾期完成
        due_status: 到期状态列表，"0"=已到期, "1"=未到期, "2"=未指定到期时间
        department_ids: 部门 ID 列表
        group_ids: 分组 ID 列表
        class_ids: 班级 ID 列表
        from_umu_ids: 分配者 umu_id 列表
        assign_umu_ids: 学员 umu_id 列表
        task_name: 学习任务名称模糊搜索
        course_keywords: 课程名称/描述/标签模糊搜索
        assign_start_ts: 分配时间起始时间戳（秒）
        assign_stop_ts: 分配时间结束时间戳（秒）
        due_start_ts: 到期时间起始时间戳（秒）
        due_stop_ts: 到期时间结束时间戳（秒）

    Returns:
        search_condition 字典，将被 JSON 序列化后作为查询参数。
    """
    condition: dict[str, Any] = {}

    if task_types:
        condition["obj_type"] = ",".join(task_types)
    if learn_status:
        condition["learn_status"] = ",".join(learn_status)
    if due_status:
        condition["due_status"] = ",".join(due_status)
    if department_ids:
        condition["department_ids"] = ",".join(department_ids)
    if group_ids:
        condition["enterprise_group_ids"] = ",".join(group_ids)
    if class_ids:
        condition["class_ids"] = ",".join(class_ids)
    if from_umu_ids:
        condition["from_umu_ids"] = ",".join(from_umu_ids)
    if assign_umu_ids:
        condition["assign_umu_ids"] = ",".join(assign_umu_ids)
    if task_name:
        condition["task_name"] = task_name
    if course_keywords:
        condition["keywords"] = course_keywords
    if assign_start_ts is not None:
        condition["assign_start_ts"] = assign_start_ts
    if assign_stop_ts is not None:
        condition["assign_stop_ts"] = assign_stop_ts
    if due_start_ts is not None:
        condition["due_start_ts"] = due_start_ts
    if due_stop_ts is not None:
        condition["due_stop_ts"] = due_stop_ts

    return condition


@mcp.tool()
async def adm_list_user_tasks(
    task_types: Annotated[
        str | None,
        Field(
            default=None,
            description="任务类型，逗号分隔：1=小节, 2=课程, 3=学习项目",
        ),
    ] = None,
    learn_status: Annotated[
        str | None,
        Field(
            default=None,
            description="完成状态，逗号分隔：0=待学习, 1=学习中, 2=按时完成, 3=逾期完成",
        ),
    ] = None,
    due_status: Annotated[
        str | None,
        Field(
            default=None,
            description="到期状态，逗号分隔：0=已到期, 1=未到期, 2=未指定到期时间",
        ),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="部门 ID 列表，多个用逗号分隔，如 '251103,251104'。不提供则不按部门筛选。",
        ),
    ] = None,
    department_names: Annotated[
        str | None,
        Field(
            default=None,
            description="部门名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分组 ID 列表，多个用逗号分隔，如 '177124,177125'。不提供则不按分组筛选。",
        ),
    ] = None,
    group_names: Annotated[
        str | None,
        Field(
            default=None,
            description="分组名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    class_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="班级 ID 列表，多个用逗号分隔，如 '442992,442993'。不提供则不按班级筛选。",
        ),
    ] = None,
    class_names: Annotated[
        str | None,
        Field(
            default=None,
            description="班级名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    assigner_umu_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分配者 umu_id 列表，多个用逗号分隔。不提供则不按分配者筛选。",
        ),
    ] = None,
    assigner_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="分配者姓名/邮箱/用户名关键词，内部自动解析为 umu_id 进行精确筛选。",
        ),
    ] = None,
    student_umu_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分配学员 umu_id 列表，多个用逗号分隔。不提供则不按学员筛选。",
        ),
    ] = None,
    student_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="分配学员姓名/邮箱/用户名关键词，内部自动解析为 umu_id 进行精确筛选。",
        ),
    ] = None,
    task_name: Annotated[
        str | None,
        Field(
            default=None,
            description="学习任务名称模糊搜索关键词。",
        ),
    ] = None,
    course_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="课程名称/描述/标签模糊搜索关键词。",
        ),
    ] = None,
    assign_start_day: Annotated[
        str | None,
        Field(
            default=None,
            description="分配时间起始日期，格式 YYYY-MM-DD。与 assign_end_day 配合使用。未提供时默认查询最近 90 天。",
        ),
    ] = None,
    assign_end_day: Annotated[
        str | None,
        Field(
            default=None,
            description="分配时间结束日期，格式 YYYY-MM-DD。与 assign_start_day 配合使用。",
        ),
    ] = None,
    due_start_day: Annotated[
        str | None,
        Field(
            default=None,
            description="到期时间起始日期，格式 YYYY-MM-DD。与 due_end_day 配合使用。",
        ),
    ] = None,
    due_end_day: Annotated[
        str | None,
        Field(
            default=None,
            description="到期时间结束日期，格式 YYYY-MM-DD。与 due_start_day 配合使用。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=500, ge=1, le=1000, description="每页数量（1-1000），默认500"),
    ] = 500,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业学习任务明细.

    触发条件：需要查看学员被分配的学习任务、完成状态、到期状态等明细时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持按任务类型、完成状态、到期状态、部门、分组、班级、分配者、学员、
    学习任务名称、课程关键词、分配时间范围、到期时间范围等多条件交集筛选。

    未提供分配时间范围（assign_start_day / assign_end_day）时，默认查询最近 90 天。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # -----------------------------------------------------------------------
    # 默认最近 90 天（UTC+8）
    # -----------------------------------------------------------------------
    if not assign_start_day and not assign_end_day:
        today = datetime.now(timezone(timedelta(hours=8)))
        start = today - timedelta(days=90)
        assign_start_day = start.strftime("%Y-%m-%d")
        assign_end_day = today.strftime("%Y-%m-%d")
        date_limited_by_default = True
    else:
        date_limited_by_default = False

    # -----------------------------------------------------------------------
    # 解析名称/关键词为 IDs
    # -----------------------------------------------------------------------
    resolved_dept_ids: list[str] | None = None
    if department_names:
        try:
            resolved_dept_ids = await _resolve_department_names(client, department_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_DEPARTMENT_ERROR",
                error_message=str(e),
                suggested_action="检查部门名称或网络连接后重试",
            )
        if resolved_dept_ids is None:
            return _err(
                error_code="DEPARTMENT_NOT_FOUND",
                error_message=f"找不到匹配的部门: {department_names}",
                suggested_action="检查部门名称拼写，或调用 adm_get_department_tree 查询部门信息",
            )

    resolved_group_ids: list[str] | None = None
    if group_names:
        try:
            resolved_group_ids = await _resolve_group_names(client, group_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_GROUP_ERROR",
                error_message=str(e),
                suggested_action="检查分组名称或网络连接后重试",
            )
        if resolved_group_ids is None:
            return _err(
                error_code="GROUP_NOT_FOUND",
                error_message=f"找不到匹配的分组: {group_names}",
                suggested_action="检查分组名称拼写，或调用 adm_list_groups 查询分组信息",
            )

    resolved_class_ids: list[str] | None = None
    if class_names:
        try:
            resolved_class_ids = await _resolve_class_names_all(client, class_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_CLASS_ERROR",
                error_message=str(e),
                suggested_action="检查班级名称或网络连接后重试",
            )
        if resolved_class_ids is None:
            return _err(
                error_code="CLASS_NOT_FOUND",
                error_message=f"找不到匹配的班级: {class_names}",
                suggested_action="检查班级名称拼写，或调用 adm_list_classes 查询班级信息",
            )

    resolved_assigner_ids: list[str] | None = None
    if assigner_keywords:
        try:
            resolved_assigner_ids = await _resolve_user_keywords(client, assigner_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_ASSIGNER_ERROR",
                error_message=str(e),
                suggested_action="检查分配者关键词或网络连接后重试",
            )
        if resolved_assigner_ids is None:
            return _err(
                error_code="ASSIGNER_NOT_FOUND",
                error_message=f"找不到匹配的分配者: {assigner_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询用户信息",
            )

    resolved_student_ids: list[str] | None = None
    if student_keywords:
        try:
            resolved_student_ids = await _resolve_user_keywords(client, student_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_STUDENT_ERROR",
                error_message=str(e),
                suggested_action="检查学员关键词或网络连接后重试",
            )
        if resolved_student_ids is None:
            return _err(
                error_code="STUDENT_NOT_FOUND",
                error_message=f"找不到匹配的学员: {student_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询学员信息",
            )

    # -----------------------------------------------------------------------
    # 合并显式 ID 与解析得到的 ID，去重
    # -----------------------------------------------------------------------
    def _merge_ids(explicit: str | None, resolved: list[str] | None) -> list[str] | None:
        result: list[str] = []
        if explicit:
            result.extend([x.strip() for x in explicit.split(",") if x.strip()])
        if resolved:
            result.extend(resolved)
        return list(dict.fromkeys(result)) if result else None

    final_department_ids = _merge_ids(department_ids, resolved_dept_ids)
    final_group_ids = _merge_ids(group_ids, resolved_group_ids)
    final_class_ids = _merge_ids(class_ids, resolved_class_ids)
    final_assigner_ids = _merge_ids(assigner_umu_ids, resolved_assigner_ids)
    final_student_ids = _merge_ids(student_umu_ids, resolved_student_ids)

    # -----------------------------------------------------------------------
    # 时间范围转时间戳
    # -----------------------------------------------------------------------
    assign_start_ts: int | None = None
    assign_stop_ts: int | None = None
    if assign_start_day:
        assign_start_ts = _day_to_timestamp(assign_start_day, end_of_day=False)
    if assign_end_day:
        assign_stop_ts = _day_to_timestamp(assign_end_day, end_of_day=True)

    due_start_ts: int | None = None
    due_stop_ts: int | None = None
    if due_start_day:
        due_start_ts = _day_to_timestamp(due_start_day, end_of_day=False)
    if due_end_day:
        due_stop_ts = _day_to_timestamp(due_end_day, end_of_day=True)

    # -----------------------------------------------------------------------
    # 构建 search_condition
    # -----------------------------------------------------------------------
    search_condition = _build_user_task_search_condition(
        task_types=[x.strip() for x in task_types.split(",") if x.strip()] if task_types else None,
        learn_status=[x.strip() for x in learn_status.split(",") if x.strip()]
        if learn_status
        else None,
        due_status=[x.strip() for x in due_status.split(",") if x.strip()] if due_status else None,
        department_ids=final_department_ids,
        group_ids=final_group_ids,
        class_ids=final_class_ids,
        from_umu_ids=final_assigner_ids,
        assign_umu_ids=final_student_ids,
        task_name=task_name,
        course_keywords=course_keywords,
        assign_start_ts=assign_start_ts,
        assign_stop_ts=assign_stop_ts,
        due_start_ts=due_start_ts,
        due_stop_ts=due_stop_ts,
    )

    # -----------------------------------------------------------------------
    # 单页获取
    # -----------------------------------------------------------------------
    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
            "search_condition": json.dumps(search_condition, ensure_ascii=False),
        }

        resp = client.get(
            client.desktop_url("/uapi/v1/dashboard/user-task-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取任务明细失败"))

        task_list = resp.get("data", {}).get("list", [])
        tasks: list[dict[str, Any]] = []
        for item in task_list:
            try:
                raw = UserTaskRaw(**item)
                tasks.append(UserTask.from_raw(raw).model_dump())
            except Exception:
                # 如果个别记录字段异常，回退到原始字典，保证列表不中断
                tasks.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return tasks, total_all

    # -----------------------------------------------------------------------
    # 执行查询
    # -----------------------------------------------------------------------
    try:
        if fetch_all:
            all_tasks: list[dict[str, Any]] = []
            current_page = 1
            batch_size = page_size
            total_all = 0

            while True:
                try:
                    tasks, total_all = _fetch_page(current_page, batch_size)
                except Exception:
                    # 单页请求失败，尝试 size 降级到 100 重试一次
                    if batch_size != 100:
                        batch_size = 100
                        tasks, total_all = _fetch_page(current_page, batch_size)
                    else:
                        raise

                all_tasks.extend(tasks)

                report_pagination_progress(
                    "adm_list_user_tasks",
                    current_page,
                    len(all_tasks),
                    total_all,
                    page_size,
                    is_complete=len(all_tasks) >= total_all or not tasks,
                )

                if len(all_tasks) >= total_all or not tasks:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_user_tasks",
                        current_page,
                        len(all_tasks),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            suggested = ""
            if date_limited_by_default:
                suggested = (
                    "默认仅查询最近 90 天，如需更长时间范围请指定 assign_start_day / assign_end_day"
                )

            return _ok(
                data={
                    "tasks": all_tasks,
                    "total": len(all_tasks),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_tasks) if all_tasks else 0,
                    },
                },
                next_action="proceed",
                suggested_action=suggested,
            )
        else:
            tasks, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "tasks": tasks,
                    "total": len(tasks),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
    except Exception as e:
        return _err(
            error_code="LIST_USER_TASKS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_list_classes(
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    fuzzy_name: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的班级名称模糊匹配关键词。提供时会自动获取全量列表"
            "并筛选最匹配的候选，返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业班级列表.

    触发条件：需要获取班级 ID 用于学习记录筛选，或查看企业班级信息时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    返回字段说明：
    - id: 班级 ID
    - name: 班级名称
    - access_code: 班级访问码
    - create_teacher_id: 创建者教师 ID
    - cover_image: 班级封面图 URL
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    effective_fetch_all = fetch_all or bool(fuzzy_name and fuzzy_name.strip())

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], int]:
        """获取单页数据，返回(班级列表, 总数量)."""
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
        }

        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/class-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取班级列表失败"))

        class_list = resp.get("data", {}).get("list", [])
        classes = []
        for item in class_list:
            try:
                raw = AdminClassRaw(**item)
                classes.append(AdminClass.from_raw(raw).model_dump())
            except Exception:
                classes.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return classes, total_all

    try:
        if effective_fetch_all:
            all_classes: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                classes, total_all = _fetch_page(current_page, batch_size)
                all_classes.extend(classes)

                report_pagination_progress(
                    "adm_list_classes",
                    current_page,
                    len(all_classes),
                    total_all,
                    20,
                    is_complete=len(all_classes) >= total_all or not classes,
                )

                if len(all_classes) >= total_all or not classes:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_classes",
                        current_page,
                        len(all_classes),
                        total_all,
                        20,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            result_classes = all_classes
            if fuzzy_name and fuzzy_name.strip():
                result_classes = fuzzy_filter_items(
                    all_classes,
                    fuzzy_name,
                    key="name",
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )

            return _ok(
                data={
                    "classes": result_classes,
                    "total": len(result_classes),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(result_classes) if result_classes else 0,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
        else:
            classes, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "classes": classes,
                    "total": len(classes),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
    except Exception as e:
        return _err(
            error_code="LIST_CLASSES_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_disable_account(
    umu_id: Annotated[
        str | None,
        Field(
            default=None,
            description="用户 umu_id，与 email 二选一。",
        ),
    ] = None,
    email: Annotated[
        str | None,
        Field(
            default=None,
            description="用户邮箱，与 umu_id 二选一。提供时自动查询对应 umu_id。",
        ),
    ] = None,
    effective_time: Annotated[
        str | None,
        Field(
            default=None,
            description='生效时间。留空或传 "immediate" 表示立即禁用；'
            '传日期时间如 "2026-06-12T09:00" 表示定时禁用（东八区）。',
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """禁用账号.

    触发条件：需要禁用某个学员/讲师/负责人的账号时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改用户在 UMU 平台的状态为禁用。

    支持两种模式：
    - 立即禁用：不传 effective_time 或传 "immediate"
    - 定时禁用：传未来的日期时间，如 "2026-06-12T09:00"
      UMU 平台会在指定时间自动执行禁用，无需 MCP 层维护定时器。

    查找优先级：
    1. 如提供 umu_id，直接使用
    2. 如未提供 umu_id 但提供 email，自动查询账号列表获取 umu_id

    【禁用后的影响】
    - 被禁用的账户将立即从系统登出，并限制不可登录平台
    - 被禁用的账户将继续占用企业账户额度
    - 创建的课程、学习任务、学习数据等都会被保留
    - 如需再次启用，可调用 adm_enable_account 或在管理后台手动启用
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 参数校验：至少提供一个标识
    if not umu_id and not email:
        return _err(
            error_code="MISSING_IDENTIFIER",
            error_message="必须提供 umu_id 或 email 之一",
            suggested_action="调用 adm_list_accounts 查询账号信息",
        )

    # 通过 email 查找 umu_id
    if not umu_id and email:
        user = _find_user_by_email(client, email)
        if user is None:
            return _err(
                error_code="USER_NOT_FOUND",
                error_message=f"找不到邮箱为 {email} 的用户",
                suggested_action="检查邮箱是否正确，或调用 adm_list_accounts 查询",
            )
        umu_id = str(user.get("umu_id", ""))
        user_name = user.get("user_name", "")
    else:
        user_name = ""

    # 解析生效时间
    try:
        effective_timestamp = _parse_effective_time(effective_time)
    except ValueError as e:
        return _err(
            error_code="INVALID_TIME_FORMAT",
            error_message=str(e),
            suggested_action='使用格式: "2026-06-12T09:00" 或 "2026-06-12 09:00"',
        )

    # 定时禁用：检查时间是否已过
    if effective_timestamp > 0:
        now_ts = int(datetime.now(timezone(timedelta(hours=8))).timestamp())
        if effective_timestamp <= now_ts:
            return _err(
                error_code="TIME_IN_PAST",
                error_message="指定的禁用时间已过，请提供未来的时间",
                suggested_action='使用未来的时间，如 "2026-06-13T09:00"',
            )

    # 调用禁用 API
    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/enterprise/update-account-status"),
            data={
                "umu_ids": umu_id,
                "status": "0",
                "effective_time": str(effective_timestamp),
            },
        )
    except Exception as e:
        return _err(
            error_code="DISABLE_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或管理员权限后重试",
        )

    if resp.get("error_code") != 0:
        return _err(
            error_code="DISABLE_FAILED",
            error_message=resp.get("error_message", "禁用账号失败"),
            suggested_action="检查用户是否存在或是否已被禁用",
        )

    # 构建人类可读的时间描述
    if effective_timestamp > 0:
        dt = datetime.fromtimestamp(effective_timestamp, tz=timezone(timedelta(hours=8)))
        time_human = dt.strftime("%Y-%m-%d %H:%M (东八区)")
        is_scheduled = True
    else:
        time_human = "立即生效"
        is_scheduled = False

    # 补全 user_name（UMU API 返回的 user_name 可能为空）
    if not user_name:
        user_name = _get_user_name_by_id(client, umu_id)

    return _ok(
        data={
            "umu_id": umu_id,
            "user_name": user_name,
            "status": "disabled",
            "effective_time": effective_timestamp,
            "effective_time_human": time_human,
            "is_scheduled": is_scheduled,
        },
        next_action="proceed",
        suggested_action="定时禁用将在指定时间自动执行；立即禁用已生效",
    )


@mcp.tool()
async def adm_enable_account(
    umu_id: Annotated[
        str | None,
        Field(
            default=None,
            description="用户 umu_id，与 email 二选一。",
        ),
    ] = None,
    email: Annotated[
        str | None,
        Field(
            default=None,
            description="用户邮箱，与 umu_id 二选一。提供时自动查询对应 umu_id。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """启用账号（支持恢复已禁用账号和取消定时禁用）.

    触发条件：需要恢复被禁用的账号，或取消已设置的定时禁用时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：恢复用户在 UMU 平台的登录权限。

    本工具同时满足两个场景：
    1. **启用已禁用的账号** — 账号被 adm_disable_account 禁用后，调用此工具恢复
    2. **取消定时禁用** — 账号被设置了未来某个时间的定时禁用，调用此工具可提前取消

    查找优先级：
    1. 如提供 umu_id，直接使用
    2. 如未提供 umu_id 但提供 email，自动查询账号列表获取 umu_id
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 参数校验：至少提供一个标识
    if not umu_id and not email:
        return _err(
            error_code="MISSING_IDENTIFIER",
            error_message="必须提供 umu_id 或 email 之一",
            suggested_action="调用 adm_list_accounts 查询账号信息",
        )

    # 通过 email 查找 umu_id
    if not umu_id and email:
        user = _find_user_by_email(client, email)
        if user is None:
            return _err(
                error_code="USER_NOT_FOUND",
                error_message=f"找不到邮箱为 {email} 的用户",
                suggested_action="检查邮箱是否正确，或调用 adm_list_accounts 查询",
            )
        umu_id = str(user.get("umu_id", ""))
        user_name = user.get("user_name", "")
    else:
        user_name = ""

    # 调用启用 API
    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/enterprise/update-account-status"),
            data={
                "umu_ids": umu_id,
                "status": "1",
                "effective_time": "0",
            },
        )
    except Exception as e:
        return _err(
            error_code="ENABLE_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或管理员权限后重试",
        )

    if resp.get("error_code") != 0:
        return _err(
            error_code="ENABLE_FAILED",
            error_message=resp.get("error_message", "启用账号失败"),
            suggested_action="检查用户是否存在或是否已是启用状态",
        )

    # 补全 user_name（UMU API 返回的 user_name 可能为空）
    if not user_name:
        user_name = _get_user_name_by_id(client, umu_id)

    return _ok(
        data={
            "umu_id": umu_id,
            "user_name": user_name,
            "status": "enabled",
            "effective_time": 0,
            "effective_time_human": "立即生效",
        },
        next_action="proceed",
        suggested_action="账号已启用，用户可以正常登录平台",
    )


@mcp.tool()
async def adm_get_scheduled_disables(
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=50, ge=1, le=100, description="每页数量（1-100），默认50"),
    ] = 50,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询所有定时禁用的账号列表.

    触发条件：需要查看哪些账号被设置了定时禁用，以及具体的生效时间。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    返回的 scheduled_at_human 字段为东八区格式化时间，方便阅读。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        # 获取全部账号，筛选出定时禁用的（account_status=3 或 2，因平台而异）
        # 策略：分别尝试两种常见状态码，合并结果
        scheduled_accounts: list[dict] = []
        seen_ids: set[str] = set()

        for status_code in (2, 3):
            resp = client.get(
                client.desktop_url("/ajax/enterprise/getUserList"),
                params={
                    "is_manager": "0",
                    "page": str(page),
                    "size": str(page_size),
                    "group_operator": "intersection",
                    "account_status": str(status_code),
                },
            )

            if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
                continue

            user_list = resp.get("data", {}).get("list", [])
            for user in user_list:
                uid = str(user.get("umu_id", ""))
                if uid in seen_ids:
                    continue
                seen_ids.add(uid)

                # 尝试从 account_joining_time 或其他字段推断生效时间
                # UMU 列表接口不返回 effective_time，需要单独查询或估算
                scheduled_accounts.append(
                    {
                        "umu_id": uid,
                        "user_name": user.get("user_name", ""),
                        "email": user.get("email", ""),
                        "account_status": int(user.get("account_status", 0) or 0),
                        "status_text": _get_status_text(int(user.get("account_status", 0) or 0)),
                        "role_type": int(user.get("role_type", 0) or 0),
                        "role_name": _ROLE_TYPE_MAP.get(int(user.get("role_type", 0) or 0), "未知"),
                        "note": "定时禁用的具体生效时间请查看管理员后台或联系技术支持",
                    }
                )

        return _ok(
            data={
                "scheduled_accounts": scheduled_accounts,
                "total": len(scheduled_accounts),
            },
            next_action="proceed",
            suggested_action="使用 adm_enable_account 取消不需要的定时禁用",
        )
    except Exception as e:
        return _err(
            error_code="LIST_SCHEDULED_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_batch_disable_accounts(
    umu_ids: Annotated[
        str,
        Field(description="要禁用的账号 umu_id 列表，多个用逗号分隔，如 '123,456,789'"),
    ],
    effective_time: Annotated[
        str | None,
        Field(
            default=None,
            description='生效时间。留空或传 "immediate" 表示立即禁用；'
            '传日期时间如 "2026-06-12T09:00" 表示定时禁用（东八区）。'
            "所有账号使用相同的生效时间。",
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """批量禁用账号.

    触发条件：需要同时禁用多个账号时调用（比逐个调用 adm_disable_account 更高效）。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：修改多个用户在 UMU 平台的状态为禁用。

    所有账号共用同一个生效时间：
    - 立即禁用：不传 effective_time
    - 定时禁用：传同一个未来时间
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 解析 umu_id 列表
    id_list = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    if not id_list:
        return _err(
            error_code="EMPTY_ID_LIST",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    # 解析生效时间
    try:
        effective_timestamp = _parse_effective_time(effective_time)
    except ValueError as e:
        return _err(
            error_code="INVALID_TIME_FORMAT",
            error_message=str(e),
            suggested_action='使用格式: "2026-06-12T09:00" 或 "2026-06-12 09:00"',
        )

    # 定时禁用：检查时间是否已过
    if effective_timestamp > 0:
        now_ts = int(datetime.now(timezone(timedelta(hours=8))).timestamp())
        if effective_timestamp <= now_ts:
            return _err(
                error_code="TIME_IN_PAST",
                error_message="指定的禁用时间已过，请提供未来的时间",
                suggested_action='使用未来的时间，如 "2026-06-13T09:00"',
            )

    # 批量调用 API
    results: list[dict] = []
    success_count = 0
    failed_count = 0

    for umu_id in id_list:
        try:
            resp = client.post(
                client.desktop_url("/uapi/v1/enterprise/update-account-status"),
                data={
                    "umu_ids": umu_id,
                    "status": "0",
                    "effective_time": str(effective_timestamp),
                },
            )
            if resp.get("error_code") == 0:
                success_count += 1
                user_name = _get_user_name_by_id(client, umu_id)
                results.append(
                    {
                        "umu_id": umu_id,
                        "user_name": user_name,
                        "success": True,
                    }
                )
            else:
                failed_count += 1
                results.append(
                    {
                        "umu_id": umu_id,
                        "success": False,
                        "error": resp.get("error_message", "禁用失败"),
                    }
                )
        except Exception as e:
            failed_count += 1
            results.append(
                {
                    "umu_id": umu_id,
                    "success": False,
                    "error": str(e),
                }
            )

    # 构建人类可读的时间描述
    if effective_timestamp > 0:
        dt = datetime.fromtimestamp(effective_timestamp, tz=timezone(timedelta(hours=8)))
        time_human = dt.strftime("%Y-%m-%d %H:%M (东八区)")
        is_scheduled = True
    else:
        time_human = "立即生效"
        is_scheduled = False

    return _ok(
        data={
            "results": results,
            "total": len(id_list),
            "success_count": success_count,
            "failed_count": failed_count,
            "effective_time": effective_timestamp,
            "effective_time_human": time_human,
            "is_scheduled": is_scheduled,
        },
        next_action="proceed",
        suggested_action="如有失败，检查对应账号是否已禁用或不存在",
    )


@mcp.tool()
async def adm_batch_enable_accounts(
    umu_ids: Annotated[
        str,
        Field(description="要启用的账号 umu_id 列表，多个用逗号分隔，如 '123,456,789'"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """批量启用账号.

    触发条件：需要同时启用多个账号时调用（比逐个调用 adm_enable_account 更高效）。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：恢复多个用户在 UMU 平台的登录权限。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 解析 umu_id 列表
    id_list = [uid.strip() for uid in umu_ids.split(",") if uid.strip()]
    if not id_list:
        return _err(
            error_code="EMPTY_ID_LIST",
            error_message="umu_ids 不能为空",
            suggested_action="提供至少一个 umu_id",
        )

    # 批量调用 API
    results: list[dict] = []
    success_count = 0
    failed_count = 0

    for umu_id in id_list:
        try:
            resp = client.post(
                client.desktop_url("/uapi/v1/enterprise/update-account-status"),
                data={
                    "umu_ids": umu_id,
                    "status": "1",
                    "effective_time": "0",
                },
            )
            if resp.get("error_code") == 0:
                success_count += 1
                user_name = _get_user_name_by_id(client, umu_id)
                results.append(
                    {
                        "umu_id": umu_id,
                        "user_name": user_name,
                        "success": True,
                    }
                )
            else:
                failed_count += 1
                results.append(
                    {
                        "umu_id": umu_id,
                        "success": False,
                        "error": resp.get("error_message", "启用失败"),
                    }
                )
        except Exception as e:
            failed_count += 1
            results.append(
                {
                    "umu_id": umu_id,
                    "success": False,
                    "error": str(e),
                }
            )

    return _ok(
        data={
            "results": results,
            "total": len(id_list),
            "success_count": success_count,
            "failed_count": failed_count,
        },
        next_action="proceed",
        suggested_action="账号已启用，用户可以正常登录平台",
    )


async def _resolve_course_owner_keywords(
    client: UMUClient,
    keywords: str,
) -> list[str] | None:
    """通过创建人关键词搜索获取匹配的 uids.

    Args:
        client: UMUClient 实例
        keywords: 创建人姓名/邮箱/手机号/用户名关键词

    Returns:
        匹配的 uids 列表，无匹配返回 None。

    Raises:
        RuntimeError: user-list 接口返回错误。
    """
    # 与学员关键词解析使用同一底层接口 /uapi/v1/enterprise/user-list
    return await _resolve_student_keywords(client, keywords)


@mcp.tool()
async def adm_list_courses(
    keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="课程搜索关键词（模糊匹配课程名称、标签、访问码），"
            "如 '数据分析'、'明星课程'、'btq943'。",
        ),
    ] = None,
    owner_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="课程创建人/拥有者搜索关键词（姓名、邮箱、手机号、用户名）。"
            "提供时工具内部会调用 user-list 接口解析为 uids。",
        ),
    ] = None,
    owner_uids: Annotated[
        str | None,
        Field(
            default=None,
            description="课程创建人/拥有者 UMU ID 列表，多个用逗号分隔。与 owner_keywords 二选一。",
        ),
    ] = None,
    access_permission: Annotated[
        int | None,
        Field(
            default=None,
            description="课程权限筛选：0=关闭，1=公开，2=企业内公开，3=指定账户。不提供则不筛选。",
        ),
    ] = None,
    source: Annotated[
        str | None,
        Field(
            default=None,
            description="课程来源筛选：'inner'=内部课程，'outer'=外部课程。"
            "通常与 access_permission 配合使用。",
        ),
    ] = None,
    is_course_in_lib: Annotated[
        int | None,
        Field(
            default=None,
            description="是否在企业知识库：0=未加入，1=已加入。不提供则不筛选。",
        ),
    ] = None,
    audit_status: Annotated[
        int | None,
        Field(
            default=None,
            description="审核状态筛选：-1=未提交，0=待审核，1=已通过，2=已拒绝，3=已撤销。"
            "不提供则不筛选。",
        ),
    ] = None,
    start_day: Annotated[
        str | None,
        Field(
            default=None,
            description="创建时间起始日期，格式 YYYY-MM-DD。与 end_day 配合使用。",
        ),
    ] = None,
    end_day: Annotated[
        str | None,
        Field(
            default=None,
            description="创建时间结束日期，格式 YYYY-MM-DD。与 start_day 配合使用。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业课程清单（支持多条件组合搜索）.

    触发条件：需要查看企业课程列表、按条件筛选课程、查找特定课程时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持的筛选条件：
    - 课程关键词：keywords（模糊匹配课程名称、标签、访问码）
    - 创建人：owner_keywords（自动解析）或 owner_uids（直接传入 UID）
    - 课程权限：access_permission（0=关闭，1=公开，2=企业内公开，3=指定账户）
    - 课程来源：source（inner/outer）
    - 企业知识库：is_course_in_lib（0/1）
    - 审核状态：audit_status（-1=未提交，0=待审核，1=已通过，2=已拒绝，3=已撤销）
    - 创建时间范围：start_day / end_day（YYYY-MM-DD）

    返回字段说明：
    - group_id: 课程分组 ID
    - title: 课程标题
    - access_code: 课程访问码
    - share_url: 课程分享链接
    - creator_umu_id: 创建者 UMU 用户 ID
    - creator_username: 创建者用户名
    - start_time / end_time / create_time / update_time: Unix 时间戳（秒）
    - *_readable: 对应时间戳的北京时间字符串
    - session_count: 小节数量
    - participant_num: 参与人数
    - finish_num: 完成人数
    - audit_status / audit_status_text: 审核状态码及人读文本
    - access_permission / access_permission_text: 课程权限码及人读文本
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 如果提供了创建人关键词，先解析为 uids
    resolved_owner_uids: list[str] | None = None
    if owner_keywords:
        try:
            resolved_owner_uids = await _resolve_course_owner_keywords(client, owner_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_OWNER_ERROR",
                error_message=str(e),
                suggested_action="检查创建人关键词或网络连接后重试",
            )
        if resolved_owner_uids is None:
            return _err(
                error_code="OWNER_NOT_FOUND",
                error_message=f"找不到匹配的创建人: {owner_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询账号信息",
            )

    # 合并直接传入的 owner_uids 和从关键词解析的 uids
    final_owner_uids: list[str] | None = None
    if owner_uids:
        final_owner_uids = [uid.strip() for uid in owner_uids.split(",") if uid.strip()]
    if resolved_owner_uids:
        if final_owner_uids:
            final_owner_uids = list(set(final_owner_uids + resolved_owner_uids))
        else:
            final_owner_uids = resolved_owner_uids

    def _parse_date_to_ms(date_str: str) -> int:
        """将 YYYY-MM-DD 解析为东八区毫秒时间戳."""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return int(dt.timestamp() * 1000)

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], int]:
        """获取单页数据，返回(课程列表, 总数量)."""
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
        }
        if keywords:
            params["group_title"] = keywords
        if final_owner_uids:
            params["uids"] = ",".join(final_owner_uids)
        if access_permission is not None:
            params["access_permission"] = str(access_permission)
        if source:
            params["source"] = source
        if is_course_in_lib is not None:
            params["is_course_in_lib"] = str(is_course_in_lib)
        if audit_status is not None:
            params["audit_status"] = str(audit_status)
        if start_day:
            params["start_day"] = start_day
            params["startDay"] = str(_parse_date_to_ms(start_day))
        if end_day:
            params["end_day"] = end_day
            params["endDay"] = str(_parse_date_to_ms(end_day))

        resp = client.get(
            client.desktop_url("/ajax/enterprise/getReportGroupList"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程列表失败"))

        course_list = resp.get("data", {}).get("list", [])
        courses = []
        for item in course_list:
            try:
                raw = AdminCourseRaw(**item)
                courses.append(AdminCourse.from_raw(raw).model_dump())
            except Exception:
                # 如果个别课程字段异常，回退到原始字典构造逻辑，保证列表不中断
                courses.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return courses, total_all

    try:
        if fetch_all:
            # 自动获取全量数据
            all_courses: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                courses, total_all = _fetch_page(current_page, batch_size)
                all_courses.extend(courses)

                report_pagination_progress(
                    "adm_list_courses",
                    current_page,
                    len(all_courses),
                    total_all,
                    20,
                    is_complete=len(all_courses) >= total_all or not courses,
                )

                if len(all_courses) >= total_all or not courses:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_courses",
                        current_page,
                        len(all_courses),
                        total_all,
                        20,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "courses": all_courses,
                    "total": len(all_courses),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_courses) if all_courses else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 或 access_code 进一步查询课程详情",
            )
        else:
            # 单页模式
            courses, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "courses": courses,
                    "total": len(courses),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 或 access_code 进一步查询课程详情",
            )
    except Exception as e:
        return _err(
            error_code="LIST_COURSES_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_list_course_audit_records(
    audit_status: Annotated[
        int,
        Field(description="审核状态：0=待审核，1=已通过，2=已拒绝"),
    ],
    course_keywords: Annotated[
        str | None,
        Field(default=None, description="课程名称关键词，模糊匹配课程标题"),
    ] = None,
    access_code: Annotated[
        str | None,
        Field(default=None, description="课程访问码，如 'btq943'"),
    ] = None,
    owner_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="课程拥有者关键词（姓名、邮箱、手机号、用户名），内部自动解析为 uids",
        ),
    ] = None,
    owner_uids: Annotated[
        str | None,
        Field(default=None, description="课程拥有者 umu_id 列表，多个用逗号分隔。与 owner_keywords 二选一"),
    ] = None,
    category_id: Annotated[
        str | None,
        Field(default=None, description="课程分类 ID（可选，后端决定是否生效）"),
    ] = None,
    filter_last_passed: Annotated[
        bool,
        Field(default=False, description="是否过滤掉上次审核状态为通过的课程"),
    ] = False,
    sort_field: Annotated[
        str,
        Field(default="submit_time", description="排序字段，如 submit_time"),
    ] = "submit_time",
    sort_order: Annotated[
        str,
        Field(default="desc", description="排序方向：asc / desc"),
    ] = "desc",
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """查询企业知识库课程审核记录.

    触发条件：需要查看待审核/已通过/已拒绝课程列表，或按条件筛选审核课程时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持的筛选条件：
    - 审核状态：audit_status（0=待审核，1=已通过，2=已拒绝）
    - 课程关键词：course_keywords（模糊匹配课程标题）
    - 访问码：access_code（可与课程关键词组合）
    - 拥有者：owner_keywords（自动解析）或 owner_uids（直接传入 UID）
    - 课程分类：category_id（可选）
    - 过滤上次通过：filter_last_passed
    - 提交时间排序：sort_field + sort_order
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    if audit_status not in (0, 1, 2):
        return _err(
            error_code="INVALID_AUDIT_STATUS",
            error_message="audit_status 必须是 0（待审核）、1（已通过）或 2（已拒绝）",
            suggested_action="检查 audit_status 参数",
        )

    # 解析拥有者关键词
    resolved_owner_uids: list[str] | None = None
    if owner_keywords:
        try:
            resolved_owner_uids = await _resolve_course_owner_keywords(client, owner_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_OWNER_ERROR",
                error_message=str(e),
                suggested_action="检查拥有者关键词或网络连接后重试",
            )
        if resolved_owner_uids is None:
            return _err(
                error_code="OWNER_NOT_FOUND",
                error_message=f"找不到匹配的拥有者: {owner_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询账号信息",
            )

    final_owner_uids: list[str] | None = None
    if owner_uids:
        final_owner_uids = [uid.strip() for uid in owner_uids.split(",") if uid.strip()]
    if resolved_owner_uids:
        if final_owner_uids:
            final_owner_uids = list(set(final_owner_uids + resolved_owner_uids))
        else:
            final_owner_uids = resolved_owner_uids

    # 构造 search_keyword：优先使用课程名称关键词
    search_keyword = course_keywords or ""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        """获取单页数据，返回(审核记录列表, 总数量)."""
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
            "audit_status": str(audit_status),
            "search_keyword": search_keyword,
            "uids": ",".join(final_owner_uids) if final_owner_uids else "",
            "filter_last_passed": "1" if filter_last_passed else "0",
            "sort_field": sort_field,
            "sort_order": sort_order,
        }
        if category_id is not None:
            params["category_id"] = category_id

        resp = client.get(
            client.desktop_url("/api/enterprise/getcourseauditlist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程审核列表失败"))

        record_list = resp.get("data", {}).get("list", [])
        records: list[dict[str, Any]] = []
        for item in record_list:
            try:
                raw = AdminCourseAuditRecordRaw(**item)
                records.append(AdminCourseAuditRecord.from_raw(raw).model_dump())
            except Exception:
                # 如果个别记录字段异常，回退到原始字典，保证列表不中断
                records.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return records, total_all

    try:
        if fetch_all:
            all_records: list[dict[str, Any]] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                records, total_all = _fetch_page(current_page, batch_size)
                all_records.extend(records)

                report_pagination_progress(
                    "adm_list_course_audit_records",
                    current_page,
                    len(all_records),
                    total_all,
                    20,
                    is_complete=len(all_records) >= total_all or not records,
                )

                if len(all_records) >= total_all or not records:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_course_audit_records",
                        current_page,
                        len(all_records),
                        total_all,
                        20,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            # 本地按 access_code 过滤
            if access_code:
                code = access_code.strip().lower()
                all_records = [
                    r for r in all_records
                    if code in (r.get("access_code", "") or "").lower()
                ]

            return _ok(
                data={
                    "records": all_records,
                    "total": len(all_records),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_records) if all_records else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 调用 adm_audit_course 执行审核操作",
            )
        else:
            records, total_all = _fetch_page(page, page_size)

            if access_code:
                code = access_code.strip().lower()
                records = [
                    r for r in records
                    if code in (r.get("access_code", "") or "").lower()
                ]

            return _ok(
                data={
                    "records": records,
                    "total": len(records),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 group_id 调用 adm_audit_course 执行审核操作",
            )
    except Exception as e:
        return _err(
            error_code="LIST_COURSE_AUDIT_RECORDS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或参数后重试",
        )


@mcp.tool()
async def adm_audit_course(
    group_ids: Annotated[
        str,
        Field(description="课程 ID 列表，多个用逗号分隔，如 '7330085,7330086'"),
    ],
    action: Annotated[
        str,
        Field(description="审核动作：approve/通过/1、reject/拒绝/2、revoke/撤销提交/3"),
    ],
    reason: Annotated[
        str | None,
        Field(default=None, description="拒绝或撤销提交的原因（可选）"),
    ] = None,
    add_to_blacklist: Annotated[
        bool,
        Field(default=False, description="拒绝时是否将课程拥有者加入黑名单（仅 action=reject 时生效）"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """对企业知识库课程执行审核操作.

    触发条件：用户需要对课程进行通过、拒绝或撤销提交时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：会改变课程审核状态；拒绝时若 add_to_blacklist=True 会将拥有者加入黑名单。

    说明：
    - 通过审核后，课程进入企业知识库，可被管理员转发/推荐，企业内其他学员可搜索学习。
    - 拒绝审核后，课程被设置为拒绝状态。
    - 撤销提交后，课程回到未提交状态，仍可编辑和分享，但管理员不会推荐，其他学员搜索不到。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    action_lower = action.strip().lower()
    action_map = {
        "approve": 1, "通过": 1, "1": 1,
        "reject": 2, "拒绝": 2, "2": 2,
        "revoke": 3, "撤销提交": 3, "撤销": 3, "3": 3,
    }
    if action_lower not in action_map:
        return _err(
            error_code="INVALID_AUDIT_ACTION",
            error_message=f"不支持的审核动作: {action}",
            suggested_action="请使用 approve/通过、reject/拒绝、revoke/撤销提交",
        )
    audit_status_code = action_map[action_lower]

    if not group_ids.strip():
        return _err(
            error_code="EMPTY_GROUP_IDS",
            error_message="group_ids 不能为空",
            suggested_action="提供需要审核的课程 ID",
        )

    if audit_status_code != 2 and add_to_blacklist:
        return _err(
            error_code="BLACKLIST_ONLY_ON_REJECT",
            error_message="add_to_blacklist 仅在拒绝操作时有效",
            suggested_action="将 action 设置为 reject/拒绝，或关闭 add_to_blacklist",
        )

    payload: dict[str, str] = {
        "group_ids": group_ids.strip(),
        "audit_status": str(audit_status_code),
    }
    if reason:
        payload["desc"] = reason
    if audit_status_code == 2:
        payload["is_add_black"] = "1" if add_to_blacklist else "0"

    try:
        resp = client.post(
            client.desktop_url("/api/group/auditCourse"),
            data=payload,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "课程审核操作失败"))

        action_text = {1: "通过", 2: "拒绝", 3: "撤销提交"}[audit_status_code]
        return _ok(
            data={
                "group_ids": group_ids.strip(),
                "action": action_text,
                "audit_status": audit_status_code,
                "add_to_blacklist": add_to_blacklist if audit_status_code == 2 else False,
            },
            next_action="proceed",
            suggested_action="可调用 adm_list_course_audit_records 查看最新状态",
        )
    except Exception as e:
        return _err(
            error_code="AUDIT_COURSE_ERROR",
            error_message=str(e),
            suggested_action="检查课程 ID、网络连接或权限后重试",
        )


@mcp.tool()
async def adm_list_course_categories(
    is_with_course_num: Annotated[
        bool,
        Field(default=False, description="是否返回每个分类下的课程数量"),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """查询企业课程分类列表.

    触发条件：需要查看课程分类、为审核列表按分类筛选做准备时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/get-category-list"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "is_with_course_num": "1" if is_with_course_num else "0",
            },
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取课程分类失败"))

        category_list = resp.get("data", {}).get("list", [])
        categories: list[dict[str, Any]] = []
        for item in category_list:
            try:
                raw = AdminCourseCategoryRaw(**item)
                categories.append(AdminCourseCategory.from_raw(raw).model_dump())
            except Exception:
                categories.append(item)

        return _ok(
            data={
                "categories": categories,
                "total": len(categories),
            },
            next_action="proceed",
            suggested_action="使用 category_id 调用 adm_list_course_audit_records 按分类筛选",
        )
    except Exception as e:
        return _err(
            error_code="LIST_COURSE_CATEGORIES_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_list_course_blacklist(
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=15, ge=1, le=100, description="每页数量（1-100），默认15"),
    ] = 15,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """查询课程提交黑名单.

    触发条件：需要查看哪些课程拥有者被加入黑名单时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        """获取单页数据，返回(黑名单列表, 总数量)."""
        resp = client.get(
            client.desktop_url("/uapi/v1/course/course-blacklist"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "page": str(p),
            },
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取黑名单失败"))

        blacklist_list = resp.get("data", {}).get("list", [])
        entries: list[dict[str, Any]] = []
        for item in blacklist_list:
            try:
                raw = AdminCourseBlacklistEntryRaw(**item)
                entries.append(AdminCourseBlacklistEntry.from_raw(raw).model_dump())
            except Exception:
                entries.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return entries, total_all

    try:
        if fetch_all:
            all_entries: list[dict[str, Any]] = []
            current_page = 1
            batch_size = 15
            total_all = 0

            while True:
                entries, total_all = _fetch_page(current_page, batch_size)
                all_entries.extend(entries)

                report_pagination_progress(
                    "adm_list_course_blacklist",
                    current_page,
                    len(all_entries),
                    total_all,
                    15,
                    is_complete=len(all_entries) >= total_all or not entries,
                )

                if len(all_entries) >= total_all or not entries:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_course_blacklist",
                        current_page,
                        len(all_entries),
                        total_all,
                        15,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "blacklist": all_entries,
                    "total": len(all_entries),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_entries) if all_entries else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_save_course_blacklist 移除黑名单",
            )
        else:
            entries, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "blacklist": entries,
                    "total": len(entries),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 umu_id 调用 adm_save_course_blacklist 移除黑名单",
            )
    except Exception as e:
        return _err(
            error_code="LIST_COURSE_BLACKLIST_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


@mcp.tool()
async def adm_save_course_blacklist(
    umu_id: Annotated[
        str,
        Field(description="用户 umu_id"),
    ],
    action: Annotated[
        str,
        Field(description="操作：add/加入/1、remove/移除/2"),
    ],
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """将用户加入或移出课程提交黑名单.

    触发条件：需要手动将课程提交人加入黑名单，或从黑名单移除时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：会改变用户在黑名单中的状态。被加入黑名单的账户，其提交的所有课程必须进入审核流程。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    action_lower = action.strip().lower()
    action_map = {
        "add": 1, "加入": 1, "1": 1,
        "remove": 2, "移除": 2, "2": 2,
    }
    if action_lower not in action_map:
        return _err(
            error_code="INVALID_BLACKLIST_ACTION",
            error_message=f"不支持的黑名单操作: {action}",
            suggested_action="请使用 add/加入 或 remove/移除",
        )
    type_code = action_map[action_lower]

    if not umu_id.strip():
        return _err(
            error_code="EMPTY_UMU_ID",
            error_message="umu_id 不能为空",
            suggested_action="提供有效的用户 umu_id",
        )

    try:
        resp = client.post(
            client.desktop_url("/uapi/v1/course/save-course-blacklist"),
            data={
                "umu_id": umu_id.strip(),
                "type": str(type_code),
            },
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "黑名单操作失败"))

        action_text = {1: "加入", 2: "移除"}[type_code]
        return _ok(
            data={
                "umu_id": umu_id.strip(),
                "action": action_text,
                "type": type_code,
            },
            next_action="proceed",
            suggested_action="可调用 adm_list_course_blacklist 查看最新黑名单",
        )
    except Exception as e:
        return _err(
            error_code="SAVE_COURSE_BLACKLIST_ERROR",
            error_message=str(e),
            suggested_action="检查 umu_id、网络连接或权限后重试",
        )


@mcp.tool()
async def adm_list_learning_programs(
    keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="学习项目搜索关键词（模糊匹配项目名称、标签、访问码），"
            "如 '数据分析'、'新员工培训'、'crj556'。",
        ),
    ] = None,
    owner_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="项目创建人/拥有者搜索关键词（姓名、邮箱、手机号、用户名）。"
            "提供时工具内部会调用 user-list 接口解析为 uids。",
        ),
    ] = None,
    owner_uids: Annotated[
        str | None,
        Field(
            default=None,
            description="项目创建人/拥有者 UMU ID 列表，多个用逗号分隔。与 owner_keywords 二选一。",
        ),
    ] = None,
    access_permission: Annotated[
        int | None,
        Field(
            default=None,
            description="项目权限筛选：0=关闭，1=公开，2=企业内公开，3=指定账户。不提供则不筛选。",
        ),
    ] = None,
    is_in_program_lib: Annotated[
        int | None,
        Field(
            default=None,
            description="是否在企业知识库：0=未加入，1=已加入。不提供则不筛选。",
        ),
    ] = None,
    category_id: Annotated[
        str | None,
        Field(
            default=None,
            description="课程分类 ID，从 adm_list_course_categories 获取。不提供则不筛选。",
        ),
    ] = None,
    start_day: Annotated[
        str | None,
        Field(
            default=None,
            description="创建时间起始日期，格式 YYYY-MM-DD。与 end_day 配合使用。",
        ),
    ] = None,
    end_day: Annotated[
        str | None,
        Field(
            default=None,
            description="创建时间结束日期，格式 YYYY-MM-DD。与 start_day 配合使用。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，"
            "自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业学习项目清单（支持多条件组合搜索）.

    触发条件：需要查看企业学习项目列表、按条件筛选学习项目、查找特定学习项目时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持的筛选条件：
    - 项目关键词：keywords（模糊匹配项目名称、标签、访问码）
    - 创建人：owner_keywords（自动解析）或 owner_uids（直接传入 UID）
    - 项目权限：access_permission（0=关闭，1=公开，2=企业内公开，3=指定账户）
    - 企业知识库：is_in_program_lib（0/1）
    - 课程分类：category_id
    - 创建时间范围：start_day / end_day（YYYY-MM-DD）

    返回字段说明：
    - program_id: 学习项目 ID
    - title: 学习项目标题
    - access_code: 访问码
    - share_url: 分享链接
    - creator_umu_id: 创建者 UMU 用户 ID
    - creator_username: 创建者用户名
    - create_time / create_time_readable: Unix 时间戳及北京时间字符串
    - group_num: 课程/分组数量
    - participate_num: 参与人数
    - assignment_count: 作业/任务数量
    - module_num: 模块数量
    - is_in_program_lib: 是否在企业知识库
    - access_permission / access_permission_text: 权限码及人读文本
    - category_name: 分类路径列表
    - enterprise_groups / enterprise_departments: 可见范围分组/部门
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # 如果提供了创建人关键词，先解析为 uids
    resolved_owner_uids: list[str] | None = None
    if owner_keywords:
        try:
            resolved_owner_uids = await _resolve_course_owner_keywords(client, owner_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_OWNER_ERROR",
                error_message=str(e),
                suggested_action="检查创建人关键词或网络连接后重试",
            )
        if resolved_owner_uids is None:
            return _err(
                error_code="OWNER_NOT_FOUND",
                error_message=f"找不到匹配的创建人: {owner_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_accounts 查询账号信息",
            )

    # 合并直接传入的 owner_uids 和从关键词解析的 uids
    final_owner_uids: list[str] | None = None
    if owner_uids:
        final_owner_uids = [uid.strip() for uid in owner_uids.split(",") if uid.strip()]
    if resolved_owner_uids:
        if final_owner_uids:
            final_owner_uids = list(set(final_owner_uids + resolved_owner_uids))
        else:
            final_owner_uids = resolved_owner_uids

    def _parse_date_to_ms(date_str: str) -> int:
        """将 YYYY-MM-DD 解析为东八区毫秒时间戳."""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return int(dt.timestamp() * 1000)

    def _fetch_page(p: int, sz: int) -> tuple[list[dict], int]:
        """获取单页数据，返回(项目列表, 总数量)."""
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
        }
        if keywords:
            params["program_title"] = keywords
        if final_owner_uids:
            params["uids"] = ",".join(final_owner_uids)
        if access_permission is not None:
            params["access_permission"] = str(access_permission)
        if is_in_program_lib is not None:
            params["is_in_program_lib"] = str(is_in_program_lib)
        if category_id:
            params["category_id"] = category_id
        if start_day:
            params["start_day"] = start_day
            params["startDay"] = str(_parse_date_to_ms(start_day))
        if end_day:
            params["end_day"] = end_day
            params["endDay"] = str(_parse_date_to_ms(end_day))

        resp = client.get(
            client.desktop_url("/ajax/enterprise/getReportProgramList"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习项目列表失败"))

        program_list = resp.get("data", {}).get("list", [])
        programs = []
        for item in program_list:
            try:
                raw = AdminLearningProgramRaw(**item)
                programs.append(AdminLearningProgram.from_raw(raw).model_dump())
            except Exception:
                # 如果个别项目字段异常，回退到原始字典，保证列表不中断
                programs.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return programs, total_all

    try:
        if fetch_all:
            # 自动获取全量数据
            all_programs: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                programs, total_all = _fetch_page(current_page, batch_size)
                all_programs.extend(programs)

                report_pagination_progress(
                    "adm_list_learning_programs",
                    current_page,
                    len(all_programs),
                    total_all,
                    page_size,
                    is_complete=len(all_programs) >= total_all or not programs,
                )

                if len(all_programs) >= total_all or not programs:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_learning_programs",
                        current_page,
                        len(all_programs),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "programs": all_programs,
                    "total": len(all_programs),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_programs) if all_programs else 0,
                    },
                },
                next_action="proceed",
                suggested_action="使用 program_id 或 access_code 进一步查询项目详情",
            )
        else:
            # 单页模式
            programs, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "programs": programs,
                    "total": len(programs),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="使用 program_id 或 access_code 进一步查询项目详情",
            )
    except Exception as e:
        return _err(
            error_code="LIST_LEARNING_PROGRAMS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


# ---------------------------------------------------------------------------
# Tools: 个人视角学习项目列表查询
# ---------------------------------------------------------------------------

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
        raise ValueError(f"不支持的 scope: {scope}")

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


@mcp.tool()
async def adm_list_personal_learning_programs(
    scope: Annotated[
        str,
        Field(description="列表视角：owned=我拥有的, cooperated=协同给我的, enrolled=我报名的"),
    ],
    keywords: Annotated[str | None, Field(default=None, description="按标题/访问码模糊搜索")] = None,
    page: Annotated[int, Field(default=1, ge=1, description="页码")] = 1,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="每页数量")] = 20,
    fetch_all: Annotated[bool, Field(default=False, description="是否自动获取全量数据")] = False,
    session_id: Annotated[str | None, Field(default=None, description="可选会话 ID")] = None,
) -> str:
    """查询当前管理员作为普通用户的学习项目清单."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    try:
        url, base_params = _program_list_url_and_params(scope, keywords or "", page, page_size)
    except ValueError as e:
        return _err(error_code="INVALID_SCOPE", error_message=str(e), next_action="needs_user_input")

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

    try:
        if fetch_all:
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            batch_size = 20

            while True:
                items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(items)

                report_pagination_progress(
                    "adm_list_personal_learning_programs",
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
                        "adm_list_personal_learning_programs",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "scope": scope,
                    "programs": all_items,
                    "total": len(all_items),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_items) if all_items else 0,
                    },
                },
                next_action="proceed",
            )

        items, _ = _fetch_page(page, page_size)
        return _ok(
            data={
                "scope": scope,
                "programs": items,
                "pagination": {
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
        )
    except Exception as e:
        logger.exception("获取学习项目列表失败")
        return _err("LIST_LEARNING_PROGRAMS_FAILED", str(e))


async def _resolve_instructor_tag_names(
    client: UMUClient,
    names: str,
) -> list[str] | None:
    """通过讲师标签名称关键词搜索获取匹配的标签 IDs.

    Args:
        client: UMUClient 实例
        names: 标签名称关键词，多个用逗号分隔

    Returns:
        匹配的标签 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: 标签接口返回错误。
    """
    keywords_list = [n.strip() for n in names.split(",") if n.strip()]
    if not keywords_list:
        return None

    resp = client.get(
        client.desktop_url("/uapi/v1/teacher-manage/tag-list"),
        params={
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": "1",
            "size": "10000",
        },
    )

    if resp.get("error_code") != 0:
        msg = resp.get("error_message", "")
        raise RuntimeError(f"查询讲师标签列表失败: {msg}" if msg else "查询讲师标签列表失败")

    tag_list = resp.get("data", {}).get("list", [])
    if not tag_list:
        return None

    matched_ids: list[str] = []
    seen: set[str] = set()
    for tag in tag_list:
        tag_name = (tag.get("tag_name", "") or "").lower()
        for kw in keywords_list:
            if kw.lower() in tag_name:
                tid = str(tag.get("tag_id", ""))
                if tid and tid not in seen:
                    seen.add(tid)
                    matched_ids.append(tid)
                break

    return matched_ids if matched_ids else None


async def _resolve_instructor_group_names(
    client: UMUClient,
    names: str,
) -> list[str] | None:
    """通过企业分组名称关键词搜索获取匹配的分组 IDs（用于讲师筛选）.

    Args:
        client: UMUClient 实例
        names: 分组名称关键词，多个用逗号分隔

    Returns:
        匹配的分组 ID 列表，无匹配返回 None。

    Raises:
        RuntimeError: 分组接口返回错误。
    """
    keywords_list = [n.strip() for n in names.split(",") if n.strip()]
    if not keywords_list:
        return None

    matched_ids: list[str] = []
    seen: set[str] = set()
    page = 1
    max_pages = 50
    page_size = 50
    records_fetched = 0

    while page <= max_pages:
        resp = client.get(
            client.desktop_url("/uapi/v1/enterprise/enterprise-group-list"),
            params={
                "t": str(int(datetime.now().timestamp() * 1000)),
                "keywords": "",
                "page": str(page),
                "size": str(page_size),
            },
        )

        if resp.get("error_code") != 0:
            msg = resp.get("error_message", "")
            raise RuntimeError(f"查询分组列表失败: {msg}" if msg else "查询分组列表失败")

        group_list = resp.get("data", {}).get("list", [])
        if not group_list:
            break

        for group in group_list:
            group_name = (group.get("group_name", "") or "").lower()
            for kw in keywords_list:
                if kw.lower() in group_name:
                    gid = str(group.get("id", ""))
                    if gid and gid not in seen:
                        seen.add(gid)
                        matched_ids.append(gid)
                    break

        records_fetched += len(group_list)
        total = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        if total > 0 and records_fetched >= total:
            break

        page += 1

    return matched_ids if matched_ids else None


def _build_instructor_search_condition(
    certification_status: int | None = None,
    tag_ids: list[str] | None = None,
    department_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    account_keyword: str | None = None,
) -> dict[str, Any]:
    """构建讲师列表查询的 search_condition JSON 对象.

    对应 GET /uapi/v1/dashboard/teacher-manage-list 的 search_condition 参数。

    Args:
        certification_status: 认证状态：1=已认证, 0=未认证
        tag_ids: 讲师标签 ID 列表
        department_ids: 部门 ID 列表
        group_ids: 企业分组 ID 列表
        account_keyword: 账号关键词（模糊搜索姓名、邮箱、手机号、用户名）

    Returns:
        search_condition 字典，将被 JSON 序列化后作为查询参数。
    """
    condition: dict[str, Any] = {}

    if certification_status is not None:
        condition["certification_status"] = certification_status
    if tag_ids:
        condition["tag_ids"] = [int(x) for x in tag_ids]
    if department_ids:
        condition["department_ids"] = department_ids
    if group_ids:
        condition["enterprise_group_ids"] = [int(x) for x in group_ids]
    if account_keyword:
        condition["account_keyword"] = account_keyword

    return condition


@mcp.tool()
async def adm_list_instructors(
    certification_status: Annotated[
        str | None,
        Field(
            default=None,
            description='认证状态筛选："all"/"certified"/"uncertified"，分别对应全部、已认证、未认证。不提供则默认全部。',
        ),
    ] = None,
    tag_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="讲师标签 ID 列表，多个用逗号分隔，如 '354,355'。不提供则不按标签筛选。",
        ),
    ] = None,
    tag_names: Annotated[
        str | None,
        Field(
            default=None,
            description="讲师标签名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="部门 ID 列表，多个用逗号分隔，如 '82064,82065'。不提供则不按部门筛选。",
        ),
    ] = None,
    department_names: Annotated[
        str | None,
        Field(
            default=None,
            description="部门名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="分组 ID 列表，多个用逗号分隔，如 '136804,127992'。不提供则不按分组筛选。",
        ),
    ] = None,
    group_names: Annotated[
        str | None,
        Field(
            default=None,
            description="分组名称关键词，多个用逗号分隔。提供时工具内部会自动解析为 ID 进行精确筛选。",
        ),
    ] = None,
    account_keyword: Annotated[
        str | None,
        Field(
            default=None,
            description="账号关键词，模糊搜索讲师姓名、邮箱、手机号、用户名。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询企业讲师列表（支持多条件组合筛选）.

    触发条件：需要查看企业讲师、按认证状态/标签/部门/分组/关键词筛选讲师时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持按以下条件的交集筛选：
    - 认证状态：certified=已认证, uncertified=未认证, all=全部
    - 讲师标签：tag_ids（直接传 ID）或 tag_names（按名称自动解析）
    - 部门：department_ids（直接传 ID）或 department_names（按名称自动解析）
    - 分组：group_ids（直接传 ID）或 group_names（按名称自动解析）
    - 账号关键词：account_keyword（模糊匹配姓名、邮箱、手机号、用户名）

    多个同类型条件（如多个标签、多个部门）使用逗号分隔，结果取并集后再与其他类型取交集。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # -----------------------------------------------------------------------
    # 解析名称关键词为 IDs
    # -----------------------------------------------------------------------
    resolved_tag_ids: list[str] | None = None
    if tag_names:
        try:
            resolved_tag_ids = await _resolve_instructor_tag_names(client, tag_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_TAG_ERROR",
                error_message=str(e),
                suggested_action="检查标签名称或网络连接后重试",
            )
        if resolved_tag_ids is None:
            return _err(
                error_code="TAG_NOT_FOUND",
                error_message=f"找不到匹配的标签: {tag_names}",
                suggested_action="检查标签名称拼写",
            )

    resolved_department_ids: list[str] | None = None
    if department_names:
        try:
            resolved_department_ids = await _resolve_department_names(client, department_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_DEPARTMENT_ERROR",
                error_message=str(e),
                suggested_action="检查部门名称或网络连接后重试",
            )
        if resolved_department_ids is None:
            return _err(
                error_code="DEPARTMENT_NOT_FOUND",
                error_message=f"找不到匹配的部门: {department_names}",
                suggested_action="检查部门名称拼写，或调用 adm_get_department_tree 查询部门信息",
            )

    resolved_group_ids: list[str] | None = None
    if group_names:
        try:
            resolved_group_ids = await _resolve_instructor_group_names(client, group_names)
        except Exception as e:
            return _err(
                error_code="RESOLVE_GROUP_ERROR",
                error_message=str(e),
                suggested_action="检查分组名称或网络连接后重试",
            )
        if resolved_group_ids is None:
            return _err(
                error_code="GROUP_NOT_FOUND",
                error_message=f"找不到匹配的分组: {group_names}",
                suggested_action="检查分组名称拼写",
            )

    # -----------------------------------------------------------------------
    # 合并显式 ID 与解析得到的 ID，去重
    # -----------------------------------------------------------------------
    def _merge_id_lists(explicit: str | None, resolved: list[str] | None) -> list[str] | None:
        result: list[str] = []
        if explicit:
            result.extend([x.strip() for x in explicit.split(",") if x.strip()])
        if resolved:
            result.extend(resolved)
        return list(dict.fromkeys(result)) if result else None

    final_tag_ids = _merge_id_lists(tag_ids, resolved_tag_ids)
    final_department_ids = _merge_id_lists(department_ids, resolved_department_ids)
    final_group_ids = _merge_id_lists(group_ids, resolved_group_ids)

    # -----------------------------------------------------------------------
    # 认证状态映射
    # -----------------------------------------------------------------------
    certification_value: int | None = None
    if certification_status:
        status_key = certification_status.strip().lower()
        if status_key == "certified":
            certification_value = 1
        elif status_key == "uncertified":
            certification_value = 0
        elif status_key not in ("all", ""):
            return _err(
                error_code="INVALID_CERTIFICATION_STATUS",
                error_message=f"不支持的认证状态: {certification_status}",
                suggested_action='请使用 "all"、"certified" 或 "uncertified"',
            )

    # -----------------------------------------------------------------------
    # 构建 search_condition
    # -----------------------------------------------------------------------
    search_condition = _build_instructor_search_condition(
        certification_status=certification_value,
        tag_ids=final_tag_ids,
        department_ids=final_department_ids,
        group_ids=final_group_ids,
        account_keyword=account_keyword,
    )

    # -----------------------------------------------------------------------
    # 单页获取
    # -----------------------------------------------------------------------
    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
            "search_condition": json.dumps(search_condition, ensure_ascii=False),
        }

        resp = client.get(
            client.desktop_url("/uapi/v1/dashboard/teacher-manage-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取讲师列表失败"))

        instructor_list = resp.get("data", {}).get("list", [])
        instructors: list[dict[str, Any]] = []
        for item in instructor_list:
            try:
                raw = InstructorRaw(**item)
                instructors.append(Instructor.from_raw(raw).model_dump())
            except Exception:
                # 如果个别记录字段异常，回退到原始字典，保证列表不中断
                instructors.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return instructors, total_all

    # -----------------------------------------------------------------------
    # 执行查询
    # -----------------------------------------------------------------------
    try:
        if fetch_all:
            all_instructors: list[dict[str, Any]] = []
            current_page = 1
            batch_size = page_size
            total_all = 0

            while True:
                try:
                    instructors, total_all = _fetch_page(current_page, batch_size)
                except Exception:
                    # 单页请求失败，尝试 size 降级到 100 重试一次
                    if batch_size != 100:
                        batch_size = 100
                        instructors, total_all = _fetch_page(current_page, batch_size)
                    else:
                        raise

                all_instructors.extend(instructors)

                report_pagination_progress(
                    "adm_list_instructors",
                    current_page,
                    len(all_instructors),
                    total_all,
                    page_size,
                    is_complete=len(all_instructors) >= total_all or not instructors,
                )

                if len(all_instructors) >= total_all or not instructors:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_instructors",
                        current_page,
                        len(all_instructors),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "instructors": all_instructors,
                    "total": len(all_instructors),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_instructors) if all_instructors else 0,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
        else:
            instructors, total_all = _fetch_page(page, page_size)
            return _ok(
                data={
                    "instructors": instructors,
                    "total": len(instructors),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
    except Exception as e:
        return _err(
            error_code="LIST_INSTRUCTORS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


def _parse_teaching_record_audit_status(status_value: str) -> int:
    """将审核状态参数解析为接口状态码."""
    mapping = {
        "pending": TeachingRecordAuditStatus.PENDING,
        "待审核": TeachingRecordAuditStatus.PENDING,
        "passed": TeachingRecordAuditStatus.PASSED,
        "已通过": TeachingRecordAuditStatus.PASSED,
        "rejected": TeachingRecordAuditStatus.REJECTED,
        "已拒绝": TeachingRecordAuditStatus.REJECTED,
    }
    key = status_value.strip().lower()
    if key in mapping:
        return mapping[key]
    try:
        code = int(status_value)
        if code in (
            TeachingRecordAuditStatus.PENDING,
            TeachingRecordAuditStatus.PASSED,
            TeachingRecordAuditStatus.REJECTED,
        ):
            return code
    except ValueError:
        pass
    raise ValueError(f"不支持的审核状态: {status_value}")


@mcp.tool()
async def adm_list_teaching_records(
    audit_status: Annotated[
        str,
        Field(
            description='审核状态："pending"/"待审核"/2，"passed"/"已通过"/3，"rejected"/"已拒绝"/4',
        ),
    ],
    teacher_umu_ids: Annotated[
        str | None,
        Field(
            default=None,
            description="授课讲师 umu_id 列表，多个用逗号分隔。不提供则不按讲师筛选。",
        ),
    ] = None,
    teacher_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="授课讲师邮箱/手机号/用户名/姓名关键词，多个用逗号分隔。内部自动解析为 umu_id。",
        ),
    ] = None,
    course_keywords: Annotated[
        str | None,
        Field(
            default=None,
            description="课程名称模糊搜索关键词。",
        ),
    ] = None,
    access_code: Annotated[
        str | None,
        Field(
            default=None,
            description="课程访问码。若与 course_keywords 同时提供，返回两者交集。",
        ),
    ] = None,
    page: Annotated[
        int,
        Field(default=1, ge=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, ge=1, le=100, description="每页数量（1-100），默认20"),
    ] = 20,
    fetch_all: Annotated[
        bool,
        Field(
            default=False,
            description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """查询讲师授课记录.

    触发条件：需要查看企业讲师的授课记录、按审核状态/讲师/课程名称/访问码筛选时调用。
    前置依赖：需先调用 adm_login 完成管理员登录。
    副作用：无（只读查询）。

    支持以下条件的交集筛选：
    - 审核状态：pending/待审核/2、passed/已通过/3、rejected/已拒绝/4
    - 授课讲师：teacher_umu_ids（直接传 ID）或 teacher_keywords（按关键词自动解析）
    - 课程名称：course_keywords
    - 访问码：access_code（与 course_keywords 同时提供时本地取交集）
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 adm_login 登录",
        )

    # -----------------------------------------------------------------------
    # 解析审核状态
    # -----------------------------------------------------------------------
    try:
        audit_status_code = _parse_teaching_record_audit_status(audit_status)
    except ValueError as e:
        return _err(
            error_code="INVALID_AUDIT_STATUS",
            error_message=str(e),
            suggested_action='请使用 "pending"/"待审核"/2、"passed"/"已通过"/3、"rejected"/"已拒绝"/4',
        )

    # -----------------------------------------------------------------------
    # 解析讲师关键词为 umu_id
    # -----------------------------------------------------------------------
    resolved_teacher_ids: list[str] | None = None
    if teacher_keywords:
        try:
            resolved_teacher_ids = await _resolve_teacher_keywords(client, teacher_keywords)
        except Exception as e:
            return _err(
                error_code="RESOLVE_TEACHER_ERROR",
                error_message=str(e),
                suggested_action="检查讲师关键词或网络连接后重试",
            )
        if resolved_teacher_ids is None:
            return _err(
                error_code="TEACHER_NOT_FOUND",
                error_message=f"找不到匹配的讲师: {teacher_keywords}",
                suggested_action="检查关键词拼写，或调用 adm_list_instructors 查询讲师信息",
            )

    explicit_teacher_ids: list[str] = []
    if teacher_umu_ids:
        explicit_teacher_ids = [x.strip() for x in teacher_umu_ids.split(",") if x.strip()]

    final_teacher_ids: list[str] = []
    if explicit_teacher_ids:
        final_teacher_ids.extend(explicit_teacher_ids)
    if resolved_teacher_ids:
        final_teacher_ids.extend(resolved_teacher_ids)
    final_teacher_ids = list(dict.fromkeys(final_teacher_ids))

    # -----------------------------------------------------------------------
    # 课程名称 / 访问码筛选
    # -----------------------------------------------------------------------
    search_keyword = (course_keywords or "").strip()
    access_code_value = (access_code or "").strip()

    def _matches_access_code(record: dict[str, Any]) -> bool:
        if not access_code_value:
            return True
        return record.get("group_access_code") == access_code_value

    # -----------------------------------------------------------------------
    # 单页获取
    # -----------------------------------------------------------------------
    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "t": str(int(datetime.now().timestamp() * 1000)),
            "page": str(p),
            "size": str(sz),
            "audit_status": str(audit_status_code),
            "search_keyword": search_keyword,
            "uids": ",".join(final_teacher_ids) if final_teacher_ids else "0",
        }

        resp = client.get(
            client.desktop_url("/uapi/v1/teacher-manage/enterprise-lecturing-record-list"),
            params=params,
        )

        if resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error_message", "获取授课记录失败"))

        record_list = resp.get("data", {}).get("list", [])
        records: list[dict[str, Any]] = []
        for item in record_list:
            try:
                raw = TeachingRecordRaw(**item)
                records.append(TeachingRecord.from_raw(raw).model_dump())
            except Exception:
                # 如果个别记录字段异常，回退到原始字典，保证列表不中断
                records.append(item)

        total_all = int(resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0)
        return records, total_all

    # -----------------------------------------------------------------------
    # 执行查询
    # -----------------------------------------------------------------------
    try:
        if fetch_all:
            all_records: list[dict[str, Any]] = []
            current_page = 1
            batch_size = page_size
            total_all = 0

            while True:
                try:
                    records, total_all = _fetch_page(current_page, batch_size)
                except Exception:
                    # 单页请求失败，尝试 size 降级到 100 重试一次
                    if batch_size != 100:
                        batch_size = 100
                        records, total_all = _fetch_page(current_page, batch_size)
                    else:
                        raise

                all_records.extend(records)

                report_pagination_progress(
                    "adm_list_teaching_records",
                    current_page,
                    len(all_records),
                    total_all,
                    page_size,
                    is_complete=len(all_records) >= total_all or not records,
                )

                if len(all_records) >= total_all or not records:
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    report_pagination_progress(
                        "adm_list_teaching_records",
                        current_page,
                        len(all_records),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            if access_code_value:
                all_records = [r for r in all_records if _matches_access_code(r)]

            return _ok(
                data={
                    "records": all_records,
                    "total": len(all_records),
                    "pagination": {
                        "total_all": len(all_records),
                        "current_page": 1,
                        "page_size": len(all_records) if all_records else 0,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
        else:
            records, total_all = _fetch_page(page, page_size)
            if access_code_value:
                records = [r for r in records if _matches_access_code(r)]

            return _ok(
                data={
                    "records": records,
                    "total": len(records),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": page,
                        "page_size": page_size,
                    },
                },
                next_action="proceed",
                suggested_action="",
            )
    except Exception as e:
        return _err(
            error_code="LIST_TEACHING_RECORDS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接后重试",
        )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def adm_account_management_guide() -> str:
    """管理员账号管理操作指南（含分页策略）."""
    return prompts.admin_account_management_guide()


@mcp.prompt()
def admin_department_management_guide() -> str:
    """管理员部门管理操作指南."""
    return prompts.admin_department_management_guide()


@mcp.prompt()
def adm_learning_records_guide() -> str:
    """管理员学习记录查询操作指南."""
    return prompts.admin_learning_records_guide()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


@mcp.tool()
async def adm_export_accounts(
    output_path: Annotated[
        str,
        Field(
            default="~/Desktop/umu_accounts.xlsx",
            description="输出文件路径，默认桌面。支持 .xlsx 或 .csv 扩展名。",
        ),
    ] = "~/Desktop/umu_accounts.xlsx",
    file_format: Annotated[
        str,
        Field(
            default="xlsx",
            pattern="^(xlsx|csv)$",
            description="文件格式：xlsx 或 csv。",
        ),
    ] = "xlsx",
    keywords: Annotated[
        str | None,
        Field(default=None, description="搜索关键词（姓名、邮箱、手机号、用户名），服务端模糊匹配。"),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(default=None, description="分组ID列表，多个用逗号分隔，如 '177124,177125'。"),
    ] = None,
    group_operator: Annotated[
        str,
        Field(
            default="intersection",
            description='多分组关系："intersection"=交集，"union"=并集。',
        ),
    ] = "intersection",
    role_type: Annotated[
        int | None,
        Field(default=None, description="角色筛选：1=学员, 2=讲师, 3=学习负责人, 4=系统管理员, 5=子管理员。"),
    ] = None,
    account_status: Annotated[
        int | None,
        Field(default=None, description="状态筛选：0=待加入, 1=已启用, 2=已禁用, 3=定时禁用。"),
    ] = None,
    is_manager: Annotated[
        int,
        Field(default=0, description="0=返回全部账号，1=仅返回管理视角账号。"),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID。"),
    ] = None,
) -> str:
    """导出企业账号列表到 Excel/CSV."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    output_path = os.path.expanduser(os.path.expandvars(output_path))
    base, ext = os.path.splitext(output_path)
    if file_format == "csv":
        if ext.lower() != ".csv":
            output_path = f"{base}.csv"
    else:
        if ext.lower() != ".xlsx":
            output_path = f"{base}.xlsx"

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    try:
        engine = ExportEngine(client)
        result = engine.export_admin_accounts(
            output_path,
            keywords=keywords,
            group_ids=group_ids,
            group_operator=group_operator,
            role_type=role_type,
            account_status=account_status,
            is_manager=is_manager,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文件已生成，可直接在本地打开查看。",
        )
    except Exception as e:
        logger.exception("导出账号列表失败")
        return _err("EXPORT_ACCOUNTS_FAILED", str(e))


@mcp.tool()
async def adm_export_learning_records(
    output_path: Annotated[
        str,
        Field(
            default="~/Desktop/umu_learning_records.xlsx",
            description="输出文件路径，默认桌面。支持 .xlsx 或 .csv 扩展名。",
        ),
    ] = "~/Desktop/umu_learning_records.xlsx",
    file_format: Annotated[
        str,
        Field(
            default="xlsx",
            pattern="^(xlsx|csv)$",
            description="文件格式：xlsx 或 csv。",
        ),
    ] = "xlsx",
    start_day: Annotated[
        str | None,
        Field(default=None, description="最后学习时间起始日期，格式 YYYY-MM-DD。"),
    ] = None,
    end_day: Annotated[
        str | None,
        Field(default=None, description="最后学习时间结束日期，格式 YYYY-MM-DD。"),
    ] = None,
    uids: Annotated[
        str | None,
        Field(default=None, description="学员 UMU ID 列表，多个用逗号分隔。"),
    ] = None,
    course_title: Annotated[
        str | None,
        Field(default=None, description="课程名称模糊搜索关键词。"),
    ] = None,
    department_ids: Annotated[
        str | None,
        Field(default=None, description="部门ID列表，多个用逗号分隔。"),
    ] = None,
    group_ids: Annotated[
        str | None,
        Field(default=None, description="企业分组ID列表，多个用逗号分隔。"),
    ] = None,
    class_ids: Annotated[
        str | None,
        Field(default=None, description="班级ID列表，多个用逗号分隔。"),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID。"),
    ] = None,
) -> str:
    """导出企业账号的课程学习明细到 Excel/CSV."""
    client = _get_client(session_id)
    auth_err = _require_auth(client)
    if auth_err:
        return _err("NOT_AUTHENTICATED", auth_err, next_action="retry")

    output_path = os.path.expanduser(os.path.expandvars(output_path))
    base, ext = os.path.splitext(output_path)
    if file_format == "csv":
        if ext.lower() != ".csv":
            output_path = f"{base}.csv"
    else:
        if ext.lower() != ".xlsx":
            output_path = f"{base}.xlsx"

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    uid_list = [u.strip() for u in uids.split(",") if u.strip()] if uids else None
    class_id_list = [c.strip() for c in class_ids.split(",") if c.strip()] if class_ids else None

    try:
        engine = ExportEngine(client)
        result = engine.export_learning_records(
            output_path,
            start_day=start_day,
            end_day=end_day,
            uids=uid_list,
            course_title=course_title,
            department_ids=department_ids,
            group_ids=group_ids,
            class_ids=class_id_list,
        )
        return _ok(
            data=result,
            next_action="proceed",
            suggested_action="文件已生成，可直接在本地打开查看。",
        )
    except Exception as e:
        logger.exception("导出学习记录失败")
        return _err("EXPORT_LEARNING_RECORDS_FAILED", str(e))


def main() -> None:
    """MCP 服务入口."""
    import asyncio

    print("=" * 60)
    print("UMU 管理员端 MCP Server")
    print("=" * 60)
    print()
    print("支持的传输方式:")
    print("  - stdio:  标准输入输出（推荐用于本地 AI 助手）")
    print()
    print("环境变量:")
    print("  UMU_BASE_URL         - UMU 基础 URL（默认: https://www.umu.cn）")
    print("  UMU_ADMIN_USERNAME   - 管理员登录用户名")
    print("  UMU_ADMIN_PASSWORD   - 管理员登录密码")
    print("  MCP_LOG_LEVEL        - 日志级别（默认: INFO）")
    print()
    print("可用 Tools:")
    print("  认证: adm_login, adm_check_auth, adm_get_user_info")
    print("  会话: adm_create_session, adm_list_sessions, adm_destroy_session")
    print("  账号: adm_create_account, adm_list_accounts,")
    print("        adm_update_account,")
    print("        adm_disable_account, adm_enable_account,")
    print("        adm_batch_disable_accounts, adm_batch_enable_accounts,")
    print("        adm_get_scheduled_disables,")
    print("        adm_list_groups, adm_list_classes")
    print("  部门: adm_get_department_tree, adm_get_department,")
    print("        adm_get_child_departments, adm_list_departments,")
    print("        adm_list_department_members, adm_search_department_members,")
    print("        adm_create_department, adm_update_department,")
    print("        adm_sort_departments, adm_add_department_members,")
    print("        adm_move_department_members, adm_remove_department_members,")
    print("        adm_delete_departments")
    print("  分组: adm_create_group, adm_update_group, adm_delete_groups,")
    print("        adm_get_group, adm_list_group_members,")
    print("        adm_list_group_managers, adm_add_group_members,")
    print("        adm_remove_group_members, adm_add_group_managers,")
    print("        adm_remove_group_managers")
    print("  课程: adm_list_courses,")
    print("        adm_set_course_access_permission, adm_get_course_access_permission,")
    print("        adm_get_course_access_list, adm_search_access_accounts,")
    print("        adm_add_course_access_accounts, adm_remove_course_access_accounts,")
    print("        adm_cancel_all_assigned_permissions,")
    print("        adm_list_course_audit_records, adm_audit_course,")
    print("        adm_list_course_categories")
    print("  课程审核黑名单: adm_list_course_blacklist, adm_save_course_blacklist")
    print("  学习项目: adm_list_learning_programs")
    print("  数据: adm_list_learning_records, adm_list_user_tasks,")
    print("        adm_list_instructors")
    print()
    print("可用 Prompts:")
    print("  - adm_account_management_guide")
    print("  - admin_department_management_guide")
    print("  - adm_learning_records_guide")
    print()

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        print(f"不支持的传输方式: {transport}")
        print("支持: stdio, sse")


if __name__ == "__main__":
    main()