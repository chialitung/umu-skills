"""Admin 学习项目查询工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import adm_list_learning_programs


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def sample_program():
    """单条学习项目样本."""
    return {
        "id": "358710",
        "creater_id": "15797500",
        "program_title": "IPD 训练营",
        "desc": "",
        "head_img": "https://example.com/cover.png",
        "ctime": "1780897214",
        "access_permission": "2",
        "create_time": "2026-06-08 13:40:14",
        "username": "Nancy Wang",
        "umu_id": "15797500",
        "share_url": "https://m.umu.cn/program/1vjE0723",
        "access_code": "crj556",
        "group_num": "1",
        "participate_num": 1,
        "partticipate_num": 1,
        "assignment_count": "0",
        "module_num": "1",
        "enterprise_groups": ["组1"],
        "enterprise_departments": ["部门1"],
        "tags": ["标签1"],
        "is_in_program_lib": 0,
        "category_name": ["分类1-子分类"],
        "enterprise_id": "11018",
    }


@pytest.fixture
def single_page_response(sample_program):
    """单页成功响应."""
    return {
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
            "list": [sample_program],
        },
    }


def _auth_patch(mock_client):
    """返回用于 patch _get_client 和 _require_auth 的上下文."""
    stack = ExitStack()
    stack.enter_context(
        patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client)
    )
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestAdmListLearningPrograms:
    """测试 adm_list_learning_programs 工具."""

    async def test_basic_query(self, mock_client, single_page_response):
        """基础查询应返回标准化项目列表."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = json.loads(await adm_list_learning_programs())

        assert result["success"] is True
        assert result["data"]["total"] == 1
        assert len(result["data"]["programs"]) == 1
        program = result["data"]["programs"][0]
        assert program["program_id"] == "358710"
        assert program["title"] == "IPD 训练营"
        assert program["access_permission"] == 2
        assert program["access_permission_text"] == "企业内公开"
        assert program["create_time"] == 1780897214

    async def test_keyword_filter(self, mock_client, single_page_response):
        """keywords 应映射为 program_title."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(keywords="数据分析"))

        call_args = mock_client.get.call_args
        params = call_args.kwargs["params"]
        assert params["program_title"] == "数据分析"

    async def test_access_permission_filter(self, mock_client, single_page_response):
        """access_permission 参数应正确传递."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(access_permission=1))

        params = mock_client.get.call_args.kwargs["params"]
        assert params["access_permission"] == "1"

    async def test_program_lib_filter(self, mock_client, single_page_response):
        """is_in_program_lib 参数应正确传递."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(is_in_program_lib=1))

        params = mock_client.get.call_args.kwargs["params"]
        assert params["is_in_program_lib"] == "1"

    async def test_category_id_filter(self, mock_client, single_page_response):
        """category_id 参数应正确传递."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(category_id="27730"))

        params = mock_client.get.call_args.kwargs["params"]
        assert params["category_id"] == "27730"

    async def test_time_range_filter(self, mock_client, single_page_response):
        """start_day / end_day 应生成对应毫秒时间戳."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(
                await adm_list_learning_programs(
                    start_day="2026-06-01",
                    end_day="2026-06-13",
                )
            )

        params = mock_client.get.call_args.kwargs["params"]
        assert params["start_day"] == "2026-06-01"
        assert params["end_day"] == "2026-06-13"
        assert params["startDay"] == "1780243200000"
        assert params["endDay"] == "1781280000000"

    async def test_owner_uids_filter(self, mock_client, single_page_response):
        """owner_uids 应解析并传递."""
        mock_client.get.return_value = {**single_page_response, "data": {**single_page_response["data"], "list": []}}
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(owner_uids="11875281,15797500"))

        params = mock_client.get.call_args.kwargs["params"]
        assert params["uids"] == "11875281,15797500"

    async def test_owner_keywords_filter(self, mock_client, single_page_response):
        """owner_keywords 应调用 user-list 解析为 uids."""
        user_list_response = {
            "error_code": 0,
            "error_message": "",
            "data": {
                "list": [{"id": "11875281", "user_name": "Charlie DONG"}],
            },
        }

        def side_effect(url, params=None):
            if "user-list" in url:
                return user_list_response
            return {**single_page_response, "data": {**single_page_response["data"], "list": []}}

        mock_client.get.side_effect = side_effect
        with _auth_patch(mock_client):
            json.loads(await adm_list_learning_programs(owner_keywords="Charlie"))

        calls = mock_client.get.call_args_list
        program_call = [c for c in calls if "getReportProgramList" in c.args[0]][0]
        assert program_call.kwargs["params"]["uids"] == "11875281"

    async def test_unauthenticated(self, mock_client):
        """未认证时应返回错误信封."""
        with _auth_patch(mock_client):
            with patch("umu_sdk.adapters.mcp.admin._require_auth", return_value="未登录"):
                result = json.loads(await adm_list_learning_programs())

        assert result["success"] is False
        assert result["error_code"] == "NOT_AUTHENTICATED"

    async def test_api_error(self, mock_client):
        """API 返回错误时应包装为错误信封."""
        mock_client.get.return_value = {
            "status": False,
            "errno": -1,
            "error_code": -1,
            "error": "服务器内部错误",
        }
        with _auth_patch(mock_client):
            result = json.loads(await adm_list_learning_programs())

        assert result["success"] is False
        assert "LIST_LEARNING_PROGRAMS_ERROR" in result["error_code"]

    async def test_fetch_all(self, mock_client, sample_program):
        """fetch_all 应自动遍历分页."""
        page1 = {
            "status": True,
            "errno": 0,
            "error_code": 0,
            "error": "success",
            "data": {
                "page_info": {"list_total_num": 2, "total_page_num": 2, "current_page": 1, "size": 1},
                "list": [sample_program],
            },
        }
        page2 = {
            "status": True,
            "errno": 0,
            "error_code": 0,
            "error": "success",
            "data": {
                "page_info": {"list_total_num": 2, "total_page_num": 2, "current_page": 2, "size": 1},
                "list": [{**sample_program, "id": "358711", "program_title": "项目2"}],
            },
        }
        mock_client.get.side_effect = [page1, page2]
        with _auth_patch(mock_client):
            result = json.loads(await adm_list_learning_programs(fetch_all=True))

        assert result["success"] is True
        assert result["data"]["total"] == 2
        assert result["data"]["pagination"]["total_all"] == 2
        assert len(result["data"]["programs"]) == 2
