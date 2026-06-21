"""显式角色斜杠入口测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from umu_sdk.skills.decorators import SkillContext
from umu_sdk.skills.mcp_client import ToolCallResult
from umu_sdk.skills.slash._runner import run_umu_command
from umu_sdk.skills.slash.umu_admin import umu_admin
from umu_sdk.skills.slash.umu_student import umu_student
from umu_sdk.skills.slash.umu_teacher import umu_teacher


@pytest.fixture
def ctx():
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
    return SkillContext(
        mcp=mcp,
        skill_name="test",
        session_state={},
    )


class TestUmuTeacher:
    @pytest.mark.asyncio
    async def test_default_role_teacher(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ):
            result = await umu_teacher(ctx, "列出我的课程")

        assert result["resolved_role"] == "teacher"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_teacher_fallback_to_admin(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["admin"],
        ), patch(
            "umu_sdk.skills.slash._runner.load_env_credentials",
            return_value=("admin@umu.cn", "secret"),
        ):
            result = await umu_teacher(ctx, "创建课程 titledemo")

        assert result["resolved_role"] == "admin"
        assert "teacher 角色未配置" in result.get("fallback_reason", "")


class TestUmuStudent:
    @pytest.mark.asyncio
    async def test_default_role_student(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["student"],
        ):
            result = await umu_student(ctx, "报名 enroll_id=123")

        assert result["resolved_role"] == "student"

    @pytest.mark.asyncio
    async def test_student_fallback_to_teacher(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["teacher"],
        ), patch(
            "umu_sdk.skills.slash._runner.load_env_credentials",
            return_value=("teacher@umu.cn", "secret"),
        ):
            result = await umu_student(ctx, "报名 enroll_id=123")

        assert result["resolved_role"] == "teacher"
        assert "student 角色未配置" in result.get("fallback_reason", "")


class TestUmuAdmin:
    @pytest.mark.asyncio
    async def test_default_role_admin(self, ctx):
        with patch(
            "umu_sdk.skills.slash._runner.get_configured_roles",
            return_value=["admin"],
        ):
            result = await umu_admin(ctx, "查看企业课程")

        assert result["resolved_role"] == "admin"


class TestRunUmuCommandSignature:
    def test_run_umu_command_accepts_default_role(self):
        # 仅做静态签名检查，确保入口函数可以传入 default_role
        import inspect

        sig = inspect.signature(run_umu_command)
        assert "default_role" in sig.parameters
        assert "remember_choice" in sig.parameters


__all__ = ["TestUmuTeacher"]
