"""Admin 任务明细模型测试."""

from __future__ import annotations

import pytest

from umu_sdk.core.admin_models import (
    UserTaskRaw,
    UserTask,
    UserTaskListPageInfo,
    UserTaskListPagination,
    UserTaskListData,
    UserTaskListResponse,
)


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
