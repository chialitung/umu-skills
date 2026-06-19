# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.17.1] - 2026-06-19

### Fixed
- 修复 Release workflow (`.github/workflows/release.yml`) 在运行发布就绪检查前未安装项目依赖的问题，导致导入 `umu_sdk.adapters.mcp.*` 失败。
- 发布就绪检查脚本现在会自动将 `src/` 加入 `sys.path`，支持在未安装 editable 包的环境中运行。

## [0.17.0] - 2026-06-19

### Added
- 新增角色解析与智能路由框架：
  - `src/umu_sdk/skills/role_resolver.py`：根据用户意图、会话上下文、已配置角色与子 MCP 可用性选择最佳执行角色，支持 admin ⊇ teacher ⊇ student 的能力层级 fallback
  - `src/umu_sdk/skills/intent_capability_map.py`：基于关键词的意图分类（teacher/student/admin）
  - `SkillContext` 增加 `session_state`，`app_lifespan` 初始化并持久化 `last_role` / `remembered_role`
- 新增 `/umu` 斜杠命令智能路由 Skill（`src/umu_sdk/skills/slash/umu.py` + `_runner.py`）：
  - 自动识别创建课程、列出课程、报名、查看进度、企业课程、学习项目等意图
  - 多角色可用时交互式请求确认
  - 高权限账号可自动登录低权限子 MCP 完成跨角色操作
- 新增显式角色斜杠入口（`src/umu_sdk/skills/slash/umu_admin.py`、`umu_teacher.py`、`umu_student.py`）：
  - `/umua` / `/umuadmin`：默认使用 admin 角色
  - `/umut` / `/umuteacher`：默认使用 teacher 角色
  - `/umus` / `/umustudent`：默认使用 student 角色
- 新增测试：`tests/test_role_resolver.py`、`tests/test_intent_capability_map.py`、`tests/test_umu_slash.py`、`tests/test_slash_role_entries.py`

### Changed
- 消除 Admin/Teacher 重复建设的课程与学习项目访问权限能力：
  - Admin MCP server 删除 14 个与 Teacher 重复的原子工具（`adm_set_course_access_permission`、`adm_get_course_access_permission`、`adm_get_course_access_list`、`adm_search_access_accounts`、`adm_add_course_access_accounts`、`adm_remove_course_access_accounts`、`adm_cancel_all_assigned_permissions`、`adm_set_program_access_permission`、`adm_get_program_access_permission`、`adm_get_program_access_list`、`adm_search_program_access_accounts`、`adm_add_program_access_accounts`、`adm_remove_program_access_accounts`、`adm_cancel_all_program_permissions`）
  - 提取共享 helper `src/umu_sdk/adapters/mcp/shared_access_permissions.py` 与工厂 `src/umu_sdk/adapters/mcp/shared_session_tools.py`，供 Admin/Teacher/Student 复用
  - Skill 层合并重复 Skill：统一使用 `course_permissions.py` 与 `program_permissions.py` 中的 canonical Skill，删除 `admin_course_permissions.py` 与 `teacher_course_permissions.py`
  - Orchestrator 对已删除的 `adm_*` 原子工具保留向后兼容重定向，调用 `skill_call_atomic_tool(server="admin", tool="adm_*")` 时自动转发到 Teacher canonical 工具并附加 `deprecated` 提示
- `SkillRegistry` 新增 `load_skill_package()` 通用加载方法，用于加载 `slash/` 包
- 发布就绪检查脚本 `.github/scripts/check_release_readiness.py` 改用 FastMCP `list_tools()` 获取实际注册工具名，并同时扫描 `skills/builtin` 与 `skills/slash` 目录统计 Skill 数量
- `README.md` 同步 Skill 数量至 105，新增斜杠命令使用说明与别名对照

### Removed
- `src/umu_sdk/skills/builtin/admin_course_permissions.py`
- `src/umu_sdk/skills/builtin/teacher_course_permissions.py`

## [0.16.1] - 2026-06-18

### Changed
- `README.md` 组织架构工具表中补充说明 `adm_update_department` 支持设置部门负责人
- `/umu` Skill 文档新增"设置/调整部门负责人"工作流示例

## [0.16.0] - 2026-06-18

### Added
- 为多个不支持服务端搜索的 MCP 列表工具增加客户端模糊匹配能力：
  - Admin：`adm_list_departments`、`adm_list_classes`、`adm_list_groups` 新增 `fuzzy_name`、`top_k`、`similarity_threshold` 参数
  - Teacher：`tch_list_sections` 新增 `fuzzy_title`；`tch_get_categories` 新增 `fuzzy_name`，同时匹配 `name` 与 `path`
  - Student：`stu_get_my_courses`、`stu_list_participated_courses` 新增 `fuzzy_title`
- `adm_list_groups` 新增 `fetch_all` 全量获取能力，提供 `fuzzy_name` 时自动启用
- `src/umu_sdk/adapters/mcp/utils.py` 新增通用模糊匹配工具函数：`compute_similarity`、`fuzzy_filter_items`、`fuzzy_filter_items_multi_key`
- 新增 `tests/test_fuzzy_matching.py` 覆盖相似度计算与单/多字段过滤

