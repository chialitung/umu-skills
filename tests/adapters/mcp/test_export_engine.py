"""ExportEngine 单元测试."""

from __future__ import annotations

import csv
import os
from unittest.mock import MagicMock

import pytest

from umu_sdk.adapters.mcp.export_engine import ExportEngine


@pytest.fixture
def engine():
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return ExportEngine(client)


class TestExportEngineToExcel:
    def test_to_excel_with_records(self, engine, tmp_path):
        output_path = str(tmp_path / "test.xlsx")
        records = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        result = engine.to_excel(records, output_path, sheet_name="Users")
        assert result == output_path
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0

    def test_to_excel_empty_records(self, engine, tmp_path):
        output_path = str(tmp_path / "empty.xlsx")
        result = engine.to_excel([], output_path)
        assert result == output_path
        assert os.path.exists(output_path)


class TestExportEngineToCsv:
    def test_to_csv_with_records(self, engine, tmp_path):
        output_path = str(tmp_path / "test.csv")
        records = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        result = engine.to_csv(records, output_path)
        assert result == output_path
        assert os.path.exists(output_path)

        with open(output_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["id"] == "1"

    def test_to_csv_empty_records(self, engine, tmp_path):
        output_path = str(tmp_path / "empty.csv")
        result = engine.to_csv([], output_path)
        assert result == output_path
        assert os.path.exists(output_path)


class TestExportEngineAccountDisplayName:
    def test_user_display_name(self):
        account = {"account_type": "user", "user_name": "Alice", "account": "alice@umu.cn"}
        assert ExportEngine._account_display_name(account) == "Alice"

    def test_class_display_name(self):
        account = {"account_type": "class", "class_name": "", "account": "Class A"}
        assert ExportEngine._account_display_name(account) == "Class A"

    def test_unknown_type_fallback(self):
        account = {"account_type": "other", "account": "Fallback"}
        assert ExportEngine._account_display_name(account) == "Fallback"


class TestExportEngineExportRecords:
    def test_export_records_to_excel(self, engine, tmp_path):
        output_path = str(tmp_path / "records.xlsx")
        records = [{"a": "1"}, {"a": "2"}]
        result = engine.export_records(records, output_path, sheet_name="Data")
        assert result["file_path"] == output_path
        assert result["total_records"] == 2
        assert os.path.exists(output_path)

    def test_export_records_to_csv(self, engine, tmp_path):
        output_path = str(tmp_path / "records.csv")
        records = [{"a": "1"}, {"a": "2"}]
        result = engine.export_records(records, output_path)
        assert result["file_path"] == output_path
        assert result["total_records"] == 2
        assert os.path.exists(output_path)

        with open(output_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2


class TestExportEngineAdminAccounts:
    def test_export_admin_accounts(self, engine, tmp_path):
        output_path = str(tmp_path / "accounts.xlsx")
        engine.client.get.return_value = {
            "status": True,
            "data": {
                "list": [
                    {"umu_id": 1, "user_name": "Alice", "email": "alice@umu.cn"},
                    {"umu_id": 2, "user_name": "Bob", "email": "bob@umu.cn"},
                ],
                "page_info": {"list_total_num": 2},
            },
        }
        result = engine.export_admin_accounts(output_path)
        assert result["file_path"] == output_path
        assert result["total_records"] == 2
        assert os.path.exists(output_path)

        called_url = engine.client.get.call_args[0][0]
        assert "/ajax/enterprise/getUserList" in called_url


class TestExportEngineLearningRecords:
    def test_export_learning_records(self, engine, tmp_path):
        output_path = str(tmp_path / "learning_records.xlsx")
        engine.client.get.return_value = {
            "error_code": 0,
            "data": {
                "list": [
                    {"group_id": "101", "title": "Course A", "user_name": "Alice"},
                    {"group_id": "102", "title": "Course B", "user_name": "Bob"},
                ],
                "page_info": {"list_total_num": 2},
            },
        }
        result = engine.export_learning_records(
            output_path,
            start_day="2026-06-01",
            end_day="2026-06-30",
        )
        assert result["file_path"] == output_path
        assert result["total_records"] == 2
        assert os.path.exists(output_path)

        called_url = engine.client.get.call_args[0][0]
        assert "/uapi/v1/dashboard/learning-group-list" in called_url


class TestExportEngineProgramPermissions:
    def test_export_program_permissions(self, engine, tmp_path):
        output_path = str(tmp_path / "program_permissions.xlsx")

        def mock_get(url, **kwargs):
            path = url.replace("https://www.umu.cn", "")
            if path == "/api/program/getlist":
                return {
                    "status": True,
                    "data": {
                        "list": [
                            {
                                "program_id": 1,
                                "program_title": "Program A",
                                "access_code": "abc123",
                            }
                        ],
                        "page_info": {"list_total_num": 1},
                    },
                }
            if path == "/api/group/getAccessPermissionOption":
                return {
                    "status": True,
                    "data": {"selected_option": "3"},
                }
            if path == "/api/manage/getcourseaccesslist":
                return {
                    "status": True,
                    "data": {
                        "list": [
                            {
                                "account": "alice@umu.cn",
                                "account_type": "user",
                                "id": "1001",
                                "user_name": "Alice",
                            }
                        ],
                        "page_info": {"list_total_num": 1},
                    },
                }
            return {"status": True, "data": {}}

        engine.client.get.side_effect = mock_get
        result = engine.export_program_permissions(output_path)
        assert result["file_path"] == output_path
        assert result["total_programs"] == 1
        assert result["total_records"] == 1
        assert os.path.exists(output_path)
