# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程协同与学员名单相关共享业务操作.

Admin 与 Teacher 在课程协同者管理、学员参与者/学习任务/学习时长查询上调用相同
的 UMU API，本模块将公共逻辑下沉为无状态业务函数。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ...adapters.mcp.shared_access_permissions import (
    _parse_access_permission_response as _parse_collaboration_response,
)
from ...core.client import UMUClient
from ..decorators import umu_operation
from ..shared.progress import report_pagination_progress

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 协同权限映射
# ---------------------------------------------------------------------------
_ROLE_TO_API: dict[str, str] = {
    "editor": "cooperator",
    "operator": "operator",
    "viewer": "viewer",
}

_API_ROLE_TO_LABEL: dict[str, str] = {
    "cooperator": "编辑者",
    "operator": "运营者",
    "viewer": "查看者",
    "creator": "拥有者",
}


def _map_role_to_api(role: str) -> str | None:
    """将面向用户的角色名映射为 UMU API 的 role_type."""
    return _ROLE_TO_API.get(role.lower())


# ---------------------------------------------------------------------------
# 协同账号搜索与匹配
# ---------------------------------------------------------------------------
def _search_collaborator_account(
    client: UMUClient,
    group_id: str,
    keyword: str,
) -> tuple[bool, list[dict[str, Any]], str]:
    """搜索可设置为协同者的账号.

    Returns:
        (success, accounts, error_message)
    """
    resp = client.post(
        client.desktop_url("/api/manage/accessaccountmatchv2"),
        data={
            "accounts": keyword,
            "search_source": "add_cooperator",
            "is_suggestion": "1",
            "group_id": group_id,
            "is_sug": "1",
        },
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        return False, [], err
    accounts = [item for item in (data or []) if item.get("is_exist") == 1]
    return True, accounts, ""


def _find_unique_account(
    accounts: list[dict[str, Any]],
    keyword: str,
) -> tuple[dict[str, Any] | None, str]:
    """从搜索结果中确定唯一账号.

    Returns:
        (account, error_message)。account 为 None 时表示未找到或不唯一。
    """
    if not accounts:
        return None, f"未找到与 '{keyword}' 匹配的可协同账号。仅支持讲师、学习负责人、子管理员、管理员角色。"
    if len(accounts) > 1:
        previews = [
            {
                "id": acc.get("id"),
                "user_name": acc.get("user_name"),
                "email": acc.get("email"),
                "phone": acc.get("phone"),
                "account": acc.get("account"),
            }
            for acc in accounts
        ]
        return None, f"找到多个匹配账号，请提供更精确的信息：{previews}"
    return accounts[0], ""


def _add_or_update_cooperator(
    client: UMUClient,
    group_id: str,
    account: dict[str, Any],
    api_role: str,
) -> tuple[bool, str]:
    """调用 addcooperators 添加或更新协同权限."""
    payload = [
        {
            "type": 1,
            "role_type": api_role,
            "account": account.get("account") or account.get("email") or account.get("phone") or "",
            "account_type": account.get("account_type", "user"),
            "umu_id": account.get("umu_id") or account.get("id") or "",
        }
    ]
    resp = client.post(
        client.desktop_url("/api/cooperation/addcooperators"),
        data={
            "obj_id": group_id,
            "obj_type": "group",
            "accounts": json.dumps(payload, ensure_ascii=False),
        },
    )
    ok, _, err = _parse_collaboration_response(resp)
    if not ok:
        return False, err or "添加/更新协同权限失败"
    return True, ""


# ---------------------------------------------------------------------------
# 协同者管理
# ---------------------------------------------------------------------------
@umu_operation(
    name="list_course_collaborators",
    description="列出课程的协同者",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "page": "页码，从 1 开始",
        "page_size": "每页数量",
    },
)
async def list_course_collaborators(
    client: UMUClient,
    group_id: str,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """列出课程的协同者.

    返回课程的协同者列表、创建者信息以及分页信息。
    """
    resp = client.get(
        client.desktop_url("/api/cooperation/getall"),
        params={
            "t": str(int(time.time() * 1000)),
            "append_manage_role": "1",
            "obj_id": group_id,
            "obj_type": "group",
            "page": str(page),
            "size": str(page_size),
        },
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        raise RuntimeError(err or "获取协同者列表失败")

    raw_list = data.get("list", []) if isinstance(data, dict) else []
    creator_info = data.get("creator_info", {}) if isinstance(data, dict) else {}
    page_info = data.get("page_info", {}) if isinstance(data, dict) else {}

    collaborators = []
    for item in raw_list:
        collaborators.append({
            "cooperation_info_id": item.get("cooperation_info_id"),
            "teacher_id": item.get("teacher_id"),
            "umu_id": item.get("umu_id"),
            "role_type": item.get("role_type"),
            "role_label": _API_ROLE_TO_LABEL.get(item.get("role_type", ""), item.get("role_type", "")),
            "teacher_name": item.get("teacher_name"),
            "teacher_email": item.get("teacher_email"),
            "cooperator_type": item.get("cooperator_type"),
            "is_manager": item.get("is_manager"),
            "manager_role_type": item.get("manager_role_type"),
        })

    creator = None
    if creator_info:
        creator = {
            "teacher_id": creator_info.get("teacher_id"),
            "role_type": creator_info.get("role_type"),
            "role_label": _API_ROLE_TO_LABEL.get(creator_info.get("role_type", ""), creator_info.get("role_type", "")),
            "teacher_name": creator_info.get("teacher_name"),
            "teacher_email": creator_info.get("teacher_email"),
        }

    return {
        "collaborators": collaborators,
        "creator": creator,
        "pagination": page_info,
    }


@umu_operation(
    name="invite_course_collaborator",
    description="邀请用户成为课程协同者，或调整已有协同者权限",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "keyword": "被邀请者查询关键词：邮箱、姓名、用户名或手机号",
        "role_type": "协同权限：editor（编辑者）/ operator（运营者）/ viewer（查看者）",
    },
)
async def invite_course_collaborator(
    client: UMUClient,
    group_id: str,
    keyword: str,
    role_type: str,
) -> dict[str, Any]:
    """邀请用户成为课程协同者，或调整已有协同者权限.

    工具内部会先搜索账号，只有唯一匹配时才会执行邀请。
    """
    api_role = _map_role_to_api(role_type)
    if api_role is None:
        raise ValueError(f"不支持的权限类型 '{role_type}'，请选择 editor/operator/viewer")

    ok, accounts, err = _search_collaborator_account(client, group_id, keyword)
    if not ok:
        raise RuntimeError(err or "搜索账号失败")

    account, err = _find_unique_account(accounts, keyword)
    if account is None:
        raise ValueError(err)

    add_ok, add_err = _add_or_update_cooperator(client, group_id, account, api_role)
    if not add_ok:
        raise RuntimeError(add_err)

    return {
        "account": account.get("account") or account.get("email"),
        "user_name": account.get("user_name"),
        "role_type": api_role,
        "role_label": _API_ROLE_TO_LABEL.get(api_role, api_role),
        "group_id": group_id,
    }


@umu_operation(
    name="update_collaborator_role",
    description="调整已有协同者的权限",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "cooperation_info_id": "协同关系 ID，从 list_course_collaborators 获取",
        "role_type": "新的协同权限：editor（编辑者）/ operator（运营者）/ viewer（查看者）",
    },
)
async def update_collaborator_role(
    client: UMUClient,
    group_id: str,
    cooperation_info_id: str,
    role_type: str,
) -> dict[str, Any]:
    """调整已有协同者的权限."""
    api_role = _map_role_to_api(role_type)
    if api_role is None:
        raise ValueError(f"不支持的权限类型 '{role_type}'，请选择 editor/operator/viewer")

    resp = client.get(
        client.desktop_url("/api/cooperation/getall"),
        params={
            "t": str(int(time.time() * 1000)),
            "append_manage_role": "1",
            "obj_id": group_id,
            "obj_type": "group",
            "page": "1",
            "size": "20",
        },
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        raise RuntimeError(err or "获取协同者列表失败")

    raw_list = data.get("list", []) if isinstance(data, dict) else []
    target = next(
        (item for item in raw_list if str(item.get("cooperation_info_id")) == str(cooperation_info_id)),
        None,
    )
    if target is None:
        raise ValueError(f"未找到协同关系 ID: {cooperation_info_id}")

    add_ok, add_err = _add_or_update_cooperator(client, group_id, target, api_role)
    if not add_ok:
        raise RuntimeError(add_err)

    return {
        "cooperation_info_id": cooperation_info_id,
        "teacher_id": target.get("teacher_id"),
        "user_name": target.get("teacher_name"),
        "new_role_type": api_role,
        "new_role_label": _API_ROLE_TO_LABEL.get(api_role, api_role),
        "group_id": group_id,
    }


@umu_operation(
    name="remove_course_collaborator",
    description="删除课程的协同者",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "cooperation_info_id": "协同关系 ID，从 list_course_collaborators 获取",
    },
)
async def remove_course_collaborator(
    client: UMUClient,
    group_id: str,
    cooperation_info_id: str,
) -> dict[str, Any]:
    """删除课程的协同者."""
    resp = client.post(
        client.desktop_url("/api/cooperation/del"),
        data={"cooperation_info_ids": str(cooperation_info_id)},
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        raise RuntimeError(err or "删除协同者失败")

    result = data.get("result") if isinstance(data, dict) else None
    return {
        "cooperation_info_id": cooperation_info_id,
        "group_id": group_id,
        "result": result,
    }


@umu_operation(
    name="transfer_course_owner",
    description="将课程拥有权转让给其他用户",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "keyword": "新拥有者查询关键词：邮箱、姓名、用户名或手机号",
    },
)
async def transfer_course_owner(
    client: UMUClient,
    group_id: str,
    keyword: str,
) -> dict[str, Any]:
    """将课程拥有权转让给其他用户.

    工具内部会先搜索账号，只有唯一匹配时才会执行转让。
    注意：转让后当前用户将失去拥有者权限，请谨慎操作。
    """
    ok, accounts, err = _search_collaborator_account(client, group_id, keyword)
    if not ok:
        raise RuntimeError(err or "搜索账号失败")

    account, err = _find_unique_account(accounts, keyword)
    if account is None:
        raise ValueError(err)

    teacher_id = account.get("id")
    if not teacher_id:
        raise ValueError("账号信息缺少 teacher_id，无法转让")

    resp = client.post(
        client.desktop_url("/uapi/v1/cooperation/permission-transfer"),
        data={
            "obj_id": group_id,
            "obj_type": "group",
            "transferred_teacher_id": str(teacher_id),
        },
    )
    ok, data, err = _parse_collaboration_response(resp)
    if not ok:
        raise RuntimeError(err or "转让课程拥有者失败")

    return {
        "group_id": group_id,
        "new_owner_id": teacher_id,
        "new_owner_name": account.get("user_name"),
        "new_owner_account": account.get("account") or account.get("email"),
        "status": data.get("status") if isinstance(data, dict) else None,
    }