### Changed
- 更新 `/umu` Skill 文档（`SKILL.md`）与工具参考（`references/tools.md`），增加模糊匹配使用原则与参数说明
- `README.md` 工具列表与数量标题已同步检查

## [0.15.0] - 2026-06-16

### Added
- `/umu` Skill 新增语义自动触发开关：默认关闭，仅响应显式 `/umu` 命令；开启后基于“需要在 UMU 在线学习平台上完成具体操作”的完整意图自动触发
- 语义触发模式下支持用户自定义平台别名，例如“敏学社”可作为 UMU 的替代词触发 Skill
- `python -m umu_sdk.skills.install` 新增 `alias add/remove/list` 子命令，用于管理平台别名

### Changed
- 优化 `/umu` Skill 的 `description` 与 `trigger` 规则，移除通用教育关键词触发，仅保留 `UMU` 及平台别名作为触发锚点
- `README.md` 更新安装与使用说明，反映语义触发开关与别名管理功能

## [0.14.0] - 2026-06-15

### Added
- Teacher MCP 新增课程协同管理能力：
  - `tch_list_course_collaborators`：列出课程协同者与创建者
  - `tch_search_collaborator_accounts`：按关键词搜索可协同账号
  - `tch_invite_course_collaborator`：邀请账号成为协同者（编辑者/运营者/查看者）
  - `tch_update_collaborator_role`：调整已有协同者的权限类型
  - `tch_remove_course_collaborator`：删除协同者权限
  - `tch_transfer_course_owner`：将课程拥有权转让给指定账号
- Skill 层新增 `manage_course_collaborators` 高阶 Skill，统一封装协同者列表、邀请、权限调整、删除、转让拥有者操作

### Changed
- `README.md` 更新 Teacher MCP 工具列表与内置 Skill 列表，补充课程协同相关条目及数量（教师工具 61 个，内置 Skill 75 个）

## [0.13.0] - 2026-06-14

### Added
- `core/rate_limiter.py` 新增 `RateLimiter`，基于最小调用间隔限制 UMU 接口请求频率
- `UMUClient` 新增 `min_request_interval` 参数（默认 0.5 秒），每次 HTTP 请求前自动等待以满足间隔要求
- `AuthManager.login` 同样纳入频率限制，确保登录接口也不会高频调用
- 新增 `tests/test_rate_limiter.py` 覆盖频率限制器、客户端集成、登录集成的单元测试

## [0.12.0] - 2026-06-14

### Added
- 为所有自动分页 / 全量获取循环增加 stderr 进度打印，统一格式包含总条数、当前页、已获取条数、百分比、完成提示及 50 页安全上限警告
- Teacher MCP 6 个列表工具新增 `fetch_all` 自动全量获取：
  - `tch_list_resources`、`tch_list_documents`、`tch_list_audio_videos`
  - `tch_list_created_courses`、`tch_list_cooperated_courses`、`tch_list_participated_courses`
- Student MCP 2 个列表工具新增 `fetch_all` 自动全量获取：
  - `stu_get_my_courses`、`stu_list_participated_courses`
- `endpoints/courses.py` 的 `CourseEndpoint.iterate_all` 增加分页进度打印
- Skill 层对应列表 Skill 透传 `fetch_all` 参数：
  - `list_scorm_resources`、`list_document_resources`、`list_video_resources`
  - `list_my_courses`（teacher）
  - `list_my_courses_student`

### Changed
- `adapters/mcp/utils.py` 新增共享辅助函数 `report_pagination_progress`，统一所有分页进度输出

## [0.11.0] - 2026-06-14

### Added
- Teacher MCP 新增提交课程至企业知识库审核能力：
  - `tch_submit_course_for_audit`：将指定课程提交给管理员审核，审核通过后课程可被推荐并支持搜索
- Skill 层新增 `submit_course_for_audit` 封装，对应 Teacher 原子工具

### Changed
- `README.md` 更新 Teacher MCP 工具列表与 Skill 列表，补充提交审核相关条目及数量

## [0.10.0] - 2026-06-14

### Added
- Admin MCP 新增企业知识库课程审核能力：
  - `adm_list_course_audit_records`：查询待审核/已通过/已拒绝课程列表，支持课程名称/访问码模糊搜索、拥有者姓名/邮箱/手机号/用户名关键词解析、课程分类筛选、提交时间排序、过滤上次审核通过课程
  - `adm_audit_course`：对课程执行通过、拒绝或撤销提交操作，拒绝时可选将提交人加入黑名单
  - `adm_list_course_categories`：查询企业课程分类列表
  - `adm_list_course_blacklist`：查询课程提交黑名单
  - `adm_save_course_blacklist`：将用户加入或移出课程提交黑名单
- Skill 层新增 5 个课程审核相关 Skill：`list_course_audit_records`、`audit_course`、`list_course_categories`、`list_course_blacklist`、`manage_course_blacklist`
- 新增 Admin 课程审核数据模型：`AdminCourseAuditRecord`、`AdminCourseCategory`、`AdminCourseBlacklistEntry`

### Changed
- `README.md` 更新 Admin MCP 工具列表与 Skill 列表，补充课程审核、分类、黑名单相关条目

## [0.9.2] - 2026-06-13

### Changed
- 将 `CLAUDE.md` 加入 `.gitignore`，使其不再纳入版本控制；本地文件保留，新克隆仓库不再包含

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
