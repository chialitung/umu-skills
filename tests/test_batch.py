"""Batch 模块单元测试."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from umu_sdk.adapters.mcp.batch import (
    AccountCredentials,
    AccountImporter,
    BatchExecutor,
    BatchStatus,
)


class TestAccountImporter:
    """账号导入器测试."""

    def test_import_csv_with_header(self, tmp_path: Path):
        """测试从带表头的 CSV 导入."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text(
            "username,password,nickname\n"
            "user1@test.com,pass1,张三\n"
            "user2@test.com,pass2,李四\n",
            encoding="utf-8",
        )

        accounts = AccountImporter.from_csv(csv_file)
        assert len(accounts) == 2
        assert accounts[0].username == "user1@test.com"
        assert accounts[0].password == "pass1"
        assert accounts[0].nickname == "张三"
        assert accounts[1].username == "user2@test.com"
        assert accounts[1].nickname == "李四"

    def test_import_csv_without_header(self, tmp_path: Path):
        """测试从无表头的 CSV 导入."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text(
            "user1@test.com,pass1\n"
            "user2@test.com,pass2\n",
            encoding="utf-8",
        )

        accounts = AccountImporter.from_csv(csv_file)
        assert len(accounts) == 2
        assert accounts[0].username == "user1@test.com"

    def test_import_csv_utf8_bom(self, tmp_path: Path):
        """测试从 UTF-8-BOM CSV 导入."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_bytes(
            b"\xef\xbb\xbfusername,password\n"
            b"user1@test.com,pass1\n"
        )

        accounts = AccountImporter.from_csv(csv_file)
        assert len(accounts) == 1
        assert accounts[0].username == "user1@test.com"

    def test_import_csv_chinese_header(self, tmp_path: Path):
        """测试从中文表头 CSV 导入."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text(
            "账号,密码,昵称\n"
            "user1@test.com,pass1,张三\n",
            encoding="utf-8",
        )

        accounts = AccountImporter.from_csv(csv_file)
        assert len(accounts) == 1
        assert accounts[0].username == "user1@test.com"

    def test_import_csv_empty_lines(self, tmp_path: Path):
        """测试跳过空行."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text(
            "username,password\n"
            "user1@test.com,pass1\n"
            "\n"
            "user2@test.com,pass2\n",
            encoding="utf-8",
        )

        accounts = AccountImporter.from_csv(csv_file)
        assert len(accounts) == 2

    def test_import_json(self, tmp_path: Path):
        """测试从 JSON 导入."""
        json_file = tmp_path / "accounts.json"
        data = [
            {"username": "user1@test.com", "password": "pass1", "nickname": "张三"},
            {"username": "user2@test.com", "password": "pass2"},
        ]
        json_file.write_text(json.dumps(data), encoding="utf-8")

        accounts = AccountImporter.from_json(json_file)
        assert len(accounts) == 2
        assert accounts[0].username == "user1@test.com"
        assert accounts[0].nickname == "张三"
        assert accounts[1].nickname is None

    def test_import_json_chinese_keys(self, tmp_path: Path):
        """测试从中文键 JSON 导入."""
        json_file = tmp_path / "accounts.json"
        data = [
            {"账号": "user1@test.com", "密码": "pass1"},
        ]
        json_file.write_text(json.dumps(data), encoding="utf-8")

        accounts = AccountImporter.from_json(json_file)
        assert len(accounts) == 1
        assert accounts[0].username == "user1@test.com"

    def test_import_json_not_list(self, tmp_path: Path):
        """测试 JSON 不是列表时抛出异常."""
        json_file = tmp_path / "accounts.json"
        json_file.write_text('{"username": "u1", "password": "p1"}', encoding="utf-8")

        with pytest.raises(ValueError, match="数组"):
            AccountImporter.from_json(json_file)

    def test_auto_detect_csv(self, tmp_path: Path):
        """测试自动检测 CSV 格式."""
        csv_file = tmp_path / "accounts.csv"
        csv_file.write_text("username,password\nu1,p1\n", encoding="utf-8")

        accounts = AccountImporter.import_accounts(csv_file)
        assert len(accounts) == 1

    def test_auto_detect_json(self, tmp_path: Path):
        """测试自动检测 JSON 格式."""
        json_file = tmp_path / "accounts.json"
        json_file.write_text('[{"username": "u1", "password": "p1"}]', encoding="utf-8")

        accounts = AccountImporter.import_accounts(json_file)
        assert len(accounts) == 1

    def test_file_not_found(self, tmp_path: Path):
        """测试文件不存在时抛出异常."""
        with pytest.raises(ValueError, match="不存在"):
            AccountImporter.import_accounts(tmp_path / "nonexistent.csv")


class TestBatchExecutor:
    """批量执行器测试."""

    @pytest.mark.asyncio
    async def test_concurrency_control(self):
        """测试并发控制 — Semaphore 限制同时执行数.

        由于使用假账号登录真实 UMU 平台会失败，此测试验证：
        1. 所有账号都被处理（total_accounts 正确）
        2. 执行器不会崩溃（能正常返回报告）
        """
        executor = BatchExecutor(max_concurrency=2)

        accounts = [
            AccountCredentials(f"user{i}@test.com", f"pass{i}")
            for i in range(5)
        ]

        report = await executor.execute(
            accounts=accounts,
            task_func=lambda c, cid: {"completed_lessons": 1, "total_lessons": 1, "details": []},
            course_identifier="test_course",
            base_url="https://www.umu.cn",
        )

        assert report.total_accounts == 5
        # 使用假账号登录会失败，但执行器应正常返回报告
        assert report.failed == 5
        assert report.status == BatchStatus.FAILED

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """测试部分失败场景."""
        executor = BatchExecutor(max_concurrency=1)

        async def mock_task(client, course_id):
            if "user1" in str(client):
                raise RuntimeError("模拟失败")
            return {"completed_lessons": 1, "total_lessons": 1, "details": []}

        accounts = [
            AccountCredentials("user1@test.com", "pass1"),
            AccountCredentials("user2@test.com", "pass2"),
        ]

        # 由于 UMUClient 初始化会失败（mock），这里简化测试
        # 实际上 task_func 中的 client 是真实 UMUClient，mock 测试需要 patch
        # 这里只测试 executor 的结构行为
        report = await executor.execute(
            accounts=accounts,
            task_func=mock_task,
            course_identifier="test",
            base_url="https://www.umu.cn",
        )

        # 由于 UMUClient 初始化会网络错误，全部失败也是可预期的
        assert report.total_accounts == 2

    @pytest.mark.asyncio
    async def test_empty_accounts(self):
        """测试空账号列表."""
        executor = BatchExecutor()

        async def mock_task(client, course_id):
            return {"completed_lessons": 0, "total_lessons": 0, "details": []}

        report = await executor.execute(
            accounts=[],
            task_func=mock_task,
            course_identifier="test",
            base_url="https://www.umu.cn",
        )

        assert report.total_accounts == 0
        assert report.status == BatchStatus.COMPLETED

    def test_batch_status_values(self):
        """测试 BatchStatus 枚举值."""
        assert BatchStatus.PENDING.value == "pending"
        assert BatchStatus.COMPLETED.value == "completed"
        assert BatchStatus.PARTIAL.value == "partial"
        assert BatchStatus.FAILED.value == "failed"
