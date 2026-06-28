"""Admin 学习项目工具测试."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import adm_delete_learning_program


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.base_url = "https://www.umu.cn"
    return client


def _auth_patch(mock_client):
    return patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client)


class TestAdmDeleteLearningProgram:
    async def test_success(self, mock_client):
        mock_client.post.return_value = {"status": True, "error_code": 0}
        with _auth_patch(mock_client):
            result = json.loads(await adm_delete_learning_program("360141"))
        assert result["success"] is True
        assert result["data"]["deleted"] is True
        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert "/api/program/deleteprogram" in call.args[0]
        assert call.kwargs["data"]["program_id"] == "360141"

    async def test_failure(self, mock_client):
        mock_client.post.return_value = {"status": False, "error": "无权限删除该项目"}
        with _auth_patch(mock_client):
            result = json.loads(await adm_delete_learning_program("360141"))
        assert result["success"] is False
        assert "无权限" in result["error_message"]
