"""Tests for skills.install."""

from __future__ import annotations

import json
import sys

from pathlib import Path
from typing import Any

from umu_sdk.skills.install import (
    MAX_ALIASES,
    _configure_mcp_servers,
    _copy_skill,
    _load_skill_config,
    _render_skill_md,
    _save_aliases,
    _save_skill_config,
    add_alias,
    list_aliases,
    remove_alias,
)


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


class TestSkillConfig:
    def test_load_skill_config_existing(self, tmp_path: Path) -> None:
        """读取已存在的 config.json."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        config_file = skill_dir / "config.json"
        config_file.write_text('{"semantic_trigger_enabled": true}', encoding="utf-8")

        config = _load_skill_config(skill_dir)
        assert config["semantic_trigger_enabled"] is True

    def test_load_skill_config_missing(self, tmp_path: Path) -> None:
        """config.json 不存在时返回空字典."""
        config = _load_skill_config(tmp_path)
        assert config == {}

    def test_load_skill_config_corrupt(self, tmp_path: Path) -> None:
        """config.json 损坏时返回空字典."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        (skill_dir / "config.json").write_text("not-json", encoding="utf-8")

        config = _load_skill_config(skill_dir)
        assert config == {}

    def test_save_skill_config(self, tmp_path: Path) -> None:
        """保存配置到 config.json."""
        _save_skill_config(tmp_path, {"semantic_trigger_enabled": False})

        config_file = tmp_path / "config.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert data["semantic_trigger_enabled"] is False


class TestRenderSkillMd:
    _TEMPLATE = """---
name: umu
description: |
  <!-- BEGIN_DESCRIPTION -->
  PLACEHOLDER
  <!-- END_DESCRIPTION -->
---

## 触发条件

<!-- BEGIN_TRIGGER -->
PLACEHOLDER
<!-- END_TRIGGER -->

## 前置条件
"""

    def test_render_explicit_mode(self, tmp_path: Path) -> None:
        """关闭状态下 SKILL.md 不包含语义触发关键词."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=False)

        content = skill_md.read_text(encoding="utf-8")
        assert "即使他们没有说" not in content
        assert "用户输入 `/umu`" in content
        assert "<!-- BEGIN_DESCRIPTION -->" in content

    def test_render_semantic_mode(self, tmp_path: Path) -> None:
        """开启状态下 SKILL.md 使用基于完整意图的 UMU 触发规则."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=True)

        content = skill_md.read_text(encoding="utf-8")
        assert "UMU 在线学习平台" in content
        assert "UMU 平台能够完成" in content
        assert "不要仅因为用户提到通用教育词汇" in content
        assert "以下情况不要调用本 skill" in content
        assert "即使他们没有说" not in content
        assert "<!-- BEGIN_TRIGGER -->" in content

    def test_config_preserved_on_reinstall(self, tmp_path: Path) -> None:
        """已有配置中的未知字段在保存时保留."""
        _save_skill_config(
            tmp_path,
            {"semantic_trigger_enabled": True, "custom_key": "custom_value"},
        )

        config = _load_skill_config(tmp_path)
        assert config["semantic_trigger_enabled"] is True
        assert config["custom_key"] == "custom_value"


