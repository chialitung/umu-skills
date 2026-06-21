# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""/umu 斜杠命令执行引擎.

负责解析用户意图、选择执行角色、处理 fallback 与交互确认，
并调用对应的统一 Skill 或原子工具。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ...core.env_loader import load_env_credentials
from ..decorators import SkillContext, get_skill_function, is_skill_function
from ..intent_capability_map import IntentCapabilityMap
from ..role_resolver import RoleResolver, ResolvedRole

logger = logging.getLogger("umu.mcp.skills")

# ---------------------------------------------------------------------------
# 类型与数据结构
# ---------------------------------------------------------------------------


@dataclass
class DispatchTarget:
    """意图分发目标."""

    skill_name: str
    """统一 Skill 名称。"""

    capability: str
    """所需能力角色：teacher/student/admin。"""

    arguments: dict[str, Any] = field(default_factory=dict)
    """已解析出的参数。"""

    missing_args: list[str] = field(default_factory=list)
    """仍需用户补充的必填参数。"""


Dispatcher = Callable[[str], DispatchTarget | None]


# ---------------------------------------------------------------------------
# 配置角色凭证
# ---------------------------------------------------------------------------


def get_configured_roles() -> list[str]:
    """返回已配置账号凭据的角色列表.

    优先从 `.env` 文件读取，若不存在则回退到当前环境变量。
    """
    configured: list[str] = []
    for role in ("admin", "teacher", "student"):
        username, password = load_env_credentials(role)
        if not username or not password:
            username = os.getenv(f"UMU_{role.upper()}_USERNAME")
            password = os.getenv(f"UMU_{role.upper()}_PASSWORD")
        if username and password:
            configured.append(role)
    return configured


async def _ensure_server_authenticated(
    ctx: SkillContext,
    server: str,
    role: str,
) -> dict[str, Any] | None:
    """当目标 server 与 resolved role 不一致时，用角色凭据登录目标 server.

    例如仅配置 admin 账号时，使用 admin 凭据登录 teacher 子 MCP，
    从而复用 teacher 侧 canonical 工具完成课程创建等操作。
    """
    if server == role:
        return None

    username, password = load_env_credentials(role)
    if not username or not password:
        username = os.getenv(f"UMU_{role.upper()}_USERNAME")
        password = os.getenv(f"UMU_{role.upper()}_PASSWORD")
    if not username or not password:
        return None

    prefix = RoleResolver.ROLE_PREFIXES[role]
    login_tool = f"{prefix}login"
    logger.info(
        "[%s] 使用 %s 凭据登录 %s 子 MCP: %s",
        ctx.skill_name,
        role,
        server,
        username,
    )
    result = await ctx.call_tool(
        server=server,
        tool=login_tool,
        arguments={"username": username, "password": password},
    )
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "AUTH_FALLBACK_FAILED",
            "error_message": (
                f"使用 {role} 凭据登录 {server} 子 MCP 失败: "
                f"{result.get('error_message')}"
            ),
            "suggested_action": "请确认该账号在目标角色下具备操作权限",
            "next_action": "retry",
        }
    return None


# ---------------------------------------------------------------------------
# 参数提取辅助函数
# ---------------------------------------------------------------------------


def _extract_after(intent: str, markers: tuple[str, ...]) -> str | None:
    """尝试提取关键词之后的文本作为参数."""
    for marker in markers:
        idx = intent.find(marker)
        if idx == -1:
            continue
        tail = intent[idx + len(marker) :].strip()
        # 去掉常见介词/标点
        tail = re.sub(r"^[：:，,、\s]+", "", tail)
        if tail:
            # 取到下一个常见分隔符之前
            parts = re.split(r"[,，.。;；]", tail)
            return parts[0].strip()
    return None


