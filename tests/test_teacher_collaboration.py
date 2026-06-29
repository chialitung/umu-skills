"""Teacher MCP 课程协同管理工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import MagicMock, patch


@contextmanager
def _patch_teacher_auth():
    """Patch teacher.py 的客户端."""
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"

    with ExitStack() as exit_stack:
        exit_stack.enter_context(
            patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=client)
        )
        exit_stack.enter_context(
            patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None)
        )
        yield client


def _coop_response(
    collaborators: list[dict[str, Any]] | None = None,
    creator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": True,
        "error_code": 0,
        "data": {
            "total": len(collaborators or []),
            "list": collaborators or [],
            "creator_info": creator or {"teacher_id": "1", "role_type": "creator", "teacher_name": "Owner"},
            "page_info": {"list_total_num": len(collaborators or []), "total_page_num": 1, "current_page": 1, "size": 20},
        },
    }


def _search_response(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": True,
        "error_code": 0,
        "data": accounts,
    }


class TestTchListCourseCollaborators:
    async def test_list_collaborators_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_collaborators

        resp = _coop_response(
            collaborators=[{
                "cooperation_info_id": "c1",
                "teacher_id": "t1",
                "role_type": "cooperator",
                "teacher_name": "Alice",
                "teacher_email": "a@example.com",
            }],
        )

        with _patch_teacher_auth() as client:
            client.get.return_value = resp
            result = json.loads(await tch_list_course_collaborators(group_id="g1"))

        assert result["success"] is True
        assert len(result["data"]["collaborators"]) == 1
        assert result["data"]["collaborators"][0]["role_label"] == "编辑者"


class TestTchSearchCollaboratorAccounts:
    async def test_search_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_search_collaborator_accounts

        resp = _search_response([{
            "id": "123",
            "umu_id": "u-123",
            "account": "a@example.com",
            "user_name": "Alice",
            "email": "a@example.com",
            "is_exist": 1,
        }])

        with _patch_teacher_auth() as client:
            client.post.return_value = resp
            result = json.loads(await tch_search_collaborator_accounts(group_id="g1", keyword="a@example.com"))

        assert result["success"] is True
        assert result["data"]["count"] == 1
        assert result["data"]["accounts"][0]["id"] == "123"


class TestTchInviteCourseCollaborator:
    async def test_invite_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_invite_course_collaborator

        search_resp = _search_response([{
            "id": "123",
            "umu_id": "u-123",
            "account": "a@example.com",
            "account_type": "user",
            "user_name": "Alice",
            "email": "a@example.com",
            "is_exist": 1,
        }])
        add_resp = {"status": True, "error_code": 0, "data": []}

        with _patch_teacher_auth() as client:
            client.post.side_effect = [search_resp, add_resp]
            result = json.loads(await tch_invite_course_collaborator(group_id="g1", keyword="a@example.com", role_type="editor"))

        assert result["success"] is True
        assert result["data"]["role_label"] == "编辑者"

    async def test_invite_ambiguous(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_invite_course_collaborator

        search_resp = _search_response([
            {"id": "1", "account": "a1@example.com", "user_name": "A1", "is_exist": 1},
            {"id": "2", "account": "a2@example.com", "user_name": "A2", "is_exist": 1},
        ])

        with _patch_teacher_auth() as client:
            client.post.return_value = search_resp
            result = json.loads(await tch_invite_course_collaborator(group_id="g1", keyword=" ambiguous", role_type="editor"))

        assert result["success"] is False
        assert result["error_code"] == "TCH_INVITE_COURSE_COLLABORATOR_ERROR"
        assert "找到多个匹配账号" in result["error_message"]
        assert result["next_action"] == "retry"


class TestTchUpdateCollaboratorRole:
    async def test_update_role_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_update_collaborator_role

        list_resp = _coop_response(
            collaborators=[{
                "cooperation_info_id": "c1",
                "teacher_id": "t1",
                "role_type": "operator",
                "teacher_name": "Alice",
                "teacher_email": "a@example.com",
            }],
        )
        add_resp = {"status": True, "error_code": 0, "data": []}

        with _patch_teacher_auth() as client:
            client.get.return_value = list_resp
            client.post.return_value = add_resp
            result = json.loads(await tch_update_collaborator_role(group_id="g1", cooperation_info_id="c1", role_type="viewer"))

        assert result["success"] is True
        assert result["data"]["new_role_label"] == "查看者"


class TestTchRemoveCourseCollaborator:
    async def test_remove_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_remove_course_collaborator

        resp = {"status": True, "error_code": 0, "data": {"result": 1}}

        with _patch_teacher_auth() as client:
            client.post.return_value = resp
            result = json.loads(await tch_remove_course_collaborator(group_id="g1", cooperation_info_id="c1"))

        assert result["success"] is True
        assert result["data"]["result"] == 1


class TestTchTransferCourseOwner:
    async def test_transfer_success(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_transfer_course_owner

        search_resp = _search_response([{
            "id": "999",
            "umu_id": "u-999",
            "account": "new@example.com",
            "account_type": "user",
            "user_name": "New Owner",
            "email": "new@example.com",
            "is_exist": 1,
        }])
        transfer_resp = {"error_code": 0, "error_message": "", "data": {"status": 1}}

        with _patch_teacher_auth() as client:
            client.post.side_effect = [search_resp, transfer_resp]
            result = json.loads(await tch_transfer_course_owner(group_id="g1", keyword="new@example.com"))

        assert result["success"] is True
        assert result["data"]["new_owner_id"] == "999"
