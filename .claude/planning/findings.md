# UMU Skills 研究与发现

## 现有能力分析

### MCP Server

- 启动命令：`umu-skills-teacher`、`umu-skills-student`、`umu-skills-admin`
- 当前账号来源：环境变量（`UMU_*_USERNAME`、`UMU_*_PASSWORD`）
- `load_env_credentials(role)` 支持从 `.env` 读取
- 项目根目录 `.env` 已被 `.gitignore` 排除

### Skill 系统

- Skill 目录：`.claude/skills/umu/`
- 核心文件：`SKILL.md`、`references/tools.md`、`evals/evals.json`
- 打包产物：`umu.skill`
- 触发方式：`/umu` 或 UMU 相关自然语言

### Claude Code 配置

- MCP server 配置在 `.claude/settings.json`
- Skill 可安装到全局 skills 目录或项目级 `.claude/skills/`

## 关键发现

1. **自动更新问题**：Claude Code 的 Skill 在启动时加载，修改后通常需要重启或刷新才能生效。
2. **凭证传递问题**：MCP server 作为独立子进程启动，环境变量在启动时确定，运行时难以由 Skill 动态注入。
3. **加密存储问题**：Fernet 跨平台、依赖轻，是合适的对称加密方案。
4. **交互录入问题**：Skill 可以通过对话收集信息，但保存后需要能被 MCP server 读取。
5. **核心安全难题**：MCP server 自己读取加密文件需要解密密钥，密钥本身不能明文存储，否则加密失去意义。

## 待验证假设

- [ ] Claude Code 是否会自动加载项目级 `.claude/skills/` 中的 skill
- [ ] `load_env_credentials` 是否会优先读取项目根目录 `.env`
- [ ] Fernet 加密是否满足跨平台需求
- [ ] MCP server 能否在运行时读取 skills 目录下的加密凭证文件
- [ ] 使用机器级密钥保护（DPAPI/keychain）是否能在无用户输入的情况下让 MCP server 自动解密
- [ ] `python -m umu_sdk.skills.install` 能否可靠地定位 Claude Code 的全局 skills 目录
