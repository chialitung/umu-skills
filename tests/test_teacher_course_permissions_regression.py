"""Teacher 课程权限工具重构回归测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import os
import tempfile

from umu_sdk.adapters.mcp.teacher import (
    tch_add_course_access_accounts,
    tch_cancel_all_assigned_permissions,
    tch_export_course_permissions,
    tch_export_program_permissions,
    tch_get_course_access_list,
    tch_get_course_access_permission,
    tch_remove_course_access_accounts,
    tch_search_access_accounts,
    tch_set_course_access_permission,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))
    return stack


class TestCourseAccessPermissionRegression:
    async def test_set_course_access_permission(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"group_id": "123", "access_permission": "3"},
        }
        with _auth_patch(mock_client):
            result = json.loads(await tch_set_course_access_permission("123", 3))
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
            result = json.loads(await tch_get_course_access_permission("123"))
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
            result = json.loads(await tch_get_course_access_list("123"))
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
            result = json.loads(await tch_search_access_accounts("123", "zhangsan"))
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
            result = json.loads(await tch_add_course_access_accounts("123", accounts))
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
            result = json.loads(await tch_remove_course_access_accounts("123", accounts))
        assert result["success"] is True
        assert result["data"]["removed"] == 1

    async def test_cancel_all_assigned_permissions(self, mock_client):
        mock_client.post.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"status": 1},
        }
        with _auth_patch(mock_client):
            result = json.loads(await tch_cancel_all_assigned_permissions("123"))
        assert result["success"] is True

    async def test_export_course_permissions(self, mock_client, tmp_path):
        def _mock_get(url: str, params: dict | None = None, **kwargs):
            if "/api/group/getgrouplist" in url:
                return {
                    "status": True,
                    "data": {
                        "page_info": {"list_total_num": 2},
                        "list": [
                            {"groupInfo": {"id": "1", "title": "Course 1", "access_code": "abc"}},
                            {"groupInfo": {"id": "2", "title": "Course 2", "access_code": "def"}},
                        ],
                    },
                }
            if "/api/group/getAccessPermissionOption" in url:
                group_id = params.get("obj_id") if params else None
                selected = "3" if group_id == "1" else "2"
                return {
                    "status": True,
                    "data": {
                        "selected_option": selected,
                        "permission_option": ["2", "3", "0"],
                    },
                }
            if "/api/manage/getcourseaccesslist" in url:
                return {
                    "status": True,
                    "data": {
                        "page_info": {"list_total_num": 1},
                        "list": [{
                            "id": "100",
                            "account": "user@umu.cn",
                            "account_type": "user",
                            "is_exist": 1,
                        }],
                    },
                }
            return {"status": False, "error": f"unexpected url: {url}"}

        mock_client.get.side_effect = _mock_get
        output_path = str(tmp_path / "course_permissions.xlsx")

        with _auth_patch(mock_client):
            result = json.loads(await tch_export_course_permissions(output_path=output_path))

        assert result["success"] is True
        assert result["data"]["file_path"] == output_path
        assert result["data"]["total_courses"] == 2
        # Course 1 展开 1 条授权记录，Course 2 企业内公开占 1 行
        assert result["data"]["total_records"] == 2
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

    async def test_export_program_permissions(self, mock_client, tmp_path):
        def _mock_get(url: str, params: dict | None = None, **kwargs):
            if "/api/program/getlist" in url:
                return {
                    "status": True,
                    "data": {
                        "page_info": {"list_total_num": 2},
                        "list": [
                            {"program_id": "1", "program_title": "Program 1", "access_code": "abc"},
                            {"program_id": "2", "program_title": "Program 2", "access_code": "def"},
                        ],
                    },
                }
            if "/api/group/getAccessPermissionOption" in url:
                program_id = params.get("obj_id") if params else None
                selected = "3" if program_id == "1" else "2"
                return {
                    "status": True,
                    "data": {
                        "selected_option": selected,
                        "permission_option": ["2", "3", "0"],
                    },
                }
            if "/api/manage/getcourseaccesslist" in url:
                return {
                    "status": True,
                    "data": {
                        "page_info": {"list_total_num": 1},
                        "list": [{
                            "id": "100",
                            "account": "user@umu.cn",
                            "account_type": "user",
                            "is_exist": 1,
                        }],
                    },
                }
            return {"status": False, "error": f"unexpected url: {url}"}

        mock_client.get.side_effect = _mock_get
        output_path = str(tmp_path / "program_permissions.xlsx")

        with _auth_patch(mock_client):
            result = json.loads(await tch_export_program_permissions(output_path=output_path))

        assert result["success"] is True
        assert result["data"]["file_path"] == output_path
        assert result["data"]["total_programs"] == 2
        # Program 1 展开 1 条授权记录，Program 2 企业内公开占 1 行
        assert result["data"]["total_records"] == 2
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

