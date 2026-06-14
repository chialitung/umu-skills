"""Admin 任务明细模型测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_list_user_tasks,
    _build_user_task_search_condition,
    _day_to_timestamp,
    _resolve_department_names,
    _resolve_group_names,
    _resolve_class_names_all,
    _resolve_user_keywords,
)
from umu_sdk.core.admin_models import (
    UserTaskRaw,
    UserTask,
    UserTaskListPageInfo,
    UserTaskListPagination,
    UserTaskListData,
    UserTaskListResponse,
)


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def sample_raw_task():
    """单个原始任务明细样本."""
    return {
        "learning_time": "00:52:25",
        "vlt": "00:52:25",
        "first_learning_time": "1781245357",
        "last_learning_time": "1781248479",
        "learn_status": 2,
        "finish_time": 1781248479,
        "assign_time": 1781125804,
        "due_time": 1788901804,
        "student": {
            "umu_id": "20453567",
            "home_url": "https://m.umu.cn/profile/abc",
            "user_name": "Mingna Bu",
            "enterprise_groups": [],
        },
        "operator": {
            "umu_id": "12944154",
            "home_url": "https://m.umu.cn/profile/def",
            "user_name": "Admin User",
            "enterprise_groups": ["Group A"],
            "on_job_status": 1,
            "is_signout_free": 0,
            "is_manager": 1,
        },
        "task_obj": {
            "obj_id": "267963",
            "task_name": "Onboarding Training",
            "obj_type": 3,
            "obj_type_name": "",
            "session_type": "",
            "course_name": "",
            "course_id": "",
            "task_url": "https://m.umu.cn/program/xxx",
            "share_url": "https://m.umu.cn/program/xxx",
        },
        "assign_obj": {
            "id": "11018",
            "type": "5",
            "name": "Company",
            "is_manager": 1,
        },
        "task_obj_id": "9532",
    }


@pytest.fixture
def single_page_response(sample_raw_task):
    """单页响应样本."""
    return {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {
                "list_total_num": 1,
                "total_page_num": 1,
                "current_page": 1,
                "size": 500,
            },
            "list": [sample_raw_task],
        },
    }


def _auth_patch(mock_client):
    """Patch _get_client and _require_auth for adm_list_user_tasks."""
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestUserTaskRaw:
    """测试 UserTaskRaw 原始模型."""

    def test_parse_full(self, sample_raw_task):
        """完整字段应正确解析."""
        raw = UserTaskRaw(**sample_raw_task)
        assert raw.learning_time == "00:52:25"
        assert raw.vlt == "00:52:25"
        assert raw.first_learning_time == "1781245357"
        assert raw.last_learning_time == "1781248479"
        assert raw.learn_status == 2
        assert raw.finish_time == 1781248479
        assert raw.assign_time == 1781125804
        assert raw.due_time == 1788901804
        assert raw.student["umu_id"] == "20453567"
        assert raw.operator["umu_id"] == "12944154"
        assert raw.task_obj["obj_id"] == "267963"
        assert raw.assign_obj["id"] == "11018"
        assert raw.task_obj_id == "9532"

    def test_parse_minimal(self):
        """最小字段应使用默认值."""
        raw = UserTaskRaw()
        assert raw.learning_time == ""
        assert raw.vlt == ""
        assert raw.learn_status == 0
        assert raw.finish_time == 0
        assert raw.student == {}
        assert raw.task_obj == {}

    def test_extra_fields_ignored(self, sample_raw_task):
        """额外字段应被忽略（extra='ignore' 默认行为）."""
        raw = UserTaskRaw(**{**sample_raw_task, "unknown_field": "xxx"})
        assert raw.task_obj_id == "9532"


class TestUserTask:
    """测试 UserTask 标准化模型."""

    def test_from_raw_full(self, sample_raw_task):
        """完整原始对象应正确标准化."""
        raw = UserTaskRaw(**sample_raw_task)
        task = UserTask.from_raw(raw)

        assert task.task_obj_id == "9532"
        assert task.learning_time == "00:52:25"
        assert task.vlt == "00:52:25"
        assert task.first_learning_time == 1781245357
        assert task.first_learning_time_readable == "2026-06-12 14:22:37"
        assert task.last_learning_time == 1781248479
        assert task.last_learning_time_readable == "2026-06-12 15:14:39"
        assert task.learn_status == 2
        assert task.learn_status_text == "按时完成"
        assert task.finish_time == 1781248479
        assert task.finish_time_readable == "2026-06-12 15:14:39"
        assert task.assign_time == 1781125804
        assert task.assign_time_readable == "2026-06-11 05:10:04"
        assert task.due_time == 1788901804
        assert task.due_time_readable == "2026-09-09 05:10:04"
        assert task.is_overdue is False
        assert task.student_umu_id == "20453567"
        assert task.student_name == "Mingna Bu"
        assert task.student_home_url == "https://m.umu.cn/profile/abc"
        assert task.student_groups == []
        assert task.operator_umu_id == "12944154"
        assert task.operator_name == "Admin User"
        assert task.operator_groups == ["Group A"]
        assert task.obj_id == "267963"
        assert task.task_name == "Onboarding Training"
        assert task.obj_type == 3
        assert task.obj_type_text == "学习项目"
        assert task.session_type == ""
        assert task.course_name == ""
        assert task.course_id == ""
        assert task.task_url == "https://m.umu.cn/program/xxx"
        assert task.share_url == "https://m.umu.cn/program/xxx"
        assert task.assign_obj_id == "11018"
        assert task.assign_obj_type == "5"
        assert task.assign_obj_name == "Company"

    def test_from_raw_overdue_status_3(self):
        """learn_status=3 时应标记为逾期."""
        raw = UserTaskRaw(
            learn_status=3,
            finish_time=1781248479,
            due_time=1788901804,
        )
        task = UserTask.from_raw(raw)
        assert task.learn_status == 3
        assert task.learn_status_text == "逾期完成"
        assert task.is_overdue is True

    def test_from_raw_overdue_finish_after_due(self):
        """learn_status=2 但 finish_time > due_time 时应标记为逾期."""
        raw = UserTaskRaw(
            learn_status=2,
            finish_time=1788901805,
            due_time=1788901804,
        )
        task = UserTask.from_raw(raw)
        assert task.is_overdue is True

    def test_from_raw_not_overdue(self):
        """learn_status=2 且 finish_time <= due_time 时不应标记为逾期."""
        raw = UserTaskRaw(
            learn_status=2,
            finish_time=1788901804,
            due_time=1788901804,
        )
        task = UserTask.from_raw(raw)
        assert task.is_overdue is False

    def test_from_raw_no_due_time(self):
        """due_time=0 时 due_time_readable 应为空字符串."""
        raw = UserTaskRaw(due_time=0)
        task = UserTask.from_raw(raw)
        assert task.due_time == 0
        assert task.due_time_readable == ""

    def test_from_raw_learn_status_map(self):
        """学习状态码应正确映射为人读文本."""
        assert UserTask.from_raw(UserTaskRaw(learn_status=0)).learn_status_text == "待学习"
        assert UserTask.from_raw(UserTaskRaw(learn_status=1)).learn_status_text == "学习中"
        assert UserTask.from_raw(UserTaskRaw(learn_status=2)).learn_status_text == "按时完成"
        assert UserTask.from_raw(UserTaskRaw(learn_status=3)).learn_status_text == "逾期完成"
        assert UserTask.from_raw(UserTaskRaw(learn_status=99)).learn_status_text == "未知(99)"

    def test_from_raw_obj_type_map(self):
        """任务类型码应正确映射为人读文本."""
        assert UserTask.from_raw(UserTaskRaw(task_obj={"obj_type": 1})).obj_type_text == "小节"
        assert UserTask.from_raw(UserTaskRaw(task_obj={"obj_type": 2})).obj_type_text == "课程"
        assert UserTask.from_raw(UserTaskRaw(task_obj={"obj_type": 3})).obj_type_text == "学习项目"
        assert UserTask.from_raw(UserTaskRaw(task_obj={"obj_type": 99})).obj_type_text == "未知(99)"

    def test_from_raw_empty_dicts(self):
        """空字典字段不应报错."""
        raw = UserTaskRaw()
        task = UserTask.from_raw(raw)
        assert task.student_umu_id == ""
        assert task.student_name == ""
        assert task.operator_umu_id == ""
        assert task.operator_name == ""
        assert task.obj_id == ""
        assert task.task_name == ""
        assert task.assign_obj_id == ""
        assert task.assign_obj_name == ""

    def test_model_dump(self, sample_raw_task):
        """model_dump 应包含所有字段."""
        raw = UserTaskRaw(**sample_raw_task)
        task = UserTask.from_raw(raw)
        data = task.model_dump()
        assert "task_obj_id" in data
        assert "is_overdue" in data
        assert "learn_status_text" in data
        assert "obj_type_text" in data


class TestUserTaskPagination:
    """测试分页模型."""

    def test_page_info(self):
        """原始分页信息应正确解析."""
        info = UserTaskListPageInfo(
            list_total_num=100,
            total_page_num=10,
            current_page=1,
            size=10,
        )
        assert info.list_total_num == 100
        assert info.total_page_num == 10

    def test_pagination(self):
        """标准化分页信息应正确解析."""
        pagination = UserTaskListPagination(
            total_all=100,
            current_page=1,
            page_size=10,
        )
        assert pagination.total_all == 100
        assert pagination.page_size == 10


class TestUserTaskListData:
    """测试列表数据模型."""

    def test_with_tasks(self, sample_raw_task):
        """包含任务列表时应正确解析."""
        raw = UserTaskRaw(**sample_raw_task)
        task = UserTask.from_raw(raw)
        data = UserTaskListData(
            tasks=[task],
            total=1,
            pagination=UserTaskListPagination(
                total_all=1,
                current_page=1,
                page_size=500,
            ),
        )
        assert len(data.tasks) == 1
        assert data.total == 1
        assert data.pagination.total_all == 1


class TestUserTaskListResponse:
    """测试响应包装模型."""

    def test_success_response(self, sample_raw_task):
        """成功响应应正确解析."""
        raw = UserTaskRaw(**sample_raw_task)
        task = UserTask.from_raw(raw)
        response = UserTaskListResponse(
            success=True,
            data=UserTaskListData(
                tasks=[task],
                total=1,
                pagination=UserTaskListPagination(
                    total_all=1,
                    current_page=1,
                    page_size=500,
                ),
            ),
        )
        assert response.success is True
        assert response.error_code == ""
        assert response.next_action == "proceed"
        assert len(response.data.tasks) == 1

    def test_error_response(self):
        """错误响应应正确解析."""
        response = UserTaskListResponse(
            success=False,
            data=UserTaskListData(
                tasks=[],
                total=0,
                pagination=UserTaskListPagination(
                    total_all=0,
                    current_page=1,
                    page_size=500,
                ),
            ),
            error_code="LIST_USER_TASKS_ERROR",
            error_message="获取失败",
            suggested_action="重试",
            next_action="needs_user_input",
        )
        assert response.success is False
        assert response.error_code == "LIST_USER_TASKS_ERROR"
        assert response.next_action == "needs_user_input"


class TestDayToTimestamp:
    """测试 _day_to_timestamp 辅助函数."""

    def test_start_of_day(self):
        """默认返回当天 00:00:00 的时间戳."""
        ts = _day_to_timestamp("2026-06-14")
        from datetime import datetime, timezone, timedelta

        dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
        assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-14 00:00:00"

    def test_end_of_day(self):
        """end_of_day=True 返回当天 23:59:59 的时间戳."""
        ts = _day_to_timestamp("2026-06-14", end_of_day=True)
        from datetime import datetime, timezone, timedelta

        dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
        assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-06-14 23:59:59"


class TestBuildUserTaskSearchCondition:
    """测试 _build_user_task_search_condition 辅助函数."""

    def test_empty_returns_empty_dict(self):
        """无参数时返回空字典."""
        condition = _build_user_task_search_condition()
        assert condition == {}

    def test_task_types(self):
        """任务类型应逗号连接."""
        condition = _build_user_task_search_condition(task_types=["1", "2", "3"])
        assert condition["obj_type"] == "1,2,3"

    def test_learn_status(self):
        """完成状态应逗号连接."""
        condition = _build_user_task_search_condition(learn_status=["0", "2"])
        assert condition["learn_status"] == "0,2"

    def test_due_status(self):
        """到期状态应逗号连接."""
        condition = _build_user_task_search_condition(due_status=["1"])
        assert condition["due_status"] == "1"

    def test_department_ids(self):
        """部门 ID 应逗号连接."""
        condition = _build_user_task_search_condition(department_ids=["82064", "82065"])
        assert condition["department_ids"] == "82064,82065"

    def test_group_ids(self):
        """分组 ID 应使用 enterprise_group_ids 键."""
        condition = _build_user_task_search_condition(group_ids=["1001", "1002"])
        assert condition["enterprise_group_ids"] == "1001,1002"

    def test_class_ids(self):
        """班级 ID 应逗号连接."""
        condition = _build_user_task_search_condition(class_ids=["3001", "3002"])
        assert condition["class_ids"] == "3001,3002"

    def test_from_umu_ids(self):
        """分配者 ID 应使用 from_umu_ids 键."""
        condition = _build_user_task_search_condition(from_umu_ids=["20439812"])
        assert condition["from_umu_ids"] == "20439812"

    def test_assign_umu_ids(self):
        """学员 ID 应使用 assign_umu_ids 键."""
        condition = _build_user_task_search_condition(assign_umu_ids=["20439812", "20439813"])
        assert condition["assign_umu_ids"] == "20439812,20439813"

    def test_task_name(self):
        """任务名称应直接传入."""
        condition = _build_user_task_search_condition(task_name="Onboarding")
        assert condition["task_name"] == "Onboarding"

    def test_course_keywords(self):
        """课程关键词应使用 keywords 键."""
        condition = _build_user_task_search_condition(course_keywords="Python")
        assert condition["keywords"] == "Python"

    def test_time_range(self):
        """时间范围应正确设置时间戳."""
        condition = _build_user_task_search_condition(
            assign_start_ts=1773676800,
            assign_stop_ts=1781452799,
            due_start_ts=1780000000,
            due_stop_ts=1789000000,
        )
        assert condition["assign_start_ts"] == 1773676800
        assert condition["assign_stop_ts"] == 1781452799
        assert condition["due_start_ts"] == 1780000000
        assert condition["due_stop_ts"] == 1789000000

    def test_full_condition(self):
        """完整条件应包含所有字段."""
        condition = _build_user_task_search_condition(
            task_types=["1", "2"],
            learn_status=["0", "3"],
            due_status=["1"],
            department_ids=["82064"],
            group_ids=["1001"],
            class_ids=["3001"],
            from_umu_ids=["20439812"],
            assign_umu_ids=["20439813"],
            task_name="test",
            course_keywords="course",
            assign_start_ts=1773676800,
            assign_stop_ts=1781452799,
            due_start_ts=1780000000,
            due_stop_ts=1789000000,
        )
        assert condition["obj_type"] == "1,2"
        assert condition["learn_status"] == "0,3"
        assert condition["due_status"] == "1"
        assert condition["department_ids"] == "82064"
        assert condition["enterprise_group_ids"] == "1001"
        assert condition["class_ids"] == "3001"
        assert condition["from_umu_ids"] == "20439812"
        assert condition["assign_umu_ids"] == "20439813"
        assert condition["task_name"] == "test"
        assert condition["keywords"] == "course"
        assert condition["assign_start_ts"] == 1773676800
        assert condition["assign_stop_ts"] == 1781452799
        assert condition["due_start_ts"] == 1780000000
        assert condition["due_stop_ts"] == 1789000000


class TestResolveDepartmentNames:
    """测试 _resolve_department_names 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回部门 ID 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "department_list": [
                    {
                        "department_id": "1001",
                        "department_name": "技术部",
                        "child_path": [],
                    },
                ],
            },
        }
        ids = await _resolve_department_names(mock_client, "技术")
        assert ids == ["1001"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/department/get-departments-by-managerid" in call_url

    @pytest.mark.asyncio
    async def test_resolves_multiple_keywords(self, mock_client):
        """多个关键词匹配时返回所有匹配的 ID."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "department_list": [
                    {
                        "department_id": "1001",
                        "department_name": "技术部",
                        "child_path": [],
                    },
                    {
                        "department_id": "1002",
                        "department_name": "销售部",
                        "child_path": [],
                    },
                ],
            },
        }
        ids = await _resolve_department_names(mock_client, "技术, 销售")
        assert "1001" in ids
        assert "1002" in ids

    @pytest.mark.asyncio
    async def test_recursive_child_departments(self, mock_client):
        """递归匹配子部门."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "department_list": [
                    {
                        "department_id": "1001",
                        "department_name": "总部",
                        "child_path": [
                            {
                                "department_id": "1003",
                                "department_name": "前端组",
                                "child_path": [],
                            },
                        ],
                    },
                ],
            },
        }
        ids = await _resolve_department_names(mock_client, "前端")
        assert ids == ["1003"]

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"department_list": []},
        }
        ids = await _resolve_department_names(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_empty_input_returns_none(self, mock_client):
        """空输入时返回 None."""
        ids = await _resolve_department_names(mock_client, "  ,  ")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="查询部门列表失败"):
            await _resolve_department_names(mock_client, "技术")


