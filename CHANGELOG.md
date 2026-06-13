# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.1] - 2026-06-13

### Changed
- 修复 `README.md` 中指向不存在的 `docs/README-MCP-SETUP.md` 的死链
- 更新 `CLAUDE.md` 以反映 Admin MCP 已实现的现状：补充 `admin.py` / `utils.py`、更新工具数量、增加 admin 启动命令与环境变量

### Removed
- 移除过时的 `mcp-config/` 客户端配置示例目录（现由 `python -m umu_sdk.skills.install` 自动配置），并在 `.gitignore` 中忽略

## [0.9.0] - 2026-06-13

### Added
- Admin MCP 新增学习项目清单查询原子工具 `adm_list_learning_programs`，支持按名称、创建人、权限、知识库状态、课程分类、创建时间筛选，并支持分页与 `fetch_all` 全量获取
- Skill 层新增 `list_learning_programs` 封装，与学习项目原子工具参数一一对应
- 优化 MCP/Skill 凭证管理：显式参数与环境变量优先于 `.env`，登录时记录凭证来源， lifespan 与登录工具返回企业/用户信息，避免静默使用错误账号
- 新增 Admin 数据字典：`docs/admin/program-data-dictionary.md`、`docs/admin/account-data-dictionary.md`、`docs/admin/learning-record-data-dictionary.md`

### Changed
- `.gitignore` 明确追加 `.mypy_cache/` 与 `.ruff_cache/`

### Security
- 将测试夹具中的 AIA 域名邮箱替换为 `example.com`，避免在仓库中保留企业相关邮箱示例

## [0.8.2] - 2026-06-13

### Added
- Admin MCP 新增账号编辑原子工具 `adm_update_account`，支持修改姓名、邮箱、用户名、手机号、工号、角色、平台权限、所属部门、所属分组及管理分组
- Skill 层新增 `update_account` 封装，统一返回旧值、新值与 warnings
- Admin MCP 新增 10 个分组管理原子工具：`adm_create_group`、`adm_update_group`、`adm_delete_groups`、`adm_get_group`、`adm_list_group_members`、`adm_list_group_managers`、`adm_add_group_members`、`adm_remove_group_members`、`adm_add_group_managers`、`adm_remove_group_managers`
- Skill 层新增 10 个分组管理 Skill：`create_group`、`update_group`、`delete_groups`、`get_group`、`list_group_members`、`list_group_managers`、`add_group_members`、`remove_group_members`、`add_group_managers`、`remove_group_managers`

### Fixed
- 修复 `adm_update_account` 调用 `add-user-check` 预检接口时误将返回对象当作布尔值判断的问题

## [0.8.1] - 2026-06-13

### Fixed
- 修正 `README.md` 中教师工具和学生工具列表与实际代码不一致的问题
  - 教师工具：补充资源管理（列表/重命名/删除）、环节管理、课程批量更新、课程分类等约 21 个遗漏工具；删除不存在的 `tch_upload_image`
  - 学生工具：数量从 23 修正为 24，补充 `stu_resolve_course_url`、`stu_get_questionnaire_questions`、`stu_submit_questionnaire_with_config`、`stu_check_in_with_rating`、`stu_submit_exam`、`stu_submit_exam_with_config`、`stu_get_lesson_status`、`stu_complete_course`
  - 重新调整分类，使工具归属更清晰

## [0.8.0] - 2026-06-13

### Added
- Admin MCP 新增完整的部门管理能力：部门树查询、部门详情、子部门、部门成员增删改查、部门创建/更新/排序/删除
- 新增 12 个 Admin 原子工具：`adm_get_department_tree`、`adm_get_department`、`adm_get_child_departments`、`adm_list_department_members`、`adm_search_department_members`、`adm_create_department`、`adm_update_department`、`adm_sort_departments`、`adm_add_department_members`、`adm_move_department_members`、`adm_remove_department_members`、`adm_delete_departments`
- Skill 层新增 12 个部门管理 Skill：`get_department_tree`、`get_department`、`get_child_departments`、`list_department_members`、`search_department_members`、`create_department`、`update_department`、`sort_departments`、`add_department_members`、`move_department_members`、`remove_department_members`、`delete_departments`
- `prompts.py` 新增 `admin_department_management_guide` 管理员部门管理操作指南
- 新增 `tests/test_admin_department_tools.py` 覆盖 Admin 部门管理原子工具

