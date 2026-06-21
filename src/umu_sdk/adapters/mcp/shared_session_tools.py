# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""会话/认证工具工厂.

Admin、Teacher、Student 三个 MCP server 的登录、认证、会话管理工具实现
高度雷同，仅文案、错误码与少量字段存在差异。本模块提供工厂函数，供三个
server 按需注册，消除重复代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable

from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import CredentialSource
from .session import SessionManager
from .utils import get_login_identity


@dataclass
class SessionToolConfig:
    """会话/认证工具的角色相关配置."""

    role: str
    role_label: str
    tool_domain_hint: str
    login_success_suffix: str = ""
    check_auth_success_suffix: str = ""
    create_session_suggested_action: str = ""
    create_session_with_password: bool = False
    isoformat_timestamps: bool = False
    include_is_authenticated_in_session: bool = False
    session_manager_not_init_code: str = "SESSION_MANAGER_NOT_INITIALIZED"
    create_session_failed_code: str = "CREATE_SESSION_FAILED"
    list_sessions_failed_code: str = "LIST_SESSIONS_FAILED"
    destroy_session_failed_code: str = "DESTROY_SESSION_FAILED"
    destroy_session_not_found_code: str = "SESSION_NOT_FOUND"
    destroy_session_success_suggested_action: str = ""


# ---------------------------------------------------------------------------
# 依赖类型别名
# ---------------------------------------------------------------------------
_GetClient = Callable[[str | None], UMUClient]
_GetSessionManager = Callable[[], SessionManager | None]
_Ok = Callable[..., str]
_Err = Callable[..., str]


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def make_login_tool(
    config: SessionToolConfig,
    get_client: _GetClient,
    get_session_manager: _GetSessionManager,
    ok: _Ok,
    err: _Err,
) -> Callable[..., Awaitable[str]]:
    """创建 ``{role}_login`` 工具."""

    async def _login(
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
        client = get_client(session_id)
        try:
            token = client.login(username, password)
            session_manager = get_session_manager()
            if session_id and session_manager:
                s = session_manager.get_session_sync(session_id)
                if s:
                    s.username = username
                    s.credential_source = CredentialSource.EXPLICIT.value
            identity = get_login_identity(client)
            return ok(
                data={
                    "token": token,
                    "session_id": session_id,
                    "credential_source": CredentialSource.EXPLICIT.value,
                    **identity,
                },
                next_action="proceed",
                suggested_action=(
                    f"已登录到企业「{identity.get('enterprise_name') or identity.get('enterprise_id', '')}」，"
                    f"{config.login_success_suffix}"
                ),
            )
        except Exception as e:
            return err(
                error_code="AUTH_FAILED",
                error_message=str(e),
                suggested_action="检查用户名密码是否正确，或稍后重试",
            )

    _login.__name__ = f"{config.role}_login"
    _login.__doc__ = f"使用用户名密码登录 UMU 平台（{config.role_label}账号）."
    return _login


# ---------------------------------------------------------------------------
# Check auth
# ---------------------------------------------------------------------------
def make_check_auth_tool(
    config: SessionToolConfig,
    get_client: _GetClient,
    ok: _Ok,
    err: _Err,
) -> Callable[..., Awaitable[str]]:
    """创建 ``{role}_check_auth`` 工具."""

    async def _check_auth(
        session_id: Annotated[
            str | None,
            Field(
                default=None,
                description="可选的会话 ID。如果提供，检查指定会话的认证状态；如果不提供，检查默认会话。",
            ),
        ] = None,
    ) -> str:
        client = get_client(session_id)
        try:
            is_auth = client.auth.is_authenticated()
            token = client.auth.get_token()
            if is_auth and token:
                return ok(
                    data={
                        "is_authenticated": True,
                        "token_preview": token[:20] + "...",
                    },
                    next_action="proceed",
                    suggested_action=f"当前已登录，可以正常调用{config.check_auth_success_suffix}",
                )
            else:
                return err(
                    error_code="NOT_AUTHENTICATED",
                    error_message="当前未登录或 Token 已过期",
                    suggested_action=f"调用 {config.role}_login 重新登录",
                )
        except Exception as e:
            return err(
                error_code="AUTH_CHECK_FAILED",
                error_message=str(e),
                suggested_action=f"调用 {config.role}_login 重新登录",
            )

    _check_auth.__name__ = f"{config.role}_check_auth"
    _check_auth.__doc__ = "检查当前是否已认证."
    return _check_auth


# ---------------------------------------------------------------------------
# Create session
# ---------------------------------------------------------------------------
def make_create_session_tool(
    config: SessionToolConfig,
    get_session_manager: _GetSessionManager,
    ok: _Ok,
    err: _Err,
) -> Callable[..., Awaitable[str]]:
    """创建 ``{role}_create_session`` 工具."""

    async def _create_session_with_password(
        username: Annotated[
            str | None,
            Field(default=None, description="可选用户名，如果提供则尝试自动登录"),
        ] = None,
        password: Annotated[
            str | None,
            Field(default=None, description="可选密码"),
        ] = None,
    ) -> str:
        session_manager = get_session_manager()
        if session_manager is None:
            return err(
                error_code=config.session_manager_not_init_code,
                error_message="会话管理器未初始化",
            )
        try:
            session = await session_manager.create_session(username, password)
            data: dict[str, Any] = {
                "session_id": session.session_id,
                "username": session.username,
            }
            if config.include_is_authenticated_in_session:
                data["is_authenticated"] = session.client.auth.is_authenticated()
            data["created_at"] = (
                session.created_at.isoformat() if config.isoformat_timestamps else session.created_at
            )
            return ok(
                data=data,
                next_action="proceed",
                suggested_action=config.create_session_suggested_action,
            )
        except Exception as e:
            return err(
                error_code=config.create_session_failed_code,
                error_message=str(e),
            )

    async def _create_session_without_password(
        username: Annotated[
            str | None,
            Field(default=None, description="可选的预设用户名"),
        ] = None,
    ) -> str:
        session_manager = get_session_manager()
        if session_manager is None:
            return err(
                error_code=config.session_manager_not_init_code,
                error_message="会话管理器未初始化",
                suggested_action="请检查 MCP 服务是否正确启动",
            )
        try:
            session = await session_manager.create_session()
            if username:
                session.username = username
            data: dict[str, Any] = {
                "session_id": session.session_id,
                "username": session.username,
                "created_at": (
                    session.created_at.isoformat()
                    if config.isoformat_timestamps
                    else session.created_at
                ),
            }
            return ok(
                data=data,
                next_action="proceed",
                suggested_action=config.create_session_suggested_action,
            )
        except Exception as e:
            return err(
                error_code=config.create_session_failed_code,
                error_message=str(e),
                suggested_action="请稍后重试",
            )

    if config.create_session_with_password:
        fn = _create_session_with_password
    else:
        fn = _create_session_without_password

    fn.__name__ = f"{config.role}_create_session"
    fn.__doc__ = "创建新的独立会话."
    return fn


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------
def make_list_sessions_tool(
    config: SessionToolConfig,
    get_session_manager: _GetSessionManager,
    ok: _Ok,
    err: _Err,
) -> Callable[..., Awaitable[str]]:
    """创建 ``{role}_list_sessions`` 工具."""

    async def _list_sessions() -> str:
        session_manager = get_session_manager()
        if session_manager is None:
            return err(
                error_code=config.session_manager_not_init_code,
                error_message="会话管理器未初始化",
            )
        try:
            sessions = await session_manager.list_sessions()
            return ok(
                data={
                    "count": len(sessions),
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "username": s.username,
                            **(
                                {"is_authenticated": s.is_authenticated}
                                if config.include_is_authenticated_in_session
                                else {}
                            ),
                            "created_at": (
                                s.created_at.isoformat()
                                if config.isoformat_timestamps
                                else s.created_at
                            ),
                            "last_used_at": (
                                s.last_used_at.isoformat()
                                if config.isoformat_timestamps
                                else s.last_used_at
                            ),
                        }
                        for s in sessions
                    ],
                },
                next_action="proceed",
            )
        except Exception as e:
            return err(
                error_code=config.list_sessions_failed_code,
                error_message=str(e),
            )

    _list_sessions.__name__ = f"{config.role}_list_sessions"
    _list_sessions.__doc__ = "列出所有活跃会话."
    return _list_sessions


