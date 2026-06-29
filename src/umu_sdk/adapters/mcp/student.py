# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU 学员端 MCP Server.

将 UMU 学习平台的学员操作暴露为原子化 MCP Tools，供 AI 自主编排完成课程流程。

Usage:
    # 启动 MCP Server（默认）
    python -m umu_sdk.mcp.server_student

    # 或使用 CLI
    umu-mcp-student

Environment Variables:
    UMU_BASE_URL: UMU 基础 URL (默认: https://www.umu.cn)
    UMU_STUDENT_USERNAME: 学生登录用户名
    UMU_STUDENT_PASSWORD: 学生登录密码
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import load_credentials_with_source
from .utils import (
    format_login_summary,
    get_login_identity,
)
from . import prompts
from .batch import AccountImporter, AccountSource
from .session import SessionManager
from .tool_factory import register_operations
from ...tools.operations import courses as _courses_ops
from ...tools.operations import learning as _learning_ops
# 以下 helper 从 learning_helpers 重新导出，保持旧测试的导入路径兼容
from ...tools.shared.learning_helpers import (
    _build_insert_answer_payload,  # noqa: F401
    _build_uscorm_12_cmi,  # noqa: F401
    _check_needs_enroll,  # noqa: F401
    _check_needs_enroll_form,  # noqa: F401
    _fetch_enroll_form_page,  # noqa: F401
    _format_scorm_total_time,  # noqa: F401
    _get_enroll_short_url,  # noqa: F401
    _parse_enroll_form,  # noqa: F401
    _parse_scorm_launch_url,  # noqa: F401
    _resolve_course_identifier,  # noqa: F401
    _validate_enroll_form,  # noqa: F401
)
from .shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    """配置结构化日志."""
    level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = os.getenv(
        "MCP_LOG_FORMAT",
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger("umu.mcp.student")
    root.setLevel(level)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger("umu.mcp.student")


# ---------------------------------------------------------------------------
# 全局实例（由 lifespan 管理）
# ---------------------------------------------------------------------------
_umu_client: UMUClient | None = None
_session_manager: SessionManager | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """应用生命周期管理.

    启动时初始化会话管理器并创建默认会话；关闭时释放所有会话资源.
    默认从 UMU_STUDENT_USERNAME / UMU_STUDENT_PASSWORD 读取学生账号自动登录.
    未配置凭据时正常启动，提示手动调用 stu_login 登录.
    """
    global _umu_client, _session_manager

    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    # 每次启动都重新读取学生账号凭据；优先级：显式参数/环境变量 > .env > 加密凭证
    username, password, source = load_credentials_with_source("student")

    # 创建会话管理器
    _session_manager = SessionManager(
        base_url=base_url,
    )

    # 创建默认会话
    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    # 如果有凭据，自动登录默认会话；否则正常启动
    if username and password:
        try:
            await _session_manager.login_session(
                default_session.session_id, username, password, credential_source=source.value
            )
            default_session.credential_source = source.value
            identity = get_login_identity(_umu_client)
            summary = format_login_summary(username, source.value, identity)
            logger.info("[MCP Student] 默认会话已自动登录: %s", summary)
        except Exception as e:
            logger.warning("[MCP Student] 默认会话自动登录失败: %s", e)
    else:
        logger.info("[MCP Student] 未配置学生账号凭据，请调用 stu_login 或 stu_create_session")

    logger.info("[MCP Student] UMU 学员端服务已启动，目标: %s", base_url)

    yield {"client": _umu_client, "session_manager": _session_manager}

    # 清理所有会话
    if _session_manager:
        _session_manager.close_all()
        _session_manager = None
    _umu_client = None
    logger.info("[MCP Student] UMU 学员端服务已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-student",
    instructions="""UMU 学习平台学员端 MCP 服务。

提供课程学习相关的原子化操作，包括：
- 课程结构查询（报名状态、小节列表）
- 学习进度查询
- 获取我参与的课程列表（支持按学习状态筛选：已学习/学习中/待学习）
- 课程报名（支持简单报名和复杂报名表单）
- 复杂报名表单查询与提交（联系信息、文本题、单选、多选）
- 小节完成（浏览、问卷、签到、考试）
- 状态验证

AI 使用本服务时，应先调用 stu_get_course_structure 获取课程结构。
若课程需要报名且 stu_enroll_course 无法直接完成（如需要填写姓名、公司、
职场地址、部门等），则调用 stu_get_enroll_form 获取表单结构，
再调用 stu_submit_enroll_form 提交答案，最后继续完成小节。
每次操作后建议验证状态。
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


def _get_client_for_ops(session_id: str | None = None) -> UMUClient:
    """运行时分发 client；通过包装层保留测试对 _get_client 的 patch 能力."""
    return _get_client(session_id)


def _require_auth(client: UMUClient) -> str | None:
    """检查客户端认证状态.

    Returns:
        None 表示认证正常；否则返回错误信息字符串.
    """
    if not client.auth.is_authenticated():
        return "当前未登录或 Token 已过期，请先调用 stu_login 登录"
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
# Tools: 共享业务操作（自动注册）
# ---------------------------------------------------------------------------

register_operations(
    mcp=mcp,
    module=_courses_ops,
    role="student",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth,
    logger=logger,
    namespace=globals(),
)

# 注册学习域共享操作
register_operations(
    mcp=mcp,
    module=_learning_ops,
    role="student",
    get_client=_get_client_for_ops,
    ok=_ok,
    err=_err,
    require_auth=_require_auth,
    logger=logger,
    namespace=globals(),
)


_STUDENT_SESSION_CONFIG = SessionToolConfig(
    role="stu",
    role_label="学员",
    tool_domain_hint="学习相关 Tool",
    login_success_suffix="现在可以调用其他学习相关 Tool",
    check_auth_success_suffix="学习相关 Tool",
    create_session_suggested_action="保存 session_id，后续调用 tool 时传入此参数",
    create_session_with_password=True,
    include_is_authenticated_in_session=True,
)

mcp.tool()(
    make_login_tool(
        _STUDENT_SESSION_CONFIG,
        get_client=_get_client,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)

mcp.tool()(
    make_check_auth_tool(
        _STUDENT_SESSION_CONFIG,
        get_client=_get_client,
        ok=_ok,
        err=_err,
    )
)

mcp.tool()(
    make_create_session_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)

mcp.tool()(
    make_list_sessions_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)

mcp.tool()(
    make_destroy_session_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)

@mcp.tool()
async def stu_batch_import_accounts(
    file_path: Annotated[str, Field(description="账号文件路径（CSV 或 JSON）")],
    file_format: Annotated[
        str,
        Field(default="auto", description="文件格式: auto(自动检测), csv, json"),
    ] = "auto",
) -> str:
    """从文件导入账号列表.

    触发条件：需要批量导入多个账号时使用。
    前置依赖：无。
    副作用：无（只读查询）。

    CSV 格式: username,password[,nickname]
    JSON 格式: [{"username": "...", "password": "..."}, ...]

    返回结果中密码会被脱敏，不会明文显示。
    """
    try:
        source = None if file_format == "auto" else AccountSource(file_format)
        accounts = AccountImporter.import_accounts(file_path, source)

        return _ok(
            data={
                "total": len(accounts),
                "accounts": [
                    {
                        "username": a.username,
                        "nickname": a.nickname,
                    }
                    for a in accounts
                ],
            },
            next_action="proceed",
            suggested_action="确认账号列表后调用 stu_batch_complete_course",
        )
    except Exception as e:
        return _err(
            error_code="IMPORT_ACCOUNTS_FAILED",
            error_message=str(e),
            suggested_action="检查文件路径和格式是否正确",
        )




# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def stu_course_completion_workflow() -> str:
    """课程完成的标准操作流程."""
    return prompts.course_completion_workflow()


@mcp.prompt()
def stu_lesson_type_guide() -> str:
    """根据小节类型选择对应操作的参考指南."""
    return prompts.lesson_type_guide()


@mcp.prompt()
def stu_error_recovery_guide() -> str:
    """常见错误和恢复策略."""
    return prompts.error_recovery_guide()


@mcp.prompt()
def stu_exam_workflow_guide() -> str:
    """考试小节专用操作流程指引."""
    return prompts.exam_workflow_guide()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    """MCP 服务入口."""
    import asyncio

    print("=" * 60, file=sys.stderr)
    print("UMU 学员端 MCP Server", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(file=sys.stderr)
    print("支持的传输方式:", file=sys.stderr)
    print("  - stdio:  标准输入输出（推荐用于本地 AI 助手）", file=sys.stderr)
    print(file=sys.stderr)
    print("环境变量:", file=sys.stderr)
    print("  UMU_BASE_URL         - UMU 基础 URL（默认: https://www.umu.cn）", file=sys.stderr)
    print("  UMU_STUDENT_USERNAME - 学生登录用户名", file=sys.stderr)
    print("  UMU_STUDENT_PASSWORD - 学生登录密码", file=sys.stderr)
    print(file=sys.stderr)
    print("可用 Tools:", file=sys.stderr)
    print("  认证: stu_login, stu_check_auth", file=sys.stderr)
    print("  会话: stu_create_session, stu_list_sessions, stu_destroy_session", file=sys.stderr)
    print("  解析: stu_resolve_course_url", file=sys.stderr)
    print("  查结构: stu_list_participated_courses,", file=sys.stderr)
    print("          stu_get_course_structure, stu_get_learning_progress", file=sys.stderr)
    print("  操作: stu_enroll_course, stu_browse_lesson,", file=sys.stderr)
    print("        stu_get_questionnaire_questions, stu_submit_questionnaire,", file=sys.stderr)
    print("        stu_check_in, stu_check_in_with_rating,", file=sys.stderr)
    print("        stu_start_exam, stu_submit_exam", file=sys.stderr)
    print("  批量: stu_batch_import_accounts, stu_batch_complete_course", file=sys.stderr)
    print("  完成: stu_complete_course", file=sys.stderr)
    print("  验证: stu_get_lesson_status", file=sys.stderr)
    print("  讲师端: 请使用 umu-mcp-teacher", file=sys.stderr)
    print(file=sys.stderr)
    print("可用 Prompts:", file=sys.stderr)
    print("  - stu_course_completion_workflow", file=sys.stderr)
    print("  - stu_lesson_type_guide", file=sys.stderr)
    print("  - stu_error_recovery_guide", file=sys.stderr)
    print("  - stu_exam_workflow_guide", file=sys.stderr)
    print(file=sys.stderr)

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        print(f"不支持的传输方式: {transport}", file=sys.stderr)
        print("支持: stdio, sse", file=sys.stderr)


if __name__ == "__main__":
    main()
