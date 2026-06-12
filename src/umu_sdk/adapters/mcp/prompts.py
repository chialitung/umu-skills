"""MCP Prompts — 给 AI 的标准操作流程指引.

这些 Prompt 不是强制流程，而是降低 AI 推理成本的参考模板。
AI 可以根据实际情况自主决定调用顺序。
"""

from __future__ import annotations


def course_completion_workflow() -> str:
    """课程完成的标准操作流程（SOP）.

    当用户想要完成一门课程时，AI 可以参考此流程执行。
    """
    return """你是一个 UMU 学习平台助手。当用户想要完成课程时，请按以下步骤执行：

【课程标识格式】
用户可以用以下任意一种方式指定课程：
1. **访问码**（推荐）：如 `aet504` → 系统会自动解析为 `https://aet504.umu.cn`
2. **短域名**：如 `aet504.umu.cn`
3. **完整 URL**：如 `https://umu.cn/course/?groupId=7324740&sKey=7fea`

注意：纯 groupId（如 `7324740`）不支持，因为无法自动获取 sKey，而 sKey 是报名检测的必需参数。
推荐使用访问码，最简洁且能确保报名检测准确。

【标准流程】
1. **获取课程列表**（可选，如果用户没有提供课程标识）
   调用 `stu_get_my_courses()` 获取用户的课程列表。
   让用户选择要学习的课程。

2. **获取课程结构**
   调用 `stu_get_course_structure(course_identifier)` 获取课程全貌。
   `course_identifier` 支持访问码、短域名、完整 URL 三种格式。
   - 检查 `enrollment_status`，如果值为 `"needs_enrollment"`，先调用 `stu_enroll_course(enroll_id)` 报名
   - 记录所有小节列表，特别关注 `is_completed=False` 的小节
   - 注意：返回结果中包含 `s_key`，后续操作可能需要用到

3. **获取当前进度**
   调用 `stu_get_learning_progress(course_identifier)` 获取当前完成率和各小节状态。
   同样支持访问码等格式。

4. **逐个完成未完成的小节**
   对每个 `is_completed=False` 的小节，根据其 `type` 选择对应操作：

   - **type=11 (视频) / type=13 (文章) / type=15 (图文) / type=14 (文档)**
     → 调用 `stu_browse_lesson(element_id)`
     → 文档如有 `vlt_min > 0`，将 vlt_min 值传入 `duration_seconds` 参数
     → 操作后调用 `stu_get_lesson_status(element_id)` 确认完成

   - **type=1 (问卷)**
     → 先调用 `stu_get_questionnaire_questions(element_id)` 获取题目
     → 向用户展示题目和选项
     → 获得用户的答案选择后，按返回的 `answer_format_example` 格式构造 JSON
     → 调用 `stu_submit_questionnaire(element_id, answers_json)`
     → 操作后调用 `stu_get_lesson_status(element_id)` 确认完成

   - **type=6, advance=0 (普通签到)**
     → 调用 `stu_check_in(element_id)`
     → 操作后调用 `stu_get_lesson_status(element_id)` 确认完成

   - **type=6, advance=1 (评分签到)**
     → 询问用户评分意愿（1-5分）
     → 用户给出评分后，调用 `stu_check_in_with_rating(element_id, rating)`
     → 如果用户拒绝评分，告知用户该小节无法自动完成
     → 操作后调用 `stu_get_lesson_status(element_id)` 确认完成

   - **type=10 (考试)**
     → 调用 `stu_start_exam(element_id)` 获取 exam_submit_id
     → 向用户展示考试题目（如有）
     → 用户完成答题后，调用 `stu_submit_exam(element_id, exam_submit_id)`
     → 操作后调用 `stu_get_lesson_status(element_id)` 确认完成

5. **最终确认**
   再次调用 `stu_get_learning_progress(course_identifier)` 确认课程 100% 完成。
   告知用户完成结果。

【重要约束】
- 不要在一个 Tool 调用里完成多个小节，必须逐个处理
- 评分签到如果用户没有明确评分意愿，先询问，不要猜测评分
- 问卷答案必须由用户提供，不要自动猜测答案
- 每次操作后必须调用 `stu_get_lesson_status` 验证状态
- 如果 `stu_get_learning_progress` 返回完成率已达 100%，可以跳过剩余小节
"""


