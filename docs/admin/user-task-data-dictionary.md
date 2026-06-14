# Admin 学习任务明细数据字典

> 对应接口：`GET /uapi/v1/dashboard/user-task-list`
>
> 用途：管理员查询企业学员被分配的学习任务明细，支持多条件组合筛选与分页。

---

## 1. MCP 工具请求参数

`adm_list_user_tasks` 工具支持以下参数。所有筛选条件为**交集**关系。

| 参数名 | 类型 | 必填 | 示例 | 说明 |
|--------|------|------|------|------|
| `task_types` | string | 否 | `1,2` | 任务类型：`1`=小节，`2`=课程，`3`=学习项目；多值逗号分隔 |
| `learn_status` | string | 否 | `0,2,3` | 完成状态：`0`=待学习，`1`=学习中，`2`=按时完成，`3`=逾期完成；多值逗号分隔 |
| `due_status` | string | 否 | `1,2` | 到期状态：`0`=已到期，`1`=未到期，`2`=未指定到期时间；多值逗号分隔 |
| `department_ids` | string | 否 | `251103,251104` | 部门 ID 列表，多个逗号分隔 |
| `department_names` | string | 否 | `销售部,华东区` | 部门名称关键词，多个逗号分隔；工具内部自动解析为 ID |
| `group_ids` | string | 否 | `177124,177125` | 分组 ID 列表，多个逗号分隔 |
| `group_names` | string | 否 | `新员工组` | 分组名称关键词，多个逗号分隔；工具内部自动解析为 ID |
| `class_ids` | string | 否 | `442992,442993` | 班级 ID 列表，多个逗号分隔 |
| `class_names` | string | 否 | `2026 届春招班` | 班级名称关键词，多个逗号分隔；工具内部自动解析为 ID |
| `assigner_umu_ids` | string | 否 | `12944154` | 分配者 umu_id 列表，多个逗号分隔 |
| `assigner_keywords` | string | 否 | `Admin` | 分配者姓名/邮箱/用户名关键词；工具内部自动解析为 umu_id |
| `student_umu_ids` | string | 否 | `20453567` | 学员 umu_id 列表，多个逗号分隔 |
| `student_keywords` | string | 否 | `Mingna` | 学员姓名/邮箱/用户名关键词；工具内部自动解析为 umu_id |
| `task_name` | string | 否 | `Onboarding` | 学习任务名称模糊搜索关键词 |
| `course_keywords` | string | 否 | `数据分析` | 课程名称/描述/标签模糊搜索关键词 |
| `assign_start_day` | string | 否 | `2026-03-16` | 分配时间起始日期，格式 `YYYY-MM-DD` |
| `assign_end_day` | string | 否 | `2026-06-14` | 分配时间结束日期，格式 `YYYY-MM-DD` |
| `due_start_day` | string | 否 | `2026-06-01` | 到期时间起始日期，格式 `YYYY-MM-DD` |
| `due_end_day` | string | 否 | `2026-12-31` | 到期时间结束日期，格式 `YYYY-MM-DD` |
| `page` | int | 否 | `1` | 页码，从 1 开始；`fetch_all=True` 时忽略 |
| `page_size` | int | 否 | `500` | 每页数量（1-1000），默认 500 |
| `fetch_all` | bool | 否 | `true` | 是否自动获取全量数据并合并所有分页结果 |
| `session_id` | string | 否 | `sess_xxx` | 可选会话 ID |

### 参数组合说明

- 未提供 `assign_start_day` 和 `assign_end_day` 时，**默认查询最近 90 天**（按东八区计算）。
- 未提供 `due_start_day` 和 `due_end_day` 时，**不限制到期时间**。
- `department_ids` 与 `department_names`、`group_ids` 与 `group_names`、`class_ids` 与 `class_names`、`assigner_umu_ids` 与 `assigner_keywords`、`student_umu_ids` 与 `student_keywords` 可同时提供，工具内部会**合并并去重**。
- `task_types`、`learn_status`、`due_status` 不提供时，服务端不做对应维度的筛选。
- `course_keywords` 同时匹配课程名称、描述和标签。
- `fetch_all=True` 时，工具先以 `size=500` 请求，单页失败会自动降级到 `size=100` 重试一次；仍然失败则返回错误。

---

## 2. 后端 `search_condition` 映射

工具内部将筛选条件构造成 JSON 字符串，作为 URL 参数 `search_condition` 发送：

