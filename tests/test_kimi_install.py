"""Tests for skills.kimi.install."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from umu_sdk.skills.kimi.install import (
    _configure_mcp_servers,
    _copy_skill,
    _get_credential_dir,
    _get_global_skill_dir,
    _get_kimi_code_home,
    _get_mcp_servers_path,
    _load_mcp_servers,
    _save_mcp_servers,
)


class TestConfigureMcpServers:
    def test_adds_three_servers(self) -> None:
        settings: dict = {}
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

    def test_env_uses_resolved_values(self) -> None:
        result = _configure_mcp_servers({})
        env = result["mcpServers"]["umu-teacher"]["env"]

        assert env["UMU_BASE_URL"] == "https://www.umu.cn"
        assert env["MCP_LOG_LEVEL"] == "INFO"
        assert env["UMU_SKILL_DIR"] == str(_get_credential_dir())

    def test_preserves_existing_servers(self) -> None:
        settings = {"mcpServers": {"existing": {"command": "existing", "args": []}}}
        result = _configure_mcp_servers(settings)

        assert "existing" in result["mcpServers"]
        assert "umu-teacher" in result["mcpServers"]

    def test_overwrites_existing_umu_servers(self) -> None:
        settings = {
            "mcpServers": {
                "umu-teacher": {"command": "old", "args": ["-m", "old.module"]}
            }
        }
        result = _configure_mcp_servers(settings)

        assert result["mcpServers"]["umu-teacher"]["command"] == sys.executable


class TestInstallPaths:
    def test_kimi_code_home_default(self) -> None:
        home = _get_kimi_code_home()
        assert home.name == ".kimi-code"
        assert home.parent == Path.home()

    def test_global_skill_dir(self) -> None:
        skill_dir = _get_global_skill_dir()
        assert skill_dir.name == "umu"
        assert skill_dir.parent.name == "skills"
        assert skill_dir.parent.parent.name == ".kimi-code"

    def test_mcp_servers_path(self) -> None:
        path = _get_mcp_servers_path()
        assert path.name == "mcp.json"
        assert path.parent.name == ".kimi-code"

    def test_credential_dir_is_generic(self) -> None:
        creds_dir = _get_credential_dir()
        assert creds_dir.name == ".umu_skills"
        assert creds_dir.parent == Path.home()


class TestMcpServersPersistence:
    def test_load_missing_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path", lambda: tmp_path / "mcp.json"
        )
        result = _load_mcp_servers()
        assert result == {"mcpServers": {}}

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "mcp.json"
        path.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path", lambda: path
        )
        result = _load_mcp_servers()
        assert result == {"mcpServers": {}}

    def test_save_creates_parent_dirs(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "nested" / "mcp.json",
        )
        settings = {"mcpServers": {"umu-teacher": {"command": "python"}}}
        _save_mcp_servers(settings)

        path = tmp_path / "nested" / "mcp.json"
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["mcpServers"]["umu-teacher"]["command"] == "python"


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
