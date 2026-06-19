# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程/学习项目访问权限共享辅助函数.

Admin 与 Teacher 在访问权限管理上调用的 UMU API 完全相同，因此把公共逻辑
抽取到本模块，避免在 `admin.py` 与 `teacher.py` 中重复实现。
"""

from __future__ import annotations

import json
from typing import Any

from ...core.client import UMUClient


# ---------------------------------------------------------------------------
# 通用响应解析与文案
# ---------------------------------------------------------------------------
_PERMISSION_TEXT_MAP: dict[int, str] = {
    0: "关闭",
    1: "公开",
    2: "企业内公开",
    3: "指定账户",
}


def _permission_text(access_permission: int) -> str:
    """将权限数值转换为中文描述."""
    return _PERMISSION_TEXT_MAP.get(access_permission, f"未知({access_permission})")


def _parse_access_permission_response(resp: dict[str, Any]) -> tuple[bool, Any, str]:
    """解析 UMU 访问权限接口响应.

    UMU 接口在业务成功时 status=true/error_code=0，但顶层 success 可能为 false。
    返回 (is_success, data_or_none, error_message)。
    """
    if not isinstance(resp, dict):
        return False, None, "响应格式异常"
    if resp.get("status") is True or resp.get("error_code") == 0:
        return True, resp.get("data"), ""
    return False, None, resp.get("error", "") or resp.get("error_message", "业务请求失败")


# ---------------------------------------------------------------------------
# 访问权限核心操作
# ---------------------------------------------------------------------------
def _search_access_permission_account(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    keyword: str,
) -> tuple[bool, list[dict[str, Any]], str]:
    """搜索可授权访问的账户/班级/部门/分组.

    对应 UMU 接口 POST /api/manage/accessaccountmatchv2，
    search_source=access_permission 用于课程/项目访问权限场景。

    Args:
        obj_id: 课程 ID 或学习项目 ID。
        obj_type: "group" 表示课程，"program" 表示学习项目。
        keyword: 搜索关键词。

    Returns:
        (success, accounts, error_message)
    """
    data: dict[str, Any] = {
        "accounts": keyword,
        "search_source": "access_permission",
        "is_suggestion": "1",
    }
    if obj_type == "group":
        data["group_id"] = obj_id
        data["is_sug"] = "1"
    elif obj_type == "program":
        data["program_id"] = obj_id
    else:
        return False, [], f"不支持的 obj_type: {obj_type}"

    resp = client.post(
        client.desktop_url("/api/manage/accessaccountmatchv2"),
        data=data,
    )
    ok, result, err = _parse_access_permission_response(resp)
    if not ok:
        return False, [], err
    accounts = [item for item in (result or []) if item.get("is_exist") == 1]
    return True, accounts, ""


def _set_obj_access_permission(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    access_permission: int,
    update_session_permission: bool = True,
) -> dict[str, Any]:
    """设置对象的整体访问权限.

    Args:
        obj_id: 课程/项目 ID。
        obj_type: "group" 或 "program"。
        access_permission: 0/2/3。
        update_session_permission: 是否同步更新小节权限。

    Returns:
        API 返回的 data 字段。
    """
    if obj_type == "group":
        endpoint = "/api/group/setgrouppermission"
        id_key = "group_id"
    elif obj_type == "program":
        endpoint = "/api/program/setprogrampermission"
        id_key = "program_id"
    else:
        raise ValueError(f"不支持的 obj_type: {obj_type}")

    resp = client.post(
        client.desktop_url(endpoint),
        data={
            id_key: str(obj_id),
            "access_permission": str(access_permission),
            "update_session_permission": "1" if update_session_permission else "0",
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "设置访问权限失败")
    return data or {}


def _get_obj_access_permission(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
) -> tuple[int, list[Any], dict[str, Any]]:
    """获取对象当前的访问权限设置.

    Returns:
        (selected_int, permission_options, detail)
    """
    resp = client.get(
        client.desktop_url("/api/group/getAccessPermissionOption"),
        params={
            "obj_id": str(obj_id),
            "obj_type": obj_type,
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "获取访问权限失败")

    selected = data.get("selected_option", "") if isinstance(data, dict) else ""
    try:
        selected_int = int(selected)
    except (ValueError, TypeError):
        selected_int = -1

    options = data.get("permission_option", []) if isinstance(data, dict) else []
    return selected_int, options, data or {}


def _get_obj_access_list(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """获取对象当前已授权的访问列表."""
    resp = client.get(
        client.desktop_url("/api/manage/getcourseaccesslist"),
        params={
            "obj_id": str(obj_id),
            "obj_type": obj_type,
            "page": page,
            "size": size,
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "获取访问列表失败")

    page_info = data.get("page_info", {}) if isinstance(data, dict) else {}
    items = data.get("list", []) if isinstance(data, dict) else []
    return [_format_access_account(item) for item in items], page_info


def _add_obj_access_accounts(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    accounts: list[dict[str, Any]],
    update_session_permission: bool = True,
) -> dict[str, Any]:
    """为对象添加指定账户/班级/部门/分组可见权限."""
    payloads = [_build_access_account_payload(acc, 1) for acc in accounts]
    resp = client.post(
        client.desktop_url("/api/manage/updateaccessuser"),
        data={
            "obj_id": str(obj_id),
            "obj_type": obj_type,
            "update_session_permission": "1" if update_session_permission else "0",
            "accounts": json.dumps(payloads, ensure_ascii=False),
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "添加指定账户失败")
    return data or {}


def _remove_obj_access_accounts(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    accounts: list[dict[str, Any]],
    update_session_permission: bool = True,
) -> dict[str, Any]:
    """移除对象的指定账户/班级/部门/分组访问权限."""
    payloads = [_build_access_account_payload(acc, 2) for acc in accounts]
    resp = client.post(
        client.desktop_url("/api/manage/updateaccessuser"),
        data={
            "obj_id": str(obj_id),
            "obj_type": obj_type,
            "update_session_permission": "1" if update_session_permission else "0",
            "accounts": json.dumps(payloads, ensure_ascii=False),
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "移除指定账户失败")
    return data or {}


def _cancel_all_assigned_permissions(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
) -> dict[str, Any]:
    """取消对象的所有指定访问权限."""
    resp = client.post(
        client.desktop_url("/uapi/v1/access-permission/cancel-all-assigned-permission"),
        data={
            "obj_id": str(obj_id),
            "obj_type": obj_type,
        },
    )
    ok, data, err = _parse_access_permission_response(resp)
    if not ok:
        raise RuntimeError(err or "取消指定权限失败")
    return data or {}


# ---------------------------------------------------------------------------
# Payload / 格式化辅助函数
# ---------------------------------------------------------------------------
def _build_access_account_payload(
    account: dict[str, Any],
    action_type: int,
) -> dict[str, Any]:
    """构造 updateaccessuser 的单个账户元素.

    Args:
        account: 账户/班级/部门/分组信息，需包含 account、account_type、id。
                 - user：使用 id
                 - class：还需包含 class_id（如缺失则回退到 id）
                 - department：还需包含 department_id（如缺失则回退到 id）
                 - group：还需包含 user_group_id（如缺失则回退到 id）
        action_type: 1=添加权限，2=删除权限。

    Returns:
        API 所需 payload 字典。
    """
    account_type = account.get("account_type", "user")
    payload: dict[str, Any] = {
        "type": action_type,
        "account": account.get("account", ""),
        "account_type": account_type,
        "id": str(account.get("id", "")),
    }
    if account_type == "class":
        payload["class_id"] = str(account.get("class_id", account.get("id", "")))
    elif account_type == "department":
        payload["department_id"] = str(account.get("department_id", account.get("id", "")))
    elif account_type == "group":
        payload["user_group_id"] = str(account.get("user_group_id", account.get("id", "")))
    return payload


def _format_access_account(account: dict[str, Any]) -> dict[str, Any]:
    """统一格式化 accessaccountmatchv2 返回的账户/班级/部门/分组信息."""
    account_type = account.get("account_type", "user")
    formatted: dict[str, Any] = {
        "id": str(account.get("id", "")),
        "account": account.get("account", ""),
        "account_type": account_type,
        "is_exist": account.get("is_exist", 0),
    }
    if account_type == "user":
        formatted["user_name"] = account.get("user_name", "")
        formatted["email"] = account.get("email", "")
        formatted["phone"] = account.get("phone", "")
        formatted["umu_id"] = str(account.get("umu_id", "") or account.get("id", ""))
        formatted["student_id"] = str(account.get("student_id", ""))
    elif account_type == "class":
        formatted["class_name"] = account.get("class_name", account.get("account", ""))
        formatted["class_id"] = str(account.get("class_id", account.get("id", "")))
    elif account_type == "department":
        formatted["department_name"] = account.get("department_name", account.get("account", ""))
        formatted["department_id"] = str(account.get("department_id", account.get("id", "")))
        formatted["user_num"] = account.get("user_num", "")
    elif account_type == "group":
        formatted["group_name"] = account.get("group_name", account.get("account", ""))
        formatted["user_group_id"] = str(account.get("user_group_id", account.get("id", "")))
        formatted["user_num"] = account.get("user_num", "")
    return formatted


__all__ = [
    "_permission_text",
    "_parse_access_permission_response",
    "_search_access_permission_account",
    "_set_obj_access_permission",
    "_get_obj_access_permission",
    "_get_obj_access_list",
    "_add_obj_access_accounts",
    "_remove_obj_access_accounts",
    "_cancel_all_assigned_permissions",
    "_build_access_account_payload",
    "_format_access_account",
]
