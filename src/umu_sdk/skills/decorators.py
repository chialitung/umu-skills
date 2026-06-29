# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skill 装饰器与执行上下文.

提供 `@skill()` 装饰器用于声明高阶 Skill，以及 Skill 执行时使用的上下文。
上下文支持按能力域（capability）自动解析最佳角色并调用底层原子工具。
"""

from __future__ import annotations

import inspect
import logging
import os
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.env_loader import load_env_credentials
from .auth_config import get_configured_roles
from .capability_registry import get_capability_registry
from .capability_resolver import ROLE_PREFIXES, CapabilityResolver
from .mcp_client import MCPClientManager
from .models import SkillInfo, SkillParameter, SkillFunction


@dataclass
class SkillContext:
    """Skill 执行上下文.

    每个 Skill 在执行时都会收到一个上下文实例，用于访问子 MCP、日志、
    能力域解析与角色 fallback 等能力。
    """

    mcp: MCPClientManager
    skill_name: str = ""
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("umu.mcp.skills"))
    session_state: dict[str, Any] = field(default_factory=dict)
    configured_roles: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """确保 configured_roles 已初始化."""
        if not self.configured_roles:
            self.configured_roles = get_configured_roles()

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """便捷方法：调用子 MCP 工具并返回解析后的 dict.

        返回值包含 success/data/error_code/error_message 等字段。
        """
        result = await self.mcp.call_tool(
            server=server,
            tool=tool,
            arguments=arguments,
            read_timeout_seconds=read_timeout_seconds,
        )
        return {
            "success": result.success,
            "data": result.data,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }

    async def call_capability_tool(
        self,
        capability: str,
        operation: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
        preferred_role: str | None = None,
    ) -> dict[str, Any]:
        """按能力域调用底层 operation，自动解析最佳角色与工具前缀.

        解析流程：
        1. 通过 CapabilityResolver 查询该 operation 支持的角色；
        2. 根据 configured_roles 选择最佳 role（带 fallback）；
        3. 若目标 server 与 resolved role 不一致，使用 resolved role 的凭据登录目标 server；
        4. 构造 tool 名 `{prefix}_{operation}` 并调用。

        Args:
            capability: 能力域名称，如 "learning"。
            operation: operation 名称（不含前缀），如 "complete_course"。
            arguments: 透传给原子工具的参数。
            read_timeout_seconds: 可选超时秒数。
            preferred_role: 显式优先角色，覆盖默认 fallback 顺序。

        Returns:
            统一返回信封 dict。
        """
        resolver = CapabilityResolver(
            configured_roles=self.configured_roles,
            registry=get_capability_registry(),
        )
        resolved = resolver.resolve(
            capability=capability,
            operation=operation,
            preferred_role=preferred_role,
        )

        # 当 resolved role 与目标 server 不一致时，用 resolved role 的凭据登录目标 server
        auth_error = await self._ensure_server_authenticated(
            server=resolved.server,
            role=resolved.role,
        )
        if auth_error is not None:
            return auth_error

        self.logger.debug(
            "[%s] capability=%s operation=%s -> %s/%s",
            self.skill_name,
            capability,
            operation,
            resolved.server,
            resolved.tool_name,
        )

        result = await self.mcp.call_tool(
            server=resolved.server,
            tool=resolved.tool_name,
            arguments=arguments or {},
            read_timeout_seconds=read_timeout_seconds,
        )
        return {
            "success": result.success,
            "data": result.data,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "resolved_role": resolved.role,
            "server": resolved.server,
            "tool": resolved.tool_name,
            **({"fallback_reason": resolved.fallback_reason} if resolved.fallback_reason else {}),
        }

    async def call_role_tool(
        self,
        role: str,
        operation: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """显式指定角色调用 operation（用于必须固定角色的场景）.

        Args:
            role: 角色名称，如 "admin"。
            operation: operation 名称（不含前缀）。
            arguments: 透传给原子工具的参数。
            read_timeout_seconds: 可选超时秒数。

        Returns:
            统一返回信封 dict。
        """
        if role not in ROLE_PREFIXES:
            return {
                "success": False,
                "data": None,
                "error_code": "INVALID_ROLE",
                "error_message": f"不支持的角色: {role}",
                "suggested_action": "请使用 admin/teacher/student 之一",
                "next_action": "needs_user_input",
            }

        prefix = ROLE_PREFIXES[role]
        tool_name = f"{prefix}_{operation}"

        auth_error = await self._ensure_server_authenticated(server=role, role=role)
        if auth_error is not None:
            return auth_error

        result = await self.mcp.call_tool(
            server=role,
            tool=tool_name,
            arguments=arguments or {},
            read_timeout_seconds=read_timeout_seconds,
        )
        return {
            "success": result.success,
            "data": result.data,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "resolved_role": role,
            "server": role,
            "tool": tool_name,
        }

    async def _ensure_server_authenticated(
        self,
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
            return {
                "success": False,
                "data": None,
                "error_code": "AUTH_FALLBACK_FAILED",
                "error_message": f"无法获取 {role} 角色凭据以登录 {server} 子 MCP",
                "suggested_action": f"请配置 UMU_{role.upper()}_USERNAME/PASSWORD",
                "next_action": "retry",
            }

        prefix = ROLE_PREFIXES[role]
        login_tool = f"{prefix}login"
        self.logger.info(
            "[%s] 使用 %s 凭据登录 %s 子 MCP: %s",
            self.skill_name,
            role,
            server,
            username,
        )
        result = await self.mcp.call_tool(
            server=server,
            tool=login_tool,
            arguments={"username": username, "password": password},
        )
        if not result.success:
            return {
                "success": False,
                "data": result.data,
                "error_code": result.error_code or "AUTH_FALLBACK_FAILED",
                "error_message": (
                    f"使用 {role} 凭据登录 {server} 子 MCP 失败: "
                    f"{result.error_message}"
                ),
                "suggested_action": "请确认该账号在目标角色下具备操作权限",
                "next_action": "retry",
            }
        return None


# 类型别名：Skill 函数签名接受 SkillContext 作为第一个位置参数
SkillCallable = Callable[..., Any]


def _python_type_to_json_type(annotation: Any) -> str:
    """将 Python 类型注解粗略映射为 JSON Schema 类型名."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        if origin is list or annotation is list:
            return "array"
        if origin is dict or annotation is dict:
            return "object"
        return "string"

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        Any: "string",
    }
    return type_map.get(annotation, "string")


