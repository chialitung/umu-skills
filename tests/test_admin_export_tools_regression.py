"""Admin 导出工具回归测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import os

from umu_sdk.adapters.mcp.admin import (
    adm_export_accounts,
    adm_export_learning_records,
)


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


class TestAdminExportAccounts:
    async def test_export_accounts(self, mock_client, tmp_path):
        mock_client.get.return_value = {
            "status": True,
            "data": {
                "list": [
                    {"umu_id": 1, "user_name": "Alice", "email": "alice@umu.cn"},
                    {"umu_id": 2, "user_name": "Bob", "email": "bob@umu.cn"},
                ],
                "page_info": {"list_total_num": 2},
            },
        }
        output_path = str(tmp_path / "accounts.xlsx")

        with _auth_patch(mock_client):
            result = json.loads(await adm_export_accounts(output_path=output_path))

        assert result["success"] is True
        assert result["data"]["file_path"] == output_path
        assert result["data"]["total_records"] == 2
        assert os.path.exists(output_path)


class TestAdminExportLearningRecords:
    async def test_export_learning_records(self, mock_client, tmp_path):
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"group_id": "101", "title": "Course A", "user_name": "Alice"},
                    {"group_id": "102", "title": "Course B", "user_name": "Bob"},
                ],
                "page_info": {"list_total_num": 2},
            },
        }
        output_path = str(tmp_path / "learning_records.xlsx")

        with _auth_patch(mock_client):
            result = json.loads(
                await adm_export_learning_records(
                    output_path=output_path,
                    start_day="2026-06-01",
                    end_day="2026-06-30",
                )
            )

        assert result["success"] is True
        assert result["data"]["file_path"] == output_path
        assert result["data"]["total_records"] == 2
        assert os.path.exists(output_path)
