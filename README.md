# UMU Skills

[![CI](https://github.com/chialitung/umu-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/chialitung/umu-skills/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

UMU Skills 是一个 AI 技能框架，它将 UMU 学习平台的管理操作封装为可供 AI 助手调用的工具。它通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 与 Claude、Cursor、Cline 等 AI 客户端集成。

## 功能特性

- **三角色 MCP 服务器**：分别为教师（课程创建、资源管理）、学生（课程报名、学习进度）和管理员（账号管理、组织架构、课程审核、学习记录、数据查询）提供独立的工具集
- **高阶 Skill 编排**：将高频、跨角色流程封装为 `skill_run` 可调用的 Skill，并保留 `skill_call_atomic_tool` 透传兜底
- **课程构建器**：支持创建包含多种环节类型的课程 —— SCORM、视频、文档、文章、信息图、问卷、考试、签到
- **自动分页与全量获取**：列表工具统一支持 `fetch_all` 自动遍历分页，所有多页循环均向 stderr 输出进度，便于观察长时间拉取状态
- **资源上传**：SCORM（腾讯云 COS 分片上传）、视频、文档、图片，并支持进度追踪
- **批量操作**：多用户课程完成处理，支持并发控制
- **会话管理**：多用户会话隔离，支持并发操作
- **类型安全的 SDK**：Pydantic 模型、类型化异常、异步优先设计

## 架构

```
umu_skills/
├── core/              # SDK 核心 —— HTTP 客户端、认证、加密、模型
├── tools/             # 业务逻辑层（学生 / 教师 / 企业域）
│   ├── student/       # 学生端：报名、进度、考试/测验
│   ├── teacher/       # 教师端：课程创建、资源上传
│   └── admin/         # 管理端：账号管理、数据查询
├── adapters/          # AI 协议适配器
│   └── mcp/           # MCP 服务器（Claude / Cursor / Cline）
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
Windows: C:\Users\<用户名>\.claude\skills\umu\credentials.enc
macOS/Linux: ~/.claude/skills/umu/credentials.enc
```

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

如果你使用 Claude Desktop、VSCode Cline 等其他 MCP 客户端，可以手动启动单个角色的 MCP server。

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

### 管理员工具（50）

| 分类 | 工具 |
|----------|-------|
| 认证 | `adm_login`, `adm_check_auth` |
| 会话 | `adm_create_session`, `adm_list_sessions`, `adm_destroy_session` |
| 当前用户 | `adm_get_user_info` |
| 账号 | `adm_create_account`, `adm_list_accounts`, `adm_update_account` |
| 账号状态 | `adm_enable_account`, `adm_disable_account`, `adm_batch_enable_accounts`, `adm_batch_disable_accounts`, `adm_get_scheduled_disables` |
| 组织架构 | `adm_list_departments`, `adm_get_department_tree`, `adm_get_department`, `adm_get_child_departments`, `adm_list_department_members`, `adm_search_department_members`, `adm_create_department`, `adm_update_department`, `adm_sort_departments`, `adm_add_department_members`, `adm_move_department_members`, `adm_remove_department_members`, `adm_delete_departments`, `adm_list_groups` |
| 分组 | `adm_create_group`, `adm_update_group`, `adm_delete_groups`, `adm_get_group`, `adm_list_group_members`, `adm_list_group_managers`, `adm_add_group_members`, `adm_remove_group_members`, `adm_add_group_managers`, `adm_remove_group_managers` |
| 班级 | `adm_list_classes` |
| 课程/学习项目 | `adm_list_courses`, `adm_list_learning_programs` |
| 课程审核 | `adm_list_course_audit_records`, `adm_audit_course`, `adm_list_course_categories`, `adm_list_course_blacklist`, `adm_save_course_blacklist` |
| 学习记录 | `adm_list_learning_records` |
| 用户任务 | `adm_list_user_tasks` |
| 讲师 | `adm_list_instructors` |
| 授课记录 | `adm_list_teaching_records` |

### 教师工具（61）

| 分类 | 工具 |
|----------|-------|
| 认证 | `tch_login`, `tch_check_auth` |
| 会话 | `tch_create_session`, `tch_list_sessions`, `tch_destroy_session` |
| 课程 | `tch_create_course`, `tch_get_course`, `tch_get_course_detail`, `tch_update_course`, `tch_update_course_basic`, `tch_update_course_type`, `tch_update_course_category`, `tch_update_course_schedule`, `tch_update_course_images`, `tch_update_course_richtext`, `tch_submit_course_for_audit` |
| 课程列表 | `tch_list_created_courses`, `tch_list_cooperated_courses`, `tch_list_participated_courses` |
| 课程协同 | `tch_list_course_collaborators`, `tch_search_collaborator_accounts`, `tch_invite_course_collaborator`, `tch_update_collaborator_role`, `tch_remove_course_collaborator`, `tch_transfer_course_owner` |
| 课程分类 | `tch_get_categories` |
| 环节 | `tch_create_scorm_section`, `tch_create_video_section`, `tch_create_article_section`, `tch_create_infographic_section`, `tch_create_document_section`, `tch_create_survey_section`, `tch_create_exam_section`, `tch_create_signin_section` |
| 环节修改 | `tch_update_scorm_section`, `tch_update_video_section`, `tch_update_article_section`, `tch_update_infographic_section`, `tch_update_document_section`, `tch_update_survey_section`, `tch_update_exam_section`, `tch_update_signin_section` |
| 环节管理 | `tch_get_section`, `tch_list_sections`, `tch_delete_section`, `tch_toggle_section_visibility`, `tch_get_infographic_content` |
| 资源（SCORM） | `tch_upload_scorm`, `tch_list_resources`, `tch_rename_resource`, `tch_delete_resource` |
| 资源（文档） | `tch_upload_document`, `tch_list_documents`, `tch_rename_document`, `tch_delete_document`, `tch_upload_documents_batch`, `tch_delete_documents_batch` |
| 资源（音视频） | `tch_upload_audio_video`, `tch_list_audio_videos`, `tch_rename_audio_video`, `tch_delete_audio_video` |

### 学生工具（24）

| 分类 | 工具 |
|----------|-------|
| 认证 | `stu_login`, `stu_check_auth` |
| 会话 | `stu_create_session`, `stu_list_sessions`, `stu_destroy_session` |
| 课程 | `stu_get_my_courses`, `stu_list_participated_courses`, `stu_get_course_structure`, `stu_get_learning_progress`, `stu_resolve_course_url` |
| 学习 | `stu_enroll_course`, `stu_browse_lesson`, `stu_get_questionnaire_questions`, `stu_submit_questionnaire`, `stu_submit_questionnaire_with_config`, `stu_check_in`, `stu_check_in_with_rating`, `stu_start_exam`, `stu_submit_exam`, `stu_submit_exam_with_config`, `stu_get_lesson_status` |
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

内置 Skill 覆盖高频场景（共 75）：

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
| `submit_course_for_audit` | teacher | 将课程提交至企业知识库审核 |
| `manage_course_collaborators` | teacher | 管理课程协同者（列出/邀请/调整/删除/转让） |
| `enroll_course` | student | 学员报名课程 |
| `get_course_progress` | student | 查询学员课程进度 |
| `resolve_course_identifier` | student | 解析课程访问码/短域名/URL |
| `list_my_courses_student` | student | 列出学员的课程 |
| `complete_browse_lesson` | student | 完成浏览类小节 |
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
| `list_course_audit_records` | admin | 查询企业知识库课程审核记录 |
| `audit_course` | admin | 对课程执行通过/拒绝/撤销提交操作 |
| `list_course_categories` | admin | 查询企业课程分类列表 |
| `list_course_blacklist` | admin | 查询课程提交黑名单 |
| `manage_course_blacklist` | admin | 将用户加入或移出课程提交黑名单 |
| `list_learning_programs` | admin | 查询企业学习项目清单 |
| `disable_account` | admin | 禁用账号 |
| `enable_account` | admin | 启用账号 |
| `update_account` | admin | 编辑账号信息（姓名、邮箱、角色、分组、工号等） |
| `get_learning_records` | admin | 查询学习记录 |
| `get_user_tasks` | admin | 查询学习任务明细 |
| `get_instructors` | admin | 查询讲师列表 |
| `get_teaching_records` | admin | 查询授课记录 |

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
