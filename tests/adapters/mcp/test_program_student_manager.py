"""ProgramStudentManager 测试."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from umu_sdk.adapters.mcp.program_student_manager import ProgramStudentManager


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "https://www.umu.cn"
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def test_list_participants_parses_dynamic_columns(mock_client):
    mock_client.get.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {
            "data_count": {
                "total_num": 2,
                "complete_num": 1,
                "uncomplete_num": 1,
                "complete_rate": 0.5,
            },
            "table_head": {
                "fixed_column_num": 3,
                "list": [
                    {"id": "user_info", "type": "user_info", "field_name": "user_info"},
                    {
                        "id": "require_complete_rate",
                        "type": "require_complete_rate",
                        "field_name": "require_complete_rate",
                    },
                    {"id": "complete_rate", "type": "complete_rate", "field_name": "complete_rate"},
                    {
                        "id": "module_100",
                        "type": "module",
                        "field_name": "module_100",
                        "title": "模块一",
                    },
                    {
                        "id": 200,
                        "type": "group",
                        "field_name": "group_200",
                        "title": "课程A",
                        "is_require": "1",
                        "share_url": "https://m.umu.cn/course/?groupId=200",
                    },
                ],
            },
            "table_body": {
                "page_info": {
                    "list_total_num": 2,
                    "total_page_num": 1,
                    "current_page": 1,
                    "size": 20,
                },
                "list": [
                    {
                        "umu_id": "1",
                        "student_id": "101",
                        "user_name": "Alice",
                        "complete_rate": 1,
                        "require_complete_rate": 1,
                        "module_100": "模块一",
                        "group_200": {"complete_rate": 1},
                    },
                    {
                        "umu_id": "2",
                        "student_id": "102",
                        "user_name": "Bob",
                        "complete_rate": 0,
                        "require_complete_rate": 0,
                        "module_100": "模块一",
                        "group_200": {"complete_rate": 0},
                    },
                ],
            },
        },
    }

    manager = ProgramStudentManager(mock_client, mock_client.base_url)
    result = manager.list_participants("358416")

    assert result["summary"]["total"] == 2
    assert result["summary"]["completed"] == 1
    assert result["summary"]["uncompleted"] == 1
    assert result["summary"]["completion_rate"] == 0.5
    assert len(result["students"]) == 2

    alice = result["students"][0]
    assert alice["user_name"] == "Alice"
    assert alice["complete_rate"] == 1
    assert len(alice["modules"]) == 1
    assert alice["modules"][0]["module_id"] == "100"
    assert alice["modules"][0]["module_title"] == "模块一"
    assert len(alice["courses"]) == 1
    assert alice["courses"][0]["group_id"] == "200"
    assert alice["courses"][0]["complete_rate"] == 1

    # 原始动态列应被移除，避免重复
    assert "group_200" not in alice
    assert "module_100" not in alice


def test_list_learning_tasks_includes_has_learning_task(mock_client):
    mock_client.get.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {
            "data_count": {
                "total_num": 1,
                "complete_num": 1,
                "uncomplete_num": 0,
                "complete_rate": 1.0,
                "exist_learning_task": 1,
            },
            "table_head": {
                "fixed_column_num": 1,
                "list": [
                    {"id": "user_info", "type": "user_info", "field_name": "user_info"},
                    {"id": "complete_rate", "type": "complete_rate", "field_name": "complete_rate"},
                    {"id": "due_date", "type": "due_date", "field_name": "due_date"},
                ],
            },
            "table_body": {
                "page_info": {
                    "list_total_num": 1,
                    "total_page_num": 1,
                    "current_page": 1,
                    "size": 20,
                },
                "list": [
                    {
                        "umu_id": "1",
                        "student_id": "101",
                        "user_name": "Alice",
                        "complete_rate": 1,
                        "due_time": 1782835200,
                    }
                ],
            },
        },
    }

    manager = ProgramStudentManager(mock_client, mock_client.base_url)
    result = manager.list_learning_tasks("358416")

    assert result["summary"]["has_learning_task"] is True
    assert len(result["students"]) == 1
    assert result["students"][0]["due_time"] == 1782835200
