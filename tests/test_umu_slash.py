"""/umu 斜杠命令测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from umu_sdk.skills.decorators import SkillContext
from umu_sdk.skills.mcp_client import ToolCallResult
from umu_sdk.skills.slash._runner import run_umu_command


@pytest.fixture
def mock_mcp():
    mcp = MagicMock()
    mcp.list_servers.return_value = ["teacher", "student", "admin"]
    mcp.call_tool = AsyncMock(
        return_value=ToolCallResult(
            success=True,
            data={"courses": []},
            error_code="",
            error_message="",
        )
    )
    return mcp


@pytest.fixture
def ctx(mock_mcp):
    return SkillContext(
        mcp=mock_mcp,
        skill_name="umu",
        session_state={},
    )


class TestRoleConfirmation:
    @pytest.mark.asyncio
    async def test_ambiguous_intent_asks_confirmation(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher", "admin"],
        ):
            result = await run_umu_command(ctx, "帮我做点事")

        assert result["success"] is False
        assert result["error_code"] == "NEEDS_ROLE_CONFIRMATION"
        assert result["next_action"] == "needs_user_input"
        assert "teacher" in result["error_message"]
        assert "admin" in result["error_message"]


class TestFallback:
    @pytest.mark.asyncio
    async def test_create_course_fallback_to_admin(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher", "admin"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["admin"],
        ), patch(
            "umu_sdk.skills.slash._runner.load_env_credentials",
            return_value=("admin@umu.cn", "secret"),
        ):
            result = await run_umu_command(ctx, "创建课程 titledemo")

        assert result["resolved_role"] == "admin"
        assert "teacher 角色未配置" in result.get("fallback_reason", "")
        # 缺少 scorm_resource_id
        assert result["error_code"] == "MISSING_REQUIRED_ARGUMENTS"
        assert "scorm_resource_id" in result["data"]["missing_args"]

    @pytest.mark.asyncio
    async def test_preferred_role_admin_used(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher", "admin"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher", "admin"],
        ), patch(
            "umu_sdk.skills.slash._runner.load_env_credentials",
            return_value=("admin@umu.cn", "secret"),
        ):
            result = await run_umu_command(
                ctx, "创建课程 titledemo", default_role="admin"
            )

        assert result["resolved_role"] == "admin"
        # 显式指定 admin 且 admin 可用，不是 fallback
        assert "fallback_reason" not in result


class TestExecution:
    @pytest.mark.asyncio
    async def test_list_my_courses_executes_teacher(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ):
            result = await run_umu_command(ctx, "列出我的课程")

        assert result["success"] is True
        assert result["resolved_role"] == "teacher"
        ctx.mcp.call_tool.assert_awaited()
        call = ctx.mcp.call_tool.await_args
        assert call.kwargs["server"] == "teacher"
        assert call.kwargs["tool"] == "tch_list_created_courses"

    @pytest.mark.asyncio
    async def test_admin_fallback_login_to_teacher(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher", "admin"]
        ctx.mcp.call_tool = AsyncMock(
            side_effect=[
                ToolCallResult(success=True, data={"token": "x"}),
                ToolCallResult(success=True, data={"courses": []}),
            ]
        )
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["admin"],
        ), patch(
            "umu_sdk.skills.slash._runner.load_env_credentials",
            return_value=("admin@umu.cn", "secret"),
        ):
            result = await run_umu_command(ctx, "列出我的课程")

        assert result["success"] is True
        assert result["resolved_role"] == "admin"
        # 第一次调用应为使用 admin 凭据登录 teacher
        calls = ctx.mcp.call_tool.await_args_list
        assert calls[0].kwargs["server"] == "teacher"
        assert calls[0].kwargs["tool"] == "adm_login"
        assert calls[0].kwargs["arguments"]["username"] == "admin@umu.cn"
        assert calls[1].kwargs["server"] == "teacher"
        assert calls[1].kwargs["tool"] == "tch_list_created_courses"


class TestMissingArgs:
    @pytest.mark.asyncio
    async def test_create_course_missing_args(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ):
            result = await run_umu_command(ctx, "创建课程")

        assert result["success"] is False
        assert result["error_code"] == "MISSING_REQUIRED_ARGUMENTS"
        assert "title" in result["data"]["missing_args"]
        assert "scorm_resource_id" in result["data"]["missing_args"]


class TestSessionState:
    @pytest.mark.asyncio
    async def test_last_role_updated(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ):
            await run_umu_command(ctx, "列出我的课程")

        assert ctx.session_state["last_role"] == "teacher"
        assert "remembered_role" not in ctx.session_state

    @pytest.mark.asyncio
    async def test_remember_choice(self, ctx):
        ctx.mcp.list_servers.return_value = ["teacher"]
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ):
            await run_umu_command(
                ctx, "列出我的课程", remember_choice=True
            )

        assert ctx.session_state["last_role"] == "teacher"
        assert ctx.session_state["remembered_role"] == "teacher"


__all__ = ["TestRoleConfirmation"]
