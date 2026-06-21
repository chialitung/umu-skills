"""RoleResolver 单元测试."""

from __future__ import annotations

import pytest

from umu_sdk.skills.role_resolver import RoleResolver


@pytest.fixture
def resolver_all_configured():
    return RoleResolver(
        available_servers=["admin", "teacher", "student"],
        configured_roles=["admin", "teacher", "student"],
        session_state={},
    )


class TestDefaultRole:
    def test_default_role_takes_priority(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve(
            "随便说点什么", required_capability="teacher", default_role="admin"
        )
        assert resolved.role == "admin"
        assert resolved.server == "admin"
        assert resolved.prefix == "adm_"


class TestExplicitRole:
    def test_explicit_admin(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("用 admin 账号查看企业课程")
        assert resolved.role == "admin"

    def test_explicit_teacher(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("用 teacher 账号创建课程")
        assert resolved.role == "teacher"

    def test_explicit_student(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("用 student 账号报名")
        assert resolved.role == "student"

    def test_admin_keyword_before_teacher(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("teacher 使用 admin 权限")
        assert resolved.role == "admin"


class TestSessionContext:
    def test_last_role_used(self, resolver_all_configured):
        resolver_all_configured.session_state["last_role"] = "student"
        resolved = resolver_all_configured.resolve("查看进度")
        assert resolved.role == "student"

    def test_explicit_overrides_last_role(self, resolver_all_configured):
        resolver_all_configured.session_state["last_role"] = "student"
        resolved = resolver_all_configured.resolve("用 admin 查看")
        assert resolved.role == "admin"


class TestRequiredCapability:
    def test_teacher_capability_prefers_teacher(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("创建课程", required_capability="teacher")
        assert resolved.role == "teacher"

    def test_student_capability_prefers_student(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("报名", required_capability="student")
        assert resolved.role == "student"

    def test_admin_capability_prefers_admin(self, resolver_all_configured):
        resolved = resolver_all_configured.resolve("查看企业课程", required_capability="admin")
        assert resolved.role == "admin"


class TestFallback:
    def test_teacher_fallback_to_admin(self):
        resolver = RoleResolver(
            available_servers=["admin"],
            configured_roles=["admin"],
            session_state={},
        )
        resolved = resolver.resolve("创建课程", required_capability="teacher")
        assert resolved.role == "admin"
        assert "teacher 角色未配置" in (resolved.fallback_reason or "")

    def test_student_fallback_to_teacher(self):
        resolver = RoleResolver(
            available_servers=["teacher"],
            configured_roles=["teacher"],
            session_state={},
        )
        resolved = resolver.resolve("报名", required_capability="student")
        assert resolved.role == "teacher"
        assert "student 角色未配置" in (resolved.fallback_reason or "")

    def test_student_fallback_to_admin_if_teacher_unavailable(self):
        resolver = RoleResolver(
            available_servers=["admin"],
            configured_roles=["admin"],
            session_state={},
        )
        resolved = resolver.resolve("报名", required_capability="student")
        assert resolved.role == "admin"
        assert "student 角色未配置" in (resolved.fallback_reason or "")

    def test_preferred_admin_but_unconfigured(self):
        resolver = RoleResolver(
            available_servers=["teacher"],
            configured_roles=["teacher"],
            session_state={},
        )
        resolved = resolver.resolve(
            "用 admin 创建课程", required_capability="teacher"
        )
        assert resolved.role == "teacher"
        assert "admin 角色未配置" in (resolved.fallback_reason or "")


class TestConfirmation:
    def test_needs_confirmation_when_ambiguous(self):
        resolver = RoleResolver(
            available_servers=["admin", "teacher"],
            configured_roles=["admin", "teacher"],
            session_state={},
        )
        resolved = resolver.resolve("随便做点什么")
        assert resolved.needs_confirmation is True
        assert resolved.confirmation_message is not None
        assert "teacher" in resolved.confirmation_message
        assert "admin" in resolved.confirmation_message

    def test_single_role_no_confirmation(self):
        resolver = RoleResolver(
            available_servers=["teacher"],
            configured_roles=["teacher"],
            session_state={},
        )
        resolved = resolver.resolve("随便做点什么")
        assert resolved.needs_confirmation is False
        assert resolved.role == "teacher"

    def test_default_role_avoids_confirmation(self):
        resolver = RoleResolver(
            available_servers=["admin", "teacher"],
            configured_roles=["admin", "teacher"],
            session_state={},
        )
        resolved = resolver.resolve("创建课程", default_role="teacher")
        assert resolved.needs_confirmation is False
        assert resolved.role == "teacher"


class TestNoRoles:
    def test_no_configured_roles(self):
        resolver = RoleResolver(
            available_servers=[],
            configured_roles=[],
            session_state={},
        )
        resolved = resolver.resolve("创建课程")
        assert resolved.role == ""
        assert "未配置任何角色" in (resolved.fallback_reason or "")


__all__ = ["TestDefaultRole"]
