# MCP Setup Guide

This guide helps you configure UMU Skills MCP servers with popular AI clients.

## What is MCP?

MCP (Model Context Protocol) is an open protocol that enables AI assistants to interact with external tools and services. UMU Skills exposes UMU Learning Platform operations as MCP tools.

## Quick Start

### 1. Install UMU Skills

```bash
pip install umu-skills[mcp]
```

### 2. Configure Environment Variables

Create a `.env` file or set environment variables:

```bash
# UMU Platform URL
export UMU_BASE_URL=https://www.umu.cn

# Teacher credentials (for teacher tools)
export UMU_TEACHER_USERNAME=your_teacher_username
export UMU_TEACHER_PASSWORD=your_teacher_password

# Student credentials (for student tools)
export UMU_STUDENT_USERNAME=your_student_username
export UMU_STUDENT_PASSWORD=your_student_password
```

### 3. Choose Your AI Client

#### Claude Code

Copy `mcp-config/claude-code/mcp.json` to your Claude Code config directory:

```json
{
  "servers": {
    "umu-teacher": {
      "type": "stdio",
      "command": "umu-skills-teacher"
    },
    "umu-student": {
      "type": "stdio",
      "command": "umu-skills-student"
    }
  }
}
```

#### Claude Desktop

Copy `mcp-config/claude-desktop/config.json` to your Claude Desktop config directory:

**macOS**: `~/Library/Application Support/Claude/config.json`
**Windows**: `%APPDATA%/Claude/config.json`

```json
{
  "mcpServers": {
    "umu-teacher": {
      "command": "umu-skills-teacher",
      "env": {
        "UMU_BASE_URL": "https://www.umu.cn",
        "UMU_TEACHER_USERNAME": "your_username",
        "UMU_TEACHER_PASSWORD": "your_password"
      }
    }
  }
}
```

#### VSCode Cline

Copy `mcp-config/vscode-cline/mcp.json` to your VSCode Cline settings.

## Available Tools

### Teacher Tools

- **Authentication**: `tch_login`, `tch_check_auth`
- **Sessions**: `tch_create_session`, `tch_list_sessions`, `tch_destroy_session`
- **Courses**: `tch_create_course`, `tch_get_course`, `tch_update_course`, `tch_list_courses`
- **Sections**: `tch_create_scorm_section`, `tch_create_video_section`, `tch_create_document_section`, `tch_create_article_section`, `tch_create_infographic_section`, `tch_create_survey_section`
- **Resources**: `tch_upload_scorm`, `tch_upload_document`, `tch_upload_audio_video`, `tch_upload_image`
- **Batch**: `tch_upload_documents_batch`

### Student Tools

- **Authentication**: `stu_login`, `stu_check_auth`
- **Sessions**: `stu_create_session`, `stu_list_sessions`, `stu_destroy_session`
- **Learning**: `stu_get_my_courses`, `stu_get_course_structure`, `stu_get_learning_progress`
- **Actions**: `stu_enroll_course`, `stu_browse_lesson`, `stu_submit_questionnaire`, `stu_check_in`, `stu_start_exam`
- **Batch**: `stu_batch_import_accounts`, `stu_batch_complete_course`

## Troubleshooting

### Connection Issues

If the MCP server fails to start:

1. Verify Python installation: `python --version` (requires 3.10+)
2. Check package installation: `pip show umu-skills`
3. Test server manually: `umu-skills-teacher`
4. Check environment variables are set correctly

### Authentication Issues

If login fails:

1. Verify username/password are correct
2. Check UMU_BASE_URL matches your organization
3. Ensure network connectivity to UMU platform
4. Check if 2FA is enabled (not yet supported)

## Architecture

```
umu_skills/
├── core/          # SDK core: HTTP client, auth, encryption
├── tools/         # Business logic layer (reserved for Phase 2)
├── adapters/      # AI protocol adapters
│   └── mcp/       # MCP Server implementation
└── skills/        # Skill orchestration layer (reserved for Phase 3)
```

For more details, see the main [README.md](../README.md).
