"""Shared access permission helper tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from umu_sdk.adapters.mcp.shared_access_permissions import (
    _add_obj_access_accounts,
    _build_access_account_payload,
    _cancel_all_assigned_permissions,
    _format_access_account,
    _get_obj_access_list,
    _get_obj_access_permission,
    _parse_access_permission_response,
    _permission_text,
    _remove_obj_access_accounts,
    _search_access_permission_account,
    _set_obj_access_permission,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


class TestPermissionText:
    def test_known_permissions(self):
        assert _permission_text(0) == "关闭"
        assert _permission_text(1) == "公开"
        assert _permission_text(2) == "企业内公开"
        assert _permission_text(3) == "指定账户"

    def test_unknown_permission(self):
        assert _permission_text(99) == "未知(99)"


class TestParseAccessPermissionResponse:
    def test_status_true(self):
        ok, data, err = _parse_access_permission_response({"status": True, "data": {"x": 1}})
        assert ok is True
        assert data == {"x": 1}
        assert err == ""

    def test_error_code_zero(self):
        ok, data, err = _parse_access_permission_response({"error_code": 0, "data": [1, 2]})
        assert ok is True
        assert data == [1, 2]
        assert err == ""

    def test_business_failure(self):
        ok, data, err = _parse_access_permission_response(
            {"status": False, "error": "无权限"}
        )
        assert ok is False
        assert data is None
        assert err == "无权限"

    def test_non_dict_response(self):
        ok, data, err = _parse_access_permission_response("bad")
        assert ok is False
        assert data is None
        assert err == "响应格式异常"


class TestSearchAccessPermissionAccount:
    def test_group_search(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "data": [
                {"id": "1", "account": "a@umu.cn", "account_type": "user", "is_exist": 1},
                {"id": "2", "account": "b@umu.cn", "account_type": "user", "is_exist": 0},
            ],
        }
        ok, accounts, err = _search_access_permission_account(
            mock_client, "g1", "group", "keyword"
        )
        assert ok is True
        assert err == ""
        assert len(accounts) == 1
        assert accounts[0]["account"] == "a@umu.cn"
        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert call.kwargs["data"]["group_id"] == "g1"
        assert call.kwargs["data"]["search_source"] == "access_permission"

    def test_program_search(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": []}
        ok, accounts, err = _search_access_permission_account(
            mock_client, "p1", "program", "keyword"
        )
        assert ok is True
        assert accounts == []
        call = mock_client.post.call_args
        assert call.kwargs["data"]["program_id"] == "p1"

    def test_unsupported_obj_type(self, mock_client):
        ok, accounts, err = _search_access_permission_account(
            mock_client, "x1", "invalid", "keyword"
        )
        assert ok is False
        assert accounts == []
        assert "不支持的 obj_type" in err
        mock_client.post.assert_not_called()


class TestSetObjAccessPermission:
    def test_group_permission(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": {"ok": 1}}
        data = _set_obj_access_permission(mock_client, "g1", "group", 3)
        assert data == {"ok": 1}
        call = mock_client.post.call_args
        assert call.kwargs["data"]["group_id"] == "g1"
        assert call.kwargs["data"]["access_permission"] == "3"

    def test_program_permission(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": {"ok": 1}}
        _set_obj_access_permission(mock_client, "p1", "program", 2)
        call = mock_client.post.call_args
        assert call.kwargs["data"]["program_id"] == "p1"
        assert call.kwargs["data"]["access_permission"] == "2"

    def test_unsupported_obj_type(self, mock_client):
        with pytest.raises(ValueError, match="不支持的 obj_type"):
            _set_obj_access_permission(mock_client, "x1", "invalid", 2)

    def test_business_error(self, mock_client):
        mock_client.post.return_value = {"status": False, "error": "失败"}
        with pytest.raises(RuntimeError, match="失败"):
            _set_obj_access_permission(mock_client, "g1", "group", 3)


class TestGetObjAccessPermission:
    def test_success(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {"selected_option": "2", "permission_option": ["0", "2", "3"]},
        }
        selected, options, detail = _get_obj_access_permission(mock_client, "g1", "group")
        assert selected == 2
        assert options == ["0", "2", "3"]
        assert detail["selected_option"] == "2"

    def test_invalid_selected(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {"selected_option": "invalid"},
        }
        selected, options, detail = _get_obj_access_permission(mock_client, "g1", "group")
        assert selected == -1


class TestGetObjAccessList:
    def test_success(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {
                "page_info": {"total": 1},
                "list": [
                    {"id": "1", "account": "a@umu.cn", "account_type": "user", "is_exist": 1}
                ],
            },
        }
        items, page_info = _get_obj_access_list(mock_client, "g1", "group", 1, 20)
        assert len(items) == 1
        assert items[0]["account"] == "a@umu.cn"
        assert page_info["total"] == 1


class TestAddRemoveObjAccessAccounts:
    def test_add(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": {"added": 1}}
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        data = _add_obj_access_accounts(mock_client, "g1", "group", accounts)
        assert data == {"added": 1}
        call = mock_client.post.call_args
        assert call.kwargs["data"]["obj_id"] == "g1"
        assert call.kwargs["data"]["obj_type"] == "group"
        payloads: list[dict[str, Any]] = __import__("json").loads(call.kwargs["data"]["accounts"])
        assert payloads[0]["type"] == 1

    def test_remove(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": {"removed": 1}}
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        data = _remove_obj_access_accounts(mock_client, "g1", "group", accounts)
        call = mock_client.post.call_args
        payloads: list[dict[str, Any]] = __import__("json").loads(call.kwargs["data"]["accounts"])
        assert payloads[0]["type"] == 2
        assert data == {"removed": 1}


class TestCancelAllAssignedPermissions:
    def test_success(self, mock_client):
        mock_client.post.return_value = {"status": True, "data": {"status": 1}}
        data = _cancel_all_assigned_permissions(mock_client, "g1", "group")
        assert data == {"status": 1}
        call = mock_client.post.call_args
        assert call.kwargs["data"]["obj_id"] == "g1"
        assert call.kwargs["data"]["obj_type"] == "group"


class TestBuildAccessAccountPayload:
    def test_user(self):
        payload = _build_access_account_payload(
            {"account": "a@umu.cn", "account_type": "user", "id": "1"}, 1
        )
        assert payload["type"] == 1
        assert payload["account_type"] == "user"
        assert payload["id"] == "1"
        assert "class_id" not in payload

    def test_class(self):
        payload = _build_access_account_payload(
            {"account": "classA", "account_type": "class", "id": "1", "class_id": "10"}, 2
        )
        assert payload["type"] == 2
        assert payload["class_id"] == "10"

    def test_class_fallback(self):
        payload = _build_access_account_payload(
            {"account": "classA", "account_type": "class", "id": "1"}, 1
        )
        assert payload["class_id"] == "1"


class TestFormatAccessAccount:
    def test_user(self):
        formatted = _format_access_account(
            {
                "id": "1",
                "account": "a@umu.cn",
                "account_type": "user",
                "is_exist": 1,
                "user_name": "A",
            }
        )
        assert formatted["account_type"] == "user"
        assert formatted["user_name"] == "A"

    def test_class(self):
        formatted = _format_access_account(
            {"id": "1", "account": "classA", "account_type": "class", "is_exist": 1}
        )
        assert formatted["class_name"] == "classA"
        assert formatted["class_id"] == "1"
