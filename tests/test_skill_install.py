"""Tests for skills.install."""

from __future__ import annotations

import sys

from pathlib import Path
from typing import Any

from umu_sdk.skills.install import _configure_mcp_servers, _copy_skill


class TestConfigureMcpServers:
    def test_adds_three_servers(self) -> None:
        settings: dict[str, Any] = {}
        result = _configure_mcp_servers(settings)

        assert "mcpServers" in result
        servers = result["mcpServers"]
        assert set(servers.keys()) == {"umu-teacher", "umu-student", "umu-admin"}
        assert servers["umu-teacher"]["command"] == sys.executable
        assert servers["umu-student"]["command"] == sys.executable
        assert servers["umu-admin"]["command"] == sys.executable
        assert servers["umu-teacher"]["args"] == ["-m", "umu_sdk.adapters.mcp.teacher"]
        assert servers["umu-student"]["args"] == ["-m", "umu_sdk.adapters.mcp.student"]
        assert servers["umu-admin"]["args"] == ["-m", "umu_sdk.adapters.mcp.admin"]

    def test_preserves_existing_settings(self) -> None:
        settings = {"otherKey": "value", "mcpServers": {"existing": {}}}
        result = _configure_mcp_servers(settings)

        assert result["otherKey"] == "value"
        assert "existing" in result["mcpServers"]
        assert "umu-teacher" in result["mcpServers"]

    def test_env_defaults(self) -> None:
        result = _configure_mcp_servers({})
        env = result["mcpServers"]["umu-teacher"]["env"]

        assert env["UMU_BASE_URL"] == "${UMU_BASE_URL:-https://www.umu.cn}"
        assert env["MCP_LOG_LEVEL"] == "${MCP_LOG_LEVEL:-INFO}"


class TestCopySkill:
    def test_copies_skill_files(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        (source / "SKILL.md").write_text("test skill", encoding="utf-8")
        subdir = source / "references"
        subdir.mkdir()
        (subdir / "tools.md").write_text("tools", encoding="utf-8")

        _copy_skill(source, target)

        assert (target / "SKILL.md").exists()
        assert (target / "references" / "tools.md").exists()
        assert (target / "SKILL.md").read_text(encoding="utf-8") == "test skill"

    def test_replaces_existing_target(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        (source / "SKILL.md").write_text("new", encoding="utf-8")
        (target / "SKILL.md").write_text("old", encoding="utf-8")
        (target / "stale.txt").write_text("stale", encoding="utf-8")

        _copy_skill(source, target)

        assert (target / "SKILL.md").read_text(encoding="utf-8") == "new"
        assert not (target / "stale.txt").exists()
