"""Admin 个人视角学习项目工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import adm_list_personal_learning_programs


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
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


class TestAdmListPersonalLearningPrograms:
    async def test_owned_scope(self, mock_client):
        resp = _program_page(1, 20, 1, [{"program_id": "359923", "program_title": "测试项目"}])
        mock_client.get.return_value = resp
        with _auth_patch(mock_client):
            result = json.loads(await adm_list_personal_learning_programs(scope="owned"))
        assert result["success"] is True
        assert result["data"]["scope"] == "owned"
        assert len(result["data"]["programs"]) == 1

    async def test_unauthenticated(self, mock_client):
        with patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client):
            with patch("umu_sdk.adapters.mcp.admin._require_auth", return_value="未登录"):
                result = json.loads(await adm_list_personal_learning_programs(scope="owned"))
        assert result["success"] is False
        assert result["error_code"] == "NOT_AUTHENTICATED"
