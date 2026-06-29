# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""能力域到角色的解析器.

根据已配置账号、能力域优先级和 operation 支持的角色范围，
选择实际执行操作的最佳角色与工具名。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .capability_registry import CapabilityRegistry, get_capability_registry

logger = logging.getLogger("umu.mcp.skills")


# 能力域到推荐角色顺序的默认映射；越靠前越优先
DEFAULT_CAPABILITY_ROLE_ORDERS: dict[str, tuple[str, ...]] = {
    "learning": ("student", "teacher", "admin"),
    "course_management": ("teacher", "admin"),
    "program_management": ("teacher", "admin"),
    "permission_management": ("teacher", "admin"),
    "organization": ("admin",),
    "account_management": ("admin",),
    "data_query": ("admin",),
    "teaching_records": ("admin",),
    "instructor_management": ("admin",),
    "course_audit": ("admin",),
    "general": ("teacher", "admin", "student"),
}

ROLE_PREFIXES: dict[str, str] = {
    "teacher": "tch",
    "admin": "adm",
    "student": "stu",
}


@dataclass(frozen=True)
class ResolvedTool:
    """能力域调用解析结果."""

    role: str
    """实际执行角色."""

    server: str
    """目标子 MCP server（与 role 同名）."""

    prefix: str
    """原子工具名前缀."""

    tool_name: str
    """完整的原子工具名."""

    fallback_reason: str | None = None
    """若发生角色 fallback，说明原因."""


class CapabilityResolver:
    """解析能力域调用到具体角色与工具."""

    def __init__(
        self,
        configured_roles: list[str],
        registry: CapabilityRegistry | None = None,
    ) -> None:
        self.configured_roles = set(configured_roles)
        self.registry = registry or get_capability_registry()
        self.registry.load()

    def resolve(
        self,
        capability: str,
        operation: str,
        preferred_role: str | None = None,
    ) -> ResolvedTool:
        """将 capability + operation 解析为具体 tool.

        Args:
            capability: 能力域名称，如 "learning"。
            operation: operation 名称（不含前缀），如 "complete_course"。
            preferred_role: 显式优先角色，覆盖默认 fallback 顺序。

        Returns:
            ResolvedTool 实例。

        Raises:
            ValueError: operation 不存在或没有可用角色可执行。
        """
        supported_roles = self.registry.get_roles_for_operation(operation)
        if not supported_roles:
            raise ValueError(f"operation [{operation}] 未注册或不支持任何角色")

        if preferred_role and preferred_role in supported_roles:
            role_order = [preferred_role]
        else:
            role_order = list(self._get_role_order(capability))

        # 优先选择既在 role_order 中、又支持该 operation、且已配置的第一个角色
        for role in role_order:
            if role in supported_roles and role in self.configured_roles:
                return self._build_resolved_tool(role, operation, None)

        # 若 preferred_role 未配置，但支持该 operation，则按 fallback 顺序继续
        if preferred_role and preferred_role in supported_roles:
            for role in role_order:
                if role in supported_roles and role in self.configured_roles:
                    return self._build_resolved_tool(
                        role,
                        operation,
                        f"{preferred_role} 角色未配置，已 fallback 到 {role}",
                    )

        # 没有任何已配置角色支持该 operation
        raise ValueError(
            f"没有已配置角色可执行 operation [{operation}]，"
            f"支持的角色: {supported_roles}，已配置: {sorted(self.configured_roles)}"
        )

    def _get_role_order(self, capability: str) -> tuple[str, ...]:
        """返回能力域对应的角色优先级顺序."""
        # 允许通过环境变量覆盖
        env_key = f"UMU_CAPABILITY_{capability.upper()}_PREFERRED_ROLE"
        env_value = os.getenv(env_key)
        if env_value and env_value in ROLE_PREFIXES:
            # 把优先角色放在第一位，其余按默认顺序
            default_order = DEFAULT_CAPABILITY_ROLE_ORDERS.get(
                capability, DEFAULT_CAPABILITY_ROLE_ORDERS["general"]
            )
            rest = [r for r in default_order if r != env_value]
            return (env_value, *rest)
        return DEFAULT_CAPABILITY_ROLE_ORDERS.get(
            capability, DEFAULT_CAPABILITY_ROLE_ORDERS["general"]
        )

    def _build_resolved_tool(
        self,
        role: str,
        operation: str,
        fallback_reason: str | None,
    ) -> ResolvedTool:
        prefix = ROLE_PREFIXES[role]
        return ResolvedTool(
            role=role,
            server=role,
            prefix=prefix,
            tool_name=f"{prefix}_{operation}",
            fallback_reason=fallback_reason,
        )

    def list_configured_capabilities(self) -> list[str]:
        """返回当前已配置账号支持的所有能力域."""
        result: set[str] = set()
        for capability, operations in self.registry._capability_operations.items():
            for _op, roles in operations.items():
                if any(role in self.configured_roles for role in roles):
                    result.add(capability)
                    break
        return sorted(result)


def get_capability_resolver(configured_roles: list[str]) -> CapabilityResolver:
    """便捷函数：创建能力域解析器."""
    return CapabilityResolver(configured_roles=configured_roles)


__all__ = [
    "CapabilityResolver",
    "ResolvedTool",
    "DEFAULT_CAPABILITY_ROLE_ORDERS",
    "ROLE_PREFIXES",
    "get_capability_resolver",
]