def _extract_named(intent: str, name: str) -> str | None:
    """提取 `name=xxx` 或 `name xxx` 形式的参数."""
    patterns = [
        rf"{re.escape(name)}\s*[:=]\s*([^,，。\s]+)",
        rf"{re.escape(name)}\s+([^,，。\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# 意图分发器
# ---------------------------------------------------------------------------


def _dispatch_create_course(intent: str) -> DispatchTarget | None:
    if not any(k in intent for k in ("创建课程", "新建课程")):
        return None
    title = _extract_after(intent, ("创建课程", "新建课程"))
    scorm_id = _extract_named(intent, "scorm")
    args: dict[str, Any] = {}
    missing: list[str] = []
    if title:
        args["title"] = title
    else:
        missing.append("title")
    if scorm_id:
        args["scorm_resource_id"] = scorm_id
    else:
        missing.append("scorm_resource_id")
    return DispatchTarget("create_course_with_scorm", "teacher", args, missing)


def _dispatch_list_my_courses(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("我的课程", "列出课程", "课程列表", "我创建的课程")):
        return DispatchTarget("list_my_courses", "teacher", {}, [])
    return None


def _dispatch_course_categories(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("课程分类", "分类树")):
        return DispatchTarget("get_course_categories", "teacher", {}, [])
    return None


def _dispatch_submit_course_for_audit(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("提交审核", "课程审核")):
        group_id = _extract_named(intent, "group_id") or _extract_named(intent, "课程")
        args: dict[str, Any] = {}
        missing: list[str] = []
        if group_id:
            args["group_id"] = group_id
        else:
            missing.append("group_id")
        return DispatchTarget("submit_course_for_audit", "teacher", args, missing)
    return None


def _dispatch_enroll_course(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("报名", "参加课程")):
        enroll_id = _extract_named(intent, "enroll_id") or _extract_named(intent, "报名")
        args: dict[str, Any] = {}
        missing: list[str] = []
        if enroll_id:
            args["enroll_id"] = enroll_id
        else:
            missing.append("enroll_id")
        return DispatchTarget("enroll_course", "student", args, missing)
    return None


def _dispatch_get_course_progress(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("进度", "学习进度")):
        course_identifier = (
            _extract_named(intent, "course_identifier")
            or _extract_named(intent, "课程")
            or _extract_named(intent, "group_id")
        )
        args: dict[str, Any] = {}
        missing: list[str] = []
        if course_identifier:
            args["course_identifier"] = course_identifier
        else:
            missing.append("course_identifier")
        return DispatchTarget("get_course_progress", "student", args, missing)
    return None


def _dispatch_list_admin_courses(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("企业课程", "所有课程")):
        return DispatchTarget("list_courses", "admin", {}, [])
    return None


def _dispatch_list_admin_learning_programs(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("学习项目", "项目列表")) and any(
        k in intent for k in ("企业", "admin", "管理员")
    ):
        return DispatchTarget("list_owned_learning_programs_admin", "admin", {}, [])
    return None


def _dispatch_list_teacher_learning_programs(intent: str) -> DispatchTarget | None:
    if any(k in intent for k in ("学习项目", "项目列表")) and not any(
        k in intent for k in ("企业", "admin", "管理员")
    ):
        return DispatchTarget("list_owned_learning_programs", "teacher", {}, [])
    return None


_DISPATCHERS: list[Dispatcher] = [
    _dispatch_create_course,
    _dispatch_list_my_courses,
    _dispatch_course_categories,
    _dispatch_submit_course_for_audit,
    _dispatch_enroll_course,
    _dispatch_get_course_progress,
    _dispatch_list_admin_courses,
    _dispatch_list_admin_learning_programs,
    _dispatch_list_teacher_learning_programs,
]


def select_target(intent: str) -> DispatchTarget | None:
    """根据意图选择分发目标."""
    for dispatcher in _DISPATCHERS:
        target = dispatcher(intent)
        if target is not None:
            return target
    return None


# ---------------------------------------------------------------------------
# 执行入口
# ---------------------------------------------------------------------------


def _build_confirmation_response(resolved: ResolvedRole) -> dict[str, Any]:
    """构造需要用户确认角色的返回信封."""
    return {
        "success": False,
        "data": None,
        "error_code": "NEEDS_ROLE_CONFIRMATION",
        "error_message": resolved.confirmation_message or "需要确认使用哪个角色执行",
        "suggested_action": "请回复数字选择：1-teacher 2-admin 3-student，或说明使用哪个角色",
        "next_action": "needs_user_input",
    }


def _build_missing_args_response(
    resolved: ResolvedRole,
    target: DispatchTarget,
) -> dict[str, Any]:
    """构造缺少必填参数的返回信封."""
    data: dict[str, Any] = {
        "resolved_role": resolved.role,
        "server": resolved.server,
        "recommended_skill": target.skill_name,
        "missing_args": target.missing_args,
    }
    if resolved.fallback_reason:
        data["fallback_reason"] = resolved.fallback_reason

    response = {
        "success": False,
        "data": data,
        "error_code": "MISSING_REQUIRED_ARGUMENTS",
        "error_message": (
            f"需要使用 {resolved.role} 角色执行「{target.skill_name}」，"
            f"缺少必填参数：{', '.join(target.missing_args)}"
        ),
        "suggested_action": "请补充上述参数后再次调用，或直接调用对应的 Skill",
        "next_action": "needs_user_input",
        "resolved_role": resolved.role,
        "server": resolved.server,
    }
    if resolved.fallback_reason:
        response["fallback_reason"] = resolved.fallback_reason
    return response


def _build_unsupported_intent_response(intent: str) -> dict[str, Any]:
    """构造不支持的意图返回信封."""
    return {
        "success": False,
        "data": None,
        "error_code": "UNSUPPORTED_INTENT",
        "error_message": f"暂未识别的意图：{intent}",
        "suggested_action": "请使用更明确的描述，或直接调用 skill_list 查看可用 Skill",
        "next_action": "needs_user_input",
    }


def _build_no_role_available_response(capability: str | None) -> dict[str, Any]:
    """构造无可用角色的返回信封."""
    return {
        "success": False,
        "data": None,
        "error_code": "NO_ROLE_CONFIGURED",
        "error_message": "未检测到任何已配置的角色账号凭据",
        "suggested_action": (
            "请在 .env 或环境变量中配置 UMU_ADMIN_USERNAME/PASSWORD、"
            "UMU_TEACHER_USERNAME/PASSWORD 或 UMU_STUDENT_USERNAME/PASSWORD"
        ),
        "next_action": "needs_user_input",
    }


def _find_skill_function(skill_name: str) -> Callable[..., Awaitable[Any]] | None:
    """从 builtin Skill 包的所有模块中查找指定名称的 Skill 函数."""
    try:
        package = importlib.import_module("umu_sdk.skills.builtin")
    except ImportError:
        return None

    prefix = package.__name__ + "."
    for _finder, mod_name, is_pkg in pkgutil.iter_modules(
        package.__path__ or [], prefix=prefix
    ):
        if is_pkg:
            continue
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            continue
        for _name, obj in inspect.getmembers(module):
            if not is_skill_function(obj):
                continue
            sf = get_skill_function(obj)
            if sf is not None and sf.info.name == skill_name:
                return sf.func
    return None


def _update_session_state(
    ctx: SkillContext,
    resolved: ResolvedRole,
    remember_choice: bool,
) -> None:
    """更新会话状态中的角色上下文."""
    ctx.session_state["last_role"] = resolved.role
    if remember_choice:
        ctx.session_state["remembered_role"] = resolved.role


async def run_umu_command(
    ctx: SkillContext,
    command: str,
    default_role: str | None = None,
    remember_choice: bool = False,
) -> dict[str, Any]:
    """执行一条 /umu 命令.

    Args:
        ctx: Skill 执行上下文。
        command: 用户自然语言命令。
        default_role: 斜杠入口指定的默认角色（如 /umut 传入 teacher）。
        remember_choice: 是否将本次选择的角色记住到 session_state。

    Returns:
        统一返回信封。
    """
    available_servers = ctx.mcp.list_servers()
    configured_roles = get_configured_roles()

    resolver = RoleResolver(
        available_servers=available_servers,
        configured_roles=configured_roles,
        session_state=ctx.session_state,
    )

    # 1. 意图分类
    target = select_target(command)
    required_capability = target.capability if target else IntentCapabilityMap.classify(command)

    # 2. 角色解析
    resolved = resolver.resolve(
        intent=command,
        required_capability=required_capability or None,
        default_role=default_role,
    )

    # 3. 需要交互确认（歧义时 role 可能为空，但确认意图优先）
    if resolved.needs_confirmation:
        return _build_confirmation_response(resolved)

    # 4. 没有任何可用角色
    if not resolved.role:
        if not configured_roles:
            return _build_no_role_available_response(required_capability)
        # configured but unavailable servers -> still return no role
        return {
            "success": False,
            "data": None,
            "error_code": "SERVER_UNAVAILABLE",
            "error_message": "已配置角色的子 MCP 均未连接",
            "suggested_action": "请启动对应的子 MCP 服务",
            "next_action": "retry",
        }

    # 5. 无法识别意图
    if target is None:
        return _build_unsupported_intent_response(command)

    # 6. 检查目标能力对应的子 MCP 是否可用
    canonical_server = target.capability
    if canonical_server not in available_servers:
        return {
            "success": False,
            "data": None,
            "error_code": "SERVER_UNAVAILABLE",
            "error_message": f"所需子 MCP [{canonical_server}] 未连接",
            "suggested_action": f"请启动 {canonical_server} 子 MCP，或配置可 fallback 的角色",
            "next_action": "retry",
        }

    # 7. 高权限 fallback 时，用 resolved role 的凭据登录 canonical server
    auth_error = await _ensure_server_authenticated(ctx, canonical_server, resolved.role)
    if auth_error is not None:
        return auth_error

    # 8. 缺少必填参数
    if target.missing_args:
        _update_session_state(ctx, resolved, remember_choice)
        return _build_missing_args_response(resolved, target)

    # 9. 调用统一 Skill
    skill_func = _find_skill_function(target.skill_name)
    if skill_func is None:
        return {
            "success": False,
            "data": None,
            "error_code": "SKILL_NOT_FOUND",
            "error_message": f"未找到 Skill [{target.skill_name}]",
            "suggested_action": "请检查 Skill 名称或调用 skill_list",
            "next_action": "needs_user_input",
        }

    try:
        result = await skill_func(ctx, **target.arguments)
    except TypeError as e:
        return {
            "success": False,
            "data": None,
            "error_code": "INVALID_ARGUMENTS",
            "error_message": f"调用 Skill 参数不匹配: {e}",
            "suggested_action": "请检查输入参数",
            "next_action": "needs_user_input",
        }
    except Exception as e:
        logger.exception("[%s] 执行 Skill [%s] 失败", ctx.skill_name, target.skill_name)
        return {
            "success": False,
            "data": None,
            "error_code": "SKILL_EXECUTION_ERROR",
            "error_message": f"执行 Skill 时发生异常: {e}",
            "suggested_action": "请检查子 MCP 日志或输入参数",
            "next_action": "retry",
        }

    # 10. 统一结果并注入角色信息
    _update_session_state(ctx, resolved, remember_choice)

    if isinstance(result, dict) and "success" in result:
        output = dict(result)
    elif isinstance(result, dict):
        output = {
            "success": True,
            "data": result,
            "error_code": "",
            "error_message": "",
            "suggested_action": "",
            "next_action": "proceed",
        }
    else:
        output = {
            "success": True,
            "data": result,
            "error_code": "",
            "error_message": "",
            "suggested_action": "",
            "next_action": "proceed",
        }

    output["resolved_role"] = resolved.role
    output["server"] = resolved.server
    if resolved.fallback_reason:
        output["fallback_reason"] = resolved.fallback_reason
    return output


__all__ = [
    "DispatchTarget",
    "get_configured_roles",
    "run_umu_command",
    "select_target",
]
