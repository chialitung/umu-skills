"""Admin course Skill tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills.builtin.admin_courses import (
    cancel_course_auto_close_admin,
    get_course_auto_close_admin,
    set_course_auto_close_admin,
)


@pytest.fixture
def ctx():
    mock = AsyncMock()
    mock.call_role_tool.return_value = {
        "success": True,
        "data": {"ok": True},
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }
    return mock


class TestAdminCourseAutoCloseSkills:
    async def test_get_course_auto_close_admin(self, ctx):
        result = await get_course_auto_close_admin(ctx, "g1")
        assert result["success"] is True
        ctx.call_role_tool.assert_awaited_once_with(
            role="teacher",
            operation="get_course_auto_close",
            arguments={"group_id": "g1"},
        )

    async def test_set_course_auto_close_admin(self, ctx):
        result = await set_course_auto_close_admin(ctx, "g1", "2026-06-30 10:00")
        assert result["success"] is True
        calls = ctx.call_role_tool.call_args_list
        assert calls[0].kwargs == {
            "role": "teacher",
            "operation": "get_course_auto_close",
            "arguments": {"group_id": "g1"},
        }
        assert calls[1].kwargs == {
            "role": "teacher",
            "operation": "set_course_auto_close",
            "arguments": {"group_id": "g1", "close_time": "2026-06-30 10:00"},
        }

    async def test_cancel_course_auto_close_admin(self, ctx):
        result = await cancel_course_auto_close_admin(ctx, "g1")
        assert result["success"] is True
        calls = ctx.call_role_tool.call_args_list
        assert calls[0].kwargs == {
            "role": "teacher",
            "operation": "get_course_auto_close",
            "arguments": {"group_id": "g1"},
        }
        assert calls[1].kwargs == {
            "role": "teacher",
            "operation": "cancel_course_auto_close",
            "arguments": {"group_id": "g1", "clear_tips": True},
        }
