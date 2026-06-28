"""显式角色斜杠入口测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from umu_sdk.skills.decorators import SkillContext
from umu_sdk.skills.mcp_client import ToolCallResult
from umu_sdk.skills.slash._runner import run_umu_command, select_target
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


class TestAutoCloseDispatch:
    def test_set_course_auto_close_with_iso_datetime(self):
        target = select_target("使用teacher权限设置课程 7339916 的自动关闭时间为 2028-05-21 12:30")
        assert target is not None
        assert target.skill_name == "set_course_auto_close"
        assert target.capability == "teacher"
        assert target.arguments == {"group_id": "7339916", "close_time": "2028-05-21 12:30"}
        assert target.missing_args == []

    def test_set_course_auto_close_with_slash_datetime(self):
        target = select_target("把课程7339916的关闭时间改成2028/05/21 12:30")
        assert target is not None
        assert target.skill_name == "set_course_auto_close"
        assert target.arguments == {"group_id": "7339916", "close_time": "2028/05/21 12:30"}

    def test_set_course_auto_close_missing_time(self):
        target = select_target("设置课程7339916的自动关闭")
        assert target is not None
        assert target.skill_name == "set_course_auto_close"
        assert target.arguments == {"group_id": "7339916"}
        assert "close_time" in target.missing_args

    def test_cancel_course_auto_close(self):
        target = select_target("取消课程 7339916 的自动关闭")
        assert target is not None
        assert target.skill_name == "cancel_course_auto_close"
        assert target.arguments == {"group_id": "7339916"}

    def test_get_course_auto_close(self):
        target = select_target("查询课程 7339916 的自动关闭时间")
        assert target is not None
        assert target.skill_name == "get_course_auto_close"
        assert target.arguments == {"group_id": "7339916"}

    def test_auto_close_does_not_match_access_permission(self):
        target = select_target("设置课程 7339916 的访问权限为企业内公开")
        assert target is None or target.skill_name != "set_course_auto_close"

    def test_auto_close_does_not_match_enrollment(self):
        target = select_target("开启课程 7339916 的报名")
        assert target is None or "auto_close" not in target.skill_name


__all__ = ["TestUmuTeacher", "TestAutoCloseDispatch"]
