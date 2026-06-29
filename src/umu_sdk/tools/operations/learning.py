# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习域跨角色共享业务操作.

将学员核心学习流程下沉为无状态业务函数，供 student/teacher/admin 三个 MCP server
复用。函数第一个参数为已登录的 UMUClient，返回 dict 供 register_operations 包装。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from ...adapters.mcp.batch import AccountImporter
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation
from ..shared.learning_helpers import (
    _build_exam_answers_json,
    _build_insert_answer_payload,
    _build_lesson_index_map,
    _build_questionnaire_answers_json,
    _build_uscorm_12_cmi,
    _check_needs_enroll,
    _check_needs_enroll_form,
    _fetch_enroll_form_page,
    _format_scorm_total_time,
    _get_enroll_short_url,
    _get_exam_submit_id,
    _is_question_required,
    _makeweikestatus_sequence,
    _parse_answers_config,
    _parse_enroll_form,
    _post_scorm_datamodel,
    _post_uscorm_commit,
    _question_type_name,
    _resolve_course_identifier,
    _resolve_lesson_answers_config,
    _resolve_scorm_launch_params,
    _validate_answers_against_questions,
    _validate_enroll_form,
)


logger = logging.getLogger(__name__)


@umu_operation(
    name="resolve_course_url",
    description="解析课程标识符，提取 group_id 和 s_key",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程链接或访问码。支持格式：纯groupId（如 7324740）、访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）",
    },
)
async def resolve_course_url(
    client: UMUClient,
    course_identifier: str,
) -> dict[str, Any]:
    """解析课程标识符，提取 group_id 和 s_key."""
    group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)
    return {
        "group_id": group_id,
        "s_key": s_key,
        "resolved_url": resolved_url,
        "input": course_identifier,
        "_next_action": "proceed",
        "_suggested_action": "调用 get_course_structure(group_id, s_key) 获取课程结构",
    }


async def _get_course_structure_impl(
    client: UMUClient,
    group_id: str,
    s_key: str,
    resolved_url: str = "",
    include_question_preview: bool = False,
) -> dict[str, Any]:
    """获取课程完整结构的内部实现（无操作装饰器）."""
    # 1. 检查是否需要报名（传入 s_key 提高准确性）
    try:
        needs_enroll, enroll_id = _check_needs_enroll(client, group_id, s_key)
        enrollment_status = "needs_enrollment" if needs_enroll else "enrolled"
    except Exception:
        needs_enroll, enroll_id = False, None
        enrollment_status = "unknown"

    # 2. 获取课程元素列表
    try:
        r = client.get(
            client.mobile_url(
                "/uapi/v2/element/list?"
                f"page=1&size=100&parent_id={group_id}&get_draft=0"
            )
        )
        if r.get("error_code") != 0:
            raise UMUError(
                f"无法获取课程元素: {r.get('message', '未知错误')}",
                code="COURSE_NOT_ACCESSIBLE",
            )
        elements = r.get("data", {}).get("list", [])
    except Exception as e:
        raise UMUError(str(e), code="FETCH_COURSE_FAILED") from e

    # 3. 构造小节列表
    lessons: list[dict[str, Any]] = []
    questionnaire_counter = 0
    exam_counter = 0
    for el in elements:
        eid = el.get("element_id", "")
        etype = el.get("type", 0)
        setup = el.get("setup", {}) or {}
        extend = el.get("extend", {}) or {}
        learn_status = extend.get("learn_status", 0)

        lesson: dict[str, Any] = {
            "element_id": str(eid),
            "title": el.get("title", ""),
            "type": etype,
            "is_completed": learn_status == 2,
            "learn_status": learn_status,
        }

        # 添加类型特定信息
        if etype == 1:  # 问卷
            questionnaire_counter += 1
            lesson["completion_type"] = "questionnaire"
            lesson["questionnaire_index"] = questionnaire_counter
            # 题目预览
            if include_question_preview:
                try:
                    r = client.get(
                        client.desktop_url(
                            f"/uapi/v1/poll/question-list?"
                            f"element_id={eid}&page=1&size=999"
                        )
                    )
                    questions = r.get("data", {}).get("list", [])
                    type_counts: dict[str, int] = {}
                    preview: list[dict[str, Any]] = []
                    for q in questions:
                        qtype = q.get("type")
                        tname = _question_type_name(qtype)
                        type_counts[tname] = type_counts.get(tname, 0) + 1
                        preview.append({
                            "type": tname,
                            "required": _is_question_required(q),
                            "title": q.get("title", "")[:50],
                        })
                    lesson["question_count"] = len(questions)
                    lesson["question_types"] = type_counts
                    lesson["questions_preview"] = preview
                except Exception:
                    pass
        elif etype == 6:  # 签到
            advance = setup.get("advance", "0")
            lesson["advance"] = advance
            if advance == "1":
                lesson["completion_type"] = "checkin_with_rating"
            else:
                lesson["completion_type"] = "checkin"
        elif etype == 10:  # 考试
            exam_counter += 1
            lesson["completion_type"] = "exam"
            lesson["exam_index"] = exam_counter
            # 题目预览
            if include_question_preview:
                try:
                    r = client.get(
                        client.mobile_url(
                            "/napi/v1/quiz/question-list"
                            f"?_type=1&element_id={eid}&page=1&size=999"
                        )
                    )
                    questions = r.get("data", {}).get("list", [])
                    exam_type_counts: dict[str, int] = {}
                    exam_preview: list[dict[str, Any]] = []
                    for q in questions:
                        qtype = q.get("type")
                        tname = _question_type_name(qtype, is_exam=True)
                        exam_type_counts[tname] = exam_type_counts.get(tname, 0) + 1
                        exam_preview.append({
                            "type": tname,
                            "required": _is_question_required(q),
                            "title": q.get("title", "")[:50],
                        })
                    lesson["question_count"] = len(questions)
                    lesson["question_types"] = exam_type_counts
                    lesson["questions_preview"] = exam_preview
                except Exception:
                    pass
        elif etype == 11:  # 微课 / SCORM / H5
            if setup.get("content_type") == "scorm":
                lesson["completion_type"] = "scorm"
            else:
                lesson["completion_type"] = "browse"
        elif etype == 13:  # 文章
            lesson["completion_type"] = "browse"
        elif etype == 14:  # 文档
            lesson["completion_type"] = "browse"
            lesson["document_finished_condition"] = setup.get("document_finished_condition", "1")
            lesson["vlt_min"] = setup.get("vlt_min", 0)
        elif etype == 15:  # 图文
            lesson["completion_type"] = "browse"
        else:
            lesson["completion_type"] = "unknown"

        lessons.append(lesson)

    total = len(lessons)
    completed = sum(1 for lesson in lessons if lesson.get("is_completed"))

    data = {
        "group_id": group_id,
        "s_key": s_key,
        "resolved_url": resolved_url,
        "enrollment_status": enrollment_status,
        "needs_enrollment": needs_enroll,
        "enroll_id": enroll_id,
        "total_lessons": total,
        "completed_lessons": completed,
        "progress_percentage": round(completed / total * 100) if total > 0 else 0,
        "lessons": lessons,
    }

    if needs_enroll:
        # 进一步检测是否需要填写复杂报名表单
        try:
            needs_form, form_summary = _check_needs_enroll_form(client, group_id, s_key)
            if needs_form and form_summary:
                data["enroll_form_required"] = True
                data["enroll_form_summary"] = form_summary
                data["_next_action"] = "needs_enroll_form"
                data["_suggested_action"] = "课程需要报名并填写报名信息。请先调用 enroll_course(enroll_id, course_identifier) 预报名，再调用 submit_enroll_form 提交报名信息。"
                return data
        except Exception:
            pass

        data["_next_action"] = "needs_enrollment"
        data["_suggested_action"] = "课程需要报名，调用 enroll_course(enroll_id) 报名后再继续学习"
        return data

    data["_next_action"] = "proceed"
    data["_suggested_action"] = "对每个 is_completed=False 的小节调用对应完成操作"
    return data


