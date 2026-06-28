# UMU Skills

[![CI](https://github.com/chialitung/umu-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/chialitung/umu-skills/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **⚠️ 免责声明**
>
> `umu-skills` 是一个**非官方、独立第三方**项目，与 UMU（优幕）及其关联公司不存在任何隶属、赞助或认可关系。
>
> 本项目通过分析 UMU 前端网页公开可见的接口行为实现，仅供学习、研究和自动化管理个人/企业自己拥有的 UMU 账号使用。
>
> 使用本工具可能违反 UMU 的服务条款，导致账号受限、功能变更或法律风险。**使用者需自行承担全部责任**。作者不对任何直接或间接后果负责。
>
> UMU 的接口随时可能变更，本项目不保证长期可用。

UMU Skills 是一个 AI 技能框架，它将 UMU 学习平台的管理操作封装为可供 AI 助手调用的工具。它通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 与 Claude Code、WorkBuddy、Kimi Code CLI、Cursor 等 AI 客户端集成。

## 功能特性

UMU Skills 把 UMU 平台上重复的教务管理、课程运营、学习执行操作交给 AI 完成，让你用自然语言就能驱动整个平台：

- **一句话搞定 UMU**：在 Claude Code 中输入 `/umu`，直接说“帮我创建新员工入职培训课程”“把张三调到销售部”“导出上月学习记录”，AI 自动完成后续步骤，无需记忆后台入口和菜单路径。

- **讲师：快速开课、上传资源、协同备课**
  - 创建图文、视频、文档、问卷、考试、签到、SCORM 等多种小节类型的课程
  - 批量上传 SCORM 课件、文档、音视频、图片到资源库
  - 邀请协同讲师、调整权限、转让课程负责人
  - 一键提交课程至企业知识库审核

- **学员：自动报名、完成学习、跟踪进度**
  - 通过访问码、短链接或完整 URL 定位课程并自动报名
  - 自动完成浏览、签到、评分签到、问卷、考试等小节
  - 批量完成整门课程，适用于测试、数据准备等场景
  - 随时查询课程结构、当前进度与小节完成状态

- **管理员：账号、组织架构、课程审核、数据一览**
  - 批量开户、启用/禁用账号、编辑姓名/邮箱/角色/分组/工号等信息
  - 维护部门、分组、班级，批量调整成员归属与管理员
  - 审核课程提交、管理课程提交黑名单
  - 查询学习记录、学习任务、授课记录、学习项目与企业课程清单

- **高频流程封装 + 原子工具兜底**：113+ 个内置 Skill 覆盖常见跨角色流程，AI 优先通过 `skill_run` 语义化调用；低频或新增能力可通过 `skill_call_atomic_tool` 直接透传 168+ 个原子工具。

- **安全的多身份会话隔离**：教师、学员、管理员三角色由独立 MCP server 承载，凭据加密存储并受系统 keyring 保护；每个身份支持多会话并发，24 小时 TTL 自动过期。

- **批量与全量自动化**：列表类工具支持 `fetch_all` 自动遍历分页，并在 stderr 实时输出进度；批量导入账号、批量完成课程等操作内置并发控制，避免手工逐条处理。

- **三种使用形态**：既可通过 `/umu` 对话式使用，也可作为 Python SDK 直接调用，还能以独立 MCP server 接入 Claude Desktop、WorkBuddy、Cursor 等客户端。
- **语义触发与平台别名（可选）**：默认仅通过显式 `/umu` 触发，避免日常对话误识别；开启语义触发后，可用自然语言描述 UMU 平台操作，并支持添加自定义别名（如“敏学社”）作为 UMU 的替代词。安装脚本提供 `--semantic-trigger` 与 `alias add/remove/list` 管理。

## 架构

```
umu_skills/
├── core/              # SDK 核心 —— HTTP 客户端、认证、加密、模型
├── tools/             # 业务逻辑层（学生 / 教师 / 企业域）
│   ├── student/       # 学生端：报名、进度、考试/测验
│   ├── teacher/       # 教师端：课程创建、资源上传
│   └── admin/         # 管理端：账号管理、数据查询
├── adapters/          # AI 协议适配器
│   └── mcp/           # MCP 服务器（Claude / WorkBuddy / Cursor）
└── skills/            # 技能编排层（声明式场景）
```

**设计原则**：业务逻辑（tools）与协议适配器（adapters）分离。新增一个 AI 平台只需添加新的适配器即可。

## 安装

```bash
pip install umu-skills
```

> 如果你正在修改本仓库源码，使用开发模式安装：
> ```bash
> pip install -e ".[dev,mcp]"
> ```

## 快速开始

### 3 分钟在 Claude Code 中使用 `/umu`（推荐）

这是最快捷的使用方式。你不需要记住任何工具名，只需用自然语言告诉 AI 你想做什么。

#### 1. 安装 Python 包

```bash
pip install umu-skills
```

#### 2. 一键配置 Claude Code

运行安装脚本，它会自动完成三件事：

- 确保 `umu-skills` 包已安装
- 把 `/umu` Skill 复制到 Claude Code 全局 skills 目录
- 在 `~/.claude/settings.json` 中配置好 `umu-teacher`、`umu-student`、`umu-admin` 三个 MCP server

```bash
python -m umu_sdk.skills.install
```

其他常用命令：

```bash
# 检查安装状态
python -m umu_sdk.skills.install --check

# 强制升级到最新版
python -m umu_sdk.skills.install --upgrade
```

#### 3. 重启 Claude Code

**必须重启**，新的 Skill 和 MCP server 配置才会生效。

#### 4. 配置账号

重启后，在对话中输入：

```text
/umu
```

如果某个角色还没有配置账号，Claude 会交互式询问用户名和密码。你可以配置以下角色：

- **Teacher（讲师）**：创建课程、上传资源、管理小节
- **Student（学员）**：报名课程、学习、查看进度
- **Admin（管理员）**：账号管理、组织架构、课程审核、学习记录、数据查询

只需配置你实际会用到的角色即可。

账号信息会加密保存到：

```text
Windows: C:\Users\<用户名>\.umu_skills\credentials.enc
macOS/Linux: ~/.umu_skills/credentials.enc
```

Skill 文件仍位于各 AI 工具默认目录（Claude Code 为 `~/.claude/skills/umu`，WorkBuddy 为 `<WorkBuddy 配置目录>/skills/umu`），与加密凭证目录分离。

加密方式：

- 凭证本身使用 **Fernet 对称加密**
- Fernet 密钥由操作系统 keyring 保护（Windows DPAPI / macOS Keychain / Linux Secret Service）
- keyring 不可用时自动回退到同目录的 `.fernet.key` 文件

保存账号后，**再次重启 Claude Code**，MCP server 才能读取凭证并开始执行 UMU 操作。

之后你可以随时通过 `/umu` 对话式管理账号：

```text
/umu 添加管理员账号
/umu 修改我的讲师账号
/umu 更新管理员密码
/umu 删除 student 的账号信息
```

修改后同样需要重启 Claude Code。

#### 5. 开始使用

配置完成后，直接用自然语言描述你的需求：

```text
/umu 帮我创建一个课程，名字叫《新员工入职培训》
/umu 获取平台上的用户清单
/umu 给学员张三报名课程 aet504
/umu 查询《销售技巧》课程的学习记录
/umu 上传 SCORM 课件 /path/to/course.zip 并创建一个新课程绑定它
```

你也可以通过显式角色入口直接指定执行账号，避免交互确认：

```text
/umut 列出我的课程
/umua 查看企业所有课程
/umus 报名 enroll_id=123
```

支持的别名：`/umua` = `/umuadmin`，`/umut` = `/umuteacher`，`/umus` = `/umustudent`。

## 在 Kimi Code CLI 中使用

Kimi Code CLI 安装后，可以通过 `/umu` 斜杠命令操作 UMU 平台。

### 安装

```bash
python -m umu_sdk.skills.kimi.install
```

或使用 PyPI 安装后的命令：

```bash
umu-skills-install-kimi
```

安装脚本会：

1. 检查并安装 `umu-skills[mcp]` 包。
2. 将 4 个 skill 复制到 `~/.kimi-code/skills/`。
3. 在 `~/.kimi-code/mcp.json` 注册 `umu-teacher`、`umu-student`、`umu-admin` 三个 MCP server。
4. 初始化加密凭证目录 `~/.umu_skills/`。

### 启用语义自动触发（可选）

默认只有输入 `/umu` 时才触发。如果你想让 Kimi 在识别到 UMU 操作意图时自动调用，可运行：

```bash
python -m umu_sdk.skills.kimi.install --semantic-trigger
```

关闭：

```bash
python -m umu_sdk.skills.kimi.install --no-semantic-trigger
```

### 重启 Kimi Code CLI

安装完成后**必须重启 Kimi Code CLI**，新 skill 和 MCP server 才会生效。

### 配置账号

首次触发 `/umu` 时，会引导你录入至少一个角色的账号和密码。账号信息加密保存在 `~/.umu_skills/credentials.enc`。

### 使用示例

```
/umu 帮我创建一个课程
/umu-teacher 上传 SCORM 课件
/umu-student 帮我报名课程 aet504
/umu-admin 查询最近的学习记录
```

### 故障排查

运行检查命令：

```bash
python -m umu_sdk.skills.kimi.install --check
```

如需强制更新：

```bash
python -m umu_sdk.skills.kimi.install --upgrade
```


### 在腾讯 WorkBuddy 中使用

如果你使用腾讯 [WorkBuddy](https://workbuddy.qq.com/) AI 桌面助手，可以通过一条命令完成集成：

#### 1. 安装 Python 包

```bash
pip install umu-skills
```

#### 2. 一键配置 WorkBuddy

```bash
python -m umu_sdk.skills.workbuddy.install
```

安装脚本会自动完成以下操作：

- 检测 WorkBuddy 配置目录（支持 Windows / macOS / Linux）
- 在 WorkBuddy 的 `mcp_servers.json` 中注册 `umu-skills` orchestrator
- 将 WorkBuddy 版 UMU skill 包复制到配置目录
- 初始化通用加密凭证目录（`~/.umu_skills/credentials.enc`，与 Claude Code 共用）

如果自动检测失败，手动指定 WorkBuddy 配置目录：

```bash
# Windows 示例
python -m umu_sdk.skills.workbuddy.install --workbuddy-dir "C:\Users\xxx\AppData\Roaming\WorkBuddy"

# macOS 示例
python -m umu_sdk.skills.workbuddy.install --workbuddy-dir ~/Library/Application\ Support/WorkBuddy

# Linux 示例
python -m umu_sdk.skills.workbuddy.install --workbuddy-dir ~/.config/WorkBuddy
```

其他常用命令：

```bash
# 检查安装状态
python -m umu_sdk.skills.workbuddy.install --check

# 强制升级到最新版
python -m umu_sdk.skills.workbuddy.install --upgrade
```

#### 3. 导入技能包并重启 WorkBuddy

安装完成后，在 WorkBuddy 中导入 `<WorkBuddy 配置目录>/skills/umu` 下的技能包（通常通过 技能市场 → 本地导入 或 设置 → Skills），然后重启 WorkBuddy。

#### 4. 配置账号

首次使用 UMU 操作时，WorkBuddy 会引导你配置账号。账号信息会加密保存到：

```text
Windows: C:\Users\<用户名>\.umu_skills\credentials.enc
macOS/Linux: ~/.umu_skills/credentials.enc
```

该文件与 Claude Code 共用，如果你已经在 Claude Code 中配置过账号，WorkBuddy 会直接复用，无需重复录入。

保存账号后，**重启 WorkBuddy**，MCP server 才能读取凭证并开始执行 UMU 操作。

#### 5. 开始使用

配置完成后，直接用自然语言描述你的需求：

```text
帮我在 UMU 上创建一个课程，名字叫《新员工入职培训》
给学员张三报名课程 aet504
查询《销售技巧》课程的学习记录
导出上个月销售部的学习完成情况
```

WorkBuddy 会通过 `umu-skills` orchestrator 自动调用合适的 Skill 完成操作。

### 进阶：作为 Python SDK 使用

如果你不想通过 AI 客户端，而是直接在自己的 Python 代码里调用 UMU 能力：

```python
from umu_sdk import UMUClient

client = UMUClient(base_url="https://www.umu.cn")
client.login("username", "password")

courses = client.courses.list()
for course in courses.data:
    print(f"{course.id}: {course.title}")
```

### 进阶：作为 MCP 服务器使用

如果你使用 Claude Desktop、WorkBuddy 等其他 MCP 客户端，可以手动启动单个角色的 MCP server。

设置环境变量并启动 Teacher MCP server：

```bash
export UMU_BASE_URL=https://www.umu.cn
export UMU_TEACHER_USERNAME=your_username
export UMU_TEACHER_PASSWORD=your_password

# 使用 python -m 启动，无需将 Scripts 目录加入 PATH
python -m umu_sdk.adapters.mcp.teacher
```

管理员端启动示例：

```bash
export UMU_ADMIN_USERNAME=your_admin_username
export UMU_ADMIN_PASSWORD=your_admin_password
python -m umu_sdk.adapters.mcp.admin
```

在 Claude Code / Claude Desktop 中手动配置的示例：

```json
{
  "mcpServers": {
    "umu-teacher": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "umu_sdk.adapters.mcp.teacher"]
    }
  }
}
```

更推荐直接使用前文的一键安装命令 `python -m umu_sdk.skills.install`，它会自动完成三个 server 的配置。

## 可用工具

### 管理员工具（53）

| 分类 | 工具 |
|----------|-------|
| 认证 | `adm_login`, `adm_check_auth` |
| 会话 | `adm_create_session`, `adm_list_sessions`, `adm_destroy_session` |
| 当前用户 | `adm_get_user_info` |
| 账号 | `adm_create_account`, `adm_list_accounts`, `adm_update_account` |
| 账号状态 | `adm_enable_account`, `adm_disable_account`, `adm_batch_enable_accounts`, `adm_batch_disable_accounts`, `adm_get_scheduled_disables` |
| 组织架构 | `adm_list_departments`, `adm_get_department_tree`, `adm_get_department`, `adm_get_child_departments`, `adm_list_department_members`, `adm_search_department_members`, `adm_create_department`, `adm_update_department`（可设置部门负责人）, `adm_sort_departments`, `adm_add_department_members`, `adm_move_department_members`, `adm_remove_department_members`, `adm_delete_departments`, `adm_list_groups` |
| 分组 | `adm_create_group`, `adm_update_group`, `adm_delete_groups`, `adm_get_group`, `adm_list_group_members`, `adm_list_group_managers`, `adm_add_group_members`, `adm_remove_group_members`, `adm_add_group_managers`, `adm_remove_group_managers` |
| 班级 | `adm_list_classes` |
| 课程/学习项目 | `adm_list_courses`, `adm_list_learning_programs`, `adm_list_personal_learning_programs` |
| 课程审核 | `adm_list_course_audit_records`, `adm_audit_course`, `adm_list_course_categories`, `adm_list_course_blacklist`, `adm_save_course_blacklist` |
| 学习记录 | `adm_list_learning_records`, `adm_export_learning_records` |
| 导出 | `adm_export_accounts` |
| 用户任务 | `adm_list_user_tasks` |
| 讲师 | `adm_list_instructors` |
| 授课记录 | `adm_list_teaching_records` |

### 教师工具（96）

| 分类 | 工具 |
|----------|-------|
| 认证 | `tch_login`, `tch_check_auth` |
| 会话 | `tch_create_session`, `tch_list_sessions`, `tch_destroy_session` |
| 课程 | `tch_create_course`, `tch_get_course`, `tch_get_course_detail`, `tch_update_course`, `tch_update_course_basic`, `tch_update_course_type`, `tch_update_course_category`, `tch_update_course_schedule`, `tch_update_course_images`, `tch_update_course_richtext`, `tch_submit_course_for_audit`, `tch_set_course_enrollment`, `tch_get_course_auto_close`, `tch_set_course_auto_close`, `tch_cancel_course_auto_close` |
| 课程列表 | `tch_list_created_courses`, `tch_list_cooperated_courses`, `tch_list_participated_courses`, `tch_list_learning_programs` |
| 学习项目 | `tch_create_learning_program`, `tch_get_learning_program`, `tch_update_learning_program`, `tch_update_learning_program_modules`, `tch_remove_courses_from_learning_program`, `tch_add_courses_to_learning_program`, `tch_configure_program_certificate`, `tch_set_program_points_status`, `tch_search_courses_for_program`, `tch_list_program_participants`, `tch_list_program_learning_tasks` |
| 课程学员 | `tch_list_course_learning_tasks`, `tch_list_course_participants`, `tch_list_course_learning_durations` |
| 课程协同 | `tch_list_course_collaborators`, `tch_search_collaborator_accounts`, `tch_invite_course_collaborator`, `tch_update_collaborator_role`, `tch_remove_course_collaborator`, `tch_transfer_course_owner` |
| 课程权限 | `tch_set_course_access_permission`, `tch_get_course_access_permission`, `tch_get_course_access_list`, `tch_search_access_accounts`, `tch_add_course_access_accounts`, `tch_remove_course_access_accounts`, `tch_cancel_all_assigned_permissions`, `tch_set_program_access_permission`, `tch_get_program_access_permission`, `tch_get_program_access_list`, `tch_search_program_access_accounts`, `tch_add_program_access_accounts`, `tch_remove_program_access_accounts`, `tch_cancel_all_program_permissions`, `tch_export_course_permissions`, `tch_export_program_permissions` |
| 课程分类 | `tch_get_categories` |
| 环节 | `tch_create_scorm_section`, `tch_create_video_section`, `tch_create_article_section`, `tch_create_infographic_section`, `tch_create_document_section`, `tch_create_survey_section`, `tch_create_exam_section`, `tch_create_signin_section` |
| 环节修改 | `tch_update_scorm_section`, `tch_update_video_section`, `tch_update_article_section`, `tch_update_infographic_section`, `tch_update_document_section`, `tch_update_survey_section`, `tch_update_exam_section`, `tch_update_signin_section` |
| 环节管理 | `tch_get_section`, `tch_list_sections`, `tch_delete_section`, `tch_toggle_section_visibility`, `tch_get_infographic_content` |
| 资源（SCORM） | `tch_upload_scorm`, `tch_list_resources`, `tch_rename_resource`, `tch_delete_resource` |
| 资源（文档） | `tch_upload_document`, `tch_list_documents`, `tch_rename_document`, `tch_delete_document`, `tch_upload_documents_batch`, `tch_delete_documents_batch` |
| 资源（音视频） | `tch_upload_audio_video`, `tch_list_audio_videos`, `tch_rename_audio_video`, `tch_delete_audio_video` |

### 学生工具（27）

| 分类 | 工具 |
|----------|-------|
| 认证 | `stu_login`, `stu_check_auth` |
| 会话 | `stu_create_session`, `stu_list_sessions`, `stu_destroy_session` |
| 课程 | `stu_get_my_courses`, `stu_list_participated_courses`, `stu_get_course_structure`, `stu_get_learning_progress`, `stu_resolve_course_url` |
| 学习 | `stu_enroll_course`, `stu_browse_lesson`, `stu_complete_scorm_section`, `stu_get_questionnaire_questions`, `stu_submit_questionnaire`, `stu_submit_questionnaire_with_config`, `stu_check_in`, `stu_check_in_with_rating`, `stu_start_exam`, `stu_submit_exam`, `stu_submit_exam_with_config`, `stu_get_lesson_status` |
| 报名 | `stu_get_enroll_form`, `stu_submit_enroll_form` |
| 完成课程 | `stu_complete_course`, `stu_batch_complete_course` |
| 批量 | `stu_batch_import_accounts` |

### 技能编排层（Skills Orchestrator）

当 Teacher / Student / Admin 三个 MCP 的原子工具数量增多后，直接让 AI 记住每个工具名和调用顺序会变得困难。
`umu-skills-orchestrator` 是一个统一入口，它将高频、跨角色的多步流程封装为更高阶的 **Skill**，同时为低频或新增原子工具保留受控的 **透传调用** 能力：

```bash
# 启动统一编排 MCP（自动连接 teacher/student/admin 三个子 MCP）
export UMU_BASE_URL=https://www.umu.cn
python -m umu_sdk.skills.server
```

暴露的核心工具：

| 工具 | 说明 |
|------|------|
| `skill_list` | 列出所有可用 Skill |
| `skill_describe` | 查看指定 Skill 的输入参数 |
| `skill_run` | 执行指定 Skill |
| `skill_call_atomic_tool` | 透传调用任意原子工具（兜底/探索场景） |

**`skill_call_atomic_tool` 使用示例：**

```json
{
  "server": "teacher",
  "tool": "tch_get_course_stats",
  "arguments": {"group_id": "123456"}
}
```

透传工具遵循以下约束：
- 仅允许 `teacher` / `student` / `admin` 三个目标服务器
- 返回与 Skill 统一的标准信封格式
- AI 应优先使用 `skill_run` 调用已封装 Skill，仅在工具未覆盖时使用透传

内置 Skill 覆盖高频场景（共 118），并支持通过 `/umu`、`/umua`、`/umut`、`/umus` 斜杠命令直接触发：

| Skill | 涉及子 MCP | 说明 |
|-------|-----------|------|
| `create_course_with_scorm` | teacher | 创建空课程并添加 SCORM 小节 |
| `upload_scorm_resource` | teacher | 上传 SCORM 资源 |
| `upload_document_resource` | teacher | 上传文档资源 |
| `upload_video_resource` | teacher | 上传视频资源 |
| `list_scorm_resources` | teacher | 列出 SCORM 资源 |
| `list_document_resources` | teacher | 列出文档资源 |
| `list_video_resources` | teacher | 列出视频资源 |
| `add_video_section` | teacher | 为课程添加视频小节 |
| `add_article_section` | teacher | 为课程添加文章小节 |
| `add_infographic_section` | teacher | 为课程添加图文小节 |
| `add_document_section` | teacher | 为课程添加文档小节 |
| `add_survey_section` | teacher | 为课程添加问卷小节 |
| `add_exam_section` | teacher | 为课程添加考试小节 |
| `add_signin_section` | teacher | 为课程添加签到小节 |
| `list_course_sections` | teacher | 列出课程小节 |
| `get_course_categories` | teacher | 获取课程分类 |
| `get_course_info` | teacher | 获取课程详情 |
| `list_my_courses` | teacher | 列出讲师创建的课程 |
| `list_course_learning_tasks` | teacher | 查询课程的学习任务分配学员清单 |
| `list_course_participants` | teacher | 查询课程学员参与者名单（含小节完成明细） |
| `list_course_learning_durations` | teacher | 查询课程学员学习时长名单（含小节时长明细） |
| `submit_course_for_audit` | teacher | 将课程提交至企业知识库审核 |
| `manage_course_collaborators` | teacher | 管理课程协同者（列出/邀请/调整/删除/转让） |
| `set_course_access_permission` | teacher | 设置课程访问权限（企业内公开/指定账户/班级/部门/分组/关闭） |
| `get_course_access_permission` | teacher | 获取课程当前访问权限设置 |
| `get_course_access_list` | teacher | 获取课程当前已授权的访问列表 |
| `search_course_access_accounts` | teacher | 搜索可授权访问课程的账户、班级、部门或分组 |
| `add_course_access_accounts` | teacher | 为课程设置指定账户、班级、部门或分组的访问权限 |
| `remove_course_access_accounts` | teacher | 移除课程的指定账户、班级、部门或分组访问权限 |
| `cancel_course_access_permissions` | teacher | 取消课程的所有指定访问权限 |
| `get_course_auto_close` | teacher | 获取课程定时自动关闭设置 |
| `set_course_auto_close` | teacher | 设置课程定时自动关闭时间 |
| `cancel_course_auto_close` | teacher | 取消课程定时自动关闭 |
| `list_teacher_learning_programs` | teacher | 列出讲师的学习项目（支持多种类型） |
| `list_owned_learning_programs` | teacher | 列出讲师负责的学习项目 |
| `list_cooperated_learning_programs` | teacher | 列出讲师协同的学习项目 |
| `list_enrolled_learning_programs` | teacher | 列出讲师报名的学习项目 |
| `create_learning_program` | teacher | 创建学习项目并添加课程，可选配置证书与积分 |
| `update_learning_program` | teacher | 修改学习项目基本信息、模块与课程关系，支持删除课程 |
| `list_program_participants` | teacher | 查询学习项目的学员名单（含 modules/courses 完成明细） |
| `list_program_learning_tasks` | teacher | 查询学习项目的学习任务学员名单（含 modules/courses 完成明细） |
| `set_program_access_permission` | teacher | 设置学习项目访问权限 |
| `get_program_access_permission` | teacher | 获取学习项目当前访问权限设置 |
| `get_program_access_list` | teacher | 获取学习项目当前已授权的访问列表 |
| `search_program_access_accounts` | teacher | 搜索可授权访问学习项目的账户、班级、部门或分组 |
| `add_program_access_accounts` | teacher | 为学习项目设置指定账户、班级、部门或分组的访问权限 |
| `remove_program_access_accounts` | teacher | 移除学习项目的指定账户、班级、部门或分组访问权限 |
| `cancel_program_access_permissions` | teacher | 取消学习项目的所有指定访问权限 |
| `enroll_course` | student | 学员报名课程，支持需要填写联系信息/单选/多选/开放题的特殊报名表单 |
| `get_course_enroll_form` | student | 获取课程复杂报名表单结构 |
| `submit_course_enroll_form` | student | 提交课程报名信息（联系信息 + 报名问题） |
| `learn_course` | student | 一站式学习课程：报名（如需）、完成可自动完成的小节 |
| `get_course_progress` | student | 查询学员课程进度 |
| `resolve_course_identifier` | student | 解析课程访问码/短域名/URL |
| `list_my_courses_student` | student | 列出学员的课程 |
| `complete_browse_lesson` | student | 完成浏览类小节 |
| `complete_scorm_section` | student | 完成 SCORM 1.2 小节 |
| `complete_checkin` | student | 完成签到 |
| `complete_rating_checkin` | student | 完成评分签到 |
| `check_lesson_completion` | student | 查询小节完成状态 |
| `get_questionnaire` | student | 获取问卷题目 |
| `submit_questionnaire` | student | 提交问卷（JSON） |
| `submit_questionnaire_simple` | student | 使用简化配置提交问卷 |
| `start_exam` | student | 开始考试 |
| `submit_exam` | student | 提交考试（JSON） |
| `submit_exam_simple` | student | 使用简化配置提交考试 |
| `complete_entire_course` | student | 自动完成整门课程 |
| `list_departments` | admin | 列出部门 |
| `get_department_tree` | admin | 获取完整部门树 |
| `get_department` | admin | 获取部门详情 |
| `get_child_departments` | admin | 获取子部门 |
| `list_department_members` | admin | 列出部门成员 |
| `search_department_members` | admin | 搜索可加入部门的成员 |
| `create_department` | admin | 创建部门 |
| `update_department` | admin | 更新部门信息 |
| `sort_departments` | admin | 调整部门排序 |
| `add_department_members` | admin | 添加成员到部门 |
| `move_department_members` | admin | 调整成员所属部门 |
| `remove_department_members` | admin | 从部门移除成员 |
| `delete_departments` | admin | 删除部门 |
| `list_groups` | admin | 列出分组 |
| `create_group` | admin | 创建分组 |
| `update_group` | admin | 更新分组信息 |
| `delete_groups` | admin | 删除分组 |
| `get_group` | admin | 获取分组详情 |
| `list_group_members` | admin | 列出分组成员 |
| `list_group_managers` | admin | 列出分组管理员 |
| `add_group_members` | admin | 添加成员到分组 |
| `remove_group_members` | admin | 从分组移除成员 |
| `add_group_managers` | admin | 添加分组管理员 |
| `remove_group_managers` | admin | 移除分组管理员 |
| `list_classes` | admin | 列出班级 |
| `list_accounts` | admin | 查询账号列表 |
| `list_courses` | admin | 查询企业课程清单 |
| `list_course_categories` | admin | 查询企业课程分类列表 |
| `list_course_audit_records` | admin | 查询企业知识库课程审核记录 |
| `audit_course` | admin | 对企业知识库课程执行通过、拒绝或撤销提交操作 |
| `list_course_blacklist` | admin | 查询课程提交黑名单 |
| `manage_course_blacklist` | admin | 将用户加入或移出课程提交黑名单 |
| `get_course_auto_close_admin` | admin | 获取课程定时自动关闭设置 |
| `set_course_auto_close_admin` | admin | 设置课程定时自动关闭时间 |
| `cancel_course_auto_close_admin` | admin | 取消课程定时自动关闭 |
| `list_learning_programs` | admin | 查询企业学习项目清单 |
| `list_admin_personal_learning_programs` | admin | 列出管理员个人的学习项目（支持多种类型） |
| `list_owned_learning_programs_admin` | admin | 列出管理员负责的学习项目 |
| `list_cooperated_learning_programs_admin` | admin | 列出管理员协同的学习项目 |
| `list_enrolled_learning_programs_admin` | admin | 列出管理员报名的学习项目 |
| `disable_account` | admin | 禁用账号 |
| `enable_account` | admin | 启用账号 |
| `update_account` | admin | 编辑账号信息（姓名、邮箱、角色、分组、工号等） |
| `get_learning_records` | admin | 查询学习记录 |
| `get_user_tasks` | admin | 查询学习任务明细 |
| `get_instructors` | admin | 查询讲师列表 |
| `get_teaching_records` | admin | 查询授课记录 |
| `umu` | orchestrator | 智能路由用户意图到最佳角色并执行统一 Skill（`/umu`） |
| `umu-admin` | orchestrator | 使用 admin 角色执行操作（`/umua` / `/umuadmin`） |
| `umu-teacher` | orchestrator | 使用 teacher 角色执行操作（`/umut` / `/umuteacher`） |
| `umu-student` | orchestrator | 使用 student 角色执行操作（`/umus` / `/umustudent`） |

自定义 Skill 示例（`src/umu_sdk/skills/builtin/`）：

```python
from umu_sdk.skills.decorators import skill, SkillContext

@skill(
    name="my_custom_flow",
    description="我的自定义流程",
    required_servers=["teacher"],
)
async def my_custom_flow(ctx: SkillContext, title: str) -> dict:
    result = await ctx.call_tool("teacher", "tch_create_course", {"title": title})
    return result
```

更多配置方式见 `src/umu_sdk/skills/config.py`。

## 开发

```bash
# 克隆仓库
git clone https://github.com/your-org/umu-skills.git
cd umu-skills

# 以可编辑模式安装
pip install -e ".[dev,mcp]"

# 运行测试
pytest tests/ -v

# 代码检查
ruff check src/

# 类型检查
mypy src/
```

## 项目阶段

| 阶段 | 功能 | 状态 |
|-------|---------|--------|
| 第一阶段 | 核心 SDK + 学生/教师 MCP | ✅ 已完成 |
| 第二阶段 | Admin MCP（账号管理、组织架构、学习记录、批量操作） | ✅ 已完成 |
| 第三阶段 | 技能编排层 | ✅ 已完成 |
| 第四阶段 | 完善 MCP 原子工具与能力边界 | 🚧 进行中 |

## 许可证

MIT 许可证 —— 详见 [LICENSE](LICENSE)。
