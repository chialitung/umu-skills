"""Admin 部门管理工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_add_department_members,
    adm_create_department,
    adm_delete_departments,
    adm_get_child_departments,
    adm_get_department,
    adm_get_department_tree,
    adm_list_department_members,
    adm_move_department_members,
    adm_remove_department_members,
    adm_sort_departments,
    adm_update_department,
)


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def _auth_patch(mock_client):
    """返回用于 patch _get_client 和 _require_auth 的上下文."""
    stack = ExitStack()
    stack.enter_context(
        patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client)
    )
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestGetDepartmentTree:
    async def test_get_department_tree(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "department_list": [
                    {
                        "department_id": "251103",
                        "parent_department_id": "0",
                        "level": "1",
                        "department_name": "A",
                        "show_index": "1",
                        "member_count": 3,
                        "managers": [],
                        "manage_permission": 1,
                        "child_path": [],
                    }
                ]
            },
        }

        with _auth_patch(mock_client):
            result = await adm_get_department_tree()

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        assert parsed["data"]["departments"][0]["department_id"] == "251103"
        mock_client.get.assert_called()


class TestGetDepartment:
    async def test_get_department(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "department_id": "251103",
                "parent_department_id": "0",
                "level": "1",
                "department_name": "A",
                "show_index": "1",
                "member_count": 3,
                "managers": [],
                "manage_permission": 1,
                "parent_path": [],
                "child_path": [],
            },
        }

        with _auth_patch(mock_client):
            result = await adm_get_department(department_id="251103")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["department_id"] == "251103"


class TestGetChildDepartments:
    async def test_get_child_departments(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "department_list": [
                    {
                        "department_id": "297479",
                        "parent_department_id": "251103",
                        "level": "2",
                        "department_name": "A_a",
                        "show_index": "1",
                        "member_count": 1,
                        "managers": [],
                        "manage_permission": 1,
                    }
                ]
            },
        }

        with _auth_patch(mock_client):
            result = await adm_get_child_departments(department_id="251103")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        assert parsed["data"]["departments"][0]["department_id"] == "297479"


class TestListDepartmentMembers:
    async def test_list_department_members(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "page_info": {
                    "list_total_num": 1,
                    "total_page_num": 1,
                    "current_page": 1,
                    "size": 15,
                },
                "list": [
                    {
                        "user_info": {
                            "umu_id": "20439812",
                            "user_name": "张三",
                            "email": "zhangsan@example.com",
                        },
                        "number": "10001",
                        "role_type": "1",
                        "member_id": 327926038,
                    }
                ],
            },
        }

        with _auth_patch(mock_client):
            result = await adm_list_department_members(department_id="251103")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["members"][0]["umu_id"] == "20439812"


class TestCreateDepartment:
    async def test_create_department(self, mock_client):
        mock_client.post.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1, "desc": "success", "department_id": "297492"},
        }

        with _auth_patch(mock_client):
            result = await adm_create_department(
                department_name="新产品线",
                parent_department_id="251103",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["department_id"] == "297492"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[1]["data"]["department_name"] == "新产品线"
        assert call_args[1]["data"]["parent_department_id"] == "251103"

    async def test_create_department_empty_name(self, mock_client):
        with _auth_patch(mock_client):
            result = await adm_create_department(department_name="   ")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "INVALID_DEPARTMENT_NAME"


class TestUpdateDepartment:
    async def test_update_department(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "department_id": "251103",
                "department_name": "A",
                "parent_department_id": "0",
                "managers": [],
            },
        }
        mock_client.post.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1, "desc": "success"},
        }

        with _auth_patch(mock_client):
            result = await adm_update_department(
                department_id="251103",
                department_name="A_新",
                manager_umu_ids="20458616",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["department_id"] == "251103"

    async def test_update_department_no_fields(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "department_id": "251103",
                "department_name": "A",
                "parent_department_id": "0",
                "managers": [],
            },
        }

        with _auth_patch(mock_client):
            result = await adm_update_department(department_id="251103")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "NO_UPDATE_FIELDS"


class TestSortDepartments:
    async def test_sort_departments(self, mock_client):
        mock_client.post.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1},
        }

        with _auth_patch(mock_client):
            result = await adm_sort_departments(
                department_orders='[{"department_id":"251103","index":1}]',
            )

        parsed = json.loads(result)
        assert parsed["success"] is True

    async def test_sort_departments_invalid_json(self, mock_client):
        with _auth_patch(mock_client):
            result = await adm_sort_departments(department_orders="not-json")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "INVALID_SORT_FORMAT"


class TestAddDepartmentMembers:
    async def test_add_department_members(self, mock_client):
        mock_client.post.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1, "desc": "success"},
        }

        with _auth_patch(mock_client):
            result = await adm_add_department_members(
                department_id="251103",
                umu_ids="20439812,20439813",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["added_count"] == 2


class TestMoveDepartmentMembers:
    async def test_move_department_members(self, mock_client):
        mock_client.post.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1, "desc": "success"},
        }

        with _auth_patch(mock_client):
            result = await adm_move_department_members(
                umu_ids="20439812",
                department_ids="251104",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["moved_count"] == 1


class TestRemoveDepartmentMembers:
    async def test_remove_department_members(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1, "desc": "success"},
        }

        with _auth_patch(mock_client):
            result = await adm_remove_department_members(member_ids="327926038")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["removed_count"] == 1


class TestDeleteDepartments:
    async def test_delete_departments_single(self, mock_client):
        """单个部门删除成功."""
        mock_client.get.side_effect = [
            # 第一次调用：查询部门层级
            {
                "error_code": 0,
                "error_message": "",
                "data": {
                    "department_id": "251105",
                    "department_name": "TestDept",
                    "level": "2",
                },
            },
            # 第二次调用：删除部门
            {
                "error_code": 0,
                "error_message": "",
                "data": {"status": 1},
            },
        ]

        with _auth_patch(mock_client):
            result = await adm_delete_departments(department_ids="251105")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["deleted_count"] == 1
        assert parsed["data"]["successful_department_ids"] == ["251105"]
        assert parsed["data"]["failed_departments"] == []

    async def test_delete_departments_sorted_deepest_first(self, mock_client):
        """多个部门时按层级从深到浅删除."""
        mock_client.get.side_effect = [
            # 查询父部门 level=2
            {
                "error_code": 0,
                "error_message": "",
                "data": {
                    "department_id": "251104",
                    "department_name": "Parent",
                    "level": "2",
                },
            },
            # 查询子部门 level=3
            {
                "error_code": 0,
                "error_message": "",
                "data": {
                    "department_id": "297497",
                    "department_name": "Child",
                    "level": "3",
                },
            },
            # 删除子部门
            {"error_code": 0, "error_message": "", "data": {"status": 1}},
            # 删除父部门
            {"error_code": 0, "error_message": "", "data": {"status": 1}},
        ]

        with _auth_patch(mock_client):
            result = await adm_delete_departments(department_ids="251104,297497")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["deleted_count"] == 2

        # 验证调用顺序：先删深层 (297497)，再删浅层 (251104)
        delete_calls = [
            call for call in mock_client.get.call_args_list
            if "delete" in str(call.args[0])
        ]
        assert len(delete_calls) == 2
        assert delete_calls[0].kwargs["params"]["department_id"] == "297497"
        assert delete_calls[1].kwargs["params"]["department_id"] == "251104"

    async def test_delete_departments_partial_failure(self, mock_client):
        """部分成功、部分失败时返回失败详情."""
        mock_client.get.side_effect = [
            # 查询两个部门层级（level 相同）
            {
                "error_code": 0,
                "error_message": "",
                "data": {"department_id": "251104", "level": "2"},
            },
            {
                "error_code": 0,
                "error_message": "",
                "data": {"department_id": "251105", "level": "2"},
            },
            # 删除第一个成功
            {"error_code": 0, "error_message": "", "data": {"status": 1}},
            # 删除第二个失败（有子部门）
            {
                "error_code": 1,
                "error_message": "该部门包含子部门，无法删除",
                "data": {},
            },
        ]

        with _auth_patch(mock_client):
            result = await adm_delete_departments(department_ids="251104,251105")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["deleted_count"] == 1
        assert parsed["data"]["successful_department_ids"] == ["251104"]
        assert len(parsed["data"]["failed_departments"]) == 1
        failed = parsed["data"]["failed_departments"][0]
        assert failed["department_id"] == "251105"
        assert failed["reason"] == "has_members_or_children"

    async def test_delete_departments_not_found(self, mock_client):
        """部门不存在时仍尝试删除并归类失败原因."""
        mock_client.get.side_effect = [
            # 查询部门不存在
            {
                "error_code": 1,
                "error_message": "部门不存在",
                "data": {},
            },
            # 删除也失败
            {
                "error_code": 1,
                "error_message": "找不到该部门",
                "data": {},
            },
        ]

        with _auth_patch(mock_client):
            result = await adm_delete_departments(department_ids="999999")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["data"]["deleted_count"] == 0
        failed = parsed["data"]["failed_departments"][0]
        assert failed["department_id"] == "999999"
        assert failed["reason"] == "not_found"

    async def test_delete_departments_all_fail(self, mock_client):
        """全部失败时返回错误信封."""
        mock_client.get.side_effect = [
            {"error_code": 0, "error_message": "", "data": {"department_id": "251105", "level": "2"}},
            {"error_code": 1, "error_message": "网络异常", "data": {}},
        ]

        with _auth_patch(mock_client):
            result = await adm_delete_departments(department_ids="251105")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "DELETE_DEPARTMENTS_FAILED"
        assert parsed["data"]["deleted_count"] == 0
