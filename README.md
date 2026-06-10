# UMU Skills

[![CI](https://github.com/your-org/umu-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/umu-skills/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/umu-skills)](https://pypi.org/project/umu-skills/)
[![Python](https://img.shields.io/pypi/pyversions/umu-skills)](https://pypi.org/project/umu-skills/)
[![License](https://img.shields.io/pypi/l/umu-skills)](LICENSE)

UMU Skills 是一个 AI 技能框架，它将 UMU 学习平台的管理操作封装为可供 AI 助手调用的工具。它通过 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 与 Claude、Cursor、Cline 等 AI 客户端集成。

## 功能特性

- **双角色 MCP 服务器**：分别为教师（课程创建、资源管理）和学生（课程报名、学习进度）提供独立的工具集
- **课程构建器**：支持创建包含多种环节类型的课程 —— SCORM、视频、文档、文章、信息图、问卷、考试、签到
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
│   └── teacher/       # 教师端：课程创建、资源上传
├── adapters/          # AI 协议适配器
│   └── mcp/           # MCP 服务器（Claude / Cursor / Cline）
└── skills/            # 技能编排层（声明式场景）
```

**设计原则**：业务逻辑（tools）与协议适配器（adapters）分离。新增一个 AI 平台只需添加新的适配器即可。

## 安装

```bash
# 基础安装（仅 SDK）
pip install umu-skills

# 带 MCP 服务器支持
pip install umu-skills[mcp]

# 开发环境安装
pip install umu-skills[dev]
```

## 快速开始

### 作为 Python SDK 使用

```python
from umu_sdk import UMUClient

client = UMUClient(base_url="https://www.umu.cn")
client.login("username", "password")

courses = client.courses.list()
for course in courses.data:
    print(f"{course.id}: {course.title}")
```

### 作为 MCP 服务器使用

```bash
# 设置环境变量
export UMU_BASE_URL=https://www.umu.cn
export UMU_TEACHER_USERNAME=your_username
export UMU_TEACHER_PASSWORD=your_password

# 启动 MCP 服务器
umu-skills-teacher
```

### 在 Claude Code 中配置

添加到你的 Claude Code MCP 配置：

```json
{
  "servers": {
    "umu-teacher": {
      "type": "stdio",
      "command": "umu-skills-teacher"
    }
  }
}
```

有关 Claude Desktop、VSCode Cline 等客户端的详细配置指南，请参见 [docs/README-MCP-SETUP.md](docs/README-MCP-SETUP.md)。

## 可用工具

### 教师工具（47）

| 分类 | 工具 |
|----------|-------|
| 认证 | `tch_login`, `tch_check_auth` |
| 会话 | `tch_create_session`, `tch_list_sessions`, `tch_destroy_session` |
| 课程 | `tch_create_course`, `tch_get_course`, `tch_get_course_detail`, `tch_update_course` |
| 课程列表 | `tch_list_created_courses`, `tch_list_cooperated_courses`, `tch_list_participated_courses` |
| 环节 | `tch_create_scorm_section`, `tch_create_video_section`, `tch_create_article_section`, `tch_create_infographic_section`, `tch_create_document_section`, `tch_create_survey_section`, `tch_create_exam_section`, `tch_create_signin_section` |
| 环节修改 | `tch_update_scorm_section`, `tch_update_video_section`, `tch_update_article_section`, `tch_update_infographic_section`, `tch_update_document_section`, `tch_update_survey_section`, `tch_update_exam_section`, `tch_update_signin_section` |
| 资源 | `tch_upload_scorm`, `tch_upload_document`, `tch_upload_audio_video`, `tch_upload_image` |
| 批量 | `tch_upload_documents_batch` |

### 学生工具（23）

| 分类 | 工具 |
|----------|-------|
| 认证 | `stu_login`, `stu_check_auth` |
| 会话 | `stu_create_session`, `stu_list_sessions`, `stu_destroy_session` |
| 课程 | `stu_get_my_courses`, `stu_list_participated_courses`, `stu_get_course_structure`, `stu_get_learning_progress` |
| 学习 | `stu_enroll_course`, `stu_browse_lesson`, `stu_submit_questionnaire`, `stu_check_in`, `stu_start_exam` |
| 批量 | `stu_batch_import_accounts`, `stu_batch_complete_course` |

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
| 第二阶段 | 企业域管理 MCP | 🚧 计划中 |
| 第三阶段 | 技能编排层 | 🚧 计划中 |
| 第四阶段 | OpenAPI 适配器（GPTs / Gemini） | 🚧 计划中 |

## 许可证

MIT 许可证 —— 详见 [LICENSE](LICENSE)。