def lesson_type_guide() -> str:
    """根据小节类型选择对应操作的快速参考.

    AI 在根据 course_structure 中的小节类型选择 Tool 时可以参考此指南。
    """
    return """UMU 课程小节类型与对应操作速查表：

| type | 名称 | 对应 Tool | 说明 |
|------|------|----------|------|
| 1 | 问卷 | `stu_get_questionnaire_questions` + `stu_submit_questionnaire` | 需要用户提供答案，按 answer_format_example 格式提交 |
| 6 (advance=0) | 普通签到 | `stu_check_in` | 无需额外参数 |
| 6 (advance=1) | 评分签到 | `stu_check_in_with_rating` | 需要用户评分，先询问 |
| 10 | 考试 | `stu_start_exam` → `stu_submit_exam` | 先开始获取 exam_submit_id，再提交 |
| 11 | 视频 | `stu_browse_lesson` | 模拟观看行为 |
| 13 | 文章 | `stu_browse_lesson` | 模拟阅读行为 |
| 14 | 文档/PPT | `stu_browse_lesson` | 模拟浏览行为，如有 vlt_min 需传入 duration_seconds |
| 15 | 图文 | `stu_browse_lesson` | 模拟浏览行为 |

【问卷答案格式详细说明】
调用 `stu_get_questionnaire_questions` 后会返回 `answer_format_example`，按此格式构造 JSON 数组。

每个 answer 对象格式：
```json
{
  "question_id": 123,
  "type": 2,
  "value": [{"id": "option_id", "other_content": ""}]
}
```

- `question_id`: 题目 ID（从 questions 中获取）
- `type`: 题目类型（2=单选, 3=多选, 4=文本, 5=评分）
- `value`: 答案数组
  - 单选(type=2): `[{"id": "选项ID", "other_content": ""}]`
  - 多选(type=3): `[{"id": "选项1ID"}, {"id": "选项2ID"}]`
  - 文本(type=4): `[{"id": "", "other_content": "用户输入的文本"}]`
  - 评分(type=5): `[{"id": "", "other_content": "评分值(如 5)"}]`

构造完成后，将所有 answer 对象组成 JSON 数组字符串，传入 `stu_submit_questionnaire` 的 `answers_json` 参数。

【注意事项】
- 文档(type=14)可能有 `document_finished_condition="2"`（需翻页到最后一页）
- 文档可能有 `vlt_min > 0`（最小学时限制），stu_browse_lesson 内部已处理
- 唯一可靠的完成状态来源是 `stu_get_learning_progress` + `stu_get_lesson_status`，不要依赖 makeweikestatus 的返回状态
"""


def error_recovery_guide() -> str:
    """常见错误和恢复策略.

    AI 在 Tool 调用失败时可以参考此指南决定下一步操作。
    """
    return """UMU 学习平台常见错误码和恢复策略：

| error_code | 含义 | 恢复策略 |
|-----------|------|----------|
| NEEDS_ENROLLMENT | 课程需要报名 | 调用 `stu_enroll_course` 报名后再继续 |
| ALREADY_COMPLETED | 小节已完成 | 跳过该小节，继续下一个 |
| PREREQUISITE_NOT_MET | 前置条件未满足 | 检查前置小节是否已完成 |
| INVALID_ANSWER_FORMAT | 答案格式错误 | 检查 answers JSON 格式是否正确，参考 answer_format_example |
| EXAM_NOT_STARTED | 考试未开始 | 先调用 `stu_start_exam` |
| EXAM_ALREADY_SUBMITTED | 考试已提交 | 跳过该考试小节 |
| EXAM_PREPARE_FAILED | 无法获取 exam_submit_id | 检查课程是否需要报名，或稍后重试 |
| AUTH_EXPIRED | 认证过期 | 调用 `stu_login` 重新登录 |
| RATE_LIMITED | 请求过于频繁 | 等待几秒后重试 |
| FETCH_MY_COURSES_FAILED | 获取课程列表失败 | 检查网络连接和认证状态 |
| STATUS_CHECK_FAILED | 无法获取小节状态 | 检查 element_id 和 group_id 是否正确 |

【通用策略】
- 任何操作失败时，先调用 `stu_get_lesson_status` 检查当前状态
- 如果返回的 `suggested_action` 不为空，优先按照 suggested_action 执行
- 报名后如果小节仍然无法访问，可能是缓存问题，等待 2-3 秒后重试
- `stu_get_lesson_status` 返回的 `check_method` 字段说明使用了哪种检测方式，可作为参考
"""


