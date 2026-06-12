"""Admin 学习记录查询工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_list_learning_records,
    adm_list_classes,
    _build_learning_records_search_condition,
    _resolve_student_keywords,
    _resolve_class_names,
)


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def sample_learning_record():
    """单条学习记录样本."""
    return {
        "first_learning_time": "1781163994",
        "last_learning_time": "1781164119",
        "sum_learning_time": "00:02:00",
        "group_required_session_total_count": 1,
        "group_required_session_finished_count": 1,
        "group_completion_rate": 1,
        "group_overall_completion_rate": 1,
        "group_completion_time": "1781164005",
        "group_overall_completion_time": 1781164005,
        "group_total_points": 60,
        "group_total_points_rank": 3,
        "id": "20439815",
        "enterprise_id": "25105",
        "create_time": "2026-06-08 10:21:28",
        "update_time": "2026-06-08 10:21:28",
        "user_enterprise_id": "25105",
        "umu_id": "20439815",
        "student_id": "42877532",
        "teacher_id": "20437528",
        "has_actived": "1",
        "user_type": "3",
        "register_from": "1",
        "user_name": "Shook-JB.Yuan",
        "email": "shook-jb.yuan@aia.com",
        "number": "",
        "on_job_status": 1,
        "phone": "",
        "login_name": "",
        "avatar": "",
        "enterprise_groups": ["分组二"],
        "enterprise_departments": ["A"],
        "class": [],
        "group_id": "7329959",
        "group_title": "高效沟通：从理论到实战",
        "group_share_url": "https://m.umu.cn/course/?groupId=7329959",
        "group_access_code": "cjr334",
        "is_assigned_task": False,
        "vlt": "00:02:00",
    }


@pytest.fixture
def single_page_response(sample_learning_record):
    """单页成功响应."""
    return {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {
                "list_total_num": 1,
                "total_page_num": 1,
                "current_page": 1,
                "size": 20,
            },
            "list": [sample_learning_record],
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


class TestBuildSearchCondition:
    """测试 search_condition 构建辅助函数."""

    def test_empty_condition(self):
        """空参数时应返回空对象."""
        condition = _build_learning_records_search_condition()
        assert condition == {}

    def test_time_range(self):
        """时间范围应映射为 start_date 和 end_date."""
        condition = _build_learning_records_search_condition(
            start_day="2026-06-01",
            end_day="2026-06-12",
        )
        assert condition == {
            "start_date": "2026-06-01",
            "end_date": "2026-06-12",
        }

    def test_uids(self):
        """uids 应作为数组传入."""
        condition = _build_learning_records_search_condition(
            uids=["20439812", "20439815"],
        )
        assert condition == {"uids": ["20439812", "20439815"]}

    def test_course_title(self):
        """课程名称应映射为 group_title."""
        condition = _build_learning_records_search_condition(
            course_title="高效沟通",
        )
        assert condition == {"group_title": "高效沟通"}

    def test_department_and_group_ids(self):
        """部门和分组 ID 应转为数组."""
        condition = _build_learning_records_search_condition(
            department_ids="251103,251104",
            group_ids="177124,177125",
        )
        assert condition == {
            "department_ids": ["251103", "251104"],
            "enterprise_group_ids": ["177124", "177125"],
        }

    def test_class_ids(self):
        """班级 ID 应保持为数组."""
        condition = _build_learning_records_search_condition(
            class_ids=["442992", "442993"],
        )
        assert condition == {"class_ids": ["442992", "442993"]}

    def test_full_condition(self):
        """完整条件应正确组合."""
        condition = _build_learning_records_search_condition(
            start_day="2026-06-01",
            end_day="2026-06-12",
            uids=["20439812"],
            course_title="高效沟通",
            department_ids="251103",
            group_ids="177124",
        )
        assert condition == {
            "start_date": "2026-06-01",
            "end_date": "2026-06-12",
            "uids": ["20439812"],
            "group_title": "高效沟通",
            "department_ids": ["251103"],
            "enterprise_group_ids": ["177124"],
        }


class TestResolveStudentKeywords:
    """测试学员关键词解析辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回 uid 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"id": "20439812", "user_name": "张三"},
                ],
            },
        }
        uids = await _resolve_student_keywords(mock_client, "张三")
        assert uids == ["20439812"]
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/enterprise/user-list" in call_url

    @pytest.mark.asyncio
    async def test_resolves_multiple_matches(self, mock_client):
        """多个匹配时返回多个 uid."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"id": "20439812", "user_name": "张三"},
                    {"id": "20439813", "user_name": "张三丰"},
                ],
            },
        }
        uids = await _resolve_student_keywords(mock_client, "张三")
        assert uids == ["20439812", "20439813"]

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"list": []},
        }
        uids = await _resolve_student_keywords(mock_client, "不存在")
        assert uids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """user-list 接口失败时抛出异常."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="搜索学员失败"):
            await _resolve_student_keywords(mock_client, "张三")


