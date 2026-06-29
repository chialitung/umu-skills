"""Admin 学习项目工具测试."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import mcp as admin_mcp
from umu_sdk.core.errors import UMUError
from umu_sdk.tools.operations.programs import delete_learning_program, list_personal_learning_programs


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.base_url = "https://www.umu.cn"
    return client


class TestDeleteLearningProgramOperation:
    async def test_success(self, mock_client):
        mock_client.post.return_value = {"status": True, "error_code": 0}
        result = await delete_learning_program(mock_client, "360141")
        assert result["deleted"] is True
        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert "/api/program/deleteprogram" in call.args[0]
        assert call.kwargs["data"]["program_id"] == "360141"

    async def test_failure(self, mock_client):
        mock_client.post.return_value = {"status": False, "error": "无权限删除该项目"}
        with pytest.raises(RuntimeError, match="无权限删除该项目"):
            await delete_learning_program(mock_client, "360141")


class TestAdmDeleteLearningProgramTool:
    async def test_tool_registered(self):
        tools = admin_mcp._tool_manager._tools
        assert "adm_delete_learning_program" in tools
        tool_fn = tools["adm_delete_learning_program"].fn
        assert tool_fn.__name__ == "adm_delete_learning_program"

    async def test_tool_success(self, mock_client):
        mock_client.post.return_value = {"status": True, "error_code": 0}
        with patch("umu_sdk.adapters.mcp.admin._umu_client", mock_client):
            tools = admin_mcp._tool_manager._tools
            result = json.loads(await tools["adm_delete_learning_program"].fn(program_id="360141"))
        assert result["success"] is True
        assert result["data"]["deleted"] is True

    async def test_tool_not_authenticated(self, mock_client):
        mock_client.auth.is_authenticated.return_value = False
        with patch("umu_sdk.adapters.mcp.admin._umu_client", mock_client):
            tools = admin_mcp._tool_manager._tools
            result = json.loads(await tools["adm_delete_learning_program"].fn(program_id="360141"))
        assert result["success"] is False
        assert "NOT_AUTHENTICATED" in result["error_code"]



class TestListPersonalLearningProgramsOperation:
    async def test_success(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {
                "list": [
                    {
                        "program_id": 1,
                        "program_title": "项目",
                        "setup": {},
                        "creator": {"umu_id": "11", "user_name": "Alice"},
                    }
                ],
                "page_info": {"list_total_num": 1},
            },
        }
        result = await list_personal_learning_programs(
            mock_client, scope="owned", page=1, page_size=20
        )
        assert result["total"] == 1
        assert result["programs"][0]["program_id"] == "1"

    async def test_invalid_scope(self, mock_client):
        with pytest.raises(UMUError, match="不支持的 scope"):
            await list_personal_learning_programs(mock_client, scope="invalid")


class TestAdmListPersonalLearningProgramsTool:
    async def test_tool_registered(self):
        tools = admin_mcp._tool_manager._tools
        assert "adm_list_personal_learning_programs" in tools
        tool_fn = tools["adm_list_personal_learning_programs"].fn
        assert tool_fn.__name__ == "adm_list_personal_learning_programs"

    async def test_tool_success(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {
                "list": [
                    {
                        "program_id": 2,
                        "program_title": "Admin 项目",
                        "setup": {},
                        "creator": {"umu_id": "22", "user_name": "Bob"},
                    }
                ],
                "page_info": {"list_total_num": 1},
            },
        }
        with patch("umu_sdk.adapters.mcp.admin._umu_client", mock_client):
            with patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None):
                tools = admin_mcp._tool_manager._tools
                result = json.loads(
                    await tools["adm_list_personal_learning_programs"].fn(
                        scope="owned", page=1, page_size=20
                    )
                )
        assert result["success"] is True
        assert result["data"]["programs"][0]["program_id"] == "2"
