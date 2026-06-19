# 学习项目访问/分享权限能力设计文档

- **日期**: 2026-06-19
- **主题**: 为 Teacher / Admin MCP 增加学习项目（Learning Program）访问/分享权限管理能力
- **方案**: 方案 B —— 提取通用 helper，课程/项目共用
- **状态**: 待评审

---

## 1. 背景与目标

UMU 平台的学习项目（Learning Program）与课程（Course）一样，支持三种访问/分享权限：

| 权限值 | 含义 |
|--------|------|
| 0 | 关闭：任何人均不可访问 |
| 2 | 企业内公开：企业内成员均可访问 |
| 3 | 指定账户：仅指定的账户/班级/部门/分组可见 |

抓包文件显示，学习项目的权限接口与课程权限接口高度同源，区别仅在于：

- 课程使用 `group_id` + `obj_type=group`。
- 学习项目使用 `program_id` + `obj_type=program`。
- 设置权限的端点从 `/api/group/setgrouppermission` 变为 `/api/program/setprogrampermission`。

仓库里已经实现了完整的**课程访问权限**工具链（Teacher / Admin MCP + Skill）。本次目标是在复用现有逻辑的前提下，为**学习项目**增加同等能力，并暴露为 Skill。

---

## 2. 设计原则

1. **复用优先**：把现有课程权限 helper 泛化为接受 `obj_id` + `obj_type`，课程和项目工具共用同一份逻辑。
2. **命名清晰**：项目权限工具名统一使用 `program` 替代 `course`，避免与现有课程工具混淆。
3. **列表视角**：Teacher / Admin 作为普通用户查看学习项目时，支持「我拥有的 / 协同给我的 / 我报名的」三个视角，与企业报表视角区分。
4. **Skill 层友好**：列表工具在底层使用 `scope` 参数，Skill 层同时提供三个快捷入口，便于 AI 直接按意图调用。

---

## 3. MCP 工具设计

### 3.1 Teacher MCP

#### 3.1.1 学习项目列表

**工具名**: `tch_list_learning_programs`