class TestAdmListLearningRecords:
    """测试 adm_list_learning_records 工具."""

    @pytest.mark.asyncio
    async def test_basic_query(self, mock_client, single_page_response):
        """无筛选时返回标准化学习记录."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_learning_records()

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert len(parsed["data"]["records"]) == 1
        record = parsed["data"]["records"][0]
        assert record["user_name"] == "Shook-JB.Yuan"
        assert record["group_title"] == "高效沟通：从理论到实战"
        assert record["last_learning_time_readable"] == "2026-06-11 15:48:39"
        assert parsed["data"]["pagination"]["total_all"] == 1

    @pytest.mark.asyncio
    async def test_time_range_filter(self, mock_client, single_page_response):
        """时间范围筛选应正确传入参数."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_learning_records(
                start_day="2026-06-01",
                end_day="2026-06-12",
            )

        call_kwargs = mock_client.get.call_args[1]
        params = call_kwargs["params"]
        assert params["start_day"] == "2026-06-01"
        assert params["end_day"] == "2026-06-12"
        condition = json.loads(params["search_condition"])
        assert condition["start_date"] == "2026-06-01"
        assert condition["end_date"] == "2026-06-12"

    @pytest.mark.asyncio
    async def test_student_keywords_filter(self, mock_client, single_page_response):
        """学员关键词筛选应先调 user-list 再查学习记录."""
        mock_client.get.side_effect = [
            {
                "error_code": 0,
                "data": {
                    "list": [{"id": "20439812", "user_name": "张三"}],
                },
            },
            single_page_response,
        ]
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(student_keywords="张三")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_client.get.call_count == 2
        second_call_kwargs = mock_client.get.call_args_list[1][1]
        condition = json.loads(second_call_kwargs["params"]["search_condition"])
        assert condition["uids"] == ["20439812"]

    @pytest.mark.asyncio
    async def test_student_keywords_not_found(self, mock_client):
        """学员关键词无匹配时返回错误."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"list": []},
        }
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(student_keywords="不存在")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "STUDENT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_course_title_filter(self, mock_client, single_page_response):
        """课程名称筛选应映射为 group_title."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_learning_records(course_title="高效沟通")

        call_kwargs = mock_client.get.call_args[1]
        condition = json.loads(call_kwargs["params"]["search_condition"])
        assert condition["group_title"] == "高效沟通"

    @pytest.mark.asyncio
    async def test_department_and_group_filter(
        self, mock_client, single_page_response
    ):
        """部门和分组筛选应同时传入独立参数和 search_condition."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_learning_records(
                department_ids="251103,251104",
                group_ids="177124,177125",
            )

        call_kwargs = mock_client.get.call_args[1]
        params = call_kwargs["params"]
        assert params["department_ids"] == "251103,251104"
        assert params["enterprise_group_ids"] == "177124,177125"
        condition = json.loads(params["search_condition"])
        assert condition["department_ids"] == ["251103", "251104"]
        assert condition["enterprise_group_ids"] == ["177124", "177125"]

    @pytest.mark.asyncio
    async def test_pagination(self, mock_client, sample_learning_record):
        """分页参数应正确传递."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {
                    "list_total_num": 100,
                    "total_page_num": 10,
                    "current_page": 3,
                    "size": 10,
                },
                "list": [sample_learning_record],
            },
        }
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(page=3, page_size=10)

        parsed = json.loads(result)
        assert parsed["data"]["pagination"]["current_page"] == 3
        assert parsed["data"]["pagination"]["page_size"] == 10
        assert parsed["data"]["pagination"]["total_all"] == 100
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["params"]["page"] == "3"
        assert call_kwargs["params"]["size"] == "10"

    @pytest.mark.asyncio
    async def test_fetch_all(self, mock_client, sample_learning_record):
        """fetch_all=True 时应遍历分页并合并结果."""
        mock_client.get.side_effect = [
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 1,
                        "size": 20,
                    },
                    "list": [{**sample_learning_record, "umu_id": "20439815"}],
                },
            },
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 2,
                        "size": 20,
                    },
                    "list": [{**sample_learning_record, "umu_id": "20439816"}],
                },
            },
        ]
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(fetch_all=True)

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert len(parsed["data"]["records"]) == 2
        assert {r["umu_id"] for r in parsed["data"]["records"]} == {
            "20439815",
            "20439816",
        }
        assert parsed["data"]["pagination"]["total_all"] == 2

    @pytest.mark.asyncio
    async def test_unauthenticated(self, mock_client):
        """未认证时返回 NOT_AUTHENTICATED 错误."""
        with patch(
            "umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client
        ), patch(
            "umu_sdk.adapters.mcp.admin._require_auth",
            return_value="当前未登录",
        ):
            result = await adm_list_learning_records()

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "NOT_AUTHENTICATED"

    @pytest.mark.asyncio
    async def test_api_error(self, mock_client):
        """学习记录接口返回错误时返回结构错误."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器内部错误",
        }
        with _auth_patch(mock_client):
            result = await adm_list_learning_records()

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "LIST_LEARNING_RECORDS_ERROR"

    @pytest.mark.asyncio
    async def test_class_ids_filter(self, mock_client, single_page_response):
        """班级 ID 筛选应同时传入独立参数和 search_condition."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_learning_records(class_ids="442992,442993")

        call_kwargs = mock_client.get.call_args[1]
        params = call_kwargs["params"]
        assert params["class_ids"] == "442992,442993"
        condition = json.loads(params["search_condition"])
        assert condition["class_ids"] == ["442992", "442993"]

    @pytest.mark.asyncio
    async def test_class_names_filter(self, mock_client, single_page_response):
        """班级名称筛选应先调 class-list 再查学习记录."""
        mock_client.get.side_effect = [
            {
                "error_code": 0,
                "data": {
                    "page_info": {"list_total_num": 1, "total_page_num": 1},
                    "list": [
                        {"id": "442992", "name": "复仇者联盟"},
                    ],
                },
            },
            single_page_response,
        ]
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(class_names="复仇者联盟")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_client.get.call_count == 2
        second_call_kwargs = mock_client.get.call_args_list[1][1]
        condition = json.loads(second_call_kwargs["params"]["search_condition"])
        assert condition["class_ids"] == ["442992"]

    @pytest.mark.asyncio
    async def test_class_names_not_found(self, mock_client):
        """班级名称无匹配时返回错误."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 0, "total_page_num": 0},
                "list": [],
            },
        }
        with _auth_patch(mock_client):
            result = await adm_list_learning_records(class_names="不存在")

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "CLASS_NOT_FOUND"


class TestResolveClassNames:
    """测试班级名称解析辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回班级 ID 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 1, "total_page_num": 1},
                "list": [
                    {"id": "442992", "name": "复仇者联盟"},
                ],
            },
        }
        class_ids = await _resolve_class_names(mock_client, "复仇者联盟")
        assert class_ids == ["442992"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/enterprise/class-list" in call_url

    @pytest.mark.asyncio
    async def test_resolves_multiple_matches(self, mock_client):
        """多个匹配时返回多个班级 ID."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 2, "total_page_num": 1},
                "list": [
                    {"id": "442992", "name": "复仇者联盟"},
                    {"id": "442993", "name": "复仇者联盟2"},
                ],
            },
        }
        class_ids = await _resolve_class_names(mock_client, "复仇者联盟")
        assert class_ids == ["442992", "442993"]

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 0, "total_page_num": 0},
                "list": [],
            },
        }
        class_ids = await _resolve_class_names(mock_client, "不存在")
        assert class_ids is None


class TestAdmListClasses:
    """测试 adm_list_classes 工具."""

    @pytest.mark.asyncio
    async def test_list_classes(self, mock_client):
        """查询班级列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {
                    "list_total_num": 1,
                    "total_page_num": 1,
                    "current_page": 1,
                    "size": 20,
                },
                "list": [
                    {
                        "id": "442992",
                        "name": "复仇者联盟",
                        "access_code": "vfuom",
                        "create_teacher_id": "17578115",
                        "cover_image": "https://example.com/cover.png",
                    }
                ],
            },
        }
        with _auth_patch(mock_client):
            result = await adm_list_classes()

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert len(parsed["data"]["classes"]) == 1
        cls = parsed["data"]["classes"][0]
        assert cls["id"] == "442992"
        assert cls["name"] == "复仇者联盟"
        assert cls["access_code"] == "vfuom"