@umu_operation(
    name="get_course_structure",
    description="获取课程完整结构，包括报名状态和所有小节列表",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）。注意：纯groupId不支持，因为无法自动获取sKey。",
        "include_question_preview": "是否包含问卷/考试小节的题目预览信息。开启后会额外获取每道问卷/考试小节的题目数量和类型分布，帮助提前准备答案。",
    },
)
async def get_course_structure(
    client: UMUClient,
    course_identifier: str,
    include_question_preview: bool = False,
) -> dict[str, Any]:
    """获取课程完整结构，包括报名状态和所有小节列表."""
    try:
        group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        raise UMUError(str(e), code="INVALID_COURSE_IDENTIFIER") from e

    return await _get_course_structure_impl(
        client, group_id, s_key, resolved_url, include_question_preview
    )


@umu_operation(
    name="get_learning_progress",
    description="获取课程学习进度",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程标识。支持格式：访问码（推荐），如 'aet504'；短域名，如 'aet504.umu.cn'；完整 URL，如 'https://<domain>/course/?groupId=7324740&sKey=7fea'。注意：纯 groupId 不支持，因为无法自动获取 sKey。",
    },
)
async def get_learning_progress(
    client: UMUClient,
    course_identifier: str,
) -> dict[str, Any]:
    """获取课程学习进度."""
    group_id, _, _ = _resolve_course_identifier(client, course_identifier)

    try:
        r = client.get(
            client.desktop_url(f"/uapi/v1/course/get-learning-progress?group_id={group_id}")
        )
        if r.get("error_code") != 0:
            raise UMUError(
                r.get("message", "获取进度失败"),
                code="FETCH_PROGRESS_FAILED",
            )

        data = r.get("data", {})
        rate = data.get("complete_rate", 0) * 100
        stats = data.get("session_stat", [])

        # 构造统计信息
        stat_summary: list[dict[str, Any]] = []
        for stat in stats:
            stat_summary.append({
                "session_type": stat.get("session_type"),
                "total": stat.get("total_num"),
                "completed": stat.get("complete_num"),
            })

        result = {
            "group_id": group_id,
            "complete_rate": round(rate, 1),
            "is_fully_completed": rate >= 100,
            "session_stats": stat_summary,
        }
        if rate >= 100:
            result["_next_action"] = "lesson_completed"
            result["_suggested_action"] = "如果完成率已达 100%，课程已完成"
        else:
            result["_next_action"] = "proceed"
            result["_suggested_action"] = "继续完成剩余小节"
        return result
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="FETCH_PROGRESS_ERROR") from e


async def _enroll_course_impl(
    client: UMUClient,
    enroll_id: str,
    course_identifier: str | None = None,
) -> dict[str, Any]:
    """报名课程的内部实现（无操作装饰器）."""
    r = client.post(
        client.mobile_url("/ajax/verify/auto"),
        {"enroll_id": str(enroll_id)},
    )
    if r.get("error_code") == 0 or r.get("status") is True:
        data = r.get("data", {})
        is_enrolled = data.get("is_enrolled")
        pay_status = data.get("pay_status", "")

        # 预报名状态：可能还需要填写复杂报名表单
        if str(is_enrolled) == "1" and pay_status == "pay" and course_identifier:
            try:
                group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)
                needs_form, form_summary = _check_needs_enroll_form(client, group_id, s_key)
                if needs_form and form_summary:
                    return {
                        "is_enrolled": is_enrolled,
                        "pay_status": pay_status,
                        "enroll_form_required": True,
                        "enroll_form_summary": form_summary,
                        "_next_action": "needs_enroll_form",
                        "_suggested_action": "课程已预报名，但还需要填写报名信息。请调用 get_enroll_form 获取完整表单并提交，或使用 submit_enroll_form 直接提交答案。",
                    }
            except Exception:
                pass

        return {
            "is_enrolled": is_enrolled,
            "pay_status": pay_status,
            "enroll_form_required": False,
            "_next_action": "proceed",
            "_suggested_action": "报名成功，现在可以调用 get_course_structure 获取课程结构并学习",
        }
    else:
        raise UMUError(
            r.get("message", "报名失败"),
            code="ENROLL_FAILED",
        )


@umu_operation(
    name="enroll_course",
    description="报名课程",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "enroll_id": "报名 ID，来自 get_course_structure 返回的 enroll_id",
        "course_identifier": "课程标识（访问码/短域名/URL）。提供后可在预报名状态下检测是否需要填写复杂报名表单。",
    },
)
async def enroll_course(
    client: UMUClient,
    enroll_id: str,
    course_identifier: str | None = None,
) -> dict[str, Any]:
    """报名课程."""
    try:
        return await _enroll_course_impl(client, enroll_id, course_identifier)
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="ENROLL_ERROR") from e



@umu_operation(
    name="get_enroll_form",
    description="获取复杂报名表单的字段和题目结构",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）",
    },
)
async def get_enroll_form(
    client: UMUClient,
    course_identifier: str,
) -> dict[str, Any]:
    """获取复杂报名表单的字段和题目结构."""
    try:
        group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        raise UMUError(str(e), code="INVALID_COURSE_IDENTIFIER") from e

    try:
        enroll_id, enroll_url = _get_enroll_short_url(client, group_id, s_key)
        if not enroll_id or not enroll_url:
            raise UMUError(
                "无法从课程页面提取报名信息，该课程可能不需要报名或访问受限",
                code="ENROLL_FORM_NOT_FOUND",
            )

        page_data = _fetch_enroll_form_page(client, enroll_url)
        if not page_data:
            raise UMUError(
                "无法解析报名表单页面",
                code="ENROLL_FORM_PARSE_FAILED",
            )

        form = _parse_enroll_form(page_data)
        form["group_id"] = group_id
        form["s_key"] = s_key

        form["_next_action"] = "proceed"
        form["_suggested_action"] = "根据返回的表单字段准备答案，调用 submit_enroll_form 提交"
        return form
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="GET_ENROLL_FORM_ERROR") from e