# ---------------------------------------------------------------------------
# Destroy session
# ---------------------------------------------------------------------------
def make_destroy_session_tool(
    config: SessionToolConfig,
    get_session_manager: _GetSessionManager,
    ok: _Ok,
    err: _Err,
) -> Callable[..., Awaitable[str]]:
    """创建 ``{role}_destroy_session`` 工具."""

    async def _destroy_session(
        session_id: Annotated[str, Field(description="要销毁的会话 ID")],
    ) -> str:
        session_manager = get_session_manager()
        if session_manager is None:
            return err(
                error_code=config.session_manager_not_init_code,
                error_message="会话管理器未初始化",
            )
        try:
            success = await session_manager.destroy_session(session_id)
            if success:
                return ok(
                    data={"session_id": session_id, "destroyed": True},
                    next_action="proceed",
                    suggested_action=config.destroy_session_success_suggested_action,
                )
            else:
                return err(
                    error_code=config.destroy_session_not_found_code,
                    error_message=f"会话不存在: {session_id}",
                )
        except Exception as e:
            return err(
                error_code=config.destroy_session_failed_code,
                error_message=str(e),
            )

    _destroy_session.__name__ = f"{config.role}_destroy_session"
    _destroy_session.__doc__ = "销毁指定会话."
    return _destroy_session