| 业务概念 | `search_condition` 字段 | 取值说明 |
|----------|------------------------|----------|
| 任务类型 | `obj_type` | `"1"`=小节，`"2"`=课程，`"3"`=学习项目；多值逗号分隔 |
| 完成状态 | `learn_status` | `"0"`=待学习，`"1"`=学习中，`"2"`=按时完成，`"3"`=逾期完成；多值逗号分隔 |
| 到期状态 | `due_status` | `"0"`=已到期，`"1"`=未到期，`"2"`=未指定到期时间；多值逗号分隔 |
| 部门 | `department_ids` | 部门 ID，多个逗号分隔 |
| 分组 | `enterprise_group_ids` | 分组 ID，多个逗号分隔 |
| 班级 | `class_ids` | 班级 ID，多个逗号分隔 |
| 分配者 | `from_umu_ids` | 分配者 umu_id，多个逗号分隔 |
| 学员 | `assign_umu_ids` | 学员 umu_id，多个逗号分隔 |
| 学习任务名称 | `task_name` | 模糊搜索 |
| 课程名称/描述/标签 | `keywords` | 模糊搜索 |
| 分配时间范围 | `assign_start_ts` / `assign_stop_ts` | Unix 时间戳（秒） |
| 到期时间范围 | `due_start_ts` / `due_stop_ts` | Unix 时间戳（秒） |

---

## 3. 响应结构

```json
{
  "error_code": 0,
  "error_message": "",
  "data": {
    "page_info": {
      "list_total_num": 21288,
      "total_page_num": 2129,
      "current_page": 1,
      "size": 10
    },
    "list": [
      { /* UserTaskRaw 对象，详见下方字段 */ }
    ]
  }
}
```

### 3.1 分页信息

| 字段 | 类型 | 示例 | 说明 |
|------|------|------|------|
| `list_total_num` | int | `21288` | 符合条件的任务总数 |
| `total_page_num` | int | `2129` | 总页数 |
| `current_page` | int | `1` | 当前页码 |
| `size` | int | `10` | 当前页大小 |

---

## 4. 任务明细对象字段（原始接口）

| 字段名 | 类型 | 示例 | 说明 |
|--------|------|------|------|
| `learning_time` | string | `00:52:25` | 学习时长文本 |
| `vlt` | string | `00:52:25` | 视频学习时长文本 |
| `first_learning_time` | string | `1781245357` | 首次学习时间，Unix 时间戳（秒） |
| `last_learning_time` | string | `1781248479` | 最后学习时间，Unix 时间戳（秒） |
| `learn_status` | int | `2` | 学习状态码 |
| `finish_time` | int | `1781248479` | 完成时间，Unix 时间戳（秒） |
| `assign_time` | int | `1781125804` | 分配时间，Unix 时间戳（秒） |
| `due_time` | int | `1788901804` | 到期时间，Unix 时间戳（秒），`0` 表示未指定 |
| `student` | object | `{...}` | 学员信息对象 |
| `operator` | object | `{...}` | 分配者信息对象 |
| `task_obj` | object | `{...}` | 任务对象信息 |
| `assign_obj` | object | `{...}` | 分配对象信息 |
| `task_obj_id` | string | `9532` | 任务对象关系 ID |

### 4.1 `student` 对象字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `umu_id` | string | 学员 UMU 用户 ID |
| `user_name` | string | 学员姓名 |
| `home_url` | string(URL) | 学员主页链接 |
| `enterprise_groups` | array[string] | 学员所属企业分组 |

### 4.2 `operator` 对象字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `umu_id` | string | 分配者 UMU 用户 ID |
| `user_name` | string | 分配者姓名 |
| `home_url` | string(URL) | 分配者主页链接 |
| `enterprise_groups` | array[string] | 分配者所属企业分组 |
| `on_job_status` | int | 在职状态 |
| `is_signout_free` | int | 是否免登录 |
| `is_manager` | int | 是否管理员 |

### 4.3 `task_obj` 对象字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `obj_id` | string | 任务对象 ID |
| `task_name` | string | 任务名称 |
| `obj_type` | int | 任务类型：`1`=小节，`2`=课程，`3`=学习项目 |
| `obj_type_name` | string | 任务类型名称（原始字段，可能为空） |
| `session_type` | string | 小节类型 |
| `course_name` | string | 课程名称 |
| `course_id` | string | 课程 ID |
| `task_url` | string(URL) | 任务链接 |
| `share_url` | string(URL) | 分享链接 |

### 4.4 `assign_obj` 对象字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | string | 分配对象 ID |
| `type` | string | 分配对象类型 |
| `name` | string | 分配对象名称 |
| `is_manager` | int | 是否管理员分配 |

---

## 5. `UserTask` 标准化对象字段

