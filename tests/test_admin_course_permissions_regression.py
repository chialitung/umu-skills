"""Admin 课程权限工具重构回归测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_add_course_access_accounts,
    adm_cancel_all_assigned_permissions,
    adm_get_course_access_list,
    adm_get_course_access_permission,
    adm_remove_course_access_accounts,
    adm_search_access_accounts,
    adm_set_course_access_permission,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestCourseAccessPermissionRegression:
    async def test_set_course_access_permission(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"group_id": "123", "access_permission": "3"},
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_set_course_access_permission("123", 3))
        assert result["success"] is True
        assert result["data"]["access_permission"] == 3
        assert result["data"]["permission_text"] == "指定账户"

    async def test_get_course_access_permission(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {
                "permission_option": ["2", "3", "0"],
                "selected_option": "2",
            },
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_get_course_access_permission("123"))
        assert result["success"] is True
        assert result["data"]["access_permission"] == 2

    async def test_get_course_access_list(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 1, "current_page": 1, "size": 20},
                "list": [{
                    "id": "1",
                    "account": "zhangsan@umu.cn",
                    "account_type": "user",
                    "is_exist": 1,
                }],
            },
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_get_course_access_list("123"))
        assert result["success"] is True
        assert len(result["data"]["list"]) == 1

    async def test_search_access_accounts(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [{
                "id": "1",
                "account": "zhangsan@umu.cn",
                "account_type": "user",
                "is_exist": 1,
            }],
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_search_access_accounts("123", "zhangsan"))
        assert result["success"] is True
        assert result["data"]["total"] == 1

    async def test_add_course_access_accounts(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"total_num": 1, "success_num": 1, "fail_num": 0, "fail_list": []},
        }
        accounts = [{"account": "zhangsan@umu.cn", "account_type": "user", "id": "1"}]
        with _auth_patch(mock_client):
            result = json.loads(await adm_add_course_access_accounts("123", accounts))
        assert result["success"] is True
        assert result["data"]["added"] == 1

    async def test_remove_course_access_accounts(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"total_num": 1, "success_num": 1, "fail_num": 0, "fail_list": []},
        }
        accounts = [{"account": "zhangsan@umu.cn", "account_type": "user", "id": "1"}]
        with _auth_patch(mock_client):
            result = json.loads(await adm_remove_course_access_accounts("123", accounts))
        assert result["success"] is True
        assert result["data"]["removed"] == 1

    async def test_cancel_all_assigned_permissions(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"status": 1},
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_cancel_all_assigned_permissions("123"))
        assert result["success"] is True
