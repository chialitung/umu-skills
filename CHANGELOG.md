# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
