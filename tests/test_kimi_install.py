"""Tests for skills.kimi.install."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from umu_sdk.skills.kimi.install import (
    _check_installation,
    _configure_mcp_servers,
    _copy_skill,
    _get_credential_dir,
    _get_global_skill_dir,
    _get_kimi_code_home,
    _get_mcp_servers_path,
    _load_mcp_servers,
    _perform_install,
    _save_mcp_servers,
    add_alias,
    list_aliases,
    remove_alias,
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


class TestAliasManagement:
    def test_add_alias_success(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社")

        assert success is True
        assert "敏学社" in msg
        assert list_aliases(skill_dir) == ["敏学社"]

    def test_add_alias_duplicate(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = add_alias(skill_dir, "敏学社")

        assert success is False
        assert "已存在" in msg

    def test_add_alias_empty(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        assert add_alias(skill_dir, "")[0] is False
        assert add_alias(skill_dir, "   ")[0] is False

    def test_add_alias_too_long(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        long_alias = "a" * 51

        success, msg = add_alias(skill_dir, long_alias)

        assert success is False
        assert "长度不能超过" in msg

    def test_add_alias_invalid_characters(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社!")

        assert success is False
        assert "只能包含" in msg

    def test_remove_alias_success(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = remove_alias(skill_dir, "敏学社")

        assert success is True
        assert list_aliases(skill_dir) == []

    def test_remove_alias_not_found(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = remove_alias(skill_dir, "不存在")

        assert success is False
        assert "不存在" in msg


class TestPerformInstall:
    def test_installs_all_skills(self, tmp_path: Path, monkeypatch) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        for name in ("umu", "umu-teacher", "umu-student", "umu-admin"):
            (source / name).mkdir()
            (source / name / "SKILL.md").write_text(
                "---\nname: " + name + "\ndescription: test\n---\n", encoding="utf-8"
            )

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_global_skills_root", lambda: target
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "mcp.json",
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        installed = _perform_install(source)

        assert set(installed) == {"umu", "umu-admin", "umu-teacher", "umu-student"}
        assert (target / "umu" / "SKILL.md").exists()
        assert (target / "umu-teacher" / "SKILL.md").exists()
        assert (tmp_path / "mcp.json").exists()

    def test_preserves_semantic_config_on_reinstall(self, tmp_path: Path, monkeypatch) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        (source / "umu").mkdir()
        (source / "umu" / "SKILL.md").write_text(
            "---\nname: umu\ndescription: |\n  <!-- BEGIN_DESCRIPTION -->\n  desc\n  <!-- END_DESCRIPTION -->\n---\n"
            "<!-- BEGIN_TRIGGER -->\ntrigger\n<!-- END_TRIGGER -->",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_global_skills_root", lambda: target
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "mcp.json",
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        # 先安装一次并开启语义触发
        _perform_install(source, semantic_trigger=True)
        config = json.loads((target / "umu" / "config.json").read_text(encoding="utf-8"))
        assert config["semantic_trigger_enabled"] is True

        # 再次安装不传参数，应保留开启状态
        _perform_install(source)
        config = json.loads((target / "umu" / "config.json").read_text(encoding="utf-8"))
        assert config["semantic_trigger_enabled"] is True


class TestCheckInstallation:
    def test_check_reports_missing(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_kimi_code_home", lambda: tmp_path
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        code = _check_installation()
        captured = capsys.readouterr()

        assert code == 1
        assert "mcp.json 不存在" in captured.out

    def test_check_reports_ok(self, tmp_path: Path, monkeypatch, capsys) -> None:
        home = tmp_path
        skills = home / "skills"
        for name in ("umu", "umu-teacher", "umu-student", "umu-admin"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text("---\nname: " + name + "\n---\n", encoding="utf-8")

        mcp = {
            "mcpServers": {
                "umu-teacher": {
                    "command": sys.executable,
                    "args": ["-m", "umu_sdk.adapters.mcp.teacher"],
                },
                "umu-student": {
                    "command": sys.executable,
                    "args": ["-m", "umu_sdk.adapters.mcp.student"],
                },
                "umu-admin": {
                    "command": sys.executable,
                    "args": ["-m", "umu_sdk.adapters.mcp.admin"],
                },
            }
        }
        mcp_path = home / "mcp.json"
        mcp_path.write_text(json.dumps(mcp), encoding="utf-8")

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_kimi_code_home", lambda: home
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        code = _check_installation()
        captured = capsys.readouterr()

        assert code == 0
        assert "状态正常" in captured.out
