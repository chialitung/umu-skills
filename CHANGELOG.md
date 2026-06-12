# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-06-12

### Fixed
- `python -m umu_sdk.skills.install` 现在可以从 PyPI 安装的包中正常运行
- install.py 优先使用项目目录下的 `.claude/skills/umu/`；不存在时自动回退到包内自带的 skill 资源
- 安装脚本不再依赖项目根目录的可编辑安装

### Changed
- wheel 通过 `force-include` 内嵌 `.claude/skills/umu/` 到 `umu_sdk/skills/bundled/umu/`

## [0.4.0] - 2026-06-12

### Added
- `/umu` Claude Skill (`.claude/skills/umu/SKILL.md`)：角色识别、交互式账号录入、工具自动编排
- 加密凭证管理器 (`src/umu_sdk/skills/credential_manager.py`)：Fernet 对称加密 + 系统 keyring 保护密钥
- 凭证加载器 (`src/umu_sdk/core/credential_loader.py`)：优先读取 `.env` / 环境变量，其次读取加密凭证文件
- 自动化安装入口：`python -m umu_sdk.skills.install`
- 统一技能编排 MCP Server：`umu-skills-orchestrator`
- `src/umu_sdk/skills/` 完整框架：
  - `config.py`：子 MCP 服务器配置与加载
  - `mcp_client.py`：stdio 子进程连接与工具调用抽象
  - `registry.py`：Skill 注册、发现、可用性校验
  - `decorators.py`：`@skill()` 装饰器与 `SkillContext`
  - `models.py`：Skill 元数据与执行结果 Pydantic 模型
  - `builtin/`：内置示例 Skill（create_course_with_scorm、enroll_course、get_course_progress、batch_onboard_users）
  - `server.py`：编排层 MCP Server 入口
- Teacher / Student / Admin MCP server 启动时自动读取加密凭证
- Skills 层与凭证管理单元测试（`tests/test_skills_*.py`、`tests/test_credential_manager.py`、`tests/test_skill_install.py`）

### Changed
- `README.md` 增加 `/umu` Skill 安装、账号配置、使用示例及编排层说明
- `pyproject.toml` 新增 `keyring` 依赖与 `umu-skills-orchestrator` console script
- `install.py` 安装完成提示不再引用明文 `.env`

### Security
- `SKILL.md` 明确禁止将账号密码保存到 `.env` 或任何明文位置
- 通过 `/umu` 录入的账号仅加密保存到 `~/.claude/skills/umu/credentials.enc`

## [0.3.2] - 2026-06-12

### Added
- Admin MCP server introduced in README with environment-variable examples and tool inventory
- Project-level minimal release rule documented in `CLAUDE.md`

### Changed
- README project roadmap updated: Admin MCP marked as completed

## [0.3.1] - 2026-06-12

### Added
- GitHub Actions release workflow for automatic PyPI publishing on tag push

## [0.3.0] - 2026-06-12

### Added
- Admin MCP server with account management tools (list, create, enable/disable, batch operations)
- Account list data dictionary (`docs/admin/account-data-dictionary.md`)
- Admin account Pydantic models (`src/umu_sdk/core/admin_models.py`)
- `.env` credential reloader for default auto-login across Admin/Teacher/Student MCP servers
- Console progress reporting for automatic pagination loops
- `first_login_time` and `last_login_time` fields in `adm_list_accounts` output
- Human-readable Beijing time string fields (`*_readable`) for all timestamp fields
- `role_type=5` mapping to "子管理员" (sub-admin)

### Changed
- `is_manager` parameter semantics: `0` returns all accounts, `1` returns management-view accounts only
- `adm_list_accounts` now uses typed models internally while preserving the original JSON response shape

### Fixed
- Corrected `role_type` descriptions in Admin MCP prompts and docstrings to include sub-admin

## [0.2.0] - 2026-06-07

### Added
- Project restructuring: `core/tools/adapters/skills` four-layer architecture
- MCP Server for both Student and Teacher roles
- Course builder with section CRUD (SCORM, Video, Document, Article, Infographic, Survey)
- Resource upload: SCORM (COS multipart), Video, Document, Image
- Batch operations for multi-user course completion
- Session management with multi-user isolation

### Changed
- Package name: `umu-sdk` -> `umu-skills` (PyPI)
- Import paths restructured under `core/` and `adapters/mcp/`
- CLI commands renamed: `umu-mcp-*` -> `umu-skills-*`

## [0.1.0] - 2026-05-XX

### Added
- Initial SDK release with HTTP client, auth, and encryption
- Student MCP server: course enrollment, learning progress, exam/questionnaire
- Teacher MCP server: course creation, resource management
- Basic course and section management
