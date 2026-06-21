"""Shared session/auth tool factory tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from umu_sdk.adapters.mcp.shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)


@pytest.fixture
def session_config():
    return SessionToolConfig(
        role="tch",
        role_label="讲师",
        tool_domain_hint="讲师端",
        login_success_suffix="可以调用讲师端工具",
        check_auth_success_suffix="讲师端 Tool",
        create_session_suggested_action="使用此 session_id 登录",
        create_session_with_password=False,
        isoformat_timestamps=True,
    )


@pytest.fixture
def ok_fn():
    def _ok(**kwargs):
        return {"success": True, **kwargs}
    return _ok


@pytest.fixture
def err_fn():
    def _err(**kwargs):
        return {"success": False, **kwargs}
    return _err


class TestMakeLoginTool:
    async def test_login_success(self, session_config, ok_fn, err_fn):
        client = MagicMock()
        client.login.return_value = "token123"
        client.auth.get_enterprise_id.return_value = "ent1"
        client.auth.get_enterprise_name.return_value = "企业"

        get_client = MagicMock(return_value=client)
        get_session_manager = MagicMock()

        tool = make_login_tool(session_config, get_client, get_session_manager, ok_fn, err_fn)
        assert tool.__name__ == "tch_login"
        result = await tool("user", "pass")

        assert result["success"] is True
        assert result["data"]["token"] == "token123"
        assert "可以调用讲师端工具" in result["suggested_action"]

    async def test_login_failure(self, session_config, ok_fn, err_fn):
        client = MagicMock()
        client.login.side_effect = Exception("密码错误")

        tool = make_login_tool(
            session_config, lambda _: client, lambda: None, ok_fn, err_fn
        )
        result = await tool("user", "pass")

        assert result["success"] is False
        assert result["error_code"] == "AUTH_FAILED"


class TestMakeCheckAuthTool:
    async def test_authenticated(self, session_config, ok_fn, err_fn):
        client = MagicMock()
        client.auth.is_authenticated.return_value = True
        client.auth.get_token.return_value = "token123"

        tool = make_check_auth_tool(session_config, lambda _: client, ok_fn, err_fn)
        assert tool.__name__ == "tch_check_auth"
        result = await tool()

        assert result["success"] is True
        assert result["data"]["is_authenticated"] is True

    async def test_not_authenticated(self, session_config, ok_fn, err_fn):
        client = MagicMock()
        client.auth.is_authenticated.return_value = False

        tool = make_check_auth_tool(session_config, lambda _: client, ok_fn, err_fn)
        result = await tool()

        assert result["success"] is False
        assert result["error_code"] == "NOT_AUTHENTICATED"


class TestMakeCreateSessionTool:
    async def test_create_session_without_password(self, session_config, ok_fn, err_fn):
        session = MagicMock()
        session.session_id = "sid1"
        session.username = "user"
        session.created_at.isoformat.return_value = "2026-06-19T10:00:00"

        sm = AsyncMock()
        sm.create_session.return_value = session

        tool = make_create_session_tool(session_config, lambda: sm, ok_fn, err_fn)
        assert tool.__name__ == "tch_create_session"
        result = await tool()

        assert result["success"] is True
        assert result["data"]["session_id"] == "sid1"

    async def test_create_session_with_password(self, ok_fn, err_fn):
        config = SessionToolConfig(
            role="stu",
            role_label="学员",
            tool_domain_hint="学员端",
            create_session_with_password=True,
        )
        session = MagicMock()
        session.session_id = "sid2"
        session.username = "user"
        session.created_at = "2026-06-19 10:00:00"

        sm = AsyncMock()
        sm.create_session.return_value = session

        tool = make_create_session_tool(config, lambda: sm, ok_fn, err_fn)
        assert tool.__name__ == "stu_create_session"
        result = await tool("user", "pass")

        assert result["success"] is True
        sm.create_session.assert_awaited_once_with("user", "pass")

    async def test_session_manager_not_initialized(self, session_config, ok_fn, err_fn):
        tool = make_create_session_tool(session_config, lambda: None, ok_fn, err_fn)
        result = await tool()

        assert result["success"] is False
        assert result["error_code"] == "SESSION_MANAGER_NOT_INITIALIZED"


class TestMakeListSessionsTool:
    async def test_list_sessions(self, session_config, ok_fn, err_fn):
        session = MagicMock()
        session.session_id = "sid1"
        session.username = "user"
        session.created_at.isoformat.return_value = "2026-06-19T10:00:00"
        session.last_used_at.isoformat.return_value = "2026-06-19T11:00:00"

        sm = AsyncMock()
        sm.list_sessions.return_value = [session]

        tool = make_list_sessions_tool(session_config, lambda: sm, ok_fn, err_fn)
        assert tool.__name__ == "tch_list_sessions"
        result = await tool()

        assert result["success"] is True
        assert result["data"]["count"] == 1


class TestMakeDestroySessionTool:
    async def test_destroy_success(self, session_config, ok_fn, err_fn):
        sm = AsyncMock()
        sm.destroy_session.return_value = True

        tool = make_destroy_session_tool(session_config, lambda: sm, ok_fn, err_fn)
        assert tool.__name__ == "tch_destroy_session"
        result = await tool("sid1")

        assert result["success"] is True
        assert result["data"]["destroyed"] is True

    async def test_destroy_not_found(self, session_config, ok_fn, err_fn):
        sm = AsyncMock()
        sm.destroy_session.return_value = False

        tool = make_destroy_session_tool(session_config, lambda: sm, ok_fn, err_fn)
        result = await tool("sid1")

        assert result["success"] is False
        assert result["error_code"] == "SESSION_NOT_FOUND"