@umu_operation(
    name="submit_enroll_form",
    description="提交复杂报名表单（联系信息 + 报名问题）",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）",
        "contact_answers": '联系信息答案。key 对应 get_enroll_form 返回的 contact_fields.key，例如 {"username": "张三", "mobile": "13800138000", "company": "腾讯"}',
        "section_answers": '报名问题答案列表。每题一个对象：文本题 {question_id, text}，单选题 {question_id, answer_id}，多选题 {question_id, answer_ids}，数值题 {question_id, number}',
    },
)
async def submit_enroll_form(
    client: UMUClient,
    course_identifier: str,
    contact_answers: dict[str, str] | None = None,
    section_answers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """提交复杂报名表单（联系信息 + 报名问题）."""
    contact_answers = contact_answers or {}
    section_answers = section_answers or []

    try:
        group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        raise UMUError(str(e), code="INVALID_COURSE_IDENTIFIER") from e

    try:
        # 1. 获取表单结构和 enroll_id/short_url
        enroll_id, enroll_url = _get_enroll_short_url(client, group_id, s_key)
        if not enroll_id or not enroll_url:
            raise UMUError(
                "无法从课程页面提取报名信息",
                code="ENROLL_FORM_NOT_FOUND",
            )

        page_data = _fetch_enroll_form_page(client, enroll_url)
        if not page_data:
            raise UMUError(
                "无法解析报名表单页面",
                code="ENROLL_FORM_PARSE_FAILED",
            )

        form = _parse_enroll_form(page_data)
        contact_fields = form.get("contact_fields", [])
        section_questions = form.get("section_questions", [])

        # 2. 校验答案
        error_msg = _validate_enroll_form(
            contact_fields, section_questions, contact_answers, section_answers
        )
        if error_msg:
            raise UMUError(
                error_msg,
                code="ENROLL_FORM_VALIDATION_FAILED",
                details={"contact_fields": contact_fields, "section_questions": section_questions},
            )

        # 3. 预报名验证
        client.post(
            client.mobile_url("/ajax/verify/auto"),
            {"enroll_id": str(enroll_id)},
        )

        # 4. 提交联系信息（只传 selected=true 的字段）
        submit_contact: dict[str, str] = {"enroll_id": str(enroll_id)}
        for field in contact_fields:
            if field.get("selected"):
                key = field["key"]
                value = contact_answers.get(key, "")
                submit_contact[key] = str(value)

        client.post(
            client.mobile_url("/signup/submitContact"),
            submit_contact,
        )

        # 5. 提交报名问题答案
        answer_payload = _build_insert_answer_payload(
            section_questions, section_answers, enroll_id
        )
        client.post(
            client.mobile_url("/ajax/insertAnswer"),
            {"q": json.dumps(answer_payload, ensure_ascii=False)},
        )

        # 6. 确认报名结果
        page_data = _fetch_enroll_form_page(client, enroll_url)
        is_enrolled = 0
        pay_status = ""
        if page_data:
            data = page_data.get("data", {})
            is_enrolled = data.get("is_enrolled", 0)
            pay_status = data.get("pay_status", "")

        if str(is_enrolled) in ("1", "2") or pay_status in ("pay", "success"):
            return {
                "enroll_id": enroll_id,
                "is_enrolled": is_enrolled,
                "pay_status": pay_status,
                "_next_action": "enrollment_completed",
                "_suggested_action": "报名成功，现在可以调用 get_course_structure 获取课程结构并学习",
            }

        raise UMUError(
            f"报名提交后状态未确认: is_enrolled={is_enrolled}, pay_status={pay_status}",
            code="ENROLL_FORM_SUBMIT_INCOMPLETE",
            details={"is_enrolled": is_enrolled, "pay_status": pay_status},
        )
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="SUBMIT_ENROLL_FORM_ERROR") from e


@umu_operation(
    name="browse_lesson",
    description="完成浏览类型小节（视频、文章、图文、文档）",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "小节元素 ID，来自 get_course_structure 的 element_id",
        "duration_seconds": "模拟浏览时长（秒），用于有最小学时限制的文档。默认 0 表示使用标准序列。",
    },
)
async def browse_lesson(
    client: UMUClient,
    element_id: str,
    duration_seconds: int = 0,
) -> dict[str, Any]:
    """完成浏览类型小节（视频、文章、图文、文档）."""
    extras: dict[str, dict] = {}
    if duration_seconds > 0:
        extras["playing"] = {"left_time": str(duration_seconds)}
        extras["achieve"] = {"left_time": str(duration_seconds), "vlt_status": "1"}

    await _makeweikestatus_sequence(client, element_id, extras)
    return {
        "element_id": element_id,
        "action": "browse_completed",
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }


@umu_operation(
    name="complete_scorm_section",
    description="完成 SCORM 1.2 格式小节",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "小节元素 ID，来自 get_course_structure 的 element_id",
        "group_id": "课程组 ID（可选），用于完成后验证进度",
        "status": 'SCORM 1.2 cmi.core.lesson_status，可选 passed/completed/failed/incomplete/browsed',
        "score": "得分 0-100，提交到 cmi.core.score.raw",
        "duration_seconds": "本次学习时长（秒），格式化为 HHHH:MM:SS.SS 提交到 cmi.core.total_time",
        "lesson_location": "可选：写入 cmi.core.lesson_location",
        "suspend_data_json": "高级：自定义 cmi.suspend_data JSON 字符串",
        "interactions_json": "高级：自定义 cmi.interactions 数组 JSON 字符串",
        "scorm_launch_url": "可选：完整的 SCORM launch URL。自动发现失败时作为兜底",
    },
)
async def complete_scorm_section(
    client: UMUClient,
    element_id: str,
    group_id: str = "",
    status: str = "passed",
    score: int | None = None,
    duration_seconds: int = 0,
    lesson_location: str = "",
    suspend_data_json: str = "",
    interactions_json: str = "",
    scorm_launch_url: str = "",
) -> dict[str, Any]:
    """完成 SCORM 1.2 格式小节."""
    # 1. 校验元素类型
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
        element_data = r.get("data", {}) or {}
        if element_data.get("type") != 11:
            raise UMUError(
                f'小节类型不是 SCORM/H5：type={element_data.get("type")}',
                code="INVALID_SECTION_TYPE",
            )
        setup = element_data.get("setup", {}) or {}
        if setup.get("content_type") != "scorm":
            raise UMUError(
                "小节不是 SCORM 类型（setup.content_type != scorm）",
                code="INVALID_SECTION_TYPE",
            )
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="ELEMENT_FETCH_FAILED") from e

    # 2. 解析 SCORM 启动参数
    try:
        launch_params = _resolve_scorm_launch_params(
            client, element_id, provided_url=scorm_launch_url or None
        )
    except Exception as e:
        raise UMUError(
            str(e),
            code="SCORM_LAUNCH_RESOLVE_FAILED",
        ) from e

    # 3. 初始化 makeweikestatus 状态机
    try:
        await _makeweikestatus_sequence(client, element_id)
    except Exception as e:
        print(f"[complete_scorm_section] makeweikestatus 失败: {e}", file=sys.stderr)

    # 4. 提交 CMI 字段
    try:
        if launch_params.get("mode") == "umu_wrapper":
            if interactions_json:
                print(
                    "[complete_scorm_section] UMU wrapper 暂不支持 interactions_json，已忽略",
                    file=sys.stderr,
                )
            cmi = _build_uscorm_12_cmi(
                launch_params,
                status=status,
                score=score,
                duration_seconds=duration_seconds,
                lesson_location=lesson_location,
                suspend_data_json=suspend_data_json,
            )
            _post_uscorm_commit(client, launch_params, cmi)
        else:
            extra: dict[str, str] = {"cmi__core__lesson_status": status}
            if score is not None:
                extra["cmi__core__score__raw"] = str(score)
                extra["cmi__core__score__min"] = "0"
                extra["cmi__core__score__max"] = "100"
            if duration_seconds > 0:
                extra["cmi__core__total_time"] = _format_scorm_total_time(duration_seconds)
            if lesson_location:
                extra["cmi__core__lesson_location"] = lesson_location
            if suspend_data_json:
                extra["cmi__suspend_data"] = suspend_data_json

            if interactions_json:
                try:
                    interactions = json.loads(interactions_json)
                    for idx, interaction in enumerate(interactions):
                        prefix = f"cmi__interactions_{idx}"
                        for field in ("id", "type", "student_response", "result", "latency", "time"):
                            val = interaction.get(field)
                            if val is not None:
                                extra[f"{prefix}__{field}"] = str(val)
                except Exception as e:
                    raise UMUError(
                        f"interactions_json 解析失败: {e}",
                        code="INVALID_INTERACTIONS_JSON",
                    ) from e

            _post_scorm_datamodel(client, launch_params, extra)
            # 空 commit，模拟 LMSCommit("")
            _post_scorm_datamodel(client, launch_params, {})
    except UMUError:
        raise
    except Exception as e:
        raise UMUError(str(e), code="SCORM_DATAMODEL_ERROR") from e

    # 5. 验证小节状态
    try:
        status_result = await get_lesson_status(client, element_id, group_id)
        if status_result.get("is_completed"):
            return {
                **status_result,
                "_next_action": "lesson_completed",
                "_suggested_action": "小节已完成",
            }
        return {
            **status_result,
            "_next_action": "proceed",
            "_suggested_action": "已提交 SCORM 数据，请稍后再次确认完成状态",
        }
    except Exception as e:
        raise UMUError(
            str(e),
            code="STATUS_CHECK_ERROR",
        ) from e



