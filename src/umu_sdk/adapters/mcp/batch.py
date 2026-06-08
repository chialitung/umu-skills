"""批量操作模块 — 支持从文件导入账号并批量完成课程.

Usage:
    # 从 CSV 导入账号
    accounts = AccountImporter.from_csv("/path/to/accounts.csv")

    # 批量执行
    executor = BatchExecutor(max_concurrency=3)
    report = await executor.execute(
        accounts=accounts,
        task_func=complete_course_for_user,
        course_identifier="course_123",
        base_url="https://www.umu.cn",
    )
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ...core.client import UMUClient


class AccountSource(str, Enum):
    """账号来源格式."""

    CSV = "csv"
    JSON = "json"


class BatchStatus(str, Enum):
    """批量任务状态."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # 部分成功
    FAILED = "failed"


class AccountResult(BaseModel):
    """单个账号的执行结果."""

    username: str
    success: bool = True
    error_message: str = ""
    completed_lessons: int = 0
    total_lessons: int = 0
    duration_seconds: float = 0.0
    details: list[dict[str, Any]] = Field(default_factory=list)


class BatchReport(BaseModel):
    """批量操作报告."""

    total_accounts: int
    successful: int
    failed: int
    total_duration_seconds: float
    results: list[AccountResult]
    status: BatchStatus


@dataclass
class AccountCredentials:
    """账号凭据."""

    username: str
    password: str
    nickname: str | None = None


class AccountImporter:
    """账号导入器 — 从 CSV/JSON 文件导入账号列表."""

    @staticmethod
    def from_csv(file_path: str | Path) -> list[AccountCredentials]:
        """从 CSV 文件导入账号.

        CSV 格式（支持两种格式）:
            格式1: username,password
            格式2: username,password,nickname

        第一行可以是表头，会被自动检测并跳过。

        Args:
            file_path: CSV 文件路径

        Returns:
            账号凭据列表
        """
        accounts: list[AccountCredentials] = []
        path = Path(file_path)

        with path.open("r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            return accounts

        # 检测是否有表头
        first_row = rows[0]
        header_keywords = {
            "username",
            "user",
            "账号",
            "用户名",
            "password",
            "pass",
            "密码",
            "nickname",
            "nick",
            "昵称",
        }
        has_header = any(
            h.strip().lower() in header_keywords for h in first_row if h.strip()
        )

        start_idx = 1 if has_header else 0

        for row in rows[start_idx:]:
            if len(row) < 2:
                continue
            username = row[0].strip()
            password = row[1].strip()
            if not username or not password:
                continue
            accounts.append(
                AccountCredentials(
                    username=username,
                    password=password,
                    nickname=row[2].strip() if len(row) > 2 and row[2].strip() else None,
                )
            )

        return accounts

    @staticmethod
    def from_json(file_path: str | Path) -> list[AccountCredentials]:
        """从 JSON 文件导入账号.

        JSON 格式:
            [
                {"username": "user1", "password": "pass1"},
                {"username": "user2", "password": "pass2", "nickname": "昵称2"}
            ]

        Args:
            file_path: JSON 文件路径

        Returns:
            账号凭据列表
        """
        path = Path(file_path)
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        accounts: list[AccountCredentials] = []

        if not isinstance(data, list):
            raise ValueError("JSON 文件必须包含一个数组")

        for item in data:
            if not isinstance(item, dict):
                continue
            username = (
                item.get("username")
                or item.get("user")
                or item.get("账号")
            )
            password = (
                item.get("password")
                or item.get("pass")
                or item.get("密码")
            )
            if username and password:
                accounts.append(
                    AccountCredentials(
                        username=str(username).strip(),
                        password=str(password).strip(),
                        nickname=item.get("nickname") or item.get("昵称"),
                    )
                )

        return accounts

    @classmethod
    def import_accounts(
        cls,
        file_path: str | Path,
        source: AccountSource | None = None,
    ) -> list[AccountCredentials]:
        """自动检测格式并导入账号.

        Args:
            file_path: 账号文件路径
            source: 文件格式，None 则根据扩展名自动检测

        Returns:
            账号凭据列表

        Raises:
            ValueError: 无法识别文件格式
        """
        path = Path(file_path)

        if not path.exists():
            raise ValueError(f"文件不存在: {path}")

        if source is None:
            ext = path.suffix.lower()
            if ext == ".csv":
                source = AccountSource.CSV
            elif ext in (".json",):
                source = AccountSource.JSON
            else:
                raise ValueError(
                    f"无法识别文件格式: '{ext}'，请指定 source 参数（csv 或 json）"
                )

        if source == AccountSource.CSV:
            return cls.from_csv(path)
        elif source == AccountSource.JSON:
            return cls.from_json(path)
        else:
            raise ValueError(f"不支持的格式: {source}")


class BatchExecutor:
    """批量执行器 — 控制并发执行批量任务."""

    def __init__(
        self,
        max_concurrency: int = 3,
        delay_between_accounts: float = 1.0,
        delay_between_lessons: float = 0.5,
    ):
        self.max_concurrency = max_concurrency
        self.delay_between_accounts = delay_between_accounts
        self.delay_between_lessons = delay_between_lessons
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def execute(
        self,
        accounts: list[AccountCredentials],
        task_func: Callable[[UMUClient, str], Any],
        course_identifier: str,
        base_url: str,
    ) -> BatchReport:
        """执行批量任务.

        Args:
            accounts: 账号列表
            task_func: 任务函数，接收 (client, course_identifier) 参数，返回 dict
            course_identifier: 课程标识
            base_url: UMU 基础 URL

        Returns:
            BatchReport: 批量执行报告
        """
        results: list[AccountResult] = []
        start_time = time.time()

        async def run_single(account: AccountCredentials) -> AccountResult:
            """执行单个账号的任务."""
            account_start = time.time()
            result = AccountResult(username=account.username)
            client: UMUClient | None = None

            async with self._semaphore:
                try:
                    # 账号间启动延迟
                    if self.delay_between_accounts > 0:
                        await asyncio.sleep(self.delay_between_accounts)

                    # 创建独立客户端
                    client = UMUClient(
                        base_url=base_url,
                    )

                    # 登录
                    client.login(account.username, account.password)

                    # 执行任务
                    task_result = await task_func(client, course_identifier)

                    result.success = True
                    result.completed_lessons = task_result.get("completed_lessons", 0)
                    result.total_lessons = task_result.get("total_lessons", 0)
                    result.details = task_result.get("details", [])

                except Exception as e:
                    result.success = False
                    result.error_message = str(e)

                finally:
                    if client is not None:
                        client.close()
                    result.duration_seconds = time.time() - account_start

            return result

        # 并发执行所有账号
        tasks = [run_single(acc) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        processed_results: list[AccountResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                processed_results.append(
                    AccountResult(
                        username=accounts[i].username,
                        success=False,
                        error_message=str(r),
                    )
                )
            else:
                processed_results.append(r)

        total_duration = time.time() - start_time
        successful = sum(1 for r in processed_results if r.success)
        failed = len(processed_results) - successful

        if failed == 0:
            status = BatchStatus.COMPLETED
        elif successful == 0:
            status = BatchStatus.FAILED
        else:
            status = BatchStatus.PARTIAL

        return BatchReport(
            total_accounts=len(accounts),
            successful=successful,
            failed=failed,
            total_duration_seconds=total_duration,
            results=processed_results,
            status=status,
        )