class TestResolveGroupNames:
    """测试 _resolve_group_names 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回分组 ID 列表."""
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {
                "list": [
                    {"id": "2001", "group_name": "新员工分组"},
                ],
                "total": 1,
            },
        }
        ids = await _resolve_group_names(mock_client, "新员工")
        assert ids == ["2001"]
        call_url = mock_client.get.call_args[0][0]
        assert "/ajax/enterprise/getGroupList" in call_url

    @pytest.mark.asyncio
    async def test_pagination_fetches_all(self, mock_client):
        """分页拉取全部数据."""
        responses = [
            {
                "status": True,
                "error_code": 0,
                "data": {
                    "list": [
                        {"id": "2001", "group_name": "分组A"},
                    ],
                    "total": 2,
                },
            },
            {
                "status": True,
                "error_code": 0,
                "data": {
                    "list": [
                        {"id": "2002", "group_name": "分组B"},
                    ],
                    "total": 2,
                },
            },
        ]
        mock_client.get.side_effect = responses
        ids = await _resolve_group_names(mock_client, "分组")
        assert "2001" in ids
        assert "2002" in ids
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {"list": [], "total": 0},
        }
        ids = await _resolve_group_names(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "status": False,
            "error_code": 500,
            "error": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="查询分组列表失败"):
            await _resolve_group_names(mock_client, "分组")