@umu_operation(
    name="get_questionnaire_questions",
    description="获取问卷的题目列表",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "问卷小节元素 ID",
    },
)
async def get_questionnaire_questions(
    client: UMUClient,
    element_id: str,
) -> dict[str, Any]:
    """获取问卷的题目列表."""
    r = client.get(
        client.desktop_url(
            f"/uapi/v1/poll/question-list?"
            f"element_id={element_id}&page=1&size=999"
        )
    )
    if r.get("error_code") != 0:
        raise UMUError(
            r.get("message", "获取题目失败"),
            code="FETCH_QUESTIONS_FAILED",
        )

    questions = r.get("data", {}).get("list", [])
    simplified: list[dict[str, Any]] = []
    for q in questions:
        sq: dict[str, Any] = {
            "question_id": q.get("id"),
            "title": q.get("title", ""),
            "type": q.get("type"),
            "type_name": _question_type_name(q.get("type")),
            "is_required": _is_question_required(q),
        }
        # 添加选项
        options = q.get("list", [])
        if options:
            sq["options"] = [
                {"option_id": opt.get("id"), "text": opt.get("title", "")}
                for opt in options
            ]
        simplified.append(sq)

    # 构造答案格式示例
    answer_example = []
    for q in simplified:
        qid = q.get("question_id")
        qtype = q.get("type")
        example: dict[str, Any] = {"question_id": qid, "type": qtype}
        if qtype == 2 and q.get("options"):  # 单选
            example["value"] = [{"id": str(q["options"][0]["option_id"]), "other_content": ""}]
        elif qtype == 3 and q.get("options"):  # 多选
            example["value"] = [{"id": str(q["options"][0]["option_id"]), "other_content": ""}]
        elif qtype == 4:  # 文本
            example["value"] = [{"id": "", "other_content": "你的答案"}]
        elif qtype == 5:  # 评分
            example["value"] = [{"id": "", "other_content": "5"}]
        else:
            example["value"] = []
        answer_example.append(example)

    return {
        "element_id": element_id,
        "total_questions": len(simplified),
        "questions": simplified,
        "answer_format_example": answer_example,
        "answer_format_note": "提交时将所有 question 的 value 作为 JSON 字符串传入 answers_json 参数",
        "_next_action": "needs_user_input",
        "_suggested_action": "向用户展示题目和选项，获得答案后按 answer_format_example 格式构造 JSON 调用 submit_questionnaire",
    }


@umu_operation(
    name="submit_questionnaire",
    description="提交问卷答案",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "问卷小节元素 ID",
        "answers_json": '答案 JSON 字符串，格式: [{"question_id": 123, "type": 2, "value": [{"id": "option_id", "other_content": ""}]}]',
    },
)
async def submit_questionnaire(
    client: UMUClient,
    element_id: str,
    answers_json: str,
) -> dict[str, Any]:
    """提交问卷答案."""
    # 1. 获取 submit_id
    r = client.post(
        client.mobile_url("/megrez/poll/v1/user-save-poll-result"),
        {"answers": "[]", "session_id": str(element_id), "submit_id": "0"},
    )
    submit_id = r.get("data", {}).get("submit_id", "0")

    # 2. 提交答案
    r = client.post(
        client.mobile_url("/megrez/poll/v1/save-poll-result"),
        {
            "submit_id": str(submit_id),
            "element_id": str(element_id),
            "is_anonymous": "0",
            "answer": answers_json,
        },
    )
    if r.get("error_code") != 0:
        raise UMUError(
            r.get("message", "提交问卷失败"),
            code="SUBMIT_QUESTIONNAIRE_FAILED",
        )

    # 3. 执行 makeweikestatus 序列
    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "submit_id": submit_id,
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }


@umu_operation(
    name="submit_questionnaire_with_config",
    description="使用简化配置提交问卷答案",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "问卷小节元素 ID",
        "answers_config": '答案配置，格式: 用分号(;)分隔每道题的答案。单选: 单个字母(A=第1个选项, B=第2个...); 多选: 连续字母(如 BCD=选第2/3/4个选项); 开放题: 直接文本; 数值题: 数字。示例: "A;BCD;我认为答案是...;5"',
    },
)
async def submit_questionnaire_with_config(
    client: UMUClient,
    element_id: str,
    answers_config: str,
) -> dict[str, Any]:
    """使用简化配置提交问卷答案."""
    # 1. 获取问卷题目
    r = client.get(
        client.desktop_url(
            f"/uapi/v1/poll/question-list?"
            f"element_id={element_id}&page=1&size=999"
        )
    )
    if r.get("error_code") != 0:
        raise UMUError(
            r.get("message", "获取题目失败"),
            code="FETCH_QUESTIONS_FAILED",
        )

    questions = r.get("data", {}).get("list", [])
    if not questions:
        raise UMUError("该问卷没有题目", code="NO_QUESTIONS")

    # 2. 解析答案配置
    answers = _parse_answers_config(answers_config)

    # 3. 验证答案与问题结构
    is_valid, error_msg = _validate_answers_against_questions(
        questions, answers, for_exam=False
    )
    if not is_valid:
        # 构造问题摘要返回给用户
        question_summary = []
        for i, q in enumerate(questions):
            qtype = q.get("type")
            options = q.get("list", [])
            opt_text = ""
            if options:
                opt_text = ", ".join(
                    f"{chr(ord('A') + j)}={opt.get('title', '')}"
                    for j, opt in enumerate(options)
                )
            type_name = _question_type_name(qtype)
            req_tag = "必填" if _is_question_required(q) else "选填"
            # 构造该题的可接受答案格式提示
            if qtype == 2:
                fmt_hint = f"单个字母(A-{chr(ord('A') + len(options) - 1)})"
            elif qtype == 3:
                fmt_hint = f"连续字母(如 AB, A-{chr(ord('A') + len(options) - 1)})"
            elif qtype == 4:
                fmt_hint = "直接文本"
            elif qtype == 5:
                fmt_hint = "数字"
            else:
                fmt_hint = "直接文本"
            question_summary.append(
                f"  第{i + 1}题 [{req_tag}] [{type_name}] {q.get('title', '')}"
                f"{(' | 选项: ' + opt_text) if opt_text else ''}"
                f" | 答案格式: {fmt_hint}"
            )

        raise UMUError(
            f"答案配置验证失败: {error_msg}",
            code="ANSWERS_VALIDATION_FAILED",
            details={
                "element_id": element_id,
                "total_questions": len(questions),
                "questions_summary": question_summary,
                "your_answers": answers,
                "expected_format": (
                    "用分号(;)分隔每题答案。"
                    "单选: 单个字母(如 A); "
                    "多选: 连续字母(如 BCD); "
                    "开放题: 直接文本; "
                    "数值题: 数字; "
                    "选填题: 留空跳过"
                ),
            },
        )

    # 4. 构造答案 JSON
    answers_json_list = _build_questionnaire_answers_json(questions, answers)
    answers_json = json.dumps(answers_json_list, ensure_ascii=False)

    # 5. 获取 submit_id
    r = client.post(
        client.mobile_url("/megrez/poll/v1/user-save-poll-result"),
        {"answers": "[]", "session_id": str(element_id), "submit_id": "0"},
    )
    submit_id = r.get("data", {}).get("submit_id", "0")

    # 6. 提交答案
    r = client.post(
        client.mobile_url("/megrez/poll/v1/save-poll-result"),
        {
            "submit_id": str(submit_id),
            "element_id": str(element_id),
            "is_anonymous": "0",
            "answer": answers_json,
        },
    )
    if r.get("error_code") != 0:
        raise UMUError(
            r.get("message", "提交问卷失败"),
            code="SUBMIT_QUESTIONNAIRE_FAILED",
        )

    # 7. 执行 makeweikestatus 序列
    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "submit_id": submit_id,
        "total_questions": len(questions),
        "answers_summary": answers,
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }



