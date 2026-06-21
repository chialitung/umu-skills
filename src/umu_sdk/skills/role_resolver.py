# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""角色解析器.

根据用户意图、会话上下文、已配置角色与子 MCP 可用性，选择执行操作的最佳角色。
遵循能力层级：admin ⊇ teacher ⊇ student。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedRole:
    """角色解析结果."""

    role: str
    """选中的角色：admin / teacher / student。"""

    server: str
    """对应子 MCP 名称（与 role 相同）。"""

    prefix: str
    """原子工具名前缀：adm_ / tch_ / stu_。"""

    fallback_reason: str | None = None
    """若发生高权限角色 fallback，说明原因。"""

    needs_confirmation: bool = False
    """是否需要用户确认角色选择。"""

    confirmation_message: str | None = None
    """需要确认时展示给用户的提示信息。"""


class RoleResolver:
    """根据意图与上下文解析最佳执行角色."""

    ROLE_PREFIXES: dict[str, str] = {
        "admin": "adm_",
        "teacher": "tch_",
        "student": "stu_",
    }

    CAPABILITY_FALLBACK_ORDER: dict[str, tuple[str, ...]] = {
        "student": ("student", "teacher", "admin"),
        "teacher": ("teacher", "admin"),
        "admin": ("admin",),
    }

    def __init__(
        self,
        available_servers: list[str],
        configured_roles: list[str],
        session_state: dict[str, Any],
    ) -> None:
        """初始化角色解析器.

        Args:
            available_servers: 已连接的子 MCP 名称列表。
            configured_roles: 已配置凭证的角色列表。
            session_state: 当前会话状态，可包含 last_role、remembered_role 等。
        """
        self.available_servers = set(available_servers)
        self.configured_roles = set(configured_roles)
        self.session_state = session_state

    def resolve(
        self,
        intent: str,
        required_capability: str | None = None,
        default_role: str | None = None,
    ) -> ResolvedRole:
        """解析意图对应的执行角色.

        解析优先级：
        1. 显式 default_role（来自斜杠指令默认角色）
        2. 用户话语中的显式角色声明（如"用 admin 账号"）
        3. 上下文延续（session_state["last_role"]）
        4. 能力最佳匹配（required_capability）
        5. 高权限 fallback（teacher 未配置 → admin；student 未配置 → teacher/admin）
        6. 仍歧义时返回 needs_confirmation

        Args:
            intent: 用户原始输入或命令。
            required_capability: 意图所需能力（student/teacher/admin）。
            default_role: 斜杠指令指定的默认角色。

        Returns:
            ResolvedRole 实例。
        """
        # 1. 斜杠指令默认角色
        if default_role and default_role in self.ROLE_PREFIXES:
            return self._resolve_with_fallback(default_role, None)

        # 2. 显式角色声明
        explicit = self._extract_explicit_role(intent)
        if explicit:
            return self._resolve_with_fallback(explicit, required_capability)

        # 3. 上下文延续
        last_role = self.session_state.get("last_role")
        if last_role in self.ROLE_PREFIXES:
            return self._resolve_with_fallback(last_role, required_capability)

        # 4. 能力最佳匹配
        if required_capability:
            return self._resolve_with_fallback(required_capability, required_capability)

        # 5. 无信息时返回需要确认
        candidates = self._available_configured_roles()
        if not candidates:
            return ResolvedRole(
                role="",
                server="",
                prefix="",
                fallback_reason="未配置任何角色凭证",
                needs_confirmation=False,
            )

        if len(candidates) == 1:
            role = candidates[0]
            return ResolvedRole(
                role=role,
                server=role,
                prefix=self.ROLE_PREFIXES[role],
            )

        return ResolvedRole(
            role="",
            server="",
            prefix="",
            needs_confirmation=True,
            confirmation_message=(
                f"检测到多个角色可用：{', '.join(candidates)}。"
                "请回复数字选择：1-teacher 2-admin 3-student，或说明使用哪个角色。"
            ),
        )

    def _resolve_with_fallback(
        self,
        preferred_role: str,
        required_capability: str | None,
    ) -> ResolvedRole:
        """按 fallback 顺序解析角色."""
        capability = required_capability or preferred_role
        fallback_order = self.CAPABILITY_FALLBACK_ORDER.get(capability, (capability,))

        for role in fallback_order:
            if role in self.configured_roles and role in self.available_servers:
                fallback_reason = None
                if role != preferred_role:
                    fallback_reason = (
                        f"{preferred_role} 角色未配置或不可用，"
                        f"已 fallback 到 {role} 角色执行"
                    )
                return ResolvedRole(
                    role=role,
                    server=role,
                    prefix=self.ROLE_PREFIXES[role],
                    fallback_reason=fallback_reason,
                )

        # 没有任何可用角色可执行该能力
        return ResolvedRole(
            role=preferred_role,
            server=preferred_role,
            prefix=self.ROLE_PREFIXES[preferred_role],
            fallback_reason=f"没有可用角色可执行 {capability} 能力，请配置相应账号",
        )

    def _extract_explicit_role(self, intent: str) -> str | None:
        """从用户输入中提取显式角色声明."""
        lowered = intent.lower()
        # 顺序：admin 在前，避免 "teacher admin" 被误判
        if "admin" in lowered or "管理员" in lowered:
            return "admin"
        if "teacher" in lowered or "讲师" in lowered:
            return "teacher"
        if "student" in lowered or "学员" in lowered:
            return "student"
        return None

    def _available_configured_roles(self) -> list[str]:
        """返回既已配置又可用子 MCP 的角色列表（按 admin/teacher/student 优先级）。"""
        return [
            role
            for role in ("admin", "teacher", "student")
            if role in self.configured_roles and role in self.available_servers
        ]


__all__ = ["ResolvedRole", "RoleResolver"]