class TestResolveClassNamesAll:
    """测试 _resolve_class_names_all 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回班级 ID 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"id": "3001", "name": "复仇者联盟"},
                ],
                "total": 1,
            },
        }
        ids = await _resolve_class_names_all(mock_client, "复仇者")
        assert ids == ["3001"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/enterprise/class-list" in call_url

    @pytest.mark.asyncio
    async def test_pagination_fetches_all(self, mock_client):
        """分页拉取全部数据."""
        responses = [
            {
                "error_code": 0,
                "data": {
                    "list": [
                        {"id": "3001", "name": "班级A"},
                    ],
                    "total": 2,
                },
            },
            {
                "error_code": 0,
                "data": {
                    "list": [
                        {"id": "3002", "name": "班级B"},
                    ],
                    "total": 2,
                },
            },
        ]
        mock_client.get.side_effect = responses
        ids = await _resolve_class_names_all(mock_client, "班级")
        assert "3001" in ids
        assert "3002" in ids
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"list": [], "total": 0},
        }
        ids = await _resolve_class_names_all(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="查询班级列表失败"):
            await _resolve_class_names_all(mock_client, "班级")


class TestResolveUserKeywords:
    """测试 _resolve_user_keywords 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回 umu_id 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"umu_id": "20439812", "user_name": "张三"},
                ],
            },
        }
        ids = await _resolve_user_keywords(mock_client, "张三")
        assert ids == ["20439812"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/enterprise/search-user" in call_url

    @pytest.mark.asyncio
    async def test_resolves_multiple_matches(self, mock_client):
        """多个匹配时返回多个 umu_id."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"umu_id": "20439812", "user_name": "张三"},
                    {"umu_id": "20439813", "user_name": "张三丰"},
                ],
            },
        }
        ids = await _resolve_user_keywords(mock_client, "张")
        assert ids == ["20439812", "20439813"]

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"list": []},
        }
        ids = await _resolve_user_keywords(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="搜索用户失败"):
            await _resolve_user_keywords(mock_client, "张三")


class TestAdmListUserTasks:
    """测试 adm_list_user_tasks 工具."""

    @pytest.mark.asyncio
    async def test_defaults_to_90_days(self, mock_client, single_page_response):
        """未提供时间范围时默认查询最近 90 天."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks()
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        # 验证 search_condition 包含默认 90 天范围
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        condition = json.loads(params["search_condition"])
        assert "assign_start_ts" in condition
        assert "assign_stop_ts" in condition

    @pytest.mark.asyncio
    async def test_single_page_query(self, mock_client, single_page_response):
        """单页查询返回正确结果."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(page=1, page_size=10)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        assert parsed["data"]["pagination"]["current_page"] == 1
        assert parsed["data"]["pagination"]["page_size"] == 10

    @pytest.mark.asyncio
    async def test_fetch_all_pagination(self, mock_client, sample_raw_task):
        """fetch_all=True 时自动翻页."""
        mock_client.get.side_effect = [
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 1,
                        "size": 500,
                    },
                    "list": [sample_raw_task],
                },
            },
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 2,
                        "size": 500,
                    },
                    "list": [{**sample_raw_task, "task_obj_id": "9533"}],
                },
            },
        ]
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(fetch_all=True)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 2
        assert parsed["data"]["pagination"]["total_all"] == 2
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_all_size_fallback(self, mock_client, sample_raw_task):
        """page_size=500 失败时自动回退到 100."""
        # 第一页：size=500 失败，size=100 成功
        mock_client.get.side_effect = [
            {"error_code": 500, "error_message": "请求超时"},  # size=500 fails
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 1,
                        "total_page_num": 1,
                        "current_page": 1,
                        "size": 100,
                    },
                    "list": [sample_raw_task],
                },
            },
        ]
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(fetch_all=True, page_size=500)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        # 验证第二次调用使用了 size=100
        second_call = mock_client.get.call_args_list[1]
        params = second_call.kwargs.get("params", second_call[1].get("params", {}))
        assert params["size"] == "100"

    @pytest.mark.asyncio
    async def test_fetch_all_size_fallback_still_fails(self, mock_client):
        """size=500 和 size=100 都失败时返回错误."""
        mock_client.get.return_value = {"error_code": 500, "error_message": "服务器错误"}
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(fetch_all=True, page_size=500)
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "LIST_USER_TASKS_ERROR"

    @pytest.mark.asyncio
    async def test_error_response(self, mock_client):
        """API 错误时返回标准错误结构."""
        mock_client.get.return_value = {"error_code": 500, "error_message": "服务器错误"}
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks()
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "LIST_USER_TASKS_ERROR"
        assert "服务器错误" in parsed["error_message"]

    @pytest.mark.asyncio
    async def test_with_filters(self, mock_client, single_page_response):
        """带筛选条件时正确构建 search_condition."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(
                task_types="1,2",
                learn_status="0,3",
                due_status="1",
                department_ids="82064",
                group_ids="1001",
                class_ids="3001",
                assigner_umu_ids="20439812",
                student_umu_ids="20439813",
                task_name="test",
                course_keywords="python",
                assign_start_day="2026-01-01",
                assign_end_day="2026-06-14",
                due_start_day="2026-03-01",
                due_end_day="2026-12-31",
            )
        parsed = json.loads(result)
        assert parsed["success"] is True
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        condition = json.loads(params["search_condition"])
        assert condition["obj_type"] == "1,2"
        assert condition["learn_status"] == "0,3"
        assert condition["due_status"] == "1"
        assert condition["department_ids"] == "82064"
        assert condition["enterprise_group_ids"] == "1001"
        assert condition["class_ids"] == "3001"
        assert condition["from_umu_ids"] == "20439812"
        assert condition["assign_umu_ids"] == "20439813"
        assert condition["task_name"] == "test"
        assert condition["keywords"] == "python"
        assert "assign_start_ts" in condition
        assert "assign_stop_ts" in condition
        assert "due_start_ts" in condition
        assert "due_stop_ts" in condition

    @pytest.mark.asyncio
    async def test_merge_explicit_and_resolved_ids(self, mock_client, single_page_response):
        """显式 ID 和解析 ID 应合并去重."""
        # Patch resolvers to return IDs
        with _auth_patch(mock_client):
            with patch(
                "umu_sdk.adapters.mcp.admin._resolve_department_names",
                return_value=["82065"],
            ):
                with patch(
                    "umu_sdk.adapters.mcp.admin._resolve_group_names",
                    return_value=["1002"],
                ):
                    mock_client.get.return_value = single_page_response
                    result = await adm_list_user_tasks(
                        department_ids="82064,82065",
                        department_names="技术部",
                        group_ids="1001",
                        group_names="新员工",
                    )
        parsed = json.loads(result)
        assert parsed["success"] is True
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        condition = json.loads(params["search_condition"])
        # 82064 (explicit) + 82065 (explicit + resolved, deduped) = 82064,82065
        assert condition["department_ids"] == "82064,82065"
        # 1001 (explicit) + 1002 (resolved) = 1001,1002
        assert condition["enterprise_group_ids"] == "1001,1002"

    @pytest.mark.asyncio
    async def test_not_authenticated(self, mock_client):
        """未认证时返回错误."""
        with patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client):
            with patch(
                "umu_sdk.adapters.mcp.admin._require_auth",
                return_value="当前未登录",
            ):
                result = await adm_list_user_tasks()
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "NOT_AUTHENTICATED"

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_client):
        """空结果应正确返回."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {
                    "list_total_num": 0,
                    "total_page_num": 0,
                    "current_page": 1,
                    "size": 500,
                },
                "list": [],
            },
        }
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks()
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 0
        assert parsed["data"]["tasks"] == []

    @pytest.mark.asyncio
    async def test_fetch_all_50_page_limit(self, mock_client, sample_raw_task):
        """fetch_all 达到 50 页安全上限时停止."""
        # 模拟每页返回 1 条，总共 100 条，但 50 页上限应该停止
        responses = []
        for i in range(1, 52):
            responses.append(
                {
                    "error_code": 0,
                    "data": {
                        "page_info": {
                            "list_total_num": 100,
                            "total_page_num": 100,
                            "current_page": i,
                            "size": 500,
                        },
                        "list": [{**sample_raw_task, "task_obj_id": str(9500 + i)}],
                    },
                }
            )
        mock_client.get.side_effect = responses
        with _auth_patch(mock_client):
            result = await adm_list_user_tasks(fetch_all=True)
        parsed = json.loads(result)
        assert parsed["success"] is True
        # 应该获取了 50 页（因为第 51 页超出上限）
        assert mock_client.get.call_count == 50
        assert parsed["data"]["total"] == 50