@umu_operation(
    name="check_in",
    description="完成普通签到小节",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "签到小节元素 ID",
    },
)
async def check_in(
    client: UMUClient,
    element_id: str,
) -> dict[str, Any]:
    """完成普通签到小节."""
    # 调用 insertAnswer
    q_payload = {
        "answerList": [],
        "answerInfo": [{"id": str(element_id), "text": "学员"}],
        "answerNumber": {},
        "enrollId": 0,
        "sessionId": str(element_id),
    }
    try:
        client.post(
            client.mobile_url("/ajax/insertAnswer"),
            {"q": json.dumps(q_payload, ensure_ascii=False)},
        )
    except Exception as e:
        logger.warning("[check_in] insertAnswer 失败（非致命）: %s", e)

    # 调用 insertWxAnswer（备选）
    try:
        client.post(
            client.mobile_url("/ajax/insertWxAnswer"),
            {
                "nickName": "学员",
                "answerId": str(element_id),
                "sessionId": str(element_id),
            },
        )
    except Exception as e:
        logger.warning("[check_in] insertWxAnswer 失败（非致命）: %s", e)

    # 执行 makeweikestatus 序列
    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "action": "checkin_completed",
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }


@umu_operation(
    name="check_in_with_rating",
    description="完成评分签到小节",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "评分签到小节元素 ID",
        "rating": "评分值（1-5），必须用户提供，不要猜测",
        "comment": "可选的评论/反馈文本",
    },
)
async def check_in_with_rating(
    client: UMUClient,
    element_id: str,
    rating: int,
    comment: str = "",
) -> dict[str, Any]:
    """完成评分签到小节."""
    # 获取 poll 问题
    try:
        r = client.get(
            client.desktop_url(f"/uapi/v1/poll/question-list?element_id={element_id}&page=1&size=999")
        )
        questions = r.get("data", {}).get("list", [])
    except Exception:
        questions = []

    # 构造 answerNumber
    answer_number: dict[str, str] = {}
    for q in questions:
        qid = q.get("id")
        qtype = q.get("type")
        if qtype == 2:  # 单选
            options = q.get("list", [])
            if options:
                answer_number[str(qid)] = str(options[0].get("id", ""))
        elif qtype == 5:  # 评分
            answer_number[str(qid)] = str(rating)

    # 调用 insertAnswer
    q_payload = {
        "answerList": [],
        "answerInfo": [{"id": str(element_id), "text": comment or "学员"}],
        "answerNumber": answer_number,
        "enrollId": 0,
        "sessionId": str(element_id),
    }
    try:
        client.post(
            client.mobile_url("/ajax/insertAnswer"),
            {"q": json.dumps(q_payload, ensure_ascii=False)},
        )
    except Exception as e:
        logger.warning("[check_in_with_rating] insertAnswer 失败（非致命）: %s", e)

    # 执行 makeweikestatus 序列
    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "rating": rating,
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }


@umu_operation(
    name="check_in_with_answers",
    description="完成复杂签到小节（支持文本/单选/多选/数值题）",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "签到小节元素 ID",
        "answers_json": '复杂签到答案列表 JSON 字符串。每项对应一道题目，支持按题目顺序或显式指定题目 ID。未提供 question_id 时，工具会尝试从签到页面自动获取题目结构并匹配。',
    },
)
async def check_in_with_answers(
    client: UMUClient,
    element_id: str,
    answers_json: str,
) -> dict[str, Any]:
    """完成复杂签到小节（支持文本/单选/多选/数值题）."""
    from ..shared.learning_helpers import _fetch_signin_questions

    try:
        raw_answers = json.loads(answers_json)
    except json.JSONDecodeError as e:
        raise UMUError(
            f"answers_json 不是有效 JSON: {e}",
            code="INVALID_ANSWERS_JSON",
        ) from e
    if not isinstance(raw_answers, list):
        raise UMUError("answers_json 必须解析为列表", code="INVALID_ANSWERS")

    # 判断是否需要自动获取题目结构
    need_discovery = any(
        isinstance(a, dict) and not a.get("question_id") for a in raw_answers
    )
    questions: list[dict[str, Any]] = []
    if need_discovery:
        try:
            questions = _fetch_signin_questions(client, element_id)
        except RuntimeError as e:
            raise UMUError(
                str(e),
                code="SIGNIN_QUESTIONS_NOT_FOUND",
            ) from e

    answer_list: list[str] = []
    answer_info: list[dict[str, str]] = []
    answer_number: dict[str, int | float] = {}

    for idx, ans in enumerate(raw_answers):
        if not isinstance(ans, dict):
            raise UMUError(f"第 {idx + 1} 个答案必须是对象", code="INVALID_ANSWERS")

        q_type = ans.get("type", "").lower()
        question_id = ans.get("question_id", "")

        if not question_id:
            if idx >= len(questions):
                raise UMUError(
                    f"第 {idx + 1} 个答案超出题目数量，请提供 question_id 或检查答案数量",
                    code="INVALID_ANSWERS",
                )
            question_id = questions[idx].get("questionInfo", {}).get("questionId", "")
            if not question_id:
                raise UMUError(f"第 {idx + 1} 题未找到 question_id", code="INVALID_ANSWERS")

        if q_type in ("radio", "single"):
            answer_id = ans.get("answer_id", "")
            if not answer_id:
                raise UMUError(f"第 {idx + 1} 题（单选）必须提供 answer_id", code="INVALID_ANSWERS")
            answer_list.append(str(answer_id))
        elif q_type in ("checkbox", "multi", "multiple"):
            answer_ids = ans.get("answer_ids", [])
            if not isinstance(answer_ids, list) or not answer_ids:
                raise UMUError(f"第 {idx + 1} 题（多选）必须提供非空 answer_ids 列表", code="INVALID_ANSWERS")
            answer_list.extend(str(aid) for aid in answer_ids)
        elif q_type in ("textarea", "text", "input"):
            text = ans.get("text", "")
            if text == "":
                raise UMUError(f"第 {idx + 1} 题（文本）必须提供 text", code="INVALID_ANSWERS")
            answer_info.append({"id": str(question_id), "text": str(text)})
        elif q_type in ("number", "range"):
            value = ans.get("value")
            if value is None:
                raise UMUError(f"第 {idx + 1} 题（数值）必须提供 value", code="INVALID_ANSWERS")
            try:
                answer_number[str(question_id)] = int(value)
            except (TypeError, ValueError):
                answer_number[str(question_id)] = float(value)
        else:
            raise UMUError(f"第 {idx + 1} 题 unsupported type: {q_type}", code="INVALID_ANSWERS")

    q_payload = {
        "answerList": answer_list,
        "answerInfo": answer_info,
        "answerNumber": answer_number,
        "enrollId": 0,
        "sessionId": str(element_id),
    }

    resp = client.post(
        client.mobile_url("/ajax/insertAnswer"),
        {"q": json.dumps(q_payload, ensure_ascii=False)},
    )
    if not resp.get("status") and resp.get("error_code") != 0:
        raise UMUError(
            f"提交签到答案失败: {resp.get('error', resp.get('error_message', 'unknown'))}",
            code="CHECKIN_WITH_ANSWERS_FAILED",
        )

    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "action": "complex_checkin_completed",
        "answer_count": len(raw_answers),
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }


