"""Unified course permission Skill tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills.builtin.course_permissions import (
    add_course_access_accounts,
    cancel_course_access_permissions,
    cancel_course_auto_close,
    get_course_access_list,
    get_course_access_permission,
    get_course_auto_close,
    remove_course_access_accounts,
    search_course_access_accounts,
    set_course_access_permission,
    set_course_auto_close,
)


@pytest.fixture
def ctx():
    mock = AsyncMock()
    mock.call_tool.return_value = {
        "success": True,
        "data": {"ok": True},
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }
    return mock


class TestCoursePermissionsSkills:
    async def test_set_course_access_permission(self, ctx):
        result = await set_course_access_permission(ctx, "g1", 2)
        assert result["success"] is True
        call = ctx.call_tool.call_args
        assert call.kwargs["server"] == "teacher"
        assert call.kwargs["tool"] == "tch_set_course_access_permission"
        assert call.kwargs["arguments"]["access_permission"] == 2

    async def test_search_course_access_accounts(self, ctx):
        await search_course_access_accounts(ctx, "g1", "key")
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_search_access_accounts"

    async def test_add_course_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await add_course_access_accounts(ctx, "g1", accounts)
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_add_course_access_accounts"

    async def test_remove_course_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await remove_course_access_accounts(ctx, "g1", accounts)
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_remove_course_access_accounts"

    async def test_cancel_course_access_permissions(self, ctx):
        await cancel_course_access_permissions(ctx, "g1")
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_cancel_all_assigned_permissions"

    async def test_get_course_access_permission(self, ctx):
        await get_course_access_permission(ctx, "g1")
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_get_course_access_permission"

    async def test_get_course_access_list(self, ctx):
        await get_course_access_list(ctx, "g1")
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_get_course_access_list"

    async def test_get_course_auto_close(self, ctx):
        result = await get_course_auto_close(ctx, "g1")
        assert result["success"] is True
        call = ctx.call_tool.call_args
        assert call.kwargs["server"] == "teacher"
        assert call.kwargs["tool"] == "tch_get_course_auto_close"
        assert call.kwargs["arguments"]["group_id"] == "g1"

    async def test_set_course_auto_close(self, ctx):
        result = await set_course_auto_close(ctx, "g1", "2026-06-30 10:00")
        assert result["success"] is True
        calls = ctx.call_tool.call_args_list
        assert calls[0].kwargs["tool"] == "tch_get_course_auto_close"
        assert calls[1].kwargs["tool"] == "tch_set_course_auto_close"
        assert calls[1].kwargs["arguments"]["group_id"] == "g1"
        assert calls[1].kwargs["arguments"]["close_time"] == "2026-06-30 10:00"

    async def test_cancel_course_auto_close(self, ctx):
        result = await cancel_course_auto_close(ctx, "g1")
        assert result["success"] is True
        calls = ctx.call_tool.call_args_list
        assert calls[0].kwargs["tool"] == "tch_get_course_auto_close"
        assert calls[1].kwargs["tool"] == "tch_cancel_course_auto_close"
        assert calls[1].kwargs["arguments"]["group_id"] == "g1"
        assert calls[1].kwargs["arguments"]["clear_tips"] is True