# ---------------------------------------------------------------------------
# 学员名单查询辅助函数
# ---------------------------------------------------------------------------
_STATUS_FILTER_MAP: dict[str, str] = {
    "all": "0",
    "completed": "1",
    "uncompleted": "2",
}


def _validate_status_filter(status_filter: str) -> None:
    if status_filter not in _STATUS_FILTER_MAP:
        raise ValueError(
            f"status_filter 必须是 all/completed/uncompleted 之一，收到: {status_filter}"
        )


# ---------------------------------------------------------------------------
# 学员名单查询
# ---------------------------------------------------------------------------
@umu_operation(
    name="list_course_learning_tasks",
    description="查询课程的学习任务分配学员清单",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID（group_id）",
        "status_filter": "学员完成状态筛选：all=全部, completed=已完成, uncompleted=未完成",
        "include_disabled": "是否包含已禁用账号，默认包含",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_course_learning_tasks(
    client: UMUClient,
    group_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询课程的学习任务分配学员清单.

    返回被分配给该课程作为学习任务的学员列表，支持按完成状态筛选和是否显示禁用账号。
    讲师及以上权限角色可调用。
    """
    _validate_status_filter(status_filter)

    def _fetch_page(
        p: int, sz: int
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/getstudenttasklist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": _STATUS_FILTER_MAP[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取学习任务学员清单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        formatted: list[dict[str, Any]] = []
        for item in student_list:
            formatted.append({
                "student_id": item.get("student_id", ""),
                "umu_id": item.get("umu_id", ""),
                "user_name": item.get("user_name", ""),
                "avatar": item.get("avatar", ""),
                "task_count": item.get("task_count", 0),
                "complete_num": item.get("complete_num", 0),
                "complete_rate": item.get("complete_rate", 0),
                "is_assign": item.get("is_assign", 0),
                "assign_time": item.get("assign_time", 0),
                "last_assign_time": item.get("last_assign_time", 0),
                "complete_time": item.get("complete_time", 0),
                "first_learning_time": item.get("first_learning_time", 0),
                "last_learning_time": item.get("last_learning_time", 0),
                "due_time": item.get("due_time", 0),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
            "has_learning_task": bool(data_count.get("exist_learning_task", 0)),
        }

    if fetch_all:
        batch_size = 50
        all_students: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1
        latest_data_count: dict[str, Any] = {}

        while True:
            page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
            all_students.extend(page_items)
            latest_data_count = data_count

            report_pagination_progress(
                "list_course_learning_tasks",
                current_page,
                len(all_students),
                total_all,
                batch_size,
            )

            if not page_items or len(all_students) >= total_all:
                report_pagination_progress(
                    "list_course_learning_tasks",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_course_learning_tasks",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        summary = _build_summary(latest_data_count)
        return {
            "summary": summary,
            "students": all_students,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    students, _, data_count, page_info = _fetch_page(page, page_size)
    summary = _build_summary(data_count)
    return {
        "summary": summary,
        "students": students,
        "pagination": {
            "total": int(page_info.get("list_total_num", 0) or 0),
            "total_pages": int(page_info.get("total_page_num", 0) or 0),
            "current_page": int(page_info.get("current_page", page)),
            "page_size": int(page_info.get("size", page_size)),
        },
    }


@umu_operation(
    name="list_course_participants",
    description="查询指定课程的学员参与者名单",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID（group_id）",
        "status_filter": "学员完成状态筛选：all=全部, completed=必修完成, uncompleted=必修未完成",
        "include_disabled": "是否包含已禁用账号，默认包含",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_course_participants(
    client: UMUClient,
    group_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询指定课程的学员参与者名单.

    返回课程的参训学员列表，支持按必修完成状态筛选和是否显示禁用账号。
    返回结果包含每位学员每个必修/选修小节的完成状态、积分、得分等明细。
    讲师及以上权限角色可调用。
    """
    _validate_status_filter(status_filter)

    def _fetch_page(
        p: int, sz: int
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/getstudentlist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": _STATUS_FILTER_MAP[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
                "v": "1",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程学员参与者名单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return student_list, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
        }

    if fetch_all:
        batch_size = 50
        all_students: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1
        latest_data_count: dict[str, Any] = {}

        while True:
            page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
            all_students.extend(page_items)
            latest_data_count = data_count

            report_pagination_progress(
                "list_course_participants",
                current_page,
                len(all_students),
                total_all,
                batch_size,
            )

            if not page_items or len(all_students) >= total_all:
                report_pagination_progress(
                    "list_course_participants",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_course_participants",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        summary = _build_summary(latest_data_count)
        return {
            "summary": summary,
            "students": all_students,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    students, _, data_count, page_info = _fetch_page(page, page_size)
    summary = _build_summary(data_count)
    return {
        "summary": summary,
        "students": students,
        "pagination": {
            "total": int(page_info.get("list_total_num", 0) or 0),
            "total_pages": int(page_info.get("total_page_num", 0) or 0),
            "current_page": int(page_info.get("current_page", page)),
            "page_size": int(page_info.get("size", page_size)),
        },
    }


@umu_operation(
    name="list_course_learning_durations",
    description="查询指定课程的学员学习时长名单",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID（group_id）",
        "status_filter": "学员完成状态筛选：all=全部, completed=必修完成, uncompleted=必修未完成",
        "include_disabled": "是否包含已禁用账号，默认包含",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_course_learning_durations(
    client: UMUClient,
    group_id: str,
    status_filter: str = "all",
    include_disabled: bool = True,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询指定课程的学员学习时长名单.

    返回课程的参训学员学习时长列表，支持按必修完成状态筛选和是否显示禁用账号。
    返回结果包含每位学员的课程总学习时长，以及每个必修/选修小节的学习时长、首次/末次学习时间等明细。
    讲师及以上权限角色可调用。
    """
    _validate_status_filter(status_filter)

    def _fetch_page(
        p: int, sz: int
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
        resp = client.get(
            client.desktop_url("/api/studentManage/grouplearningtimelist"),
            params={
                "t": str(int(time.time() * 1000)),
                "group_id": group_id,
                "type": _STATUS_FILTER_MAP[status_filter],
                "filter_disabled_user": "0" if include_disabled else "1",
                "is_enroll": "0",
                "is_require": "0",
                "only_enterprise_on_job": "0",
                "student_only": "0",
                "page": str(p),
                "size": str(sz),
                "sort_field": "1",
                "sort_type": "2",
                "v": "2",
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取课程学员学习时长名单失败"))

        data = resp.get("data", {})
        page_info = data.get("table_body", {}).get("page_info", {})
        student_list = data.get("table_body", {}).get("list", [])

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return student_list, total_all, data.get("data_count", {}), page_info

    def _build_summary(data_count: dict[str, Any]) -> dict[str, Any]:
        return {
            "total": data_count.get("total_num", 0),
            "completed": data_count.get("complete_num", 0),
            "uncompleted": data_count.get("uncomplete_num", 0),
            "completion_rate": data_count.get("complete_rate", 0),
            "avg_vlt": data_count.get("avg_vlt", ""),
        }

    if fetch_all:
        batch_size = 50
        all_students: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1
        latest_data_count: dict[str, Any] = {}

        while True:
            page_items, total_all, data_count, _ = _fetch_page(current_page, batch_size)
            all_students.extend(page_items)
            latest_data_count = data_count

            report_pagination_progress(
                "list_course_learning_durations",
                current_page,
                len(all_students),
                total_all,
                batch_size,
            )

            if not page_items or len(all_students) >= total_all:
                report_pagination_progress(
                    "list_course_learning_durations",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_course_learning_durations",
                    current_page,
                    len(all_students),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        summary = _build_summary(latest_data_count)
        return {
            "summary": summary,
            "students": all_students,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    students, _, data_count, page_info = _fetch_page(page, page_size)
    summary = _build_summary(data_count)
    return {
        "summary": summary,
        "students": students,
        "pagination": {
            "total": int(page_info.get("list_total_num", 0) or 0),
            "total_pages": int(page_info.get("total_page_num", 0) or 0),
            "current_page": int(page_info.get("current_page", page)),
            "page_size": int(page_info.get("size", page_size)),
        },
    }