@umu_operation(
    name="start_exam",
    description="开始考试",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "考试小节元素 ID",
    },
)
async def start_exam(
    client: UMUClient,
    element_id: str,
) -> dict[str, Any]:
    """开始考试."""
    # 1. 获取 exam_submit_id
    exam_submit_id = _get_exam_submit_id(client, element_id)

    # 2. 获取 student_id
    student_id = ""
    try:
        r = client.get(client.desktop_url("/uapi/v1/user/get"))
        student_id = r.get("data", {}).get("student_id", "")
    except Exception:
        pass

    # 3. 调用 startExam
    if exam_submit_id and student_id:
        r = client.post(
            client.mobile_url("/megrez/exam/v1/startExam"),
            {
                "session_id": str(element_id),
                "student_id": str(student_id),
                "exam_submit_id": str(exam_submit_id),
            },
        )
        if r.get("error_code") != 0:
            raise UMUError(
                r.get("message", "开始考试失败"),
                code="START_EXAM_FAILED",
            )

        return {
            "element_id": element_id,
            "exam_submit_id": exam_submit_id,
            "student_id": student_id,
            "_next_action": "needs_user_input",
            "_suggested_action": "向用户展示考试题目，答题完成后调用 submit_exam 提交",
        }
    else:
        raise UMUError(
            "无法获取 exam_submit_id 或 student_id",
            code="EXAM_PREPARE_FAILED",
        )


@umu_operation(
    name="submit_exam",
    description="提交考试",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "考试小节元素 ID",
        "exam_submit_id": "考试提交 ID，来自 start_exam 的返回值",
        "answers_json": '考试答案 JSON（可选，可留空），格式: {"question_id": "answer"}',
    },
)
async def submit_exam(
    client: UMUClient,
    element_id: str,
    exam_submit_id: str,
    answers_json: str = "{}",
) -> dict[str, Any]:
    """提交考试."""
    # 获取 student_id
    student_id = ""
    try:
        r = client.get(client.desktop_url("/uapi/v1/user/get"))
        student_id = r.get("data", {}).get("student_id", "")
    except Exception:
        pass

    r = client.post(
        client.mobile_url("/megrez/exam/v1/submitExam"),
        {
            "session_id": str(element_id),
            "status": "2",
            "name": "",
            "submit_type": "2",
            "student_id": str(student_id),
            "exam_submit_id": str(exam_submit_id),
        },
    )

    if r.get("error_code") == 0:
        return {
            "element_id": element_id,
            "exam_submit_id": exam_submit_id,
            "_next_action": "proceed",
            "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
        }
    elif "not in testing" in str(r.get("message", "")).lower():
        return {
            "element_id": element_id,
            "_next_action": "lesson_completed",
            "_suggested_action": "考试已提交过，该小节已完成",
        }
    else:
        raise UMUError(
            r.get("message", "提交考试失败"),
            code="SUBMIT_EXAM_FAILED",
        )


