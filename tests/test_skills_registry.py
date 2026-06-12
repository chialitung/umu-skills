"""Tests for skills.registry and skills.decorators."""

from __future__ import annotations

import pytest

from umu_sdk.skills.decorators import SkillContext, skill
from umu_sdk.skills.registry import SkillRegistry


class TestSkillRegistry:
    def test_register_and_list(self) -> None:
        registry = SkillRegistry()

        @skill(
            name="test_skill",
            description="A test skill",
            required_servers=["teacher"],
        )
        async def test_skill(ctx: SkillContext, value: int) -> dict:
            return {"result": value * 2}

        registry.register_function(test_skill)
        infos = registry.list_skills()
        assert len(infos) == 1
        assert infos[0].name == "test_skill"
        assert infos[0].description == "A test skill"
        assert infos[0].required_servers == ["teacher"]

    def test_get_skill(self) -> None:
        registry = SkillRegistry()

        @skill(name="hello", description="Say hello")
        async def hello(ctx: SkillContext, name: str = "world") -> dict:
            return {"message": f"hello {name}"}

        registry.register_function(hello)
        sf = registry.get_skill("hello")
        assert sf.info.name == "hello"
        assert any(p.name == "name" and not p.required for p in sf.info.parameters)

    def test_get_skill_not_found(self) -> None:
        registry = SkillRegistry()
        with pytest.raises(KeyError, match="Skill \\[missing\\] 不存在"):
            registry.get_skill("missing")

    def test_validate_servers(self) -> None:
        registry = SkillRegistry()

        @skill(name="needs_teacher", description="", required_servers=["teacher"])
        async def needs_teacher(ctx: SkillContext) -> dict:
            return {}

        @skill(name="needs_student", description="", required_servers=["student"])
        async def needs_student(ctx: SkillContext) -> dict:
            return {}

        registry.register_function(needs_teacher)
        registry.register_function(needs_student)

        missing = registry.validate_servers(["teacher"])
        assert missing == ["student"]

        missing = registry.validate_servers(["teacher", "student"])
        assert missing == []

    def test_load_builtin_skills(self) -> None:
        registry = SkillRegistry()
        registry.load_builtin_skills()
        names = {s.name for s in registry.list_skills()}
        assert "create_course_with_scorm" in names
        assert "enroll_course" in names
        assert "batch_onboard_users" in names


class TestSkillDecorator:
    def test_builds_parameters_from_signature(self) -> None:
        @skill(name="demo", description="Demo")
        async def demo(
            ctx: SkillContext,
            title: str,
            count: int = 1,
        ) -> dict:
            return {}

        info = demo.info
        params = {p.name: p for p in info.parameters}
        assert "title" in params
        assert params["title"].required is True
        assert params["title"].type == "string"
        assert "count" in params
        assert params["count"].required is False
        assert params["count"].default == 1
        assert params["count"].type == "integer"

    def test_skill_function_is_callable(self) -> None:
        @skill(name="callable", description="Callable")
        async def callable_skill(ctx: SkillContext, value: int) -> dict:
            return {"value": value}

        assert callable_skill.info.name == "callable"
