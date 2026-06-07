# UMU Skills

[![CI](https://github.com/your-org/umu-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/umu-skills/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/umu-skills)](https://pypi.org/project/umu-skills/)
[![Python](https://img.shields.io/pypi/pyversions/umu-skills)](https://pypi.org/project/umu-skills/)
[![License](https://img.shields.io/pypi/l/umu-skills)](LICENSE)

UMU Skills is an AI Skill Framework that exposes UMU Learning Platform management operations as callable tools for AI assistants. It integrates with Claude, Cursor, Cline, and other AI clients via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

## Features

- **Dual-Role MCP Servers**: Separate tools for Teachers (course creation, resource management) and Students (course enrollment, learning progress)
- **Course Builder**: Create courses with multiple section types — SCORM, Video, Document, Article, Infographic, Survey
- **Resource Upload**: SCORM (Tencent COS multipart), Video, Document, Image with progress tracking
- **Batch Operations**: Multi-user course completion with concurrency control
- **Session Management**: Multi-user session isolation for concurrent operations
- **Type-Safe SDK**: Pydantic models, typed exceptions, async-first design

## Architecture

```
umu_skills/
├── core/              # SDK Core — HTTP client, auth, encryption, models
├── tools/             # Business Logic Layer (Student / Teacher / Domain)
│   ├── student/       # Student-side: enrollment, progress, exam/quiz
│   └── teacher/       # Teacher-side: course creation, resource upload
├── adapters/          # AI Protocol Adapters
│   └── mcp/           # MCP Server (Claude / Cursor / Cline)
└── skills/            # Skill Orchestration Layer (declarative scenarios)
```

**Design Principle**: Business logic (tools) is separated from protocol adapters (adapters). Adding a new AI platform only requires adding a new adapter.

## Installation

```bash
# Basic install (SDK only)
pip install umu-skills

# With MCP server support
pip install umu-skills[mcp]

# Development install
pip install umu-skills[dev]
```

## Quick Start

### As a Python SDK

```python
from umu_sdk import UMUClient

client = UMUClient(base_url="https://www.umu.cn")
client.login("username", "password")

courses = client.courses.list()
for course in courses.data:
    print(f"{course.id}: {course.title}")
```

### As an MCP Server

```bash
# Set environment variables
export UMU_BASE_URL=https://www.umu.cn
export UMU_TEACHER_USERNAME=your_username
export UMU_TEACHER_PASSWORD=your_password

# Start MCP server
umu-skills-teacher
```

### Configure with Claude Code

Add to your Claude Code MCP configuration:

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

See [docs/README-MCP-SETUP.md](docs/README-MCP-SETUP.md) for detailed setup guides for Claude Desktop, VSCode Cline, and other clients.

## Available Tools

### Teacher Tools (40+)

| Category | Tools |
|----------|-------|
| Auth | `tch_login`, `tch_check_auth` |
| Session | `tch_create_session`, `tch_list_sessions`, `tch_destroy_session` |
| Course | `tch_create_course`, `tch_get_course`, `tch_update_course`, `tch_list_courses` |
| Section | `tch_create_scorm_section`, `tch_create_video_section`, `tch_create_document_section`, `tch_create_article_section`, `tch_create_infographic_section`, `tch_create_survey_section` |
| Resource | `tch_upload_scorm`, `tch_upload_document`, `tch_upload_audio_video`, `tch_upload_image` |
| Batch | `tch_upload_documents_batch` |

### Student Tools (20+)

| Category | Tools |
|----------|-------|
| Auth | `stu_login`, `stu_check_auth` |
| Session | `stu_create_session`, `stu_list_sessions`, `stu_destroy_session` |
| Learning | `stu_get_my_courses`, `stu_get_course_structure`, `stu_get_learning_progress` |
| Actions | `stu_enroll_course`, `stu_browse_lesson`, `stu_submit_questionnaire`, `stu_check_in`, `stu_start_exam` |
| Batch | `stu_batch_import_accounts`, `stu_batch_complete_course` |

## Development

```bash
# Clone repository
git clone https://github.com/your-org/umu-skills.git
cd umu-skills

# Install in editable mode
pip install -e ".[dev,mcp]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/

# Type check
mypy src/
```

## Project Phases

| Phase | Feature | Status |
|-------|---------|--------|
| Phase 1 | Core SDK + Student/Teacher MCP | ✅ Complete |
| Phase 2 | Domain Management MCP | 🚧 Planned |
| Phase 3 | Skill Orchestration Layer | 🚧 Planned |
| Phase 4 | OpenAPI Adapter (GPTs / Gemini) | 🚧 Planned |

## License

MIT License — see [LICENSE](LICENSE) for details.
