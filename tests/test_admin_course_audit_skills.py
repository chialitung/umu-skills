"""Admin 课程审核 Skill 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from umu_sdk.skills.builtin.admin_course_audit import (
    audit_course,
    list_course_audit_records,
    list_course_blacklist,
    list_course_categories,
    manage_course_blacklist,
)


@pytest.fixture
def mock_ctx():
    """构造 mock SkillContext."""
    ctx = MagicMock()
    ctx.call_role_tool = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_list_course_audit_records_skill(mock_ctx):
    """测试 list_course_audit_records Skill."""
    mock_ctx.call_role_tool.return_value = {
        "success": True,
        "data": {"records": [], "total": 0},
        "error_code": "",
        "error_message": "",
    }

    result = await list_course_audit_records(
        ctx=mock_ctx,
        audit_status=0,
        course_keywords="AI",
    )

    assert result["success"] is True
    assert result["data"]["total"] == 0
    mock_ctx.call_role_tool.assert_awaited_once_with(
        role="admin",
        operation="list_course_audit_records",
        arguments={
            "audit_status": 0,
            "page": 1,
            "page_size": 20,
            "fetch_all": False,
            "sort_field": "submit_time",
            "sort_order": "desc",
            "filter_last_passed": False,
            "course_keywords": "AI",
        },
    )


@pytest.mark.asyncio
async def test_audit_course_skill(mock_ctx):
    """测试 audit_course Skill."""
    mock_ctx.call_role_tool.return_value = {
        "success": True,
        "data": {"group_ids": "7330085", "action": "通过"},
        "error_code": "",
        "error_message": "",
    }

    result = await audit_course(
        ctx=mock_ctx,
        group_ids="7330085",
        action="approve",
    )

    assert result["success"] is True
    mock_ctx.call_role_tool.assert_awaited_once_with(
        role="admin",
        operation="audit_course",
        arguments={"group_ids": "7330085", "action": "approve"},
    )


@pytest.mark.asyncio
async def test_list_course_categories_skill(mock_ctx):
    """测试 list_course_categories Skill."""
    mock_ctx.call_role_tool.return_value = {
        "success": True,
        "data": {"categories": [], "total": 0},
        "error_code": "",
        "error_message": "",
    }

    result = await list_course_categories(ctx=mock_ctx)

    assert result["success"] is True
    mock_ctx.call_role_tool.assert_awaited_once_with(
        role="admin",
        operation="list_course_categories",
        arguments={"is_with_course_num": False},
    )


@pytest.mark.asyncio
async def test_list_course_blacklist_skill(mock_ctx):
    """测试 list_course_blacklist Skill."""
    mock_ctx.call_role_tool.return_value = {
        "success": True,
        "data": {"blacklist": [], "total": 0},
        "error_code": "",
        "error_message": "",
    }

    result = await list_course_blacklist(ctx=mock_ctx)

    assert result["success"] is True
    mock_ctx.call_role_tool.assert_awaited_once_with(
        role="admin",
        operation="list_course_blacklist",
        arguments={"page": 1, "page_size": 15, "fetch_all": False},
    )


@pytest.mark.asyncio
async def test_manage_course_blacklist_skill(mock_ctx):
    """测试 manage_course_blacklist Skill."""
    mock_ctx.call_role_tool.return_value = {
        "success": True,
        "data": {"umu_id": "20440690", "action": "加入"},
        "error_code": "",
        "error_message": "",
    }

    result = await manage_course_blacklist(
        ctx=mock_ctx,
        umu_id="20440690",
        action="add",
    )

    assert result["success"] is True
    mock_ctx.call_role_tool.assert_awaited_once_with(
        role="admin",
        operation="save_course_blacklist",
        arguments={"umu_id": "20440690", "action": "add"},
    )


@pytest.mark.asyncio
async def test_list_course_audit_records_skill_failure(mock_ctx):
    """测试 Skill 失败时返回统一信封."""
    mock_ctx.call_role_tool.return_value = {
        "success": False,
        "data": None,
        "error_code": "NOT_AUTHENTICATED",
        "error_message": "未登录",
    }

    result = await list_course_audit_records(
        ctx=mock_ctx,
        audit_status=0,
    )

    assert result["success"] is False
    assert result["error_code"] == "NOT_AUTHENTICATED"
    assert result["next_action"] == "retry"