@umu_operation(
    name="submit_exam_with_config",
    description="使用简化配置提交考试答案",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "考试小节元素 ID",
        "answers_config": '答案配置，格式: 用分号(;)分隔每道题的答案。单选: 单个字母(A=第1个选项, B=第2个...); 多选: 连续字母(如 BCD=选第2/3/4个选项); 开放题: 直接文本。示例: "A;BCD;我的观点是..." 注意: 考试不支持数值题。',
    },
)
async def submit_exam_with_config(
    client: UMUClient,
    element_id: str,
    answers_config: str,
) -> dict[str, Any]:
    """使用简化配置提交考试答案."""
    # 1. 获取 exam_submit_id
    exam_submit_id = _get_exam_submit_id(client, element_id)

    # 2. 获取 student_id
    student_id = ""
    try:
        r = client.get(client.desktop_url("/uapi/v1/user/get"))
        student_id = r.get("data", {}).get("student_id", "")
    except Exception:
        pass

    if not exam_submit_id or not student_id:
        raise UMUError(
            "无法获取 exam_submit_id 或 student_id",
            code="EXAM_PREPARE_FAILED",
        )

    # 3. 检查考试是否已完成
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
        el_data = r.get("data", {}) or {}
        extend = el_data.get("extend", {}) or {}
        if extend.get("learn_status") == 2:
            return {
                "element_id": element_id,
                "status": "already_completed",
                "_next_action": "lesson_completed",
                "_suggested_action": "考试小节已完成",
            }
    except Exception:
        pass

    # 4. 调用 startExam
    r = client.post(
        client.mobile_url("/megrez/exam/v1/startExam"),
        {
            "session_id": str(element_id),
            "student_id": str(student_id),
            "exam_submit_id": str(exam_submit_id),
        },
    )
    if r.get("error_code") != 0:
        msg = str(r.get("message", "")).lower()
        # 如果考试已在进行中，尝试继续
        if not ("already" in msg or "started" in msg or "status" in msg):
            raise UMUError(
                r.get("message", "开始考试失败"),
                code="START_EXAM_FAILED",
            )

    # 5. 获取考试题目
    r = client.get(
        client.mobile_url(
            "/napi/v1/quiz/question-list"
            f"?_type=1&element_id={element_id}&page=1&size=999"
        )
    )
    questions = r.get("data", {}).get("list", [])
    if not questions:
        raise UMUError("该考试没有获取到题目", code="NO_EXAM_QUESTIONS")

    # 6. 解析答案配置
    answers = _parse_answers_config(answers_config)

    # 7. 验证答案与问题结构
    is_valid, error_msg = _validate_answers_against_questions(
        questions, answers, for_exam=True
    )
    if not is_valid:
        question_summary = []
        for i, q in enumerate(questions):
            qtype = q.get("type")
            options = q.get("list", [])
            opt_text = ""
            if options:
                opt_text = ", ".join(
                    f"{chr(ord('A') + j)}={opt.get('title', '')}"
                    for j, opt in enumerate(options)
                )
            type_name = _question_type_name(qtype, is_exam=True)
            req_tag = "必填" if _is_question_required(q) else "选填"
            # 构造该题的可接受答案格式提示（考试类型: 0=单选, 1=多选, 2/3=开放题）
            if qtype == 0:
                fmt_hint = f"单个字母(A-{chr(ord('A') + len(options) - 1)})"
            elif qtype == 1:
                if options:
                    fmt_hint = f"连续字母(如 AB, A-{chr(ord('A') + len(options) - 1)})"
                else:
                    fmt_hint = "直接文本"
            elif qtype in (2, 3):
                fmt_hint = "直接文本"
            elif qtype == 4:
                fmt_hint = "直接文本"
            elif qtype == 5:
                fmt_hint = "数字"
            else:
                fmt_hint = "直接文本"
            question_summary.append(
                f"  第{i + 1}题 [{req_tag}] [{type_name}] {q.get('title', '')}"
                f"{(' | 选项: ' + opt_text) if opt_text else ''}"
                f" | 答案格式: {fmt_hint}"
            )

        raise UMUError(
            f"答案配置验证失败: {error_msg}",
            code="ANSWERS_VALIDATION_FAILED",
            details={
                "element_id": element_id,
                "total_questions": len(questions),
                "questions_summary": question_summary,
                "your_answers": answers,
                "expected_format": (
                    "用分号(;)分隔每题答案。"
                    "单选: 单个字母(如 A); "
                    "多选: 连续字母(如 BCD); "
                    "开放题: 直接文本; "
                    "数值题: 数字; "
                    "选填题: 留空跳过"
                ),
            },
        )

    # 8. 构造考试答案 JSON
    exam_answers = _build_exam_answers_json(questions, answers)

    # 9. 逐题保存答案
    for answer in exam_answers:
        try:
            client.post(
                client.mobile_url("/megrez/exam/v1/saveAnswer"),
                {
                    "session_id": str(element_id),
                    "answer_list": json.dumps([answer], ensure_ascii=False),
                    "student_id": str(student_id),
                    "exam_submit_id": str(exam_submit_id),
                },
            )
        except Exception as e:
            logger.warning("[submit_exam_with_config] saveAnswer 失败（非致命）: %s", e)
        await asyncio.sleep(0.3)

    # 10. 提交考试
    r = client.post(
        client.mobile_url("/megrez/exam/v1/submitExam"),
        {
            "session_id": str(element_id),
            "status": "2",
            "name": "",
            "submit_type": "2",
            "student_id": str(student_id),
            "exam_submit_id": str(exam_submit_id),
        },
    )

    if r.get("error_code") != 0 and "not in testing" not in str(
        r.get("message", "")
    ).lower():
        raise UMUError(
            r.get("message", "提交考试失败"),
            code="SUBMIT_EXAM_FAILED",
        )

    # 11. 执行 makeweikestatus 序列
    await _makeweikestatus_sequence(client, element_id)

    return {
        "element_id": element_id,
        "exam_submit_id": exam_submit_id,
        "total_questions": len(questions),
        "answers_summary": answers,
        "_next_action": "proceed",
        "_suggested_action": "调用 get_lesson_status 验证小节是否已完成",
    }



@umu_operation(
    name="get_lesson_status",
    description="获取单个小节的完成状态",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "element_id": "小节元素 ID",
        "group_id": "课程组 ID（可选），如果提供则同时返回课程整体进度",
    },
)
async def get_lesson_status(
    client: UMUClient,
    element_id: str,
    group_id: str = "",
) -> dict[str, Any]:
    """获取单个小节的完成状态."""
    r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
    if r.get("error_code") != 0:
        raise UMUError(
            r.get("message", "获取小节状态失败"),
            code="FETCH_LESSON_STATUS_FAILED",
        )

    data = r.get("data", {}) or {}
    extend = data.get("extend", {}) or {}
    result: dict[str, Any] = {
        "element_id": element_id,
        "session_id": data.get("session_id", element_id),
        "title": data.get("title", ""),
        "is_completed": extend.get("learn_status") == 2,
        "learn_status": extend.get("learn_status", 0),
        "status": extend.get("status", 0),
        "_next_action": "proceed",
        "_suggested_action": "查看 is_completed 判断小节是否已完成",
    }

    if group_id:
        try:
            progress_r = client.get(
                client.desktop_url(f"/ajax/course/course_progress?group_id={group_id}")
            )
            result["course_progress"] = progress_r.get("data", progress_r)
        except Exception:
            pass

    return result


