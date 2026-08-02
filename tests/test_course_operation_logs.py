"""课程操作日志 operation 单元测试."""

from __future__ import annotations

from typing import Any

import pytest

from umu_sdk.core.errors import UMUError
from umu_sdk.tools.operations.course_management import get_course_operation_logs


def _raw_log(
    log_id: str,
    umu_id: str,
    action_type: str,
    create_time: int,
    user_name: str = "Alice",
    email: str = "alice@example.com",
) -> dict[str, Any]:
    return {
        "id": log_id,
        "enterprise_id": "11018",
        "umu_id": umu_id,
        "log_type": "1",
        "log_obj_id": "7389227",
        "action_type": action_type,
        "create_time": str(create_time),
        "log_detail": {},
        "user_info": {
            "user_name": user_name,
            "avatar": "",
            "login_name": "",
            "email": email,
            "phone": "",
        },
    }


class _StubClient:
    """轻量 UMUClient stub，按页返回预置响应."""

    def __init__(self, pages: dict[int, tuple[list[dict[str, Any]], int]]):
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    def desktop_url(self, path: str) -> str:
        return f"https://www.umu.cn{path}"

    def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        assert params is not None
        self.calls.append({"url": url, "params": params})
        page = int(params["page"])
        items, total = self._pages.get(page, ([], 0))
        return {
            "error_code": 0,
            "error_message": "",
            "data": {"page_info": {"list_total_num": total}, "list": items},
        }


class _ErrorClient(_StubClient):
    def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return {"error_code": 403, "error_message": "无权限"}


def _client_with(logs: list[dict[str, Any]]) -> _StubClient:
    return _StubClient({1: (logs, len(logs))})


class TestGetCourseOperationLogs:
    async def test_single_page_formats_logs(self):
        client = _client_with([
            _raw_log("2", "111", "2000", 1700000200),
            _raw_log("1", "222", "1001", 1700000100, user_name="Bob", email="bob@example.com"),
        ])
        result = await get_course_operation_logs(client, "7389227")

        assert len(result["logs"]) == 2
        first = result["logs"][0]
        assert first["log_id"] == "2"
        assert first["umu_id"] == "111"
        assert first["action_type"] == "2000"
        assert first["create_time"] == "1700000200"
        assert first["create_time_readable"] != ""
        assert first["user_info"]["user_name"] == "Alice"
        assert result["pagination"]["total_all"] == 2

        call = client.calls[0]
        assert call["url"] == "https://www.umu.cn/uapi/v1/group/get-operation-log-list"
        assert call["params"]["log_type"] == "1"
        assert call["params"]["log_obj_id"] == "7389227"
        assert call["params"]["sort"] == "desc"

    async def test_creator_confident_when_earliest_is_create_action(self):
        client = _client_with([
            _raw_log("2", "111", "2009", 1700000200),
            _raw_log("1", "222", "1001", 1700000100, user_name="Bob", email="bob@example.com"),
        ])
        result = await get_course_operation_logs(client, "7389227")

        creator = result["creator"]
        assert creator is not None
        assert creator["umu_id"] == "222"
        assert creator["user_name"] == "Bob"
        assert creator["email"] == "bob@example.com"
        assert creator["creator_confident"] is True
        assert creator["note"] == ""

    async def test_creator_not_confident_without_create_action(self):
        client = _client_with([
            _raw_log("2", "111", "2009", 1700000200),
            _raw_log("1", "222", "2000", 1700000100),
        ])
        result = await get_course_operation_logs(client, "7389227")

        creator = result["creator"]
        assert creator is not None
        assert creator["umu_id"] == "222"
        assert creator["creator_confident"] is False
        assert "仅供参考" in creator["note"]

    async def test_creator_none_when_no_logs(self):
        client = _client_with([])
        result = await get_course_operation_logs(client, "7389227")
        assert result["logs"] == []
        assert result["creator"] is None

    async def test_fetch_all_merges_pages(self):
        client = _StubClient({
            1: ([_raw_log(str(i), "111", "2000", 1700000000 + i) for i in range(3)], 5),
            2: ([_raw_log("4", "222", "1001", 1699999900), _raw_log("5", "222", "1001", 1699999800)], 5),
        })
        result = await get_course_operation_logs(client, "7389227", fetch_all=True, page_size=3)

        assert len(result["logs"]) == 5
        assert len(client.calls) == 2
        creator = result["creator"]
        assert creator is not None
        assert creator["umu_id"] == "222"
        assert creator["creator_confident"] is True

    async def test_action_types_filter_keeps_creator_from_full_logs(self):
        client = _client_with([
            _raw_log("2", "111", "2000", 1700000200),
            _raw_log("1", "222", "1001", 1700000100),
        ])
        result = await get_course_operation_logs(client, "7389227", action_types="2000")

        assert [log["action_type"] for log in result["logs"]] == ["2000"]
        assert result["filter"]["action_types"] == ["2000"]
        creator = result["creator"]
        assert creator is not None
        assert creator["creator_confident"] is True

    async def test_error_response_raises(self):
        with pytest.raises(UMUError) as exc_info:
            await get_course_operation_logs(_ErrorClient({}), "7389227")
        assert exc_info.value.code == "GET_COURSE_OPERATION_LOGS_FAILED"

    async def test_invalid_sort_raises(self):
        with pytest.raises(UMUError) as exc_info:
            await get_course_operation_logs(_client_with([]), "7389227", sort="sideways")
        assert exc_info.value.code == "INVALID_SORT"
