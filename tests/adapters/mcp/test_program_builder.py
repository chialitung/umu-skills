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


def _make_program_info(program_id: str = "359929") -> dict:
    return {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {
            "program_info": {
                "program_id": program_id,
                "program_title": "原标题",
                "head_img": "https://example.com/cover.jpg",
                "desc": "原介绍",
                "setup": {
                    "pc_skin_id": 1,
                    "skin_data": {
                        "1": {"show_banner": 1},
                        "2": {"show_banner": 0},
                        "3": {"show_banner": 0},
                        "4": {"show_banner": 0},
                        "5": {"show_banner": 0},
                    },
                    "show_banner": 1,
                    "bg_img": "https://example.com/bg.jpg",
                    "enable_certificate": 0,
                    "skin_id": 1,
                    "sort": "asc",
                },
                "multimedia_type": 1,
                "multimedia_id": "77093375",
                "open_module": "1",
                "unlock_type": "1",
                "show_type": "1",
                "tags": [{"tag": "原标签"}],
            },
            "category_arr": [{"id": "47849", "name": "通用力"}],
        },
    }


def test_update_program_merges_changes(mock_client):
    get_resp = _make_program_info()
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder, "_resolve_categories", return_value=[{"category_id": "47850"}]):
        result = builder.update_program(
            program_id="359929",
            title="新标题",
            tags=["新标签"],
            category_ids=["47850"],
            skin_id=2,
            show_banner=False,
        )

    assert result["program_id"] == "359929"
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://www.umu.cn/api/program/updateinfo"
    payload = call_args.kwargs["data"]
    assert payload["program_id"] == "359929"
    assert payload["module"] == "program"
    data = json.loads(payload["data"])
    assert data["program_info"]["program_title"] == "新标题"
    assert data["program_info"]["tags"] == [{"tag": "新标签"}]
    assert data["program_info"]["setup"]["skin_id"] == "2"
    assert data["program_info"]["setup"]["show_banner"] == 0
    assert data["category_arr"] == [{"category_id": "47850"}]
    # 未修改字段保持原值
    assert data["program_info"]["desc"] == "原介绍"


def test_update_program_with_richtext(mock_client):
    get_resp = _make_program_info()
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder._course_builder, "_update_fulltext") as mock_update:
        result = builder.update_program(
            program_id="359929",
            desc_richtext="<p>新富文本</p>",
        )

    assert result["program_id"] == "359929"
    mock_update.assert_called_once_with("77093375", "<p>新富文本</p>", "359929", ref_type="program")
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["multimedia_type"] == 1
    assert data["program_info"]["multimedia_id"] == "77093375"


def test_update_modules(mock_client):
    get_resp = _make_program_info()
    get_resp["data"]["module_list"] = [
        {
            "module_id": "197797",
            "module_title": "阶段一",
            "module_desc": "描述一",
            "multimedia_type": 1,
            "multimedia_id": "77093376",
            "group_list": [
                {"id": "1791554", "group_id": "7329920", "order_index": 0, "is_require": "1", "lesson_type": "0"},
                {"id": "1791555", "group_id": "7329935", "order_index": 1, "is_require": "1", "lesson_type": "0"},
            ],
        }
    ]
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    result = builder.update_modules(
        program_id="359929",
        modules=[
            {
                "module_id": "197797",
                "module_title": "阶段一改名",
                "group_list": [
                    {"id": "1791555", "group_id": "7329935", "order_index": 0, "is_require": "0"},
                    {"id": "1791554", "group_id": "7329920", "order_index": 1, "is_require": "1"},
                ],
            }
        ],
    )

    assert result["program_id"] == "359929"
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "https://www.umu.cn/api/program/updateinfo"
    assert call_args.kwargs["data"]["module"] == "program,module"
    data = json.loads(call_args.kwargs["data"]["data"])
    assert data["module_list"][0]["module_title"] == "阶段一改名"
    assert data["module_list"][0]["module_desc"] == "描述一"
    assert data["module_list"][0]["group_list"][0]["id"] == "1791555"
    assert data["module_list"][0]["group_list"][0]["is_require"] == "0"


def test_remove_courses(mock_client):
    mock_client.post.return_value = {"status": True, "error_code": 0, "error": "success", "data": {"module_id": None}}
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    result = builder.remove_courses("359929", ["1791558", "1791562"])
    assert result["removed"] == ["1791558", "1791562"]
    calls = mock_client.post.call_args_list
    assert calls[0].kwargs["data"]["module_group_id"] == "1791558"
    assert calls[1].kwargs["data"]["module_group_id"] == "1791562"


