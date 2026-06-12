"""Tests for skills.config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umu_sdk.skills.config import get_config, load_config_from_dict
from umu_sdk.skills.models import ServerConfig


class TestSkillsConfig:
    def test_default_config_contains_three_servers(self) -> None:
        config = get_config(use_env_overrides=False)
        names = {s.name for s in config.servers}
        assert names == {"teacher", "student", "admin"}
        assert all(s.enabled for s in config.servers)

    def test_load_config_from_dict(self) -> None:
        data = {
            "servers": [
                {"name": "teacher", "command": "custom-teacher", "enabled": False},
            ],
            "read_timeout_seconds": 30.0,
        }
        config = load_config_from_dict(data)
        assert len(config.servers) == 1
        assert config.servers[0].name == "teacher"
        assert config.servers[0].command == "custom-teacher"
        assert config.servers[0].enabled is False
        assert config.read_timeout_seconds == 30.0

    def test_env_override_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UMU_SKILLS_SERVERS", "teacher,admin")
        config = get_config(use_env_overrides=True)
        enabled = {s.name for s in config.servers if s.enabled}
        assert enabled == {"teacher", "admin"}

    def test_env_override_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UMU_SKILLS_TIMEOUT", "120")
        config = get_config(use_env_overrides=True)
        assert config.read_timeout_seconds == 120.0

    def test_load_config_from_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "skills_config.json"
        data = {
            "servers": [
                {"name": "custom", "command": "echo", "args": ["hello"]},
            ]
        }
        config_file.write_text(json.dumps(data), encoding="utf-8")
        config = get_config(path=config_file, use_env_overrides=False)
        assert len(config.servers) == 1
        assert config.servers[0].name == "custom"
        assert config.servers[0].command == "echo"
        assert config.servers[0].args == ["hello"]

    def test_server_config_env(self) -> None:
        config = ServerConfig(
            name="teacher",
            command="umu-skills-teacher",
            env={"KEY": "VALUE"},
        )
        assert config.env == {"KEY": "VALUE"}