`adm_list_user_tasks` 工具会对原始任务对象做标准化处理，返回以下字段：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `task_obj_id` | string | 任务对象关系 ID |
| `learning_time` | string | 学习时长文本 |
| `vlt` | string | 视频学习时长文本 |
| `first_learning_time` | int | 首次学习时间，Unix 时间戳（秒） |
| `first_learning_time_readable` | string | 首次学习时间，北京时间 `YYYY-MM-DD HH:MM:SS` |
| `last_learning_time` | int | 最后学习时间，Unix 时间戳（秒） |
| `last_learning_time_readable` | string | 最后学习时间，北京时间 |
| `learn_status` | int | 学习状态码 |
| `learn_status_text` | string | 学习状态人读文本：`待学习` / `学习中` / `按时完成` / `逾期完成` |
| `finish_time` | int | 完成时间，Unix 时间戳（秒） |
| `finish_time_readable` | string | 完成时间，北京时间 |
| `assign_time` | int | 分配时间，Unix 时间戳（秒） |
| `assign_time_readable` | string | 分配时间，北京时间 |
| `due_time` | int | 到期时间，Unix 时间戳（秒），`0` 表示未指定 |
| `due_time_readable` | string | 到期时间，北京时间；未指定时为空字符串 |
| `is_overdue` | bool | 是否逾期完成：`learn_status=3` 为 True；`learn_status=2` 且 `finish_time > due_time > 0` 也为 True |
| `student_umu_id` | string | 学员 umu_id |
| `student_name` | string | 学员姓名 |
| `student_home_url` | string | 学员主页链接 |
| `student_groups` | array[string] | 学员所属分组 |
| `operator_umu_id` | string | 分配者 umu_id |
| `operator_name` | string | 分配者姓名 |
| `operator_groups` | array[string] | 分配者所属分组 |
| `obj_id` | string | 任务对象 ID |
| `task_name` | string | 任务名称 |
| `obj_type` | int | 任务类型码：`1`=小节，`2`=课程，`3`=学习项目 |
| `obj_type_text` | string | 任务类型人读文本 |
| `session_type` | string | 小节类型 |
| `course_name` | string | 课程名称 |
| `course_id` | string | 课程 ID |
| `task_url` | string | 任务链接 |
| `share_url` | string | 分享链接 |
| `assign_obj_id` | string | 分配对象 ID |
| `assign_obj_type` | string | 分配对象类型 |
| `assign_obj_name` | string | 分配对象名称 |

---

## 6. 名称解析说明

### 6.1 部门名称解析

当传入 `department_names` 时，工具内部调用：

```
GET /uapi/v1/department/get-departments-by-managerid?t={ms}&type=2
```

递归遍历 `data.department_list`（含子部门），按名称子串匹配，返回匹配的 `department_id` 列表。

### 6.2 分组名称解析

当传入 `group_names` 时，工具内部调用：

```
GET /ajax/enterprise/getGroupList?t={ms}&page={page}&size=100
```

全量拉取企业分组列表，按 `group_name` 子串匹配，返回匹配的 `id` 列表。

### 6.3 班级名称解析

当传入 `class_names` 时，工具内部调用：

```
GET /uapi/v1/enterprise/class-list?t={ms}&page={page}&size=100
```

全量拉取班级列表，按 `name` 子串匹配，返回匹配的 `id` 列表。

### 6.4 分配者/学员关键词解析

当传入 `assigner_keywords` 或 `student_keywords` 时，工具内部调用：

```
GET /uapi/v1/enterprise/search-user?t={ms}&condition=&keyword={keyword}&page=1&size=50
```

返回用户列表中所有 `umu_id` 作为筛选条件。

---

## 7. 使用示例

### 7.1 查询最近 90 天所有任务明细

```python
adm_list_user_tasks()
```

### 7.2 按任务类型筛选

```python
adm_list_user_tasks(task_types="2,3")
```

### 7.3 按完成状态筛选

```python
adm_list_user_tasks(learn_status="2,3")
```

### 7.4 按分配时间范围筛选

```python
adm_list_user_tasks(assign_start_day="2026-01-01", assign_end_day="2026-06-14")
```

### 7.5 按部门名称自动解析

```python
adm_list_user_tasks(department_names="销售部")
```

### 7.6 按学员姓名自动解析

```python
adm_list_user_tasks(student_keywords="Mingna")
```

### 7.7 获取全量数据

```python
adm_list_user_tasks(fetch_all=True)
```

### 7.8 组合筛选：逾期完成 + 指定课程关键词

```python
adm_list_user_tasks(
    learn_status="3",
    course_keywords="数据分析",
    fetch_all=True,
)
```

---

## 8. Skill 封装

高阶 Skill `get_user_tasks` 位于 `src/umu_sdk/skills/builtin/admin_tasks.py`，参数与 `adm_list_user_tasks` 原子工具一一对应，返回统一信封：

```python
{
  "success": true,
  "data": { "tasks": [...], "total": 20, "pagination": {...} },
  "error_code": "",
  "error_message": "",
  "suggested_action": "",
  "next_action": "proceed"
}
```

AI 应优先调用 `get_user_tasks` Skill；如需使用未封装的参数组合，可通过原子工具直接调用 `adm_list_user_tasks`。
