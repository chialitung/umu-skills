#!/usr/bin/env python3
"""Export teacher-created course access permissions to Excel.

Uses the umu-teacher MCP-equivalent APIs via umu_sdk's UMUClient.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project src is on path when run from anywhere
script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir / "src"))

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

from umu_sdk.core.client import UMUClient
from umu_sdk.core.credential_loader import load_credentials


OUTPUT_PATH = Path("C:/Users/jiali/Desktop/umu_teacher_courses_permissions.xlsx")
BASE_URL = os.getenv("UMU_BASE_URL", "https://www.umu.cn")


def permission_text(access_permission: int) -> str:
    mapping = {0: "关闭", 1: "公开", 2: "企业内公开", 3: "指定账户"}
    return mapping.get(access_permission, f"未知({access_permission})")


def fetch_created_courses(client: UMUClient) -> list[dict[str, Any]]:
    """Fetch all courses created by the teacher, paginating as needed."""
    all_courses: list[dict[str, Any]] = []
    page = 1
    page_size = 50
    total = 0

    while True:
        resp = client.get(
            client.desktop_url("/api/group/getgrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "from_type": "web",
                "order": "update_time",
                "page": str(page),
                "size": str(page_size),
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取已创建课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])
        total = int(page_info.get("list_total_num", 0) or 0)

        for item in course_list:
            info = item.get("groupInfo", {})
            all_courses.append({
                "group_id": str(info.get("id", "")),
                "title": info.get("title", ""),
                "access_code": info.get("access_code", ""),
            })

        if not course_list or len(all_courses) >= total:
            break
        if page >= 50:
            print(f"Reached safety limit at page {page}; fetched {len(all_courses)}/{total}")
            break
        page += 1

    return all_courses


def fetch_access_permission(client: UMUClient, group_id: str) -> dict[str, Any]:
    """Fetch course access permission setting."""
    resp = client.get(
        client.desktop_url("/api/group/getAccessPermissionOption"),
        params={"obj_id": str(group_id), "obj_type": "group"},
    )
    if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
        raise RuntimeError(resp.get("error", "获取访问权限失败"))

    data = resp.get("data", {}) or {}
    selected = data.get("selected_option", "")
    try:
        selected_int = int(selected)
    except (ValueError, TypeError):
        selected_int = -1

    return {
        "access_permission": selected_int,
        "permission_text": permission_text(selected_int),
        "detail": data,
    }


def fetch_access_list(client: UMUClient, group_id: str) -> list[dict[str, Any]]:
    """Fetch authorized accounts/classes/departments/groups for a course."""
    all_items: list[dict[str, Any]] = []
    page = 1
    size = 100
    total = 0

    while True:
        resp = client.get(
            client.desktop_url("/api/manage/getcourseaccesslist"),
            params={
                "obj_id": str(group_id),
                "obj_type": "group",
                "page": str(page),
                "size": str(size),
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取访问列表失败"))

        data = resp.get("data", {}) or {}
        page_info = data.get("page_info", {})
        items = data.get("list", [])
        total = int(page_info.get("list_total_num", 0) or 0)

        for item in items:
            account_type = item.get("account_type", "user")
            formatted: dict[str, Any] = {
                "id": str(item.get("id", "")),
                "account": item.get("account", ""),
                "account_type": account_type,
            }
            if account_type == "user":
                formatted["detail"] = (
                    f"{item.get('user_name', '')} / {item.get('email', '')} / {item.get('phone', '')}"
                ).strip(" /")
            elif account_type == "class":
                formatted["detail"] = item.get("class_name", item.get("account", ""))
            elif account_type == "department":
                formatted["detail"] = item.get("department_name", item.get("account", ""))
            elif account_type == "group":
                formatted["detail"] = item.get("group_name", item.get("account", ""))
            else:
                formatted["detail"] = ""
            all_items.append(formatted)

        if not items or len(all_items) >= total:
            break
        page += 1

    return all_items


def format_account_row(course: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_id": course["group_id"],
        "title": course["title"],
        "access_code": course["access_code"],
        "access_permission": course["access_permission"],
        "permission_text": course["permission_text"],
        "authorized_account": account.get("account", ""),
        "authorized_account_type": account.get("account_type", ""),
        "authorized_account_id": account.get("id", ""),
        "authorized_account_detail": account.get("detail", ""),
        "error": "",
    }


def build_rows(client: UMUClient, courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(courses)

    for idx, course in enumerate(courses, start=1):
        group_id = course["group_id"]
        print(f"[{idx}/{total}] Processing {group_id} - {course['title'][:40]}")

        try:
            perm = fetch_access_permission(client, group_id)
        except Exception as e:
            rows.append({
                "group_id": group_id,
                "title": course["title"],
                "access_code": course["access_code"],
                "access_permission": "",
                "permission_text": "",
                "authorized_account": "",
                "authorized_account_type": "",
                "authorized_account_id": "",
                "authorized_account_detail": "",
                "error": f"access_permission error: {e}",
            })
            continue

        course.update(perm)

        if perm["access_permission"] != 3:
            rows.append({
                "group_id": group_id,
                "title": course["title"],
                "access_code": course["access_code"],
                "access_permission": perm["access_permission"],
                "permission_text": perm["permission_text"],
                "authorized_account": "",
                "authorized_account_type": "",
                "authorized_account_id": "",
                "authorized_account_detail": "",
                "error": "",
            })
            continue

        try:
            accounts = fetch_access_list(client, group_id)
        except Exception as e:
            rows.append({
                "group_id": group_id,
                "title": course["title"],
                "access_code": course["access_code"],
                "access_permission": perm["access_permission"],
                "permission_text": perm["permission_text"],
                "authorized_account": "",
                "authorized_account_type": "",
                "authorized_account_id": "",
                "authorized_account_detail": "",
                "error": f"access_list error: {e}",
            })
            continue

        if not accounts:
            rows.append({
                "group_id": group_id,
                "title": course["title"],
                "access_code": course["access_code"],
                "access_permission": perm["access_permission"],
                "permission_text": perm["permission_text"],
                "authorized_account": "(none assigned)",
                "authorized_account_type": "",
                "authorized_account_id": "",
                "authorized_account_detail": "",
                "error": "",
            })
        else:
            for account in accounts:
                rows.append(format_account_row(course, account))

    return rows


def save_to_excel(rows: list[dict[str, Any]], output_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Course Permissions"

    headers = [
        "group_id",
        "title",
        "access_code",
        "access_permission",
        "permission_text",
        "authorized_account",
        "authorized_account_type",
        "authorized_account_id",
        "authorized_account_detail",
        "error",
    ]

    # Header row
    ws.append(headers)
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Data rows
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    # Auto-width columns (rough)
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                length = len(str(cell.value))
                if length > max_length:
                    max_length = length
            except Exception:
                pass
        adjusted_width = min(max_length + 2, 80)
        ws.column_dimensions[column].width = adjusted_width

    # Freeze header
    ws.freeze_panes = "A2"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> int:
    username, password = load_credentials("teacher")
    if not username or not password:
        print("Teacher credentials not found. Checked env vars and .env file.")
        return 1

    client = UMUClient(base_url=BASE_URL)
    client.login(username, password)
    print(f"Logged in as teacher: {username}")

    print("Fetching created courses...")
    courses = fetch_created_courses(client)
    print(f"Found {len(courses)} courses.")

    print("Fetching permission details for each course...")
    rows = build_rows(client, courses)

    print(f"Saving Excel to {OUTPUT_PATH}...")
    save_to_excel(rows, OUTPUT_PATH)
    print(f"Saved {len(rows)} rows.")

    # Summary
    counts: dict[str, int] = {}
    errors = 0
    for row in rows:
        if row.get("error"):
            errors += 1
        else:
            key = str(row.get("permission_text", "unknown"))
            counts[key] = counts.get(key, 0) + 1

    print("\nSummary:")
    for text, count in sorted(counts.items()):
        print(f"  {text}: {count}")
    if errors:
        print(f"  Errors: {errors}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