**参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scope` | `str` | 是 | `owned`（我拥有的）、`cooperated`（协同给我的）、`enrolled`（我报名的） |
| `keywords` | `str` | 否 | 按标题/访问码模糊搜索 |
| `page` | `int` | 否 | 页码，默认 1 |
| `page_size` | `int` | 否 | 每页数量，默认 20，最大 100 |
| `fetch_all` | `bool` | 否 | 是否自动拉取全部分页 |
| `session_id` | `str` | 否 | 可选会话 ID |

**返回值**:

```json
{
  "success": true,
  "data": {
    "programs": [
      {
        "program_id": "359923",
        "program_title": "测试版学习项目",
        "access_code": "byt303",
        "share_url": "https://m.umu.cn/program/1vDdf372",
        "share_pc_url": "https://www.umu.cn/program/1vDdf372/detail",
        "create_time": 1781839949,
        "creator_umu_id": "17580402",
        "creator_name": "友邦人寿保险有限公司",
        "group_num": 4,
        "module_num": 1
      }
    ],
    "pagination": { ... }
  }
}
```

**端点规划**:

| scope | 端点（按课程侧接口类推） | 备注 |
|-------|--------------------------|------|
| `owned` | `GET /api/program/getlist?owner=1&type=1&page=&size=` | 抓包已验证 |
| `cooperated` | 尝试 `GET /api/program/getcooperateprogramlist` 或 `/api/program/getlist?cooperate=1` | 实现时以实际 HAR 为准 |
| `enrolled` | 尝试 `GET /api/program/getmyparticipatedprogramlist` 或类似 | 实现时以实际 HAR 为准 |

#### 3.1.2 学习项目访问权限工具

| 工具名 | 作用 | UMU 端点 |
|--------|------|----------|
| `tch_get_program_access_permission` | 获取当前权限及可选值 | `GET /api/group/getAccessPermissionOption?obj_id={program_id}&obj_type=program` |
| `tch_set_program_access_permission` | 设置权限为 0/2/3 | `POST /api/program/setprogrampermission` |
| `tch_get_program_access_list` | 获取已授权对象列表 | `GET /api/manage/getcourseaccesslist?obj_id={program_id}&obj_type=program` |
| `tch_search_program_access_accounts` | 搜索可授权账户/班级/部门/分组 | `POST /api/manage/accessaccountmatchv2`（`program_id`） |
| `tch_add_program_access_accounts` | 添加指定对象 | `POST /api/manage/updateaccessuser`（`obj_type=program`, `type=1`） |
| `tch_remove_program_access_accounts` | 移除指定对象 | `POST /api/manage/updateaccessuser`（`obj_type=program`, `type=2`） |
| `tch_cancel_all_program_permissions` | 清空所有指定权限 | `POST /uapi/v1/access-permission/cancel-all-assigned-permission` |

---

### 3.2 Admin MCP

#### 3.2.1 企业报表视角（保留现有）

- `adm_list_learning_programs`：继续使用 `/ajax/enterprise/getReportProgramList`，看全企业学习项目。

#### 3.2.2 个人视角（新增）

- **`adm_list_personal_learning_programs`**：参数、行为、端点与 `tch_list_learning_programs` 完全一致，只是运行在 Admin 会话下。

> 设计意图：当用户说「我的学习项目 / 我被协同的学习项目 / 我报名的学习项目」时，Admin MCP 走这套个人视角接口；当用户说「企业学习项目 / 全公司学习项目」时，继续使用 `adm_list_learning_programs`。

#### 3.2.3 学习项目访问权限工具

与 Teacher 侧一一对应，前缀为 `adm_`：

- `adm_get_program_access_permission`
- `adm_set_program_access_permission`
- `adm_get_program_access_list`
- `adm_search_program_access_accounts`
- `adm_add_program_access_accounts`
- `adm_remove_program_access_accounts`
- `adm_cancel_all_program_permissions`

端点与 Teacher 侧相同。

---

## 4. 通用 helper 改造

现有 `teacher.py` / `admin.py` 中课程权限相关的内部函数将泛化。以 `teacher.py` 为例（`admin.py` 结构相同）：

### 4.1 改造后的 helper

```python
def _search_access_permission_account(
    client: UMUClient,
    obj_id: str,
    obj_type: str,      # "group" | "program"
    keyword: str,
) -> tuple[bool, list[dict[str, Any]], str]:
    ...

def _build_access_account_payload(
    account: dict[str, Any],
    action_type: int,   # 1=添加, 2=移除
) -> dict[str, Any]:
    ...

def _format_access_account(account: dict[str, Any]) -> dict[str, Any]:
    ...

def _set_obj_access_permission(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    endpoint: str,      # "/api/group/setgrouppermission" | "/api/program/setprogrampermission"
    access_permission: int,
    update_session_permission: bool = True,
) -> dict[str, Any]:
    ...

def _get_obj_access_permission(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
) -> dict[str, Any]:
    ...

def _get_obj_access_list(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    page: int,
    size: int,
) -> dict[str, Any]:
    ...

def _add_obj_access_accounts(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    accounts: list[dict[str, Any]],
    update_session_permission: bool = True,
) -> dict[str, Any]:
    ...

def _remove_obj_access_accounts(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
    accounts: list[dict[str, Any]],
    update_session_permission: bool = True,
) -> dict[str, Any]:
    ...

def _cancel_all_assigned_permissions(
    client: UMUClient,
    obj_id: str,
    obj_type: str,
) -> dict[str, Any]:
    ...