### Fixed
- 修复 `tests/test_session.py` 中会话 TTL 测试在 Windows 低精度计时器下偶发失败的问题

## [0.7.2] - 2026-06-12

### Fixed
- `umu_sdk.__version__` 改为通过 `importlib.metadata` 动态读取 `pyproject.toml` 版本，解决硬编码 `0.2.0` 与实际包版本不一致的问题

## [0.7.1] - 2026-06-12

### Fixed
- `README.md` 遗漏内置 Skill `list_courses`，现已补充到管理员 Skill 列表

## [0.7.0] - 2026-06-12

### Added
- Admin MCP 新增 `adm_list_courses` 原子工具，支持按多维度查询平台课程列表
- 新增 `list_courses` 高阶 Skill（`skills/builtin/admin_courses.py`），封装管理员课程列表查询
- `src/umu_sdk/core/admin_models.py` 新增 `AdminCourse` / `AdminCourseRaw` Pydantic 模型与字段标准化映射
- 新增管理员课程列表数据字典

### Changed
- `workbench/` 加入 `.gitignore`
- 更新 `README.md` 项目路线图阶段描述

## [0.6.0] - 2026-06-12

### Added
- Skill 层高频封装：Teacher 17 个、Student 13 个、Admin 8 个 Skill（资源上传、小节管理、学习流程、问卷考试、组织架构、账号管理、学习记录等）
- 新增 `skill_call_atomic_tool` 透传工具，作为未封装原子工具的兜底调用方式
- 新增 `tests/test_teacher_skills.py`、`tests/test_student_skills.py`、`tests/test_admin_skills.py`、`tests/test_skills_integration.py`

### Fixed
- 修复 `batch_onboard_users` 调用 `adm_create_account` 和 `stu_enroll_course` 时的参数不匹配问题
- 修复 `enroll_course` 调用 `stu_enroll_course` 时错误传递 `course_identifier` 的问题，改为接收 `enroll_id`

### Changed
- 更新 `tests/test_skills_registry.py` 验证所有新增 Skill 正确注册
- 更新 `README.md` 与 `CLAUDE.md` 补充 Skill 列表与透传工具说明

## [0.5.1] - 2026-06-12

### Changed
- 更新 `README.md`：补充 Admin MCP 学习记录与班级工具，调整管理员工具数量与功能描述

## [0.5.0] - 2026-06-12

### Added
- Admin MCP server 工具集扩展：账号管理、学习记录查询等核心管理员能力
- `src/umu_sdk/core/admin_models.py` 新增 Admin 领域 Pydantic 模型
- `src/umu_sdk/adapters/mcp/prompts.py` 扩展 Admin 角色 prompts
- `tests/test_admin_learning_records.py` 新增学习记录相关单元测试

## [0.4.3] - 2026-06-12

### Fixed
- 百度网盘同步偶尔导致 `.git/index` 写入失败，已重试恢复并继续发布流程

### Changed
- 优化 `install.py` 安装流程：新增 `--check` 状态检查、更简洁的输出、Windows UTF-8 输出修复
- 简化 `.claude/skills/umu/SKILL.md` 前置条件说明，加入 `--check` 排查指引
- README 安装章节增加 `--check` / `--upgrade` 示例，表述更简洁

## [0.4.2] - 2026-06-12

### Fixed
- `umu-skills-admin` 等 console scripts 因 user Scripts 目录不在 PATH 中而无法找到
- `install.py` 现在生成 `python -m umu_sdk.adapters.mcp.<role>` 的 MCP server 配置，不再依赖 console scripts
- 更新 `.claude/settings.json` 示例与 README，统一使用 `python -m` 方式启动 MCP server

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
