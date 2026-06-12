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
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Annotated, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import load_credentials
from ...core.admin_models import (
    AdminAccount,
    AdminAccountRaw,
    AdminClass,
    AdminClassRaw,
    format_timestamp_beijing,
    LearningRecord,
    LearningRecordRaw,
)
from .session import SessionManager
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
    # 每次启动都重新读取管理员账号凭据；优先 .env / 环境变量，其次加密凭证文件
    username, password = load_credentials("admin")

    _session_manager = SessionManager(
        base_url=base_url,
    )

    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    if username and password:
        try:
            await _session_manager.login_session(
                default_session.session_id, username, password
            )
            logger.info("默认会话已自动登录: %s", username)
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


# ---------------------------------------------------------------------------
# Tools: 认证
# ---------------------------------------------------------------------------


@mcp.tool()
async def adm_login(
    username: Annotated[str, Field(description="用户名/邮箱/手机号")],
    password: Annotated[str, Field(description="明文密码，服务端会自动加密")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中登录；如果不提供，在默认会话中登录。",
        ),
    ] = None,
) -> str:
    """使用管理员账号登录 UMU 平台.

    触发条件：当用户需要登录或当前认证已过期时调用。
    前置依赖：无。
    副作用：会设置认证 Token，后续 Tool 可以使用相同 session_id 复用此 Token。
    """
    client = _get_client(session_id)
    try:
        token = client.login(username, password)
        # 更新会话用户名
        if session_id and _session_manager:
            s = _session_manager.get_session_sync(session_id)
            if s:
                s.username = username
        # 登录后获取用户信息
        try:
            r = client.get(client.desktop_url("/uapi/v1/user/get"))
            user_data = r.get("data", {})
            user_id = user_data.get("user_id", "")
            user_name = user_data.get("name", "")
        except Exception:
            user_id = ""
            user_name = ""
        return _ok(
            data={
                "token": token,
                "user_id": user_id,
                "user_name": user_name,
                "session_id": session_id,
            },
            next_action="proceed",
            suggested_action="现在可以调用管理员端相关 Tool",
        )
    except Exception as e:
        return _err(
            error_code="AUTH_FAILED",
            error_message=str(e),
            suggested_action="检查用户名密码是否正确，或稍后重试",
        )


@mcp.tool()
async def adm_check_auth(
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，检查指定会话的认证状态；如果不提供，检查默认会话。",
        ),
    ] = None,
) -> str:
    """检查当前是否已认证.

    触发条件：在执行管理操作前，确认当前登录状态。
    前置依赖：无。
    副作用：无。
    """
    client = _get_client(session_id)
    try:
        is_auth = client.auth.is_authenticated()
        token = client.auth.get_token()
        if is_auth and token:
            return _ok(
                data={
                    "is_authenticated": True,
                    "token_preview": token[:20] + "...",
                },
                next_action="proceed",
                suggested_action="当前已登录，可以正常调用管理相关 Tool",
            )
        else:
            return _err(
                error_code="NOT_AUTHENTICATED",
                error_message="当前未登录或 Token 已过期",
                suggested_action="调用 adm_login 重新登录",
            )
    except Exception as e:
        return _err(
            error_code="AUTH_CHECK_FAILED",
            error_message=str(e),
            suggested_action="调用 adm_login 重新登录",
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


@mcp.tool()
async def adm_create_session(
    username: Annotated[
        str | None,
        Field(default=None, description="可选用户名，如果提供则尝试自动登录"),
    ] = None,
    password: Annotated[
        str | None,
        Field(default=None, description="可选密码"),
    ] = None,
) -> str:
    """创建新的独立会话.

    触发条件：当需要为不同用户创建隔离的登录环境时调用。
    前置依赖：无。
    副作用：创建独立会话，拥有独立的 Cookie 和 Token。

    每个会话拥有独立的 UMUClient 实例（含独立的 httpx.Client），
    确保多用户并发使用时登录状态互不干扰。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        session = await _session_manager.create_session(username, password)
        return _ok(
            data={
                "session_id": session.session_id,
                "username": session.username,
                "is_authenticated": session.client.auth.is_authenticated(),
                "created_at": session.created_at,
            },
            next_action="proceed",
            suggested_action="保存 session_id，后续调用 tool 时传入此参数",
        )
    except Exception as e:
        return _err(
            error_code="CREATE_SESSION_FAILED",
            error_message=str(e),
        )


@mcp.tool()
async def adm_list_sessions() -> str:
    """列出所有活跃会话.

    触发条件：需要查看当前有哪些会话在使用中。
    前置依赖：无。
    副作用：无（只读查询）。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        sessions = await _session_manager.list_sessions()
        return _ok(
            data={
                "count": len(sessions),
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "username": s.username,
                        "is_authenticated": s.is_authenticated,
                        "created_at": s.created_at,
                        "last_used_at": s.last_used_at,
                    }
                    for s in sessions
                ],
            },
            next_action="proceed",
        )
    except Exception as e:
        return _err(
            error_code="LIST_SESSIONS_FAILED",
            error_message=str(e),
        )


