# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习项目学员名单与学习任务名单查询."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .utils import report_pagination_progress

logger = logging.getLogger("umu.mcp.program_student_manager")


class ProgramStudentManager:
    """学习项目学员/学习任务名单管理器."""

    def __init__(self, client: Any, base_url: str):
        """初始化.

        Args:
            client: UMUClient 实例.
            base_url: UMU 基础 URL.
        """
        self.client = client
        self.base_url = base_url

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def list_participants(
        self,
        program_id: str,
        status_filter: str = "all",
        include_disabled: bool = True,
        page: int = 1,
        page_size: int = 20,
        fetch_all: bool = False,
    ) -> dict[str, Any]:
        """查询学习项目学员名单."""
        return self._list_students(
            endpoint="/api/program/getstudentlist",
            program_id=program_id,
            status_filter=status_filter,
            include_disabled=include_disabled,
            page=page,
            page_size=page_size,
            fetch_all=fetch_all,
            search_condition={"is_enroll_student": 0, "is_require": 0, "is_student": 0},
        )

    def list_learning_tasks(
        self,
        program_id: str,
        status_filter: str = "all",
        include_disabled: bool = True,
        page: int = 1,
        page_size: int = 20,
        fetch_all: bool = False,
    ) -> dict[str, Any]:
        """查询学习项目学习任务学员名单."""
        return self._list_students(
            endpoint="/api/program/getstudenttasklist",
            program_id=program_id,
            status_filter=status_filter,
            include_disabled=include_disabled,
            page=page,
            page_size=page_size,
            fetch_all=fetch_all,
            search_condition={},
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _list_students(
        self,
        endpoint: str,
        program_id: str,
        status_filter: str,
        include_disabled: bool,
        page: int,
        page_size: int,
        fetch_all: bool,
        search_condition: dict[str, Any],
    ) -> dict[str, Any]:
        status_map = {"all": "0", "completed": "1", "uncompleted": "2"}
        if status_filter not in status_map:
            raise ValueError(f"不支持的 status_filter: {status_filter}")

        params_base = {
            "t": str(int(time.time() * 1000)),
            "program_id": str(program_id),
            "type": status_map[status_filter],
            "filter_disabled_user": "0" if include_disabled else "1",
            "search_condition": json.dumps(search_condition, ensure_ascii=False),
            "size": str(page_size),
        }

        def _fetch_page(
            p: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
            params = {**params_base, "page": str(p)}
            resp = self.client.get(self.client.desktop_url(endpoint), params=params)
            if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
                raise RuntimeError(resp.get("error", "查询学习项目学员名单失败"))

            data = resp.get("data", {})
            table_body = data.get("table_body", {})
            page_info = table_body.get("page_info", {})
            raw_students = table_body.get("list", [])
            table_head = data.get("table_head", {})
            columns = table_head.get("list", [])

            students = [self._format_student(row, columns) for row in raw_students]
            return students, page_info, data.get("data_count", {}), table_head

        if fetch_all:
            all_students: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            latest_data_count: dict[str, Any] = {}
            latest_table_head: dict[str, Any] = {}

            while True:
                page_students, page_info, data_count, table_head = _fetch_page(current_page)
                all_students.extend(page_students)
                total_all = int(page_info.get("list_total_num", 0) or 0)
                latest_data_count = data_count
                latest_table_head = table_head

                report_pagination_progress(
                    f"program_student_manager_{endpoint.split('/')[-1]}",
                    current_page,
                    len(all_students),
                    total_all,
                    page_size,
                )

                if not page_students or len(all_students) >= total_all:
                    report_pagination_progress(
                        f"program_student_manager_{endpoint.split('/')[-1]}",
                        current_page,
                        len(all_students),
                        total_all,
                        page_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        f"program_student_manager_{endpoint.split('/')[-1]}",
                        current_page,
                        len(all_students),
                        total_all,
                        page_size,
                        is_safety_limit=True,
                    )
                    logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                    break

                current_page += 1

            return self._build_result(
                all_students,
                latest_data_count,
                {
                    "total": total_all,
                    "total_pages": current_page,
                    "current_page": current_page,
                    "page_size": page_size,
                },
                latest_table_head,
            )

        students, page_info, data_count, table_head = _fetch_page(page)
        return self._build_result(students, data_count, page_info, table_head)

    def _format_student(
        self,
        row: dict[str, Any],
        columns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """解析单条学员记录，把动态列转成 modules / courses."""
        formatted = dict(row)
        modules: list[dict[str, Any]] = []
        courses: list[dict[str, Any]] = []

        for col in columns:
            col_type = col.get("type", "")
            field_name = col.get("field_name", "")
            if not field_name or field_name not in formatted:
                continue

            if col_type == "module":
                raw_value = formatted.pop(field_name)
                module_id = str(col.get("id", "")).replace("module_", "")
                modules.append({
                    "module_id": module_id,
                    "module_title": col.get("title", ""),
                    "field_name": field_name,
                    "raw_value": raw_value,
                })
            elif col_type == "group":
                raw_value = formatted.pop(field_name)
                group_id = str(col.get("id", ""))
                course_info: dict[str, Any] = {
                    "group_id": group_id,
                    "course_title": col.get("title", ""),
                    "is_require": col.get("is_require", ""),
                    "share_url": col.get("share_url", ""),
                    "field_name": field_name,
                }
                if isinstance(raw_value, dict):
                    course_info["complete_rate"] = raw_value.get("complete_rate", 0)
                else:
                    course_info["raw_value"] = raw_value
                courses.append(course_info)

        formatted["modules"] = modules
        formatted["courses"] = courses
        return formatted

    def _build_result(
        self,
        students: list[dict[str, Any]],
        data_count: dict[str, Any],
        page_info: dict[str, Any],
        table_head: dict[str, Any],
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
        }
        if "exist_learning_task" in data_count:
            summary["has_learning_task"] = bool(data_count.get("exist_learning_task", 0))

        return {
            "summary": summary,
            "students": students,
            "pagination": {
                "total": int(page_info.get("list_total_num", 0) or 0),
                "total_pages": int(page_info.get("total_page_num", 0) or 0),
                "current_page": int(page_info.get("current_page", 1)),
                "page_size": int(page_info.get("size", 20)),
            },
            "table_head": table_head,
        }
