"""Admin 分组管理工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_add_group_managers,
    adm_add_group_members,
    adm_create_group,
    adm_delete_groups,
    adm_get_group,
    adm_list_group_managers,
    adm_list_group_members,
    adm_remove_group_managers,
    adm_remove_group_members,
    adm_update_group,
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


def _make_user(umu_id: str, user_name: str = "", role_type: int = 2) -> dict:
    return {
        "umu_id": umu_id,
        "user_name": user_name,
        "user_name_letter": "",
        "area_code": "",
        "phone": "",
        "email": "",
        "login_name": "",
        "manage_permission": 1,
        "role_type": role_type,
    }


def _group_user_list_response(users: list[dict]) -> dict:
    return {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {
                "list_total_num": len(users),
                "total_page_num": 1,
                "current_page": 1,
                "size": len(users),
            },
            "list": users,
        },
    }


class TestCreateGroup:
    async def test_create_group(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"enterprise_group_id": "177155"},
        }

        with _auth_patch(mock_client):
            result = await adm_create_group(group_name="新产品组")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["group_id"] == "177155"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[1]["data"]["group_name"] == "新产品组"

    async def test_create_group_empty_name(self, mock_client):
        with _auth_patch(mock_client):
            result = await adm_create_group(group_name="   ")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "INVALID_GROUP_NAME"


class TestUpdateGroup:
    async def test_update_group(self, mock_client):
        mock_client.get.return_value = {
            "error_code": 0,
            "error_message": "",
            "data": {"status": 1},
        }

        with _auth_patch(mock_client):
            result = await adm_update_group(group_id="177155", group_name="重命名组")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["group_name"] == "重命名组"
        call_args = mock_client.get.call_args
        assert call_args[1]["params"]["group_id"] == "177155"
        assert call_args[1]["params"]["new_group_name"] == "重命名组"


class TestDeleteGroups:
    async def test_delete_groups(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [],
        }

        with _auth_patch(mock_client):
            result = await adm_delete_groups(group_ids="177155,177156")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["deleted_count"] == 2
        assert mock_client.post.call_count == 2


class TestGetGroup:
    async def test_get_group(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {
                "total": 1,
                "list": [
                    {
                        "id": "177155",
                        "group_name": "测试组",
                        "group_name_letter": "ceshizu",
                        "member_count": "3",
                        "umu_id": "17580402",
                        "create_time": 1781342214,
                        "managers": [{"user_name": "管理员", "email": "admin@example.com"}],
                        "creator": {"umu_id": "17580402", "user_name": "创建者", "manage_permission": 1},
                    }
                ],
            },
        }

        with _auth_patch(mock_client):
            result = await adm_get_group(group_id="177155")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["id"] == "177155"
        assert parsed["data"]["member_count"] == 3


class TestListGroupMembers:
    async def test_list_group_members(self, mock_client):
        mock_client.get.return_value = _group_user_list_response(
            [_make_user("20439812", "张三")]
        )

        with _auth_patch(mock_client):
            result = await adm_list_group_members(group_id="177155")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["members"][0]["umu_id"] == "20439812"


class TestListGroupManagers:
    async def test_list_group_managers(self, mock_client):
        mock_client.get.return_value = _group_user_list_response(
            [_make_user("20458620", "管理员", role_type=3)]
        )

        with _auth_patch(mock_client):
            result = await adm_list_group_managers(group_id="177155")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["managers"][0]["umu_id"] == "20458620"


class TestAddGroupMembers:
    async def test_add_group_members(self, mock_client):
        # 更新前读取成员/管理员，更新后再次读取校验
        mock_client.get.side_effect = [
            _group_user_list_response([_make_user("20439812", "张三")]),
            _group_user_list_response([]),
            _group_user_list_response(
                [
                    _make_user("20439812", "张三"),
                    _make_user("20439813", "用户A"),
                    _make_user("20439814", "用户B"),
                ]
            ),
            _group_user_list_response([]),
        ]
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [],
        }

        with _auth_patch(mock_client):
            result = await adm_add_group_members(
                group_id="177155", umu_ids="20439813,20439814"
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["member_count"] == 3

        post_data = mock_client.post.call_args[1]["data"]
        assert json.loads(post_data["member_id"]) == [
            "20439812",
            "20439813",
            "20439814",
        ]
        assert json.loads(post_data["manager_id"]) == []
        assert post_data["is_delete"] == "2"


class TestRemoveGroupMembers:
    async def test_remove_group_members(self, mock_client):
        mock_client.get.side_effect = [
            _group_user_list_response(
                [
                    _make_user("20439812", "张三"),
                    _make_user("20439813", "李四"),
                ]
            ),
            _group_user_list_response([]),
            _group_user_list_response([_make_user("20439813", "李四")]),
            _group_user_list_response([]),
        ]
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [],
        }

        with _auth_patch(mock_client):
            result = await adm_remove_group_members(
                group_id="177155", umu_ids="20439812"
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["removed_member_ids"] == ["20439812"]
        assert parsed["data"]["member_count"] == 1

        post_data = mock_client.post.call_args[1]["data"]
        assert json.loads(post_data["member_id"]) == ["20439813"]


class TestAddGroupManagers:
    async def test_add_group_managers(self, mock_client):
        mock_client.get.side_effect = [
            _group_user_list_response([_make_user("20439812", "张三")]),
            _group_user_list_response([_make_user("20458620", "管理员", role_type=3)]),
            _group_user_list_response([_make_user("20439812", "张三")]),
            _group_user_list_response(
                [
                    _make_user("20458620", "管理员", role_type=3),
                    _make_user("17580402", "管理员B", role_type=4),
                ]
            ),
        ]
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [],
        }

        with _auth_patch(mock_client):
            result = await adm_add_group_managers(
                group_id="177155", umu_ids="17580402"
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["manager_count"] == 2

        post_data = mock_client.post.call_args[1]["data"]
        assert json.loads(post_data["member_id"]) == ["20439812"]
        assert json.loads(post_data["manager_id"]) == ["20458620", "17580402"]


class TestRemoveGroupManagers:
    async def test_remove_group_managers(self, mock_client):
        mock_client.get.side_effect = [
            _group_user_list_response([_make_user("20439812", "张三")]),
            _group_user_list_response(
                [
                    _make_user("20458620", "管理员 A", role_type=3),
                    _make_user("17580402", "管理员 B", role_type=4),
                ]
            ),
            _group_user_list_response([_make_user("20439812", "张三")]),
            _group_user_list_response([_make_user("17580402", "管理员 B", role_type=4)]),
        ]
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": [],
        }

        with _auth_patch(mock_client):
            result = await adm_remove_group_managers(
                group_id="177155", umu_ids="20458620"
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["removed_manager_ids"] == ["20458620"]
        assert parsed["data"]["manager_count"] == 1

        post_data = mock_client.post.call_args[1]["data"]
        assert json.loads(post_data["manager_id"]) == ["17580402"]
