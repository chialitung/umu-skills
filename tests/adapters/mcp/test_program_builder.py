"""ProgramBuilder 测试."""

from __future__ import annotations

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
    import json
    import urllib.parse

    data = json.loads(urllib.parse.unquote(payload["data"]))
    assert data["program_info"]["program_title"] == "创建学习项目的能力"
    assert data["program_info"]["program_id"] == 0
    assert data["category_arr"][0]["category_id"] == "47849"