class TestAliasManagement:
    def test_add_alias_success(self, tmp_path: Path) -> None:
        """成功添加别名."""
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社")

        assert success is True
        assert "敏学社" in msg
        assert list_aliases(skill_dir) == ["敏学社"]

    def test_add_alias_duplicate(self, tmp_path: Path) -> None:
        """重复添加同名别名失败."""
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = add_alias(skill_dir, "敏学社")

        assert success is False
        assert "已存在" in msg

    def test_add_alias_empty(self, tmp_path: Path) -> None:
        """空别名或纯空白别名被拒绝."""
        skill_dir = tmp_path / "umu"
        assert add_alias(skill_dir, "")[0] is False
        assert add_alias(skill_dir, "   ")[0] is False

    def test_add_alias_too_long(self, tmp_path: Path) -> None:
        """超长别名被拒绝."""
        skill_dir = tmp_path / "umu"
        long_alias = "a" * 51

        success, msg = add_alias(skill_dir, long_alias)

        assert success is False
        assert "长度不能超过" in msg

    def test_add_alias_invalid_characters(self, tmp_path: Path) -> None:
        """包含非法字符的别名被拒绝."""
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社!")

        assert success is False
        assert "只能包含" in msg

    def test_add_alias_max_limit(self, tmp_path: Path) -> None:
        """别名数量达到上限后无法再添加."""
        skill_dir = tmp_path / "umu"
        for i in range(MAX_ALIASES):
            assert add_alias(skill_dir, f"别名{i}")[0] is True

        success, msg = add_alias(skill_dir, "超限别名")

        assert success is False
        assert "已达上限" in msg

    def test_remove_alias_success(self, tmp_path: Path) -> None:
        """成功删除别名."""
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = remove_alias(skill_dir, "敏学社")

        assert success is True
        assert list_aliases(skill_dir) == []

    def test_remove_alias_not_found(self, tmp_path: Path) -> None:
        """删除不存在的别名失败."""
        skill_dir = tmp_path / "umu"
        success, msg = remove_alias(skill_dir, "不存在")

        assert success is False
        assert "不存在" in msg

    def test_list_aliases_empty(self, tmp_path: Path) -> None:
        """未配置别名时返回空列表."""
        assert list_aliases(tmp_path / "umu") == []

    def test_save_aliases_preserves_other_config(self, tmp_path: Path) -> None:
        """保存别名时保留 config.json 中的其他字段."""
        skill_dir = tmp_path / "umu"
        _save_skill_config(skill_dir, {"custom_key": "custom_value"})

        _save_aliases(skill_dir, ["敏学社"])

        config = _load_skill_config(skill_dir)
        assert config["custom_key"] == "custom_value"
        assert config["aliases"] == ["敏学社"]


class TestRenderSkillMdWithAliases:
    _TEMPLATE = """---
name: umu
description: |
  <!-- BEGIN_DESCRIPTION -->
  PLACEHOLDER
  <!-- END_DESCRIPTION -->
---

## 触发条件

<!-- BEGIN_TRIGGER -->
PLACEHOLDER
<!-- END_TRIGGER -->

## 前置条件
"""

    def test_render_semantic_mode_with_aliases(self, tmp_path: Path) -> None:
        """开启语义触发并传入别名时，SKILL.md 应包含别名说明和触发规则."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=True, aliases=["敏学社", "优幕学堂"])

        content = skill_md.read_text(encoding="utf-8")
        assert "敏学社" in content
        assert "优幕学堂" in content
        assert "别名指代 UMU 平台" in content
        assert "<!-- ALIASES_PLACEHOLDER -->" not in content
        assert "<!-- ALIASES_TRIGGER_PLACEHOLDER -->" not in content

    def test_render_semantic_mode_without_aliases(self, tmp_path: Path) -> None:
        """开启语义触发但没有别名时，应清理占位符且不出现别名内容."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=True, aliases=[])

        content = skill_md.read_text(encoding="utf-8")
        assert "别名指代 UMU 平台" not in content
        assert "<!-- ALIASES_PLACEHOLDER -->" not in content
        assert "<!-- ALIASES_TRIGGER_PLACEHOLDER -->" not in content

    def test_render_explicit_mode_does_not_show_aliases(self, tmp_path: Path) -> None:
        """关闭语义触发时，即使传入别名也不渲染."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=False, aliases=["敏学社"])

        content = skill_md.read_text(encoding="utf-8")
        assert "敏学社" not in content
        assert "<!-- ALIASES_PLACEHOLDER -->" not in content
        assert "<!-- ALIASES_TRIGGER_PLACEHOLDER -->" not in content

    def test_render_loads_aliases_from_config(self, tmp_path: Path) -> None:
        """未传入 aliases 时，自动从 config.json 读取."""
        skill_dir = tmp_path / "umu"
        skill_dir.mkdir()
        _save_skill_config(skill_dir, {"aliases": ["敏学社"]})
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(self._TEMPLATE, encoding="utf-8")

        _render_skill_md(skill_dir, semantic_trigger_enabled=True)

        content = skill_md.read_text(encoding="utf-8")
        assert "敏学社" in content
        assert "别名指代 UMU 平台" in content


class TestConfigPreserveAliases:
    def test_config_preserves_aliases_and_custom_key(self, tmp_path: Path) -> None:
        """config.json 中的 aliases 和未知字段都应保留."""
        _save_skill_config(
            tmp_path,
            {
                "semantic_trigger_enabled": True,
                "aliases": ["敏学社", "优幕学堂"],
                "custom_key": "custom_value",
            },
        )

        config = _load_skill_config(tmp_path)
        assert config["semantic_trigger_enabled"] is True
        assert config["aliases"] == ["敏学社", "优幕学堂"]
        assert config["custom_key"] == "custom_value"