def exam_workflow_guide() -> str:
    """考试小节的专用操作流程指引.

    考试是流程最复杂的小节类型，此 Prompt 提供详细步骤。
    """
    return """UMU 考试小节完成流程：

【步骤】
1. 调用 `stu_start_exam(element_id)`
   - 返回 `exam_submit_id` 和 `student_id`
   - 如果返回错误 "EXAM_PREPARE_FAILED"，表示无法获取 exam_submit_id
     → 检查课程是否需要报名
     → 如果已报名，可能是考试页面结构变化，需要人工处理

2. 向用户展示考试信息
   - 告知用户考试已开始
   - 如果需要答题，请用户给出答案
   - 如果用户选择跳过/交白卷，可以直接调用 stu_submit_exam 提交空答案

3. 调用 `stu_submit_exam(element_id, exam_submit_id, answers_json)`
   - `answers_json` 为可选参数，可以留空（`"{}"`）
   - 如果返回 "考试已提交过"，表示该小节已完成，跳过即可

4. 调用 `stu_get_lesson_status(element_id, group_id)` 确认完成

【注意事项】
- exam_submit_id 是一次性的，每次开始考试都会生成新的
- 如果 stu_start_exam 失败，不要重复尝试，先检查报名状态
- 考试提交后无法修改答案
- 空答案提交也能完成小节（只是得分可能为0）
"""


def admin_account_management_guide() -> str:
    """管理员账号管理操作指南，重点说明列表分页策略."""
    return """UMU 管理员账号管理操作指南：

【查询账号列表】
调用 `adm_list_accounts` 查询企业账号。

1. **何时使用 `fetch_all=True`**
   - 用户说“获取所有账号”“列出全部成员”“导出账号”“不分页”
   - 用户没有指定页码，但明显需要完整结果（例如“有多少人已禁用”）
   - 需要基于全部账号做进一步操作（例如批量禁用、统计）
   - 设置为 True 时会自动遍历分页，最多获取 50 页（约 25000 条）

2. **何时使用 `page` / `page_size`**
   - 用户明确要求分页，例如“第 2 页”“前 500 条”“每页 100 条”
   - 结果集很大，用户只想先看一部分
   - `page_size` 范围 1-500，默认 500

3. **常用筛选条件组合**
   - 按角色：role_type=1（学员）/ 2（讲师）/ 3（学习负责人）/ 4（系统管理员）/ 5（子管理员）
   - 按状态：account_status=0（待加入）/ 1（已启用）/ 2（已禁用）/ 3（定时禁用）
   - 按分组：group_ids="177124,177125"，配合 group_operator
     - intersection：同时属于所有分组
     - union：属于任意一个分组
   - 按关键词：keywords 支持姓名、邮箱、手机号、用户名模糊匹配

4. **处理大结果集**
   - 如果 `fetch_all=True` 达到 50 页上限仍未获取完整数据，应在回复中告知用户“已获取前 25000 条，后续数据请使用 page/page_size 手动翻页”
   - 响应中的 `pagination.total_all` 是服务端总数量，`total` 是当前返回数量

【禁用/启用账号】
- 单个操作：`adm_disable_account` / `adm_enable_account`
  - 可通过 umu_id 或 email 定位用户
  - 提供 email 时会自动查询对应 umu_id
- 批量操作：`adm_batch_disable_accounts` / `adm_batch_enable_accounts`
  - 传入 umu_id 列表，多个用逗号分隔
  - 批量禁用可指定 effective_time 实现定时禁用（东八区时间）

【创建账号】
- `adm_create_account` 用于创建单个账号
- accounts 参数可填多个邮箱，用逗号分隔，可一次性创建多个
- role_type 必填：1=学员, 2=讲师, 3=学习负责人, 4=系统管理员, 5=子管理员

【标准工作流示例】
1. 查询目标账号：`adm_list_accounts(keywords="张三", fetch_all=True)`
2. 根据返回的 umu_id 执行操作：`adm_disable_account(umu_id="12345")`
3. 验证结果：再次调用 `adm_list_accounts` 检查账号状态
"""