def _build_skill_info(
    func: SkillCallable,
    name: str,
    description: str,
    required_capabilities: list[str],
    return_description: str,
) -> SkillInfo:
    """根据函数签名构建 SkillInfo."""
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    parameters: list[SkillParameter] = []

    for param_name, param in sig.parameters.items():
        # 第一个参数约定为 SkillContext，不暴露为 Skill 参数
        if param_name == "ctx":
            continue

        annotation = hints.get(param_name, param.annotation)
        param_type = _python_type_to_json_type(annotation)
        required = param.default is inspect.Parameter.empty
        default = None if required else param.default

        parameters.append(
            SkillParameter(
                name=param_name,
                description="",
                type=param_type,
                required=required,
                default=default,
            )
        )

    return SkillInfo(
        name=name,
        description=description,
        required_capabilities=required_capabilities,
        parameters=parameters,
        return_description=return_description,
    )


def skill(
    name: str,
    description: str,
    required_capabilities: list[str] | None = None,
    return_description: str = "",
) -> Callable[[SkillCallable], SkillFunction]:
    """声明一个高阶 Skill.

    被装饰的函数第一个参数必须是 `ctx: SkillContext`，其余参数作为 Skill 的输入。
    Skill 通过 `required_capabilities` 声明所需能力域，由编排层自动解析最佳角色。

    示例：
        @skill(
            name="create_course_with_scorm",
            description="创建空课程并添加 SCORM 小节",
            required_capabilities=["course_management"],
        )
        async def create_course_with_scorm(
            ctx: SkillContext,
            title: str,
            scorm_path: str,
        ) -> str:
            ...
    """

    def decorator(func: SkillCallable) -> SkillFunction:
        info = _build_skill_info(
            func=func,
            name=name,
            description=description,
            required_capabilities=list(required_capabilities or []),
            return_description=return_description,
        )

        wrapped = SkillFunction(func=func, info=info)
        # 保留原始函数引用，便于 registry 通过属性识别
        func._umu_skill_function = wrapped
        return wrapped

    return decorator


def is_skill_function(obj: Any) -> bool:
    """判断对象是否是被 @skill 装饰的函数."""
    return isinstance(obj, SkillFunction) or hasattr(obj, "_umu_skill_function")


def get_skill_function(obj: Any) -> SkillFunction | None:
    """从对象中提取 SkillFunction."""
    if isinstance(obj, SkillFunction):
        return obj
    if hasattr(obj, "_umu_skill_function"):
        return obj._umu_skill_function
    return None


__all__ = [
    "SkillContext",
    "SkillCallable",
    "skill",
    "is_skill_function",
    "get_skill_function",
]
