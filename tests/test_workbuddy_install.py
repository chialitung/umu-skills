# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""WorkBuddy 安装脚本单元测试."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

from umu_sdk.skills.workbuddy.install import (
    _configure_mcp_servers,
    _detect_workbuddy_config_dir,
    _get_credential_dir,
    _get_mcp_servers_path,
    _get_old_credential_dir,
    _load_mcp_servers,
    _save_mcp_servers,
)


class TestConfigureMcpServers:
    """测试 mcp_servers.json 配置生成."""

    def test_adds_umu_skills_server(self) -> None:
        """应添加 umu-skills orchestrator 配置."""
        settings = {"mcpServers": {}}
        result = _configure_mcp_servers(settings)

        assert "umu-skills" in result["mcpServers"]
        server = result["mcpServers"]["umu-skills"]
        assert server["type"] == "stdio"
        assert server["command"] == sys.executable
        assert server["args"] == ["-m", "umu_sdk.skills.server"]
        assert server["env"]["UMU_BASE_URL"] == "https://www.umu.cn"
        assert server["env"]["MCP_LOG_LEVEL"] == "INFO"
        assert server["env"]["UMU_SKILL_DIR"] == str(_get_credential_dir())

    def test_credential_dir_is_generic(self) -> None:
        """凭证目录应为独立的 ~/.umu_skills."""
        creds_dir = _get_credential_dir()
        assert creds_dir.name == ".umu_skills"
        assert creds_dir.parent == Path.home()

    def test_old_credential_dir_points_to_claude(self) -> None:
        """旧凭证目录辅助函数仍指向 Claude Code 路径."""
        old_dir = _get_old_credential_dir()
        assert old_dir.name == "umu"
        assert old_dir.parent.parent.name == ".claude"
    def test_preserves_existing_servers(self) -> None:
        """应保留已有的 MCP server 配置."""
        settings = {
            "mcpServers": {
                "existing-server": {
                    "type": "stdio",
                    "command": "existing",
                    "args": [],
                }
            }
        }
        result = _configure_mcp_servers(settings)

        assert "existing-server" in result["mcpServers"]
        assert result["mcpServers"]["existing-server"]["command"] == "existing"
        assert "umu-skills" in result["mcpServers"]

    def test_overwrites_existing_umu_skills(self) -> None:
        """应覆盖已有的 umu-skills 配置."""
        settings = {
            "mcpServers": {
                "umu-skills": {
                    "type": "stdio",
                    "command": "old-python",
                    "args": ["-m", "old.module"],
                }
            }
        }
        result = _configure_mcp_servers(settings)

        server = result["mcpServers"]["umu-skills"]
        assert server["command"] == sys.executable
        assert server["args"] == ["-m", "umu_sdk.skills.server"]


class TestDetectWorkbuddyConfigDir:
    """测试 WorkBuddy 配置目录自动探测."""

    def test_env_var_priority(self, tmp_path: Path) -> None:
        """环境变量 WORKBUDDY_CONFIG_DIR 优先级最高."""
        expected = tmp_path / "from-env"
        expected.mkdir()

        def _fake_getenv(key: str, default: str | None = None) -> str | None:
            if key == "WORKBUDDY_CONFIG_DIR":
                return str(expected)
            return default

        with mock.patch("umu_sdk.skills.workbuddy.install.os.getenv", _fake_getenv):
            assert _detect_workbuddy_config_dir() == expected

    def test_returns_existing_common_path(self, tmp_path: Path) -> None:
        """当存在常见路径时返回该路径."""
        # 模拟 Linux/macOS 的 .config/WorkBuddy
        config_dir = tmp_path / ".config" / "WorkBuddy"
        config_dir.mkdir(parents=True)

        def _fake_getenv(key: str, default: str | None = None) -> str | None:
            if key in ("WORKBUDDY_CONFIG_DIR", "APPDATA", "LOCALAPPDATA"):
                return None
            return default

        with (
            mock.patch("umu_sdk.skills.workbuddy.install.os.getenv", _fake_getenv),
            mock.patch("umu_sdk.skills.workbuddy.install.Path.home", return_value=tmp_path),
        ):
            # 在非 Windows 平台上生效
            if sys.platform != "win32":
                assert _detect_workbuddy_config_dir() == config_dir

    def test_returns_none_when_nothing_exists(self, tmp_path: Path) -> None:
        """没有任何路径存在时返回 None."""

        def _fake_getenv(key: str, default: str | None = None) -> str | None:
            if key in ("WORKBUDDY_CONFIG_DIR", "APPDATA", "LOCALAPPDATA"):
                return None
            return default

        with (
            mock.patch("umu_sdk.skills.workbuddy.install.os.getenv", _fake_getenv),
            mock.patch("umu_sdk.skills.workbuddy.install.Path.home", return_value=tmp_path),
        ):
            assert _detect_workbuddy_config_dir() is None


class TestMcpServersPersistence:
    """测试 mcp_servers.json 读写."""

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空结构."""
        path = tmp_path / "mcp_servers.json"
        result = _load_mcp_servers(path)
        assert result == {"mcpServers": {}}

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        """文件损坏时返回空结构."""
        path = tmp_path / "mcp_servers.json"
        path.write_text("not json", encoding="utf-8")
        result = _load_mcp_servers(path)
        assert result == {"mcpServers": {}}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        """保存时应自动创建父目录."""
        path = tmp_path / "nested" / "mcp_servers.json"
        settings = {"mcpServers": {"umu-skills": {"type": "stdio"}}}
        _save_mcp_servers(path, settings)

        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["mcpServers"]["umu-skills"]["type"] == "stdio"

    def test_get_mcp_servers_path(self, tmp_path: Path) -> None:
        """应返回正确的 mcp_servers.json 路径."""
        assert _get_mcp_servers_path(tmp_path) == tmp_path / "mcp_servers.json"


class TestEndToEndInstall:
    """端到端配置写入测试."""

    def test_full_install_flow(self, tmp_path: Path) -> None:
        """模拟完整安装流程的配置写入."""
        workbuddy_dir = tmp_path / "WorkBuddy"
        workbuddy_dir.mkdir()

        mcp_path = _get_mcp_servers_path(workbuddy_dir)
        settings = _load_mcp_servers(mcp_path)
        settings = _configure_mcp_servers(settings)
        _save_mcp_servers(mcp_path, settings)

        assert mcp_path.exists()
        loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "umu-skills" in loaded["mcpServers"]
        server = loaded["mcpServers"]["umu-skills"]
        assert server["args"] == ["-m", "umu_sdk.skills.server"]
        assert "UMU_BASE_URL" in server["env"]
        assert "MCP_LOG_LEVEL" in server["env"]