def test_create_program_with_setup_params(mock_client):
    mock_client.post.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"program_id": "359929"},
    }
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    result = builder.create_program(
        title="参数化项目",
        skin_id=3,
        pc_skin_id=2,
        show_banner=False,
        unlock_type=2,
        show_type=2,
        open_module=1,
        sort="desc",
    )

    assert result["program_id"] == "359929"
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["unlock_type"] == "2"
    assert data["program_info"]["show_type"] == "2"
    assert data["program_info"]["open_module"] == "1"
    assert data["program_info"]["setup"]["skin_id"] == "3"
    assert data["program_info"]["setup"]["pc_skin_id"] == "2"
    assert data["program_info"]["setup"]["show_banner"] == 0
    assert data["program_info"]["setup"]["sort"] == "desc"


def test_update_program_with_pc_skin_id_and_sort(mock_client):
    get_resp = _make_program_info()
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    result = builder.update_program(
        program_id="359929",
        pc_skin_id=2,
        sort="desc",
    )

    assert result["program_id"] == "359929"
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["setup"]["pc_skin_id"] == "2"
    assert data["program_info"]["setup"]["sort"] == "desc"
    assert data["category_arr"] == [{"category_id": "47849"}]


def test_update_modules_with_desc_richtext(mock_client):
    get_resp = _make_program_info()
    get_resp["data"]["module_list"] = [
        {
            "module_id": "197797",
            "module_title": "阶段一",
            "module_desc": "描述一",
            "multimedia_type": 1,
            "multimedia_id": "77093376",
            "group_list": [],
        }
    ]
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder._course_builder, "_update_fulltext") as mock_update:
        result = builder.update_modules(
            program_id="359929",
            modules=[{"module_id": "197797", "module_desc_richtext": "<p>富文本描述</p>"}],
        )

    assert result["program_id"] == "359929"
    mock_update.assert_called_once_with("77093376", "<p>富文本描述</p>", "359929", ref_type="")
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["module_list"][0]["multimedia_type"] == "1"


def test_update_modules_creates_desc_richtext(mock_client):
    get_resp = _make_program_info()
    get_resp["data"]["module_list"] = [
        {
            "module_id": "197797",
            "module_title": "阶段一",
            "module_desc": "描述一",
            "multimedia_type": 0,
            "multimedia_id": "",
            "group_list": [],
        }
    ]
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder._course_builder, "_create_fulltext", return_value="77099999") as mock_create:
        result = builder.update_modules(
            program_id="359929",
            modules=[{"module_id": "197797", "module_desc_richtext": "<p>新建富文本</p>"}],
        )

    assert result["program_id"] == "359929"
    mock_create.assert_called_once_with("<p>新建富文本</p>", ref_type="")
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["module_list"][0]["multimedia_id"] == "77099999"
    assert data["module_list"][0]["multimedia_type"] == "1"


def test_create_program_with_image_urls_and_certificate(mock_client):
    mock_client.post.return_value = {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"program_id": "359929"},
    }
    builder = ProgramBuilder(mock_client, mock_client.base_url)
    result = builder.create_program(
        title="URL 参数化项目",
        cover_image_url="https://example.com/cover.jpg",
        bg_image_url="https://example.com/bg.jpg",
        enable_certificate=True,
    )

    assert result["program_id"] == "359929"
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["head_img"] == "https://example.com/cover.jpg"
    assert data["program_info"]["setup"]["bg_img"] == "https://example.com/bg.jpg"
    assert data["program_info"]["setup"]["enable_certificate"] == 1


def test_update_program_with_image_urls_and_certificate(mock_client):
    get_resp = _make_program_info()
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    result = builder.update_program(
        program_id="359929",
        cover_image_url="https://example.com/new-cover.jpg",
        bg_image_url="https://example.com/new-bg.jpg",
        enable_certificate=True,
    )

    assert result["program_id"] == "359929"
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["head_img"] == "https://example.com/new-cover.jpg"
    assert data["program_info"]["setup"]["bg_img"] == "https://example.com/new-bg.jpg"
    assert data["program_info"]["setup"]["enable_certificate"] == 1
    # skin_data 应保留服务端原始值
    assert data["program_info"]["setup"]["skin_data"]["1"]["show_banner"] == 1


def test_update_program_image_url_takes_priority_over_path(mock_client):
    get_resp = _make_program_info()
    post_resp = {"status": True, "error_code": 0, "error": "success", "data": {"program_id": "359929"}}
    mock_client.get.return_value = get_resp
    mock_client.post.return_value = post_resp
    builder = ProgramBuilder(mock_client, mock_client.base_url)

    with patch.object(builder, "_resolve_categories", return_value=[{"category_id": "47849"}]):
        result = builder.update_program(
            program_id="359929",
            cover_path="/local/cover.jpg",
            cover_image_url="https://example.com/url-cover.jpg",
        )

    assert result["program_id"] == "359929"
    payload = mock_client.post.call_args.kwargs["data"]
    data = json.loads(payload["data"])
    assert data["program_info"]["head_img"] == "https://example.com/url-cover.jpg"
    # 由于 URL 优先，不应触发上传
    mock_client.post.assert_called_once()