@umu_operation(
    name="complete_course",
    description="一键完成单门课程的所有未完成小节",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "course_identifier": "课程标识（访问码/短域名/URL）",
        "questionnaire_answers": "问卷答案配置，格式同问卷。提供后不再跳过问卷小节。示例: \"A;BCD;我的观点是...;5\"",
        "questionnaire_answers_map": '按小节指定问卷答案，JSON 格式: {"element_id": "A;B;C", ...}。优先于 questionnaire_answers。',
        "questionnaire_answers_by_index": '按问卷序号指定答案，JSON 格式: {"1": "A;B;C", ...}。',
        "exam_answers": "考试答案配置，格式同问卷。提供后不再跳过考试小节。示例: \"A;BCD;我的观点是...\"",
        "exam_answers_map": '按小节指定考试答案，JSON 格式: {"element_id": "A;B;C", ...}。优先于 exam_answers。',
        "exam_answers_by_index": '按考试序号指定答案，JSON 格式: {"1": "A;B;C", ...}。',
        "lesson_answers_by_index": '按小节顺序指定答案（不区分类型），JSON 格式: {"1": "A;B;C", ...}。优先级最高，覆盖所有其他答案配置方式。',
        "skip_questionnaire": "是否跳过问卷小节",
        "skip_exam": "是否跳过考试小节",
    },
)
async def complete_course(
    client: UMUClient,
    course_identifier: str,
    questionnaire_answers: str | None = None,
    questionnaire_answers_map: str | None = None,
    questionnaire_answers_by_index: str | None = None,
    exam_answers: str | None = None,
    exam_answers_map: str | None = None,
    exam_answers_by_index: str | None = None,
    lesson_answers_by_index: str | None = None,
    skip_questionnaire: bool = True,
    skip_exam: bool = True,
) -> dict[str, Any]:
    """一键完成单门课程的所有未完成小节."""
    group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)

    # 1. 获取课程结构
    structure = await _get_course_structure_impl(client, group_id, s_key)
    lessons = structure.get("lessons", [])

    # 2. 如果未报名，执行报名
    enrollment_status = structure.get("enrollment_status")
    if enrollment_status == "needs_enrollment":
        enroll_id = structure.get("enroll_id")
        if not enroll_id:
            raise UMUError(
                "课程需要报名但未找到 enroll_id",
                code="ENROLL_ID_MISSING",
            )
        await _enroll_course_impl(client, enroll_id)
        # 重新获取结构
        structure = await _get_course_structure_impl(client, group_id, s_key)
        lessons = structure.get("lessons", [])

    # 3. 检查报名表单
    needs_enroll_form, form_summary = _check_needs_enroll_form(
        client, group_id, s_key, structure.get("is_enrolled", 1)
    )
    if needs_enroll_form:
        return {
            "group_id": group_id,
            "s_key": s_key,
            "enroll_status": "needs_enroll_form",
            "enroll_form_required": True,
            "enroll_form_summary": form_summary,
            "_next_action": "needs_enroll_form",
            "_suggested_action": "先调用 get_enroll_form 获取报名表单，然后调用 submit_enroll_form 提交",
        }

    # 4. 构建 lesson_index 映射
    lesson_index_map = _build_lesson_index_map(lessons)

    # 5. 逐个完成未完成小节
    completed: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, Any]] = []

    for idx, lesson in enumerate(lessons, start=1):
        element_id = lesson.get("element_id")
        ltype = lesson.get("type")
        title = lesson.get("title", "")
        advance = lesson.get("advance", 0)
        is_completed = lesson.get("is_completed", False)

        if not element_id or is_completed:
            continue

        try:
            if ltype in (11, 13, 14, 15):
                # 浏览类型
                duration_seconds = 0
                if ltype == 14:
                    vlt_min = lesson.get("vlt_min", 0) or 0
                    if vlt_min > 0:
                        duration_seconds = int(vlt_min)
                await browse_lesson(client, element_id, duration_seconds=duration_seconds)
                completed.append(title)
            elif ltype == 6:
                # 签到
                if advance == 1:
                    # 复杂签到，需要答案
                    if lesson.get("is_rating"):
                        skipped.append(f"评分签到: {title}")
                    else:
                        skipped.append(f"复杂签到: {title}")
                else:
                    await check_in(client, element_id)
                    completed.append(title)
            elif ltype == 1:
                # 问卷
                if skip_questionnaire:
                    skipped.append(f"问卷: {title}")
                    continue
                answers_config = _resolve_lesson_answers_config(
                    idx,
                    lesson,
                    lesson_index_map,
                    lesson_answers_by_index=lesson_answers_by_index,
                    questionnaire_answers=questionnaire_answers,
                    questionnaire_answers_map=questionnaire_answers_map,
                    questionnaire_answers_by_index=questionnaire_answers_by_index,
                )
                if answers_config is None:
                    skipped.append(f"问卷: {title}")
                    continue
                await submit_questionnaire_with_config(client, element_id, answers_config)
                completed.append(title)
            elif ltype == 10:
                # 考试
                if skip_exam:
                    skipped.append(f"考试: {title}")
                    continue
                answers_config = _resolve_lesson_answers_config(
                    idx,
                    lesson,
                    lesson_index_map,
                    lesson_answers_by_index=lesson_answers_by_index,
                    exam_answers=exam_answers,
                    exam_answers_map=exam_answers_map,
                    exam_answers_by_index=exam_answers_by_index,
                )
                if answers_config is None:
                    skipped.append(f"考试: {title}")
                    continue
                await submit_exam_with_config(client, element_id, answers_config)
                completed.append(title)
            else:
                skipped.append(f"未知类型({ltype}): {title}")

        except Exception as e:
            logger.error("完成小节失败: %s, error=%s", title, e)
            errors.append({"title": title, "error": str(e)})

        await asyncio.sleep(0.5)

    # 6. 获取最终进度
    try:
        progress_r = client.get(
            client.desktop_url(f"/ajax/course/course_progress?group_id={group_id}")
        )
        progress_data = progress_r.get("data", progress_r)
    except Exception:
        progress_data = {}

    return {
        "group_id": group_id,
        "course_identifier": course_identifier,
        "completed_lessons": completed,
        "skipped_lessons": skipped,
        "errors": errors,
        "course_progress": progress_data,
        "_next_action": "proceed",
        "_suggested_action": "查看 course_progress 确认完成率；对于 skipped_lessons 可补充答案后单独完成",
    }


@umu_operation(
    name="batch_complete_course",
    description="批量完成课程 — 为多个账号自动完成指定课程",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "file_path": "账号文件路径（CSV 或 JSON）",
        "course_identifier": "课程标识（访问码/短域名/URL）",
        "questionnaire_answers": "问卷答案配置",
        "questionnaire_answers_map": "按小节指定问卷答案",
        "questionnaire_answers_by_index": "按问卷序号指定答案",
        "exam_answers": "考试答案配置",
        "exam_answers_map": "按小节指定考试答案",
        "exam_answers_by_index": "按考试序号指定答案",
        "lesson_answers_by_index": "按小节顺序指定答案",
        "skip_questionnaire": "是否跳过问卷小节",
        "skip_exam": "是否跳过考试小节",
        "max_concurrency": "最大并发数（1-10）",
        "delay_between_accounts": "账号间启动延迟（秒）",
    },
)
async def batch_complete_course(
    client: UMUClient,
    file_path: str,
    course_identifier: str,
    questionnaire_answers: str | None = None,
    questionnaire_answers_map: str | None = None,
    questionnaire_answers_by_index: str | None = None,
    exam_answers: str | None = None,
    exam_answers_map: str | None = None,
    exam_answers_by_index: str | None = None,
    lesson_answers_by_index: str | None = None,
    skip_questionnaire: bool = True,
    skip_exam: bool = True,
    max_concurrency: int = 3,
    delay_between_accounts: float = 1.0,
) -> dict[str, Any]:
    """批量完成课程 — 为多个账号自动完成指定课程."""

    # 1. 导入账号
    importer = AccountImporter(file_path=file_path)
    accounts = importer.load_accounts()
    if not accounts:
        raise UMUError("账号文件为空或解析失败", code="NO_ACCOUNTS")

    # 2. 解析课程标识
    group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)

    # 3. 定义每个账号的执行函数
    async def run_for_account(account: dict[str, str]) -> dict[str, Any]:
        account_session = None
        try:
            # 创建新会话并登录
            from ...adapters.mcp.session import SessionManager
            session_manager = SessionManager()
            account_session = await session_manager.create_session()
            await session_manager.login_session(
                account_session,
                account["username"],
                account["password"],
            )
            account_client = await session_manager.get_session(account_session)

            result = await complete_course(
                account_client,
                course_identifier,
                questionnaire_answers=questionnaire_answers,
                questionnaire_answers_map=questionnaire_answers_map,
                questionnaire_answers_by_index=questionnaire_answers_by_index,
                exam_answers=exam_answers,
                exam_answers_map=exam_answers_map,
                exam_answers_by_index=exam_answers_by_index,
                lesson_answers_by_index=lesson_answers_by_index,
                skip_questionnaire=skip_questionnaire,
                skip_exam=skip_exam,
            )

            await session_manager.destroy_session(account_session)
            return {
                "username": account["username"],
                "success": True,
                "result": result,
            }
        except Exception as e:
            logger.error("批量完成课程失败 [%s]: %s", account.get("username", ""), e)
            if account_session:
                try:
                    from ...adapters.mcp.session import SessionManager
                    session_manager = SessionManager()
                    await session_manager.destroy_session(account_session)
                except Exception:
                    pass
            return {
                "username": account.get("username", ""),
                "success": False,
                "error": str(e),
            }

    # 4. 并发执行
    semaphore = asyncio.Semaphore(max(1, min(10, max_concurrency)))

    async def bounded_run(account: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            await asyncio.sleep(delay_between_accounts)
            return await run_for_account(account)

    tasks = [bounded_run(account) for account in accounts]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r.get("success"))
    failure_count = len(results) - success_count

    return {
        "course_identifier": course_identifier,
        "total_accounts": len(accounts),
        "success_count": success_count,
        "failure_count": failure_count,
        "results": results,
        "_next_action": "proceed",
        "_suggested_action": "查看 results 中的失败账号明细并单独处理",
    }
