"""Admin 课程审核相关 MCP 工具测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_audit_course,
    adm_list_course_audit_records,
    adm_list_course_blacklist,
    adm_list_course_categories,
    adm_save_course_blacklist,
)


@pytest.fixture
def mock_client():
    """构造已认证的 mock UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def patch_get_client(mock_client):
    """注入 mock 客户端."""
    with patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client):
        yield


@pytest.mark.asyncio
async def test_adm_list_course_audit_records_success(patch_get_client, mock_client):
    """测试查询审核记录成功."""
    mock_client.get.return_value = {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": {
            "page_info": {
                "list_total_num": 1,
                "total_page_num": 1,
                "current_page": 1,
                "size": 20,
            },
            "list": [
                {
                    "group_id": "7330085",
                    "course_version_id": "v1",
                    "release_status": "2",
                    "release_time": 1781422727,
                    "teacher_id": "20438403",
                    "umu_id": "20440690",
                    "is_blacklist": 0,
                    "group_title": "测试课程",
                    "shareUrl": "https://m.umu.cn/course/?groupId=7330085",
                    "auditUrl": "https://m.umu.cn/model/groupAudit?groupId=7330085",
                    "user_name": "teacher",
                    "avatar": "",
                    "session_num": "1",
                    "participate_num": 2,
                    "like_num": 0,
                    "release_num": 5,
                    "reject_num": 0,
                    "current_reject_times": 0,
                    "release_activity": {},
                }
            ],
        },
    }

    result = await adm_list_course_audit_records(audit_status=0)
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["total"] == 1
    assert parsed["data"]["records"][0]["group_id"] == "7330085"
    assert parsed["data"]["records"][0]["title"] == "测试课程"
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_adm_list_course_audit_records_invalid_status(patch_get_client, mock_client):
    """测试传入非法审核状态."""
    result = await adm_list_course_audit_records(audit_status=5)
    parsed = json.loads(result)

    assert parsed["success"] is False
    assert parsed["error_code"] == "INVALID_AUDIT_STATUS"
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_adm_list_course_audit_records_with_owner_keywords(patch_get_client, mock_client):
    """测试通过拥有者关键词解析筛选."""
    mock_client.get.side_effect = [
        {
            "error_code": 0,
            "error_message": "",
            "data": {
                "page_info": {"list_total_num": 1, "total_page_num": 1, "current_page": 1, "size": 50},
                "list": [{"id": "20440690", "user_name": "teacher"}],
            },
        },
        {
            "status": True,
            "errno": 0,
            "error_code": 0,
            "error": "success",
            "data": {
                "page_info": {"list_total_num": 0, "total_page_num": 0, "current_page": 1, "size": 20},
                "list": [],
            },
        },
    ]

    result = await adm_list_course_audit_records(audit_status=0, owner_keywords="teacher")
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_adm_audit_course_approve_success(patch_get_client, mock_client):
    """测试通过审核成功."""
    mock_client.post.return_value = {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": [True],
    }

    result = await adm_audit_course(group_ids="7330085", action="approve")
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["action"] == "通过"
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args.kwargs["data"]["audit_status"] == "1"


@pytest.mark.asyncio
async def test_adm_audit_course_reject_with_blacklist(patch_get_client, mock_client):
    """测试拒绝审核并加入黑名单."""
    mock_client.post.return_value = {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": [True],
    }

    result = await adm_audit_course(
        group_ids="7330085",
        action="reject",
        reason="不符合规范",
        add_to_blacklist=True,
    )
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["action"] == "拒绝"
    call_args = mock_client.post.call_args
    assert call_args.kwargs["data"]["audit_status"] == "2"
    assert call_args.kwargs["data"]["is_add_black"] == "1"
    assert call_args.kwargs["data"]["desc"] == "不符合规范"


@pytest.mark.asyncio
async def test_adm_audit_course_revoke_success(patch_get_client, mock_client):
    """测试撤销提交成功."""
    mock_client.post.return_value = {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": [True],
    }

    result = await adm_audit_course(group_ids="7330085", action="revoke", reason="需要修改")
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["action"] == "撤销提交"
    call_args = mock_client.post.call_args
    assert call_args.kwargs["data"]["audit_status"] == "3"
    assert call_args.kwargs["data"]["desc"] == "需要修改"


@pytest.mark.asyncio
async def test_adm_audit_course_blacklist_only_on_reject(patch_get_client, mock_client):
    """测试非拒绝操作不能加黑名单."""
    result = await adm_audit_course(
        group_ids="7330085",
        action="approve",
        add_to_blacklist=True,
    )
    parsed = json.loads(result)

    assert parsed["success"] is False
    assert parsed["error_code"] == "BLACKLIST_ONLY_ON_REJECT"
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_adm_list_course_categories_success(patch_get_client, mock_client):
    """测试查询课程分类成功."""
    mock_client.get.return_value = {
        "error_code": 0,
        "error_message": "",
        "data": {
            "list": [
                {
                    "id": "-1",
                    "parent_id": "0",
                    "name": "暂无分类",
                    "name_letter": "zanwufenlei",
                    "name_i18n": "",
                    "show_index": "999",
                    "auth_type": "0",
                }
            ]
        },
    }

    result = await adm_list_course_categories()
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["total"] == 1
    assert parsed["data"]["categories"][0]["name"] == "暂无分类"


@pytest.mark.asyncio
async def test_adm_list_course_blacklist_success(patch_get_client, mock_client):
    """测试查询黑名单成功."""
    mock_client.get.return_value = {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {"list_total_num": 1, "total_page_num": 1, "current_page": 1, "size": 15},
            "list": [
                {
                    "user_name": "teacher",
                    "teacher_id": "20438403",
                    "student_id": "42878693",
                    "enterprise_id": "25105",
                    "email": "teacher@umu_aia.com",
                    "phone": "",
                    "login_name": "",
                    "umu_id": "20440690",
                    "source": "course_audit_reject",
                    "role_type": 2,
                    "release_num": 5,
                    "reject_num": 1,
                    "last_login_time": 1781422779,
                }
            ],
        },
    }

    result = await adm_list_course_blacklist()
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["total"] == 1
    assert parsed["data"]["blacklist"][0]["umu_id"] == "20440690"
    assert parsed["data"]["blacklist"][0]["role_type_text"] == "讲师"


@pytest.mark.asyncio
async def test_adm_save_course_blacklist_success(patch_get_client, mock_client):
    """测试黑名单写入成功."""
    mock_client.post.return_value = {
        "error_code": 0,
        "error_message": "",
        "data": {"status": 1},
    }

    result = await adm_save_course_blacklist(umu_id="20440690", action="add")
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["action"] == "加入"
    call_args = mock_client.post.call_args
    assert call_args.kwargs["data"]["umu_id"] == "20440690"
    assert call_args.kwargs["data"]["type"] == "1"


@pytest.mark.asyncio
async def test_adm_save_course_blacklist_remove(patch_get_client, mock_client):
    """测试移出黑名单成功."""
    mock_client.post.return_value = {
        "error_code": 0,
        "error_message": "",
        "data": {"status": 1},
    }

    result = await adm_save_course_blacklist(umu_id="20440690", action="remove")
    parsed = json.loads(result)

    assert parsed["success"] is True
    assert parsed["data"]["action"] == "移除"
    call_args = mock_client.post.call_args
    assert call_args.kwargs["data"]["type"] == "2"