```

### 4.2 兼容性保证

- 现有 `tch_set_course_access_permission` 等课程工具继续存在，内部改为调用 `_set_obj_access_permission(..., obj_type="group", endpoint="/api/group/setgrouppermission")`。
- 新增项目工具调用 `_set_obj_access_permission(..., obj_type="program", endpoint="/api/program/setprogrampermission")`。
- `_search_access_permission_account` 对 `group` 保留现有参数（含 `group_id`、`is_sug`），对 `program` 使用 `program_id` 并去掉 `is_sug`（以抓包为准）。

---

## 5. Skill 层设计

### 5.1 新增 Skill 文件

- `src/umu_sdk/skills/builtin/teacher_learning_programs.py`
- `src/umu_sdk/skills/builtin/admin_learning_programs_personal.py`

> 说明：`admin_learning_programs.py` 已存在并包含企业报表视角的 `list_learning_programs`，因此个人视角放在新文件，避免单文件过大。

### 5.2 Teacher Skill

#### 底层列表 Skill

```python
@skill(
    name="list_teacher_learning_programs",
    description="查询讲师视角的学习项目清单，支持我拥有的/协同给我的/我报名的三个视角",
    required_servers=["teacher"],
)
async def list_teacher_learning_programs(
    ctx: SkillContext,
    scope: str,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    ...
```

#### 快捷入口 Skill

```python
@skill(name="list_owned_learning_programs", ...)
async def list_owned_learning_programs(ctx, keywords="", page=1, page_size=20, fetch_all=False): ...

@skill(name="list_cooperated_learning_programs", ...)
async def list_cooperated_learning_programs(ctx, ...): ...

@skill(name="list_enrolled_learning_programs", ...)
async def list_enrolled_learning_programs(ctx, ...): ...
```

#### 权限 Skill

与课程权限 Skill 一一对应，使用 `program` 替换 `course`：

- `set_program_access_permission`
- `get_program_access_permission`
- `get_program_access_list`
- `search_program_access_accounts`
- `add_program_access_accounts`
- `remove_program_access_accounts`
- `cancel_program_access_permissions`

### 5.3 Admin Skill

- 企业报表视角保留在 `admin_learning_programs.py` 的 `list_learning_programs`。
- 个人视角底层 Skill：`list_admin_personal_learning_programs(scope=...)`。
- 个人视角快捷入口：
  - `list_owned_learning_programs_admin`
  - `list_cooperated_learning_programs_admin`
  - `list_enrolled_learning_programs_admin`
- 权限 Skill：
  - `set_program_access_permission_admin`
  - `get_program_access_permission_admin`
  - `get_program_access_list_admin`
  - `search_program_access_accounts_admin`
  - `add_program_access_accounts_admin`
  - `remove_program_access_accounts_admin`
  - `cancel_program_access_permissions_admin`

### 5.4 `__init__.py` 导出

更新 `src/umu_sdk/skills/builtin/__init__.py`，导出所有新增 Skill。

---

## 6. 数据流示例

**场景**：把学习项目 `359923` 设置为「指定账户可见」，并添加 `zhangsan@umu_aia.com`。

1. AI 调用 Skill `set_program_access_permission(program_id="359923", access_permission=3)`。
2. Skill 调用 Teacher MCP 工具 `tch_set_program_access_permission`。
3. MCP 工具向 `POST /api/program/setprogrampermission` 发送 `program_id=359923&access_permission=3`。
4. AI 调用 Skill `search_program_access_accounts(program_id="359923", keyword="zhangsan@umu_aia.com")`。
5. Skill 调用 `tch_search_program_access_accounts`，MCP 向 `POST /api/manage/accessaccountmatchv2` 发送 `program_id=359923&accounts=zhangsan@umu_aia.com`。
6. AI 拿到返回的账户信息后，调用 Skill `add_program_access_accounts(program_id="359923", accounts=[...])`。
7. Skill 调用 `tch_add_program_access_accounts`，MCP 向 `POST /api/manage/updateaccessuser` 发送 `obj_id=359923&obj_type=program&accounts=[{type:1,...}]`。

---

## 7. 错误处理

完全复用现有错误信封：

```json
{
  "success": false,
  "data": null,
  "error_code": "SET_PROGRAM_ACCESS_PERMISSION_FAILED",
  "error_message": "...",
  "suggested_action": "...",
  "next_action": "retry"
}
```

新增错误码以 `PROGRAM` 为前缀，例如：

- `LIST_LEARNING_PROGRAMS_FAILED`
- `GET_PROGRAM_ACCESS_PERMISSION_FAILED`
- `SET_PROGRAM_ACCESS_PERMISSION_FAILED`
- `SEARCH_PROGRAM_ACCESS_ACCOUNTS_FAILED`
- `ADD_PROGRAM_ACCESS_ACCOUNTS_FAILED`
- `REMOVE_PROGRAM_ACCESS_ACCOUNTS_FAILED`
- `CANCEL_PROGRAM_ACCESS_PERMISSIONS_FAILED`

---

## 8. 测试与回归

### 8.1 必须运行的检查

```bash
pytest tests/ -v
ruff check src/
mypy src/
python -m build
```

### 8.2 回归重点

由于改造了课程权限的 helper，必须验证现有课程权限工具行为不变：

- `tch_set_course_access_permission`
- `tch_get_course_access_permission`
- `tch_get_course_access_list`
- `tch_search_access_accounts`
- `tch_add_course_access_accounts`
- `tch_remove_course_access_accounts`
- `tch_cancel_all_assigned_permissions`
- 以及 Admin 侧对应的 `adm_*` 课程权限工具。

### 8.3 新增测试建议

- 新增 program 权限工具的 mock 测试，覆盖：
  - 设置权限 0/2/3
  - 获取权限及选项
  - 获取授权列表
  - 搜索账户/班级/部门/分组
  - 添加/移除账户
  - 清空全部指定权限
- 新增 `tch_list_learning_programs` / `adm_list_personal_learning_programs` 的 mock 测试，覆盖三个 scope 的参数拼接。

### 8.4 README 更新

发布前需更新 `README.md` 中 Teacher / Admin 工具表格、Skill 表格及数量标题，并运行：

```bash
python .github/scripts/check_release_readiness.py
```

---

## 9. 待确认/待验证项

1. `cooperated` / `enrolled` 两个 scope 的实际 UMU 端点需要在实现阶段通过 HAR 或浏览器验证。当前按课程侧接口做合理类推。
2. `tch_search_program_access_accounts` 是否需要传 `is_sug=1` 以抓包为准；实现时会分别对 `group` 和 `program` 使用最小可用参数集。
3. 是否需要为学习项目增加「定时自动关闭」能力？抓包中只出现 `access_permission` 相关调用，本次不在范围内。

---

## 10. 附录：抓包端点映射

| 动作 | UMU 端点 | 请求示例 |
|------|----------|----------|
| 获取权限选项 | `GET /api/group/getAccessPermissionOption` | `obj_id=359923&obj_type=program` |
| 设置权限 | `POST /api/program/setprogrampermission` | `program_id=359923&access_permission=0/2/3` |
| 获取已授权列表 | `GET /api/manage/getcourseaccesslist` | `obj_id=359923&obj_type=program&page=1&size=20` |
| 搜索可授权对象 | `POST /api/manage/accessaccountmatchv2` | `search_source=access_permission&is_suggestion=1&program_id=359923&accounts=zhangsan@umu_aia.com` |
| 添加/移除对象 | `POST /api/manage/updateaccessuser` | `obj_id=359923&obj_type=program&update_session_permission=0/1&accounts=[...]` |
| 清空指定权限 | `POST /uapi/v1/access-permission/cancel-all-assigned-permission` | `obj_id=359923&obj_type=program` |
| 列出拥有的项目 | `GET /api/program/getlist` | `owner=1&page=1&size=10&type=1` |
