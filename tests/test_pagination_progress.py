"""分页进度上报辅助函数测试."""

from __future__ import annotations

import inspect
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from umu_sdk.adapters.mcp.utils import report_pagination_progress


class TestReportPaginationProgress:
    def test_initial_total_on_first_page(self) -> None:
        stderr = StringIO()
        report_pagination_progress("adm_test", 1, 0, 100, 20, file=stderr)
        output = stderr.getvalue()
        assert "共 100 条" in output
        assert "预计 5 页" in output
        assert "已获取第 1 页" in output

    def test_progress_percentage(self) -> None:
        stderr = StringIO()
        report_pagination_progress("adm_test", 2, 40, 100, 20, file=stderr)
        output = stderr.getvalue()
        assert "已获取第 2 页" in output
        assert "40 / 100 条 (40%)" in output

    def test_completion_message(self) -> None:
        stderr = StringIO()
        report_pagination_progress(
            "adm_test", 5, 100, 100, 20, is_complete=True, file=stderr
        )
        output = stderr.getvalue()
        assert "获取完成" in output
        assert "共 100 条" in output
        assert "合计 5 页" in output

    def test_safety_limit_warning(self) -> None:
        stderr = StringIO()
        report_pagination_progress(
            "adm_test", 51, 1000, 2000, 20, is_safety_limit=True, file=stderr
        )
        output = stderr.getvalue()
        assert "警告" in output
        assert "50 页安全上限" in output
        assert "已获取 1000 条" in output

    def test_zero_total_no_percentage(self) -> None:
        stderr = StringIO()
        report_pagination_progress("adm_test", 1, 0, 0, 20, file=stderr)
        output = stderr.getvalue()
        assert "0 / 0 条" in output
        assert "%" not in output

    def test_output_goes_to_stderr_by_default(self) -> None:
        """未传入 file 时默认应为 None，运行时解析为当前 sys.stderr."""
        sig = inspect.signature(report_pagination_progress)
        default = sig.parameters["file"].default
        assert default is None

        stderr = StringIO()
        with patch.object(sys, "stderr", stderr):
            report_pagination_progress("adm_test", 1, 0, 0, 20)
        assert "0 / 0 条" in stderr.getvalue()

    def test_fetched_exceeds_total_caps_percentage(self) -> None:
        """已获取数超过总数时百分比应封顶在 100%."""
        stderr = StringIO()
        report_pagination_progress("adm_test", 2, 120, 100, 20, file=stderr)
        output = stderr.getvalue()
        assert "120 / 100 条 (100%)" in output

    def test_single_page_complete(self) -> None:
        """只有一页时完成消息应正确。"""
        stderr = StringIO()
        report_pagination_progress(
            "adm_test", 1, 5, 5, 20, is_complete=True, file=stderr
        )
        output = stderr.getvalue()
        assert "获取完成，共 5 条，合计 1 页" in output
