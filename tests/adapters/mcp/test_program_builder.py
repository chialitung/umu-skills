"""ProgramBuilder 测试."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.program_builder import ProgramBuilder


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "https://www.umu.cn"
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def test_create_program_request_payload(mock_client):
    mock_client.post.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"program_id": "359929"},
    }
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder, "_resolve_categories", return_value=[{"category_id": "47849"}]):
        result = builder.create_program(
            title="创建学习项目的能力",
            desc_plain="项目介绍",
            tags=["标签1", "标签2"],
            category_ids=["47849"],
        )

    assert result["program_id"] == "359929"
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://www.umu.cn/api/program/updateinfo"
    payload = call_args.kwargs["data"]
    assert payload["program_id"] == "0"
    assert "data" in payload


def test_add_creates_module_and_adds_courses(mock_client):
    responses = [
        {
            "status": True,
            "error_code": 0,
            "error": "success",
            "data": {"obj_id": "1791554", "module_id": "197797"},
        },
        {
            "status": True,
            "error_code": 0,
            "error": "success",
            "data": {"obj_id": "1791555", "module_id": "197797"},
        },
    ]
    mock_client.post.side_effect = responses
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    result = builder.add_courses(
        program_id="359929",
        modules=[{"module_title": "学习阶段一", "course_ids": ["7329920", "7329935"]}],
    )

    assert len(result["added"]) == 2
    assert result["failed"] == []
    calls = mock_client.post.call_args_list
    first_payload = calls[0].kwargs["data"]
    assert first_payload["module_id"] == "0"
    assert first_payload["module_title"] == "学习阶段一"
    second_payload = calls[1].kwargs["data"]
    assert second_payload["module_id"] == "197797"


def test_add_with_existing_module(mock_client):
    mock_client.post.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"obj_id": "1791556", "module_id": "197797"},
    }
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    result = builder.add_courses(
        program_id="359929",
        modules=[{"module_id": "197797", "course_ids": ["7329943"]}],
    )
    assert len(result["added"]) == 1
    payload = mock_client.post.call_args.kwargs["data"]
    assert payload["module_id"] == "197797"
    assert "module_title" not in payload


def test_configure_certificate_fetches_default_template(mock_client):
    mock_client.get.return_value = {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {"list_total_num": 2},
            "list": [
                {"id": "50", "template_data": {"title": ""}},
                {"id": "47", "template_data": {"title": ""}},
            ],
        },
    }
    mock_client.post.return_value = {"error_code": 0, "error_message": "", "data": {"status": 1}}
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    result = builder.configure_certificate("359929", text="成功学完")

    assert result["status"] == 1
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://www.umu.cn/uapi/v1/program/save-certificate"
    payload = call_args.kwargs["data"]
    assert payload["program_id"] == "359929"
    cert_data = json.loads(payload["certificate_data"])
    assert cert_data["theme_id"] == "50"


def test_set_points_status(mock_client):
    mock_client.post.return_value = {"error_code": 0, "error_message": "", "data": {"status": 1}}
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    result = builder.set_points_status("359929", True)
    assert result["status"] == 1
    payload = mock_client.post.call_args.kwargs["data"]
    assert payload["is_open_point"] == "1"


def test_search_courses(mock_client):
    mock_client.get.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {
            "page_info": {"list_total_num": 1, "total_page_num": 1, "current_page": 1, "size": 10},
            "list": [{"obj_type": "group", "obj_id": "7329920", "group_title": "测试课程"}],
        },
    }
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    items, total = builder.search_courses("359929", keywords="测试")
    assert total == 1
    assert items[0]["obj_id"] == "7329920"

