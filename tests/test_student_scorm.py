"""Student MCP SCORM 小节完成能力测试."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.student import (
    _format_scorm_total_time,
    _parse_scorm_launch_url,
)


def test_format_scorm_total_time() -> None:
    assert _format_scorm_total_time(0) == "0000:00:00.00"
    assert _format_scorm_total_time(3661) == "0001:01:01.00"
    assert _format_scorm_total_time(360000) == "0100:00:00.00"


def test_parse_scorm_launch_url_success() -> None:
    url = (
        "https://vfua3ytp5.m.umu.cn/scorm/324/launch/456/course/789/element/abc"
        "?sesskey=XYZ&attempt=2"
    )
    params = _parse_scorm_launch_url(url)
    assert params["subdomain"] == "vfua3ytp5"
    assert params["a"] == "324"
    assert params["scoid"] == "456"
    assert params["course"] == "789"
    assert params["sesskey"] == "XYZ"
    assert params["attempt"] == "2"


def test_parse_scorm_launch_url_missing_params() -> None:
    with pytest.raises(ValueError, match="缺少必要参数"):
        _parse_scorm_launch_url("https://vfua3ytp5.m.umu.cn/scorm/324/launch/456")


@contextmanager
def _patch_student_client():
    """模拟 Student MCP 客户端，返回可断言的 MagicMock。"""
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.mobile_url.side_effect = lambda path: f"https://m.umu.cn{path}"
    client.auth.get_auth_headers.return_value = {"Authorization": "Bearer token"}
    client.auth.is_authenticated.return_value = True

    with patch("umu_sdk.adapters.mcp.student._get_client", return_value=client):
        yield client


@pytest.mark.asyncio
async def test_stu_complete_scorm_section_success() -> None:
    from umu_sdk.adapters.mcp.student import stu_complete_scorm_section

    launch_url = (
        "https://vfua3ytp5.m.umu.cn/scorm/324/launch/456/course/789/element/abc"
        "?sesskey=XYZ&attempt=1"
    )

    with _patch_student_client() as client:
        client.get.return_value = {
            "error_code": 0,
            "data": {
                "id": "e123",
                "type": 11,
                "setup": {"content_type": "scorm", "share_url": "https://m.umu.cn/ssu_xxx"},
            },
        }
        client.http.post.return_value.text = "true\n0"
        client.post.return_value = {}

        with patch("umu_sdk.adapters.mcp.student.stu_get_lesson_status") as mock_status:
            mock_status.return_value = json.dumps({
                "success": True,
                "data": {"is_completed": True, "element_id": "e123"},
            })
            result = await stu_complete_scorm_section(
                element_id="e123",
                scorm_launch_url=launch_url,
                status="passed",
                score=85,
                duration_seconds=125,
            )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"]["is_completed"] is True
    assert parsed["next_action"] == "lesson_completed"

    # 验证 datamodel.php 提交内容
    calls = client.http.post.call_args_list
    assert len(calls) == 2  # 一次带字段 + 一次空 commit
    form = calls[0].kwargs["data"]
    assert form["cmi__core__lesson_status"] == "passed"
    assert form["cmi__core__score__raw"] == "85"
    assert form["cmi__core__total_time"] == "0000:02:05.00"


@pytest.mark.asyncio
async def test_stu_complete_scorm_section_invalid_type() -> None:
    from umu_sdk.adapters.mcp.student import stu_complete_scorm_section

    with _patch_student_client() as client:
        client.get.return_value = {
            "error_code": 0,
            "data": {
                "id": "e456",
                "type": 13,
                "setup": {"content_type": "article"},
            },
        }

        result = await stu_complete_scorm_section(element_id="e456")

    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed["error_code"] == "INVALID_SECTION_TYPE"


@pytest.mark.asyncio
async def test_stu_get_course_structure_scorm_detection() -> None:
    from umu_sdk.adapters.mcp.student import stu_get_course_structure

    with _patch_student_client() as client, \
            patch(
                "umu_sdk.adapters.mcp.student._resolve_course_identifier",
                return_value=("g1", "sk1", "https://m.umu.cn/course?groupId=g1&sKey=sk1"),
            ), \
            patch("umu_sdk.adapters.mcp.student._check_needs_enroll", return_value=(False, None)):
        client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {
                        "element_id": "e1",
                        "type": 11,
                        "title": "SCORM 小节",
                        "extend": {"learn_status": 0},
                        "setup": {"content_type": "scorm"},
                    },
                    {
                        "element_id": "e2",
                        "type": 11,
                        "title": "普通视频",
                        "extend": {"learn_status": 0},
                        "setup": {"content_type": "video"},
                    },
                ],
            },
        }

        result = await stu_get_course_structure(course_identifier="aet123")
        parsed = json.loads(result)
        lessons = parsed["data"]["lessons"]
        assert lessons[0]["completion_type"] == "scorm"
        assert lessons[1]["completion_type"] == "browse"
