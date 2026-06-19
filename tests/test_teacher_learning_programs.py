"""Teacher 学习项目工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.teacher import tch_list_learning_programs


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))
    return stack


def _program_page(page: int, size: int, total: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": True,
        "error_code": 0,
        "data": {
            "page_info": {
                "list_total_num": total,
                "total_page_num": max(1, (total + size - 1) // size),
                "current_page": page,
                "size": size,
            },
            "list": items,
        },
    }


class TestTchListLearningPrograms:
    async def test_owned_scope(self, mock_client, capsys: pytest.CaptureFixture[str]):
        resp = _program_page(
            1, 20, 1,
            [{
                "program_id": "359923",
                "program_title": "测试版学习项目",
                "access_code": "byt303",
                "share_url": "https://m.umu.cn/program/1vDdf372",
                "share_pc_url": "https://www.umu.cn/program/1vDdf372/detail",
                "create_time": 1781839949,
                "creator": {"umu_id": "17580402", "user_name": "友邦人寿"},
                "group_num": 4,
                "module_num": 1,
                "is_creator": 1,
                "setup": {"bg_img": "https://example.com/bg.png"},
            }],
        )
        mock_client.get.return_value = resp
        with _auth_patch(mock_client):
            result = json.loads(await tch_list_learning_programs(scope="owned"))

        assert result["success"] is True
        assert result["data"]["scope"] == "owned"
        assert len(result["data"]["programs"]) == 1
        assert result["data"]["programs"][0]["program_id"] == "359923"

        call_args = mock_client.get.call_args
        assert "owner=1" in call_args.kwargs["params"].values() or call_args.kwargs["params"].get("owner") == "1"

    async def test_unauthenticated(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client):
            with patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value="未登录"):
                result = json.loads(await tch_list_learning_programs(scope="owned"))
        assert result["success"] is False
        assert result["error_code"] == "NOT_AUTHENTICATED"

    async def test_invalid_scope(self, mock_client):
        with _auth_patch(mock_client):
            result = json.loads(await tch_list_learning_programs(scope="unknown"))
        assert result["success"] is False
        assert result["error_code"] == "INVALID_SCOPE"