@mcp.tool()
async def adm_destroy_session(
    session_id: Annotated[str, Field(description="要销毁的会话 ID")],
) -> str:
    """销毁指定会话.

    触发条件：会话不再需要使用，或需要释放资源时调用。
    前置依赖：无。
    副作用：关闭会话的客户端连接，释放资源。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        success = await _session_manager.destroy_session(session_id)
        if success:
            return _ok(
                data={"session_id": session_id, "destroyed": True},
                next_action="proceed",
            )
        else:
            return _err(
                error_code="SESSION_NOT_FOUND",
                error_message=f"会话不存在: {session_id}",
            )
    except Exception as e:
        return _err(
            error_code="DESTROY_SESSION_FAILED",
            error_message=str(e),
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
        if resp.get("status") is not True and resp.get("error_code") != 0:
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
        if resp.get("status") is not True and resp.get("error_code") != 0:
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
        condition["department_ids"] = [
            d.strip() for d in department_ids.split(",") if d.strip()
        ]
    if group_ids:
        condition["enterprise_group_ids"] = [
            g.strip() for g in group_ids.split(",") if g.strip()
        ]
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
                error_message=precheck_resp.get(
                    "error_message", "预检未通过，参数可能不合法"
                ),
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
async def adm_list_departments(
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

    try:
        resp = client.get(
            client.desktop_url("/ajax/enterprise/getGroupList"),
            params={
                "page": str(page),
                "size": str(page_size),
            },
        )

        if resp.get("status") is not True and resp.get("error_code") != 0:
            return _err(
                error_code="LIST_GROUPS_FAILED",
                error_message=resp.get("error", "获取分组列表失败"),
                suggested_action="检查管理员权限或稍后重试",
            )

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

        if resp.get("status") is not True and resp.get("error_code") != 0:
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
                        "role_name": _ROLE_TYPE_MAP.get(
                            int(user.get("role_type", 0) or 0), "未知"
                        ),
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

        total_all = int(
            resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0
        )
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
                progress_pct = ""
                if total_all > 0:
                    pct = min(100, int(len(all_accounts) / total_all * 100))
                    progress_pct = f" ({pct}%)"
                if total_all > 0 and current_page == 1:
                    print(
                        f"[adm_list_accounts] 共 {total_all} 条，预计 {max(1, (total_all + batch_size - 1) // batch_size)} 页",
                        file=sys.stderr,
                    )
                print(
                    f"[adm_list_accounts] 已获取第 {current_page} 页，累计 {len(all_accounts)} / {total_all} 条{progress_pct}",
                    file=sys.stderr,
                )

                if len(all_accounts) >= total_all or not accounts:
                    print(
                        f"[adm_list_accounts] 获取完成，共 {len(all_accounts)} 条，合计 {current_page} 页",
                        file=sys.stderr,
                    )
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    warning_msg = (
                        f"[adm_list_accounts] 警告：达到 50 页安全上限，停止获取"
                        f"（已获取 {len(all_accounts)} 条）"
                    )
                    print(warning_msg, file=sys.stderr)
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

        total_all = int(
            resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0
        )
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

                progress_pct = ""
                if total_all > 0:
                    pct = min(100, int(len(all_records) / total_all * 100))
                    progress_pct = f" ({pct}%)"
                if total_all > 0 and current_page == 1:
                    print(
                        f"[adm_list_learning_records] 共 {total_all} 条，"
                        f"预计 {max(1, (total_all + batch_size - 1) // batch_size)} 页",
                        file=sys.stderr,
                    )
                print(
                    f"[adm_list_learning_records] 已获取第 {current_page} 页，"
                    f"累计 {len(all_records)} / {total_all} 条{progress_pct}",
                    file=sys.stderr,
                )

                if len(all_records) >= total_all or not records:
                    print(
                        f"[adm_list_learning_records] 获取完成，共 {len(all_records)} 条，"
                        f"合计 {current_page} 页",
                        file=sys.stderr,
                    )
                    break
                current_page += 1
                # 安全上限：最多 50 页
                if current_page > 50:
                    warning_msg = (
                        f"[adm_list_learning_records] 警告：达到 50 页安全上限，停止获取"
                        f"（已获取 {len(all_records)} 条）"
                    )
                    print(warning_msg, file=sys.stderr)
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

        total_all = int(
            resp.get("data", {}).get("page_info", {}).get("list_total_num", 0) or 0
        )
        return classes, total_all

    try:
        if fetch_all:
            all_classes: list[dict] = []
            current_page = 1
            batch_size = 20
            total_all = 0

            while True:
                classes, total_all = _fetch_page(current_page, batch_size)
                all_classes.extend(classes)

                progress_pct = ""
                if total_all > 0:
                    pct = min(100, int(len(all_classes) / total_all * 100))
                    progress_pct = f" ({pct}%)"
                if total_all > 0 and current_page == 1:
                    print(
                        f"[adm_list_classes] 共 {total_all} 条，"
                        f"预计 {max(1, (total_all + batch_size - 1) // batch_size)} 页",
                        file=sys.stderr,
                    )
                print(
                    f"[adm_list_classes] 已获取第 {current_page} 页，"
                    f"累计 {len(all_classes)} / {total_all} 条{progress_pct}",
                    file=sys.stderr,
                )

                if len(all_classes) >= total_all or not classes:
                    print(
                        f"[adm_list_classes] 获取完成，共 {len(all_classes)} 条，"
                        f"合计 {current_page} 页",
                        file=sys.stderr,
                    )
                    break
                current_page += 1
                if current_page > 50:
                    warning_msg = (
                        f"[adm_list_classes] 警告：达到 50 页安全上限，停止获取"
                        f"（已获取 {len(all_classes)} 条）"
                    )
                    print(warning_msg, file=sys.stderr)
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

            return _ok(
                data={
                    "classes": all_classes,
                    "total": len(all_classes),
                    "pagination": {
                        "total_all": total_all,
                        "current_page": 1,
                        "page_size": len(all_classes) if all_classes else 0,
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

            if resp.get("status") is not True and resp.get("error_code") != 0:
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
                        "status_text": _get_status_text(
                            int(user.get("account_status", 0) or 0)
                        ),
                        "role_type": int(user.get("role_type", 0) or 0),
                        "role_name": _ROLE_TYPE_MAP.get(
                            int(user.get("role_type", 0) or 0), "未知"
                        ),
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


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def adm_account_management_guide() -> str:
    """管理员账号管理操作指南（含分页策略）."""
    return prompts.admin_account_management_guide()


@mcp.prompt()
def adm_learning_records_guide() -> str:
    """管理员学习记录查询操作指南."""
    return prompts.admin_learning_records_guide()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


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
    print("        adm_disable_account, adm_enable_account,")
    print("        adm_batch_disable_accounts, adm_batch_enable_accounts,")
    print("        adm_get_scheduled_disables,")
    print("        adm_list_departments, adm_list_groups")
    print()
    print("可用 Prompts:")
    print("  - adm_account_management_guide")
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
