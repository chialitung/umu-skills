"""UMU 学员端 MCP Server.

将 UMU 学习平台的学员操作暴露为原子化 MCP Tools，供 AI 自主编排完成课程流程。

Usage:
    # 启动 MCP Server（默认）
    python -m umu_sdk.mcp.server_student

    # 或使用 CLI
    umu-mcp-student

Environment Variables:
    UMU_BASE_URL: UMU 基础 URL (默认: https://www.umu.cn)
    UMU_STUDENT_USERNAME: 学生登录用户名
    UMU_STUDENT_PASSWORD: 学生登录密码
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from umu_sdk.core.client import UMUClient
from umu_sdk.core.encrypt import encrypt_password
from . import prompts
from .batch import AccountImporter, AccountSource, BatchExecutor
from .session import SessionManager

# ---------------------------------------------------------------------------
# 全局实例（由 lifespan 管理）
# ---------------------------------------------------------------------------
_umu_client: UMUClient | None = None
_session_manager: SessionManager | None = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """应用生命周期管理.

    启动时初始化会话管理器并创建默认会话；关闭时释放所有会话资源.
    默认从 UMU_STUDENT_USERNAME / UMU_STUDENT_PASSWORD 读取学生账号自动登录.
    未配置凭据时正常启动，提示手动调用 stu_login 登录.
    """
    global _umu_client, _session_manager

    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    username = os.getenv("UMU_STUDENT_USERNAME")
    password = os.getenv("UMU_STUDENT_PASSWORD")

    # 创建会话管理器
    _session_manager = SessionManager(
        base_url=base_url,
        environment="default",
        enable_environment_check=True,
    )

    # 创建默认会话
    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    # 如果有凭据，自动登录默认会话；否则正常启动
    if username and password:
        try:
            await _session_manager.login_session(default_session.session_id, username, password)
            print(f"[MCP Student] 默认会话已自动登录: {username}")
        except Exception as e:
            print(f"[MCP Student] 默认会话自动登录失败: {e}")
    else:
        print("[MCP Student] 未配置学生账号凭据，请调用 stu_login 或 stu_create_session")

    print(f"[MCP Student] UMU 学员端服务已启动，目标: {base_url}")

    yield {"client": _umu_client, "session_manager": _session_manager}

    # 清理所有会话
    if _session_manager:
        _session_manager.close_all()
        _session_manager = None
    _umu_client = None
    print("[MCP Student] UMU 学员端服务已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-student",
    instructions="""UMU 学习平台学员端 MCP 服务。

提供课程学习相关的原子化操作，包括：
- 课程结构查询（报名状态、小节列表）
- 学习进度查询
- 课程报名
- 小节完成（浏览、问卷、签到、考试）
- 状态验证

AI 使用本服务时，应先调用 stu_get_course_structure 获取课程结构，
然后逐个完成未完成的小节，每次操作后验证状态。
""",
    lifespan=app_lifespan,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_client(session_id: str | None = None) -> UMUClient:
    """获取客户端实例.

    Args:
        session_id: 会话 ID。如果提供，从会话池中获取对应客户端；
                   如果不提供，使用默认全局客户端（向后兼容）。

    Raises:
        RuntimeError: 客户端未初始化或会话不存在/已过期
    """
    if session_id:
        if _session_manager is None:
            raise RuntimeError("会话管理器未初始化")
        session = _session_manager.get_session_sync(session_id)
        if session is None:
            raise RuntimeError(f"会话不存在或已过期: {session_id}")
        return session.client

    # 向后兼容：使用默认全局客户端
    if _umu_client is None:
        raise RuntimeError("UMU 客户端未初始化，请先登录")
    return _umu_client


def _ok(
    data: Any = None,
    next_action: str = "proceed",
    suggested_action: str = "",
    **kwargs: Any,
) -> str:
    """构造成功返回结构."""
    result: dict[str, Any] = {
        "success": True,
        "data": data,
        "error_code": "",
        "error_message": "",
        "suggested_action": suggested_action,
        "next_action": next_action,
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


def _err(
    error_code: str,
    error_message: str,
    suggested_action: str = "",
    data: Any = None,
    **kwargs: Any,
) -> str:
    """构造失败返回结构."""
    result: dict[str, Any] = {
        "success": False,
        "data": data,
        "error_code": error_code,
        "error_message": error_message,
        "suggested_action": suggested_action,
        "next_action": "",
    }
    result.update(kwargs)
    return json.dumps(result, ensure_ascii=False, default=str)


def _get_html(client: UMUClient, url: str) -> str:
    """获取页面 HTML 内容（用于从页面提取 enroll_id 等）.

    自动跟随重定向（httpx.Client 初始化时已配置 follow_redirects=True）.
    """
    headers = client.auth.get_auth_headers()
    headers["Accept"] = "text/html,application/xhtml+xml"
    resp = client.http.get(url, headers=headers, timeout=client.timeout)
    return resp.text


def _resolve_course_identifier(
    client: UMUClient, identifier: str
) -> tuple[str, str, str]:
    """解析课程标识符，提取 group_id 和 s_key.

    支持三种输入格式：
    1. 访问码: "aet504"
    2. 短域名: "aet504.umu.cn"
    3. 完整 URL: "https://<domain>/course/?groupId=7324740&sKey=7fea"

    注意：纯 groupId（如 "7324740"）不支持，因为无法自动获取 sKey，
    而 sKey 是报名检测的必需参数。

    Returns:
        (group_id, s_key, resolved_url)

    Raises:
        ValueError: 无法解析时抛出
    """
    url = identifier.strip()

    # 格式1: 访问码（字母+数字混合，不含 . 和 /）
    # 排除纯数字（那是 groupId），访问码必须包含至少一个字母
    if re.match(r"^(?=.*[a-zA-Z])[a-zA-Z0-9]+$", url):
        url = f"https://{url}.umu.cn"

    # 格式2: 短域名（不含协议头）
    elif re.match(r"^[a-zA-Z0-9]+\.umu\.cn$", url):
        url = f"https://{url}"

    # 注意：纯 groupId（如 "7324740"）不再支持，因为无法自动获取 sKey，
    # 而 sKey 是报名检测的必需参数。请使用访问码、短域名或带 sKey 的完整 URL。

    # 尝试从 URL 中提取参数
    parsed_match = re.search(r"groupId[=:](\d+)", url, re.IGNORECASE)
    skey_match = re.search(r"sKey[=:]([a-zA-Z0-9]+)", url, re.IGNORECASE)

    if parsed_match:
        group_id = parsed_match.group(1)
        s_key = skey_match.group(1) if skey_match else ""
        return group_id, s_key, url

    # 需要通过 HTTP 请求获取重定向后的真实 URL
    try:
        h = client.auth.get_auth_headers() if client.auth.is_authenticated() else {}
        h["Accept"] = "text/html"
        resp = client.http.get(url, headers=h, timeout=client.timeout)
        final_url = str(resp.url)

        parsed_match = re.search(r"groupId[=:](\d+)", final_url, re.IGNORECASE)
        skey_match = re.search(r"sKey[=:]([a-zA-Z0-9]+)", final_url, re.IGNORECASE)

        if parsed_match:
            group_id = parsed_match.group(1)
            s_key = skey_match.group(1) if skey_match else ""
            return group_id, s_key, final_url
    except Exception:
        pass

    raise ValueError(f"无法解析课程标识符: {identifier}")


def _check_needs_enroll(client: UMUClient, group_id: str, s_key: str = "") -> tuple[bool, str | None]:
    """检测课程是否需要报名.

    多层检测策略：
    1. 访问课程页面（带 sKey），检查是否被重定向到 course/pay
    2. 如果被重定向到 access-denied，也判定为需要报名
    3. 从 course/pay 页面 HTML 提取 enrollId（大写 I）
    4. 检查 element-list / course/detail API 是否返回权限错误

    Args:
        s_key: 课程 URL 中的 sKey 参数，有助于正确提取 enrollId

    Returns:
        (是否需要报名, enroll_id 或 None)
    """
    # 方法1: 访问课程页面，检查重定向情况
    try:
        h = client.auth.get_auth_headers()
        h["Accept"] = "text/html"

        # 构造 URL，优先使用 sKey
        course_url = client.desktop_url(f"/course/?groupId={group_id}")
        if s_key:
            course_url += f"&sKey={s_key}"

        resp = client.http.get(course_url, headers=h, timeout=client.timeout)
        final_url = str(resp.url)
        html = resp.text

        # 如果最终 URL 包含 course/pay，说明需要报名
        if "course/pay" in final_url:
            # 从 pay 页面 HTML 提取 enrollId（注意大写 I）
            matches = re.findall(r'enrollId["\']?\s*[:=]\s*["\']?(\d+)', html)
            if matches:
                real_ids = [m for m in matches if m != "0"]
                if real_ids:
                    return True, real_ids[0]
            return True, None

        # 如果被重定向到 access-denied，也是权限不足的表现
        if "access-denied" in final_url:
            # 尝试直接访问 course/pay 页面获取 enrollId（带 sKey）
            try:
                pay_url = client.desktop_url(f"/course/pay?groupId={group_id}")
                if s_key:
                    pay_url += f"&sKey={s_key}"
                pay_resp = client.http.get(pay_url, headers=h, timeout=client.timeout)
                pay_html = pay_resp.text
                matches = re.findall(r'enrollId["\']?\s*[:=]\s*["\']?(\d+)', pay_html)
                if matches:
                    real_ids = [m for m in matches if m != "0"]
                    if real_ids:
                        return True, real_ids[0]
            except Exception:
                pass
            return True, None

        # 检查页面 HTML 中是否包含有效 enroll_id
        matches = re.findall(r'enroll_id["\']?\s*[:=]\s*["\']?(\d+)', html, re.IGNORECASE)
        if matches:
            real_ids = [m for m in matches if m != "0"]
            if real_ids:
                return True, real_ids[0]
    except Exception:
        pass

    # 方法2: 检查 element-list API 是否返回权限错误
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/element-list?group_id={group_id}"))
        if r.get("error_code") in (10003, 30007):
            return True, None
    except Exception:
        pass

    # 方法3: 检查 course/detail 是否返回权限错误
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/course/detail?group_id={group_id}"))
        if r.get("error_code") in (10003, 30007):
            return True, None
    except Exception:
        pass

    return False, None


def _makeweikestatus_sequence(
    client: UMUClient, element_id: str | int, extras: dict[str, dict] | None = None
) -> dict[str, Any]:
    """执行 makeweikestatus 状态机序列.

    标准序列: init(0) -> start(1) -> playing(3) -> achieve(3, vlt_status=1) -> end(2)

    Returns:
        执行摘要，包含每个状态的成功/失败情况
    """
    sequence = [
        ("init", "0", {}),
        ("start", "1", {}),
        ("playing", "3", {}),
        ("achieve", "3", {"vlt_status": "1"}),
        ("end", "2", {}),
    ]
    results: list[dict[str, Any]] = []
    for action, status, extra in sequence:
        data: dict[str, Any] = {"sessionId": str(element_id), "status": status, "action": action}
        data.update(extra)
        if extras and action in extras:
            data.update(extras[action])
        try:
            client.post(client.mobile_url("/api/session/makeweikestatus"), data)
            results.append({"action": action, "status": "ok"})
        except Exception as e:
            results.append({"action": action, "status": "failed", "error": str(e)})
            print(f"[makeweikestatus] {action} 失败: {e}")
        time.sleep(0.3)

    failed = [r for r in results if r["status"] == "failed"]
    return {
        "all_succeeded": len(failed) == 0,
        "failed_actions": [r["action"] for r in failed],
        "results": results,
    }


# ---------------------------------------------------------------------------
# Tools: 认证
# ---------------------------------------------------------------------------

@mcp.tool()
async def stu_login(
    username: Annotated[str, Field(description="用户名/邮箱/手机号")],
    password: Annotated[str, Field(description="明文密码，服务端会自动加密")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中登录；如果不提供，在默认会话中登录。",
        ),
    ] = None,
) -> str:
    """使用用户名密码登录 UMU 学习平台.

    触发条件：当用户需要登录或当前认证已过期时调用。
    前置依赖：无。
    副作用：会设置认证 Token，后续 Tool 可以使用相同 session_id 复用此 Token。
    """
    client = _get_client(session_id)
    try:
        token = client.login(username, password)
        # 更新会话用户名
        if session_id and _session_manager:
            s = _session_manager.get_session_sync(session_id)
            if s:
                s.username = username
        # 登录后获取 student_id
        try:
            r = client.get(client.desktop_url("/uapi/v1/user/get"))
            student_id = r.get("data", {}).get("student_id", "")
        except Exception:
            student_id = ""
        return _ok(
            data={"token": token, "student_id": student_id, "session_id": session_id},
            next_action="proceed",
            suggested_action="现在可以调用其他学习相关 Tool",
        )
    except Exception as e:
        return _err(
            error_code="AUTH_FAILED",
            error_message=str(e),
            suggested_action="检查用户名密码是否正确，或稍后重试",
        )


@mcp.tool()
async def stu_check_auth(
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，检查指定会话的认证状态；如果不提供，检查默认会话。",
        ),
    ] = None,
) -> str:
    """检查当前是否已认证.

    触发条件：在执行学习操作前，确认当前登录状态。
    前置依赖：无。
    副作用：无。
    """
    client = _get_client(session_id)
    try:
        is_auth = client.auth.is_authenticated()
        token = client.auth.get_token()
        if is_auth and token:
            return _ok(
                data={
                    "is_authenticated": True,
                    "token_preview": token[:20] + "...",
                },
                next_action="proceed",
                suggested_action="当前已登录，可以正常调用学习相关 Tool",
            )
        else:
            return _err(
                error_code="NOT_AUTHENTICATED",
                error_message="当前未登录或 Token 已过期",
                suggested_action="调用 stu_login 重新登录",
            )
    except Exception as e:
        return _err(
            error_code="AUTH_CHECK_FAILED",
            error_message=str(e),
            suggested_action="调用 stu_login 重新登录",
        )


# ---------------------------------------------------------------------------
# Tools: 查结构（只读查询）
# ---------------------------------------------------------------------------

@mcp.tool()
async def stu_resolve_course_url(
    course_identifier: Annotated[
        str,
        Field(description="课程链接或访问码。支持格式：纯groupId（如 7324740）、访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """解析课程标识符，提取 group_id 和 s_key.

    触发条件：当用户提供课程链接或访问码时，先调用此接口解析出标准参数。
    前置依赖：无（不需要登录）。
    副作用：无（只读查询）。

    支持四种输入格式：
    1. 纯 groupId: "7324740"
    2. 访问码: "aet504"
    3. 短域名: "aet504.umu.cn"
    4. 完整 URL: "https://<domain>/course/?groupId=7324740&sKey=7fea"

    访问码和短域名会被重定向到真实课程链接，从中提取 group_id 和 s_key。
    """
    client = _get_client(session_id)
    try:
        group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)
        return _ok(
            data={
                "group_id": group_id,
                "s_key": s_key,
                "resolved_url": resolved_url,
                "input": course_identifier,
            },
            next_action="proceed",
            suggested_action="调用 stu_get_course_structure(group_id, s_key) 获取课程结构",
        )
    except ValueError as e:
        return _err(
            error_code="RESOLVE_URL_FAILED",
            error_message=str(e),
            suggested_action="检查链接或访问码是否正确",
        )
    except Exception as e:
        return _err(
            error_code="RESOLVE_URL_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接或链接格式",
        )


@mcp.tool()
async def stu_get_course_structure(
    course_identifier: Annotated[
        str,
        Field(description="课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）。注意：纯groupId不支持，因为无法自动获取sKey。"),
    ],
    include_question_preview: Annotated[
        bool,
        Field(
            default=False,
            description="是否包含问卷/考试小节的题目预览信息。开启后会额外获取每道问卷/考试小节的题目数量和类型分布，帮助提前准备答案。",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程完整结构，包括报名状态和所有小节列表.

    触发条件：当用户想要学习某门课程时，必须先调用此接口了解课程全貌。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：无（只读查询）。

    支持三种输入格式：
    1. 访问码: "aet504"（推荐）
    2. 短域名: "aet504.umu.cn"
    3. 完整 URL: "https://<domain>/course/?groupId=7324740&sKey=7fea"

    注意：纯 groupId（如 "7324740"）不支持，因为无法自动获取 sKey。
    访问码和短域名需要网络解析，会多一次 HTTP 请求。

    返回值说明：
    - enrollment_status: "enrolled"(已报名) / "needs_enrollment"(需要报名) / "unknown"
    - lessons: 小节列表，每个包含 element_id, title, type, is_completed 等
      - type=1: 问卷 (含 questionnaire_index: 第几个问卷)
      - type=6 advance=0: 普通签到
      - type=6 advance=1: 评分签到
      - type=10: 考试 (含 exam_index: 第几个考试)
      - type=11: 视频
      - type=13: 文章
      - type=14: 文档/PPT
      - type=15: 图文
    - 当 include_question_preview=True 时，问卷/考试小节会额外包含:
      - question_count: 题目数量
      - question_types: 各类型题目数量统计
      - questions_preview: 每道题的简化预览（类型、必填、标题）
    """
    client = _get_client(session_id)

    # 解析课程标识符
    try:
        group_id, s_key, resolved_url = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        return _err(
            error_code="INVALID_COURSE_IDENTIFIER",
            error_message=str(e),
            suggested_action="提供有效的课程链接、访问码或 groupId",
        )

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
            client.mobile_url(f"/uapi/v2/element/list?"
            f"page=1&size=100&parent_id={group_id}&get_draft=0")
        )
        if r.get("error_code") != 0:
            return _err(
                error_code="COURSE_NOT_ACCESSIBLE",
                error_message=f"无法获取课程元素: {r.get('message', '未知错误')}",
                suggested_action="检查课程是否需要报名，或 group_id 是否正确",
            )
        elements = r.get("data", {}).get("list", [])
    except Exception as e:
        return _err(
            error_code="FETCH_COURSE_FAILED",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )

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
                            f"/napi/v1/quiz/question-list"
                            f"?_type=1&element_id={eid}&page=1&size=999"
                        )
                    )
                    questions = r.get("data", {}).get("list", [])
                    type_counts: dict[str, int] = {}
                    preview: list[dict[str, Any]] = []
                    for q in questions:
                        qtype = q.get("type")
                        tname = _question_type_name(qtype, is_exam=True)
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
        elif etype == 11:  # 视频
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
    completed = sum(1 for l in lessons if l.get("is_completed"))

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
        return _ok(
            data=data,
            next_action="needs_enrollment",
            suggested_action="课程需要报名，调用 stu_enroll_course(enroll_id) 报名后再继续学习",
        )

    return _ok(
        data=data,
        next_action="proceed",
        suggested_action="对每个 is_completed=False 的小节调用对应完成操作",
    )


@mcp.tool()
async def stu_get_learning_progress(
    course_identifier: Annotated[
        str,
        Field(
            description="课程标识。支持格式：\n"
            "1. 访问码（推荐），如 'aet504'\n"
            "2. 短域名，如 'aet504.umu.cn'\n"
            "3. 完整 URL，如 'https://<domain>/course/?groupId=7324740&sKey=7fea'\n"
            "注意：纯 groupId 不支持，因为无法自动获取 sKey。"
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取课程学习进度.

    触发条件：想了解课程完成率，或在小节操作后验证进度。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：无（只读查询）。

    注意：这是唯一可靠的进度来源。makeweikestatus 的返回状态不等于课程完成状态。
    """
    client = _get_client(session_id)
    group_id, _, _ = _resolve_course_identifier(client, course_identifier)

    try:
        r = client.get(
            client.desktop_url(f"/uapi/v1/course/get-learning-progress?group_id={group_id}")
        )
        if r.get("error_code") != 0:
            return _err(
                error_code="FETCH_PROGRESS_FAILED",
                error_message=r.get("message", "获取进度失败"),
                suggested_action="检查 group_id 是否正确",
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

        return _ok(
            data={
                "group_id": group_id,
                "complete_rate": round(rate, 1),
                "is_fully_completed": rate >= 100,
                "session_stats": stat_summary,
            },
            next_action="lesson_completed" if rate >= 100 else "proceed",
            suggested_action="如果完成率已达 100%，课程已完成" if rate >= 100 else "继续完成剩余小节",
        )
    except Exception as e:
        return _err(
            error_code="FETCH_PROGRESS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )


@mcp.tool()
async def stu_get_my_courses(
    page: Annotated[
        int,
        Field(default=1, description="页码，默认第1页"),
    ] = 1,
    page_size: Annotated[
        int,
        Field(default=20, description="每页数量，默认20"),
    ] = 20,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取当前用户已加入的课程列表.

    触发条件：当用户没有提供 group_id，或 AI 需要主动发现用户的课程时调用。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：无（只读查询）。

    返回的课程列表包含 group_id、标题、封面图、完成状态等信息。
    获取 group_id 后，可调用 stu_get_course_structure 获取课程详情。
    """
    client = _get_client(session_id)
    courses: list[dict[str, Any]] = []

    # 尝试多个可能的 API 端点
    endpoints = [
        client.desktop_url(f"/uapi/v1/course/list-my-course?page={page}&size={page_size}"),
        client.mobile_url(f"/uapi/v2/course/list-my-course?page={page}&size={page_size}"),
        client.desktop_url(f"/uapi/v1/course/my-courses?page={page}&size={page_size}"),
    ]

    last_error = ""
    for url in endpoints:
        try:
            r = client.get(url)
            if r.get("error_code") == 0:
                data = r.get("data", {})
                items = data.get("list", []) if isinstance(data, dict) else data
                for c in items:
                    courses.append({
                        "group_id": str(c.get("group_id", c.get("id", ""))),
                        "title": c.get("title", ""),
                        "cover_url": c.get("cover_url", c.get("cover", "")),
                        "status": c.get("status", ""),
                        "is_finished": c.get("is_finished", False),
                        "complete_rate": c.get("complete_rate", 0),
                    })
                return _ok(
                    data={
                        "total": len(courses),
                        "page": page,
                        "page_size": page_size,
                        "courses": courses,
                    },
                    next_action="proceed",
                    suggested_action="选择要学习的课程，调用 stu_get_course_structure 获取详情",
                )
            else:
                last_error = r.get("message", "未知错误")
        except Exception as e:
            last_error = str(e)
            continue

    return _err(
        error_code="FETCH_MY_COURSES_FAILED",
        error_message=f"无法获取课程列表: {last_error}",
        suggested_action="检查网络连接和认证状态，或确认 API 端点是否正确",
    )


# ---------------------------------------------------------------------------
# Tools: 原子操作
# ---------------------------------------------------------------------------

@mcp.tool()
async def stu_enroll_course(
    enroll_id: Annotated[str, Field(description="报名 ID，来自 stu_get_course_structure 返回的 enroll_id")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """报名课程.

    触发条件：当 stu_get_course_structure 返回 needs_enrollment=True 时调用。
    前置依赖：需先调用 stu_get_course_structure 获取 enroll_id。
    副作用：报名成功后可以正常访问课程内容和完成小节。

    注意：免费课程报名后 pay_status 也会返回 "success"。
    """
    client = _get_client(session_id)
    try:
        r = client.post(
            client.mobile_url("/ajax/verify/auto"),
            {"enroll_id": str(enroll_id)},
        )
        if r.get("error_code") == 0 or r.get("status") is True:
            data = r.get("data", {})
            is_enrolled = data.get("is_enrolled")
            pay_status = data.get("pay_status", "")
            return _ok(
                data={
                    "is_enrolled": is_enrolled,
                    "pay_status": pay_status,
                },
                next_action="proceed",
                suggested_action="报名成功，现在可以调用 stu_get_course_structure 获取课程结构并学习",
            )
        else:
            return _err(
                error_code="ENROLL_FAILED",
                error_message=r.get("message", "报名失败"),
                suggested_action="检查 enroll_id 是否正确，或课程是否需要付费",
            )
    except Exception as e:
        return _err(
            error_code="ENROLL_ERROR",
            error_message=str(e),
            suggested_action="稍后重试，或检查网络连接",
        )


@mcp.tool()
async def stu_browse_lesson(
    element_id: Annotated[str, Field(description="小节元素 ID，来自 stu_get_course_structure 的 element_id")],
    duration_seconds: Annotated[
        int,
        Field(
            default=0,
            description="模拟浏览时长（秒），用于有最小学时限制的文档。默认 0 表示使用标准序列。",
        ),
    ] = 0,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """完成浏览类型小节（视频、文章、图文、文档）.

    触发条件：当小节 type=11(视频)/13(文章)/15(图文)/14(文档) 时调用。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：会模拟浏览/观看行为，发送 makeweikestatus 状态序列。

    注意：
    - 文档(type=14)如有 vlt_min > 0，会自动传入 duration_seconds
    - 操作完成后应调用 stu_get_lesson_status 验证是否真的完成
    """
    client = _get_client(session_id)
    try:
        extras: dict[str, dict] = {}
        if duration_seconds > 0:
            extras["playing"] = {"left_time": str(duration_seconds)}
            extras["achieve"] = {"left_time": str(duration_seconds), "vlt_status": "1"}

        _makeweikestatus_sequence(client, element_id, extras)
        return _ok(
            data={"element_id": element_id, "action": "browse_completed"},
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="BROWSE_FAILED",
            error_message=str(e),
            suggested_action="检查 element_id 是否正确",
        )


@mcp.tool()
async def stu_get_questionnaire_questions(
    element_id: Annotated[str, Field(description="问卷小节元素 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取问卷的题目列表.

    触发条件：当需要完成问卷(type=1)小节时，先调用此接口获取题目，再向用户展示题目。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：无（只读查询）。

    注意：问卷答案必须由用户提供，不要自动猜测。获取题目后向用户展示，获得答案后再调用 stu_submit_questionnaire。
    """
    client = _get_client(session_id)
    try:
        r = client.get(
            client.desktop_url(f"/uapi/v1/poll/question-list?"
            f"element_id={element_id}&page=1&size=999")
        )
        if r.get("error_code") != 0:
            return _err(
                error_code="FETCH_QUESTIONS_FAILED",
                error_message=r.get("message", "获取题目失败"),
                suggested_action="检查 element_id 是否正确，或课程是否需要报名",
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

        return _ok(
            data={
                "element_id": element_id,
                "total_questions": len(simplified),
                "questions": simplified,
                "answer_format_example": answer_example,
                "answer_format_note": "提交时将所有 question 的 value 作为 JSON 字符串传入 answers_json 参数",
            },
            next_action="needs_user_input",
            suggested_action="向用户展示题目和选项，获得答案后按 answer_format_example 格式构造 JSON 调用 stu_submit_questionnaire",
        )
    except Exception as e:
        return _err(
            error_code="FETCH_QUESTIONS_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )


def _question_type_name(qtype: int | None, is_exam: bool = False) -> str:
    """问卷/考试题目类型名称.

    问卷 API 和考试 API 返回的 type 值不同:
    - 问卷: 2=单选, 3=多选, 4=文本, 5=评分
    - 考试: 0=单选, 1=多选, 3=文本
    """
    if is_exam:
        names = {0: "单选", 1: "多选", 3: "文本"}
    else:
        names = {2: "单选", 3: "多选", 4: "文本", 5: "评分"}
    return names.get(qtype or 0, "未知")


def _get_exam_submit_id(client: UMUClient, element_id: str) -> str | None:
    """获取考试提交 ID (exam_submit_id)，多层 fallback.

    尝试顺序:
    1. 从 element detail 的 setup 中获取
    2. 从 share_url 页面 HTML 中提取
    3. 从 /exam/?sessionId={id} 页面 HTML 中提取
    4. 从 /exam/?examId={id} 页面 HTML 中提取

    Args:
        client: UMUClient 实例
        element_id: 考试小节元素 ID

    Returns:
        exam_submit_id 字符串，或 None（无法获取）
    """
    # 1. 获取 element detail（包含 share_url 和 exam_id）
    element_data: dict[str, Any] = {}
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
        element_data = r.get("data", {}) or {}
    except Exception:
        pass

    exam_submit_id: str | None = None
    exam_id: str | None = None
    share_url = element_data.get("share_url", "")
    setup = element_data.get("setup", {}) or {}
    if setup:
        exam_id = setup.get("exam_id") or setup.get("id", "")

    # Fallback 1: 从 share_url 页面提取
    if share_url and not exam_submit_id:
        try:
            html = _get_html(client, share_url)
            matches = re.findall(
                r'exam_submit_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
                html,
                re.IGNORECASE,
            )
            if matches:
                exam_submit_id = matches[0]
        except Exception:
            pass

    # Fallback 2: 从 exam 页面直接获取
    if not exam_submit_id:
        try:
            html = _get_html(
                client, client.desktop_url(f"/exam/?sessionId={element_id}")
            )
            matches = re.findall(
                r'exam_submit_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
                html,
                re.IGNORECASE,
            )
            if matches:
                exam_submit_id = matches[0]
        except Exception:
            pass

    # Fallback 3: 尝试通过 exam_id 构造 exam 页面 URL
    if exam_id and not exam_submit_id:
        try:
            html = _get_html(
                client, client.desktop_url(f"/exam/?examId={exam_id}")
            )
            matches = re.findall(
                r'exam_submit_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
                html,
                re.IGNORECASE,
            )
            if matches:
                exam_submit_id = matches[0]
        except Exception:
            pass

    return exam_submit_id


# ---------------------------------------------------------------------------
# 辅助函数: 答案配置解析/验证/构建
# ---------------------------------------------------------------------------


def _parse_answers_config(config_str: str) -> list[str]:
    """将答案配置字符串解析为列表.

    格式: 用分号分隔各题答案，例如: ``A;BCD;开放答案;5``

    Args:
        config_str: 用户提供的答案配置字符串

    Returns:
        每题答案的字符串列表
    """
    if not config_str or not config_str.strip():
        return []
    return [a.strip() for a in config_str.split(";")]


def _resolve_answers_config_for_element(
    element_id: str,
    default_config: str | None,
    config_map: dict[str, str] | None,
) -> str | None:
    """根据小节 element_id 解析对应的答案配置.

    优先级: ``config_map[element_id]`` > ``default_config`` > ``None``

    Args:
        element_id: 小节元素 ID
        default_config: 默认答案配置（所有同类型小节共用）
        config_map: 按 element_id 映射的答案配置字典

    Returns:
        该小节对应的答案配置字符串，或 ``None``（表示未配置，应跳过）
    """
    if config_map and element_id in config_map:
        cfg = config_map[element_id]
        return cfg if cfg.strip() else None
    return default_config


def _build_lesson_index_map(
    elements: list[dict[str, Any]]
) -> dict[int, tuple[str, int]]:
    """构建小节序号到 (element_id, type) 的映射.

    按课程结构中的小节顺序编号（从1开始），不区分类型。
    用于支持 ``lesson_answers_by_index`` 参数。

    Args:
        elements: 课程元素列表（来自 element/list API）

    Returns:
        {1: ("element_id_1", type_1), 2: ("element_id_2", type_2), ...}
    """
    index_map: dict[int, tuple[str, int]] = {}
    idx = 1
    for el in elements:
        eid = str(el.get("element_id", ""))
        etype = el.get("type", 0)
        if eid:
            index_map[idx] = (eid, etype)
            idx += 1
    return index_map


def _resolve_lesson_answers_config(
    lesson_index: int,
    element_id: str,
    lesson_type: int,
    lesson_answers_by_index: dict[str, str] | None,
    q_answers_map: dict[str, str] | None,
    e_answers_map: dict[str, str] | None,
    default_q_answers: str | None,
    default_e_answers: str | None,
) -> tuple[str | None, str]:
    """解析单个小节的答案配置.

    优先级（从高到低）:
    1. ``lesson_answers_by_index[序号]`` — 按小节顺序（不区分类型）
    2. ``q_answers_map[element_id]`` / ``e_answers_map[element_id]`` — 按 element_id
    3. ``default_q_answers`` / ``default_e_answers`` — 默认配置

    Args:
        lesson_index: 小节序号（从1开始）
        element_id: 小节元素 ID
        lesson_type: 小节类型
        lesson_answers_by_index: 按小节序号映射的答案配置
        q_answers_map: 按 element_id 映射的问卷答案
        e_answers_map: 按 element_id 映射的考试答案
        default_q_answers: 默认问卷答案
        default_e_answers: 默认考试答案

    Returns:
        (答案配置字符串, 配置来源)。来源值为:
        ``lesson_index``, ``element_map``, ``default``, ``none``
    """
    # 1. 按小节序号查找（不区分类型）
    if lesson_answers_by_index:
        idx_str = str(lesson_index)
        if idx_str in lesson_answers_by_index:
            cfg = lesson_answers_by_index[idx_str]
            return (cfg if cfg.strip() else None, "lesson_index")

    # 2. 按 element_id + 类型查找
    if lesson_type == 1:  # 问卷
        if q_answers_map and element_id in q_answers_map:
            cfg = q_answers_map[element_id]
            return (cfg if cfg.strip() else None, "element_map")
        if default_q_answers:
            return (default_q_answers, "default")
    elif lesson_type == 10:  # 考试
        if e_answers_map and element_id in e_answers_map:
            cfg = e_answers_map[element_id]
            return (cfg if cfg.strip() else None, "element_map")
        if default_e_answers:
            return (default_e_answers, "default")

    return (None, "none")


def _is_question_required(q: dict[str, Any]) -> bool:
    """判断题目是否为必填.

    从 ``extend.setup.required`` 字段判断。
    ``required == 1`` 表示必填，其他值（包括不存在该字段）视为选填。
    """
    setup = q.get("extend", {}).get("setup", {})
    return setup.get("required", 0) == 1


def _validate_answers_against_questions(
    questions: list[dict[str, Any]],
    answers: list[str],
    for_exam: bool = False,
) -> tuple[bool, str]:
    """验证答案配置是否与问题结构匹配.

    验证规则:
    1. 答案数量 == 问题数量
    2. 单选题: 单个字母(A-Z)，对应选项索引在范围内；选填可跳过
    3. 多选题: 多个字母(无分隔符)，每个字母对应选项在范围内，无重复；选填可跳过
    4. 开放题: 必填时非空；选填可跳过
    5. 数值题: 必填时可解析为数字；考试(for_exam=True)时不支持数值题
    6. 未知类型: 返回错误

    Args:
        questions: 从 API 获取的问题列表
        answers: 解析后的答案列表
        for_exam: 是否为考试验证模式

    Returns:
        (是否通过验证, 错误信息)。通过时错误信息为空字符串。
    """
    if len(answers) != len(questions):
        return False, (
            f"答案数量({len(answers)})与问题数量({len(questions)})不匹配。"
            f"请确保用分号(;)分隔每道题的答案，"
            f"例如：{';'.join(['A'] * len(questions))}"
        )

    for i, (q, a) in enumerate(zip(questions, answers)):
        qtype = q.get("type")
        options = q.get("list", [])
        q_title = q.get("title", f"第{i + 1}题")
        opt_count = len(options)
        opt_max_label = chr(ord("A") + opt_count - 1) if opt_count > 0 else "N/A"
        is_required = _is_question_required(q)

        # 空答案：选填题目允许跳过，必填题目必须回答
        if not a.strip():
            if is_required:
                return False, (
                    f"第{i + 1}题「{q_title}」为必填题，答案不能为空。"
                    f"请为该题提供答案。"
                )
            continue  # 选填题目跳过验证

        # 统一处理问卷和考试的单选/多选类型
        # 问卷: 2=单选, 3=多选 | 考试: 0=单选, 1=多选
        is_single = qtype == 2 or (for_exam and qtype == 0)
        is_multi = qtype == 3 or (for_exam and qtype == 1)

        if is_single:  # 单选（问卷type=2 或 考试type=0）
            if len(a) != 1:
                return False, (
                    f"第{i + 1}题「{q_title}」为单选题，答案应为单个字母"
                    f"(如 A、B)，实际为「{a}」。"
                    f"请将答案改为单个字母(如 A)。"
                )
            if not a.isalpha():
                return False, (
                    f"第{i + 1}题「{q_title}」为单选题，答案「{a}」不是有效字母。"
                    f"请使用 A-Z 的单个字母作答。"
                )
            idx = ord(a.upper()) - ord("A")
            if idx < 0 or idx >= opt_count:
                return False, (
                    f"第{i + 1}题「{q_title}」为单选题，答案「{a}」超出选项范围"
                    f"(共 {opt_count} 个选项: A-{opt_max_label})。"
                    f"请将答案改为 A-{opt_max_label} 中的一个。"
                )

        elif is_multi:  # 多选（问卷type=3 或 考试type=1）
            if opt_count == 0:
                # 无选项的多选题（如考试中标记为多选但实际是开放式问题），
                # 跳过字母/范围验证，将答案当作文本处理
                continue
            seen: set[str] = set()
            for ch in a:
                if not ch.isalpha():
                    return False, (
                        f"第{i + 1}题「{q_title}」为多选题，答案「{a}」"
                        f"包含非法字符「{ch}」(应为字母)。"
                        f"多选答案请用连续字母表示(如 ABC)，不要用分隔符。"
                    )
                idx = ord(ch.upper()) - ord("A")
                if idx < 0 or idx >= opt_count:
                    return False, (
                        f"第{i + 1}题「{q_title}」为多选题，选项「{ch}」超出范围"
                        f"(共 {opt_count} 个选项: A-{opt_max_label})。"
                        f"请只选择 A-{opt_max_label} 范围内的选项。"
                    )
                if ch.upper() in seen:
                    return False, (
                        f"第{i + 1}题「{q_title}」为多选题，选项「{ch}」重复。"
                        f"请移除重复选项。"
                    )
                seen.add(ch.upper())

        elif qtype == 4 or (for_exam and qtype == 3):  # 开放题（问卷type=4 或 考试type=3）
            pass  # 非空已在上面检查

        elif qtype == 5:  # 数值/评分（仅问卷）
            if for_exam:
                return False, (
                    f"第{i + 1}题「{q_title}」为数值题，但考试暂不支持数值题。"
                    f"请检查答案配置是否对应正确的小节。"
                )
            try:
                float(a)
            except ValueError:
                return False, (
                    f"第{i + 1}题「{q_title}」为数值题，答案「{a}」不是有效数字。"
                    f"请提供数字形式的答案(如 5)。"
                )

        else:
            return False, (
                f"第{i + 1}题「{q_title}」为未知类型({qtype})，无法验证。"
                f"请检查课程结构或联系管理员。"
            )

    return True, ""


def _build_questionnaire_answers_json(
    questions: list[dict[str, Any]],
    answers: list[str],
) -> list[dict[str, Any]]:
    """将简化答案格式转换为问卷 API 需要的 JSON 格式.

    选填且空答案的题目会被跳过（不包含在结果中）。

    Args:
        questions: 问题列表
        answers: 解析后的答案列表

    Returns:
        问卷 save-poll-result API 需要的 answer 数组
    """
    result: list[dict[str, Any]] = []
    for q, a in zip(questions, answers):
        # 选填题目且空答案：跳过不提交
        if not a.strip() and not _is_question_required(q):
            continue

        qid = q.get("id")
        qtype = q.get("type")
        options = q.get("list", [])
        entry: dict[str, Any] = {"question_id": qid, "type": qtype}

        if qtype == 2 and options:  # 单选
            idx = ord(a.upper()) - ord("A")
            opt_id = options[idx].get("id", "")
            entry["value"] = [{"id": str(opt_id), "other_content": ""}]

        elif qtype == 3 and options:  # 多选
            vals: list[dict[str, str]] = []
            for ch in a:
                idx = ord(ch.upper()) - ord("A")
                opt_id = options[idx].get("id", "")
                vals.append({"id": str(opt_id), "other_content": ""})
            entry["value"] = vals

        elif qtype == 4:  # 开放题
            entry["value"] = [{"id": "", "other_content": a}]

        elif qtype == 5:  # 数值
            entry["value"] = [{"id": "", "other_content": a}]

        result.append(entry)
    return result


def _build_exam_answers_json(
    questions: list[dict[str, Any]],
    answers: list[str],
) -> list[dict[str, Any]]:
    """将简化答案格式转换为考试 API 需要的 JSON 格式.

    用于 ``/megrez/exam/v1/saveAnswer`` API。
    选填且空答案的题目会被跳过（不包含在结果中）。

    Args:
        questions: 问题列表
        answers: 解析后的答案列表

    Returns:
        考试 saveAnswer API 需要的 answer_list 数组
    """
    # 考试 API 的题目类型: 0=单选, 1=多选, 3=文本
    type_map = {0: "radio", 1: "checkbox", 3: "textarea"}
    result: list[dict[str, Any]] = []

    for q, a in zip(questions, answers):
        # 选填题目且空答案：跳过不提交
        if not a.strip() and not _is_question_required(q):
            continue

        qid = q.get("id")
        qtype = q.get("type")
        options = q.get("list", [])
        entry: dict[str, Any] = {
            "type": type_map.get(qtype, "radio"),
            "question_id": qid,
            "level": 2,
        }

        if qtype == 0 and options:  # 单选
            idx = ord(a.upper()) - ord("A")
            opt_id = options[idx].get("id", "")
            entry["answer_ids"] = [str(opt_id)]
            entry["content"] = ""

        elif qtype == 1:  # 多选
            if options:  # 有选项的多选题
                answer_ids: list[str] = []
                for ch in a:
                    idx = ord(ch.upper()) - ord("A")
                    opt_id = options[idx].get("id", "")
                    answer_ids.append(str(opt_id))
                entry["answer_ids"] = answer_ids
                entry["content"] = ""
            else:  # 无选项的多选题（考试中标记为多选但实际是开放式问题）
                entry["answer_ids"] = []
                entry["content"] = a
                entry["pic_url"] = []

        elif qtype == 3:  # 文本/开放题
            entry["answer_ids"] = []
            entry["content"] = a
            entry["pic_url"] = []

        result.append(entry)
    return result


@mcp.tool()
async def stu_submit_questionnaire(
    element_id: Annotated[str, Field(description="问卷小节元素 ID")],
    answers_json: Annotated[
        str,
        Field(
            description="答案 JSON 字符串，格式: [{\"question_id\": 123, \"type\": 2, \"value\": [{\"id\": \"option_id\", \"other_content\": \"\"}]}]"
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """提交问卷答案.

    触发条件：用户明确给出问卷答案后调用。不要猜测答案。
    前置依赖：需先调用 stu_get_questionnaire_questions 获取题目并展示给用户。
    副作用：会提交问卷答案并执行 makeweikestatus 序列完成该小节。

    answers_json 格式说明：
    - 单选(type=2): [{"id": "option_id", "other_content": ""}]
    - 多选(type=3): [{"id": "opt1"}, {"id": "opt2"}]
    - 文本(type=4): [{"id": "", "other_content": "答案文本"}]
    - 评分(type=5): [{"id": "", "other_content": "5"}]
    """
    client = _get_client(session_id)
    try:
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
            return _err(
                error_code="SUBMIT_QUESTIONNAIRE_FAILED",
                error_message=r.get("message", "提交问卷失败"),
                suggested_action="检查 answers_json 格式是否正确，或重新获取题目",
            )

        # 3. 执行 makeweikestatus 序列
        _makeweikestatus_sequence(client, element_id)

        return _ok(
            data={"element_id": element_id, "submit_id": submit_id},
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="SUBMIT_QUESTIONNAIRE_ERROR",
            error_message=str(e),
            suggested_action="检查 answers_json 格式是否正确",
        )


@mcp.tool()
async def stu_submit_questionnaire_with_config(
    element_id: Annotated[str, Field(description="问卷小节元素 ID")],
    answers_config: Annotated[
        str,
        Field(
            description="答案配置，格式: 用分号(;)分隔每道题的答案。"
            "单选: 单个字母(A=第1个选项, B=第2个...); "
            "多选: 连续字母(如 BCD=选第2/3/4个选项); "
            "开放题: 直接文本; "
            "数值题: 数字。"
            '示例: "A;BCD;我认为答案是...;5"'
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """使用简化配置提交问卷答案.

    触发条件：用户以简化格式(如 ``A;BCD;答案;5``)提供问卷答案后调用。
    前置依赖：需先调用 stu_get_questionnaire_questions 获取题目结构。
    副作用：会验证答案配置与问题结构是否匹配，验证通过后提交答案并执行 makeweikestatus。

    执行流程:
    1. 获取问卷题目列表
    2. 解析 answers_config（按分号分隔）
    3. 逐题验证答案格式（数量、类型、选项范围）
    4. 验证失败则返回详细错误信息，提示用户修正
    5. 验证通过则构造标准 JSON 答案并提交
    6. 执行 makeweikestatus 序列完成小节
    """
    client = _get_client(session_id)
    try:
        # 1. 获取问卷题目
        r = client.get(
            client.desktop_url(
                f"/uapi/v1/poll/question-list?"
                f"element_id={element_id}&page=1&size=999"
            )
        )
        if r.get("error_code") != 0:
            return _err(
                error_code="FETCH_QUESTIONS_FAILED",
                error_message=r.get("message", "获取题目失败"),
                suggested_action="检查 element_id 是否正确，或课程是否需要报名",
            )

        questions = r.get("data", {}).get("list", [])
        if not questions:
            return _err(
                error_code="NO_QUESTIONS",
                error_message="该问卷没有题目",
                suggested_action="确认 element_id 是否正确",
            )

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

            return _err(
                error_code="ANSWERS_VALIDATION_FAILED",
                error_message=f"答案配置验证失败: {error_msg}",
                data={
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
                suggested_action="根据 questions_summary 修正 answers_config 后重试",
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
            return _err(
                error_code="SUBMIT_QUESTIONNAIRE_FAILED",
                error_message=r.get("message", "提交问卷失败"),
                suggested_action="检查答案配置是否正确，或重新获取题目",
            )

        # 7. 执行 makeweikestatus 序列
        _makeweikestatus_sequence(client, element_id)

        return _ok(
            data={
                "element_id": element_id,
                "submit_id": submit_id,
                "total_questions": len(questions),
                "answers_summary": answers,
            },
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="SUBMIT_QUESTIONNAIRE_WITH_CONFIG_ERROR",
            error_message=str(e),
            suggested_action="检查 answers_config 格式是否正确",
        )


@mcp.tool()
async def stu_check_in(
    element_id: Annotated[str, Field(description="签到小节元素 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """完成普通签到小节.

    触发条件：当小节 type=6 且 advance=0（普通签到）时调用。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：会执行签到操作和 makeweikestatus 状态序列。
    """
    client = _get_client(session_id)
    try:
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
            print(f"[stu_check_in] insertAnswer 失败（非致命）: {e}")

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
            print(f"[stu_check_in] insertWxAnswer 失败（非致命）: {e}")

        # 执行 makeweikestatus 序列
        _makeweikestatus_sequence(client, element_id)

        return _ok(
            data={"element_id": element_id, "action": "checkin_completed"},
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="CHECKIN_FAILED",
            error_message=str(e),
            suggested_action="检查 element_id 是否正确",
        )


@mcp.tool()
async def stu_check_in_with_rating(
    element_id: Annotated[str, Field(description="评分签到小节元素 ID")],
    rating: Annotated[
        int,
        Field(description="评分值（1-5），必须用户提供，不要猜测", ge=1, le=5),
    ],
    comment: Annotated[
        str,
        Field(
            default="",
            description="可选的评论/反馈文本",
        ),
    ] = "",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """完成评分签到小节.

    触发条件：当小节 type=6 且 advance=1（评分签到）时调用。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：会提交评分答案并执行 makeweikestatus 状态序列。

    重要：rating 必须由用户明确给出。如果用户没有评分意愿，先询问用户，不要猜测评分。
    """
    client = _get_client(session_id)
    try:
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
            print(f"[stu_check_in_with_rating] insertAnswer 失败（非致命）: {e}")

        # 执行 makeweikestatus 序列
        _makeweikestatus_sequence(client, element_id)

        return _ok(
            data={"element_id": element_id, "rating": rating},
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="CHECKIN_RATING_FAILED",
            error_message=str(e),
            suggested_action="检查 element_id 和 rating 是否正确",
        )


@mcp.tool()
async def stu_start_exam(
    element_id: Annotated[str, Field(description="考试小节元素 ID")],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """开始考试.

    触发条件：当小节 type=10（考试）时调用。必须先调用此接口获取 exam_submit_id，才能提交考试。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：会初始化考试会话。

    注意：
    - 返回的 exam_submit_id 必须保存，后续提交考试需要用到
    - 返回的 questions 可以展示给用户
    """
    client = _get_client(session_id)
    try:
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
                return _err(
                    error_code="START_EXAM_FAILED",
                    error_message=r.get("message", "开始考试失败"),
                    suggested_action="检查考试是否已开始或已提交",
                )

            return _ok(
                data={
                    "element_id": element_id,
                    "exam_submit_id": exam_submit_id,
                    "student_id": student_id,
                },
                next_action="needs_user_input",
                suggested_action="向用户展示考试题目，答题完成后调用 stu_submit_exam 提交",
            )
        else:
            return _err(
                error_code="EXAM_PREPARE_FAILED",
                error_message="无法获取 exam_submit_id 或 student_id",
                suggested_action="检查课程是否需要报名，或稍后重试",
            )
    except Exception as e:
        return _err(
            error_code="START_EXAM_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )


@mcp.tool()
async def stu_submit_exam(
    element_id: Annotated[str, Field(description="考试小节元素 ID")],
    exam_submit_id: Annotated[
        str, Field(description="考试提交 ID，来自 stu_start_exam 的返回值")
    ],
    answers_json: Annotated[
        str,
        Field(
            default="{}",
            description='考试答案 JSON（可选，可留空），格式: {"question_id": "answer"}',
        ),
    ] = "{}",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """提交考试.

    触发条件：用户完成考试答题后调用。可以不带答案（空答案也会提交）。
    前置依赖：必须先调用 stu_start_exam 获取 exam_submit_id。
    副作用：会提交考试答案。

    注意：如果考试已提交过（返回 "exam status not in testing"），表示该小节已完成。
    """
    client = _get_client(session_id)
    try:
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
            return _ok(
                data={"element_id": element_id, "exam_submit_id": exam_submit_id},
                next_action="proceed",
                suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
            )
        elif "not in testing" in str(r.get("message", "")).lower():
            return _ok(
                data={"element_id": element_id},
                next_action="lesson_completed",
                suggested_action="考试已提交过，该小节已完成",
            )
        else:
            return _err(
                error_code="SUBMIT_EXAM_FAILED",
                error_message=r.get("message", "提交考试失败"),
                suggested_action="检查 exam_submit_id 是否正确",
            )
    except Exception as e:
        return _err(
            error_code="SUBMIT_EXAM_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )


@mcp.tool()
async def stu_submit_exam_with_config(
    element_id: Annotated[str, Field(description="考试小节元素 ID")],
    answers_config: Annotated[
        str,
        Field(
            description="答案配置，格式: 用分号(;)分隔每道题的答案。"
            "单选: 单个字母(A=第1个选项, B=第2个...); "
            "多选: 连续字母(如 BCD=选第2/3/4个选项); "
            "开放题: 直接文本。"
            '示例: "A;BCD;我的观点是..."'
            "注意: 考试不支持数值题。"
        ),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """使用简化配置提交考试答案.

    触发条件：用户以简化格式(如 ``A;BCD;答案``)提供考试答案后调用。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：会验证答案配置与考试题目结构是否匹配，验证通过后逐题保存答案并提交考试。

    执行流程:
    1. 获取 exam_submit_id 和 student_id
    2. 调用 startExam 初始化考试
    3. 获取考试题目列表
    4. 解析 answers_config（按分号分隔）
    5. 逐题验证答案格式（数量、类型、选项范围）
    6. 验证失败则返回详细错误信息，提示用户修正
    7. 验证通过则逐题调用 saveAnswer 保存答案
    8. 调用 submitExam 提交考试
    9. 执行 makeweikestatus 序列完成小节
    """
    client = _get_client(session_id)
    try:
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
            return _err(
                error_code="EXAM_PREPARE_FAILED",
                error_message="无法获取 exam_submit_id 或 student_id",
                suggested_action="检查课程是否需要报名，或稍后重试",
            )

        # 3. 检查考试是否已完成
        try:
            r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
            el_data = r.get("data", {}) or {}
            extend = el_data.get("extend", {}) or {}
            if extend.get("learn_status") == 2:
                return _ok(
                    data={"element_id": element_id, "status": "already_completed"},
                    next_action="lesson_completed",
                    suggested_action="考试小节已完成",
                )
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
            if "already" in msg or "started" in msg or "status" in msg:
                pass  # 继续执行
            else:
                return _err(
                    error_code="START_EXAM_FAILED",
                    error_message=r.get("message", "开始考试失败"),
                    suggested_action="检查考试是否已开始或已提交",
                )

        # 5. 获取考试题目
        r = client.get(
            client.mobile_url(
                f"/napi/v1/quiz/question-list"
                f"?_type=1&element_id={element_id}&page=1&size=999"
            )
        )
        questions = r.get("data", {}).get("list", [])
        if not questions:
            return _err(
                error_code="NO_EXAM_QUESTIONS",
                error_message="该考试没有获取到题目",
                suggested_action="确认 element_id 是否正确，或考试是否已过期",
            )

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
                # 构造该题的可接受答案格式提示
                if qtype == 2:
                    fmt_hint = f"单个字母(A-{chr(ord('A') + len(options) - 1)})"
                elif qtype == 3:
                    if options:
                        fmt_hint = f"连续字母(如 AB, A-{chr(ord('A') + len(options) - 1)})"
                    else:
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

            return _err(
                error_code="ANSWERS_VALIDATION_FAILED",
                error_message=f"答案配置验证失败: {error_msg}",
                data={
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
                suggested_action="根据 questions_summary 修正 answers_config 后重试",
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
                print(f"[stu_submit_exam_with_config] saveAnswer 失败（非致命）: {e}")
            time.sleep(0.3)

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
            return _err(
                error_code="SUBMIT_EXAM_FAILED",
                error_message=r.get("message", "提交考试失败"),
                suggested_action="检查 exam_submit_id 是否正确",
            )

        # 11. 执行 makeweikestatus 序列
        _makeweikestatus_sequence(client, element_id)

        return _ok(
            data={
                "element_id": element_id,
                "exam_submit_id": exam_submit_id,
                "total_questions": len(questions),
                "answers_summary": answers,
            },
            next_action="proceed",
            suggested_action="调用 stu_get_lesson_status 验证小节是否已完成",
        )
    except Exception as e:
        return _err(
            error_code="SUBMIT_EXAM_WITH_CONFIG_ERROR",
            error_message=str(e),
            suggested_action="检查 answers_config 格式是否正确",
        )


@mcp.tool()
async def stu_get_lesson_status(
    element_id: Annotated[str, Field(description="小节元素 ID")],
    group_id: Annotated[
        str,
        Field(
            default="",
            description="课程组 ID（可选），如果提供则同时返回课程整体进度",
        ),
    ] = "",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取单个小节的完成状态.

    触发条件：在执行任何小节操作后，都应调用此接口确认是否已完成。
    前置依赖：需先调用 stu_get_course_structure 获取 element_id。
    副作用：无（只读查询）。

    注意：唯一可靠的完成状态来源。makeweikestatus 的返回状态不等于课程完成状态。
    """
    client = _get_client(session_id)
    try:
        # 方法1（首选）: 通过课程进度接口获取整体状态
        # get-learning-progress 是唯一可靠的完成状态来源。
        # element/{id} 和 element/list 的 learn_status 有延迟/缓存问题。
        if group_id:
            try:
                r = client.get(
                    client.desktop_url(f"/uapi/v1/course/get-learning-progress?group_id={group_id}")
                )
                if r.get("error_code") == 0:
                    data = r.get("data", {})
                    rate = data.get("complete_rate", 0) * 100
                    is_fully = rate >= 100
                    # 如果课程完成率 100%，直接返回已完成
                    if is_fully:
                        return _ok(
                            data={
                                "element_id": element_id,
                                "group_id": group_id,
                                "is_completed": True,
                                "course_complete_rate": round(rate, 1),
                                "is_course_completed": True,
                                "check_method": "course_progress",
                                "note": "课程已完成 100%",
                            },
                            next_action="lesson_completed",
                            suggested_action="课程已完成",
                        )
                    # 否则继续用 element/list 检查单个小节状态
            except Exception:
                pass

        # 方法2: 通过 element/list 查找该元素状态
        if group_id:
            try:
                r = client.get(
                    client.mobile_url(f"/uapi/v2/element/list?page=1&size=100&parent_id={group_id}&get_draft=0")
                )
                if r.get("error_code") == 0:
                    elements = r.get("data", {}).get("list", [])
                    for el in elements:
                        if str(el.get("element_id", "")) == str(element_id):
                            extend = el.get("extend", {}) or {}
                            learn_status = extend.get("learn_status", 0)
                            is_completed = learn_status == 2
                            return _ok(
                                data={
                                    "element_id": element_id,
                                    "group_id": group_id,
                                    "is_completed": is_completed,
                                    "learn_status": learn_status,
                                    "check_method": "element_list",
                                },
                                next_action="lesson_completed" if is_completed else "proceed",
                                suggested_action="该小节已完成" if is_completed else "该小节尚未完成",
                            )
            except Exception:
                pass

        # 方法3（fallback）: 通过 element/detail 获取单个小节状态
        try:
            r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
            if r.get("error_code") == 0:
                data = r.get("data", {})
                extend = data.get("extend", {})
                learn_status = extend.get("learn_status", 0)
                result_data: dict[str, Any] = {
                    "element_id": element_id,
                    "is_completed": learn_status == 2,
                    "learn_status": learn_status,
                    "check_method": "element_detail",
                    "note": "此方法可能不可靠，建议优先提供 group_id",
                }
                if group_id:
                    result_data["group_id"] = group_id
                return _ok(
                    data=result_data,
                    next_action="lesson_completed" if learn_status == 2 else "proceed",
                    suggested_action="该小节已完成" if learn_status == 2 else "该小节尚未完成",
                )
        except Exception:
            pass

        return _err(
            error_code="STATUS_CHECK_FAILED",
            error_message="无法获取小节状态",
            suggested_action="检查 element_id 和 group_id 是否正确",
        )
    except Exception as e:
        return _err(
            error_code="STATUS_CHECK_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和认证状态",
        )


# ---------------------------------------------------------------------------
# Tools: 会话管理
# ---------------------------------------------------------------------------

@mcp.tool()
async def stu_create_session(
    username: Annotated[
        str | None,
        Field(default=None, description="可选用户名，如果提供则尝试自动登录"),
    ] = None,
    password: Annotated[
        str | None,
        Field(default=None, description="可选密码"),
    ] = None,
) -> str:
    """创建新的独立会话.

    触发条件：当需要为不同用户创建隔离的登录环境时调用。
    前置依赖：无。
    副作用：创建独立会话，拥有独立的 Cookie 和 Token。

    每个会话拥有独立的 UMUClient 实例（含独立的 httpx.Client），
    确保多用户并发使用时登录状态互不干扰。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        session = await _session_manager.create_session(username, password)
        return _ok(
            data={
                "session_id": session.session_id,
                "username": session.username,
                "is_authenticated": session.client.auth.is_authenticated(),
                "created_at": session.created_at,
            },
            next_action="proceed",
            suggested_action="保存 session_id，后续调用 tool 时传入此参数",
        )
    except Exception as e:
        return _err(
            error_code="CREATE_SESSION_FAILED",
            error_message=str(e),
        )


@mcp.tool()
async def stu_list_sessions() -> str:
    """列出所有活跃会话.

    触发条件：需要查看当前有哪些会话在使用中。
    前置依赖：无。
    副作用：无（只读查询）。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        sessions = await _session_manager.list_sessions()
        return _ok(
            data={
                "count": len(sessions),
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "username": s.username,
                        "is_authenticated": s.is_authenticated,
                        "created_at": s.created_at,
                        "last_used_at": s.last_used_at,
                    }
                    for s in sessions
                ],
            },
            next_action="proceed",
        )
    except Exception as e:
        return _err(
            error_code="LIST_SESSIONS_FAILED",
            error_message=str(e),
        )


@mcp.tool()
async def stu_destroy_session(
    session_id: Annotated[str, Field(description="要销毁的会话 ID")],
) -> str:
    """销毁指定会话.

    触发条件：会话不再需要使用，或需要释放资源时调用。
    前置依赖：无。
    副作用：关闭会话的客户端连接，释放资源。
    """
    if _session_manager is None:
        return _err(
            error_code="SESSION_MANAGER_NOT_INITIALIZED",
            error_message="会话管理器未初始化",
        )
    try:
        success = await _session_manager.destroy_session(session_id)
        if success:
            return _ok(
                data={"session_id": session_id, "destroyed": True},
                next_action="proceed",
            )
        else:
            return _err(
                error_code="SESSION_NOT_FOUND",
                error_message=f"会话不存在: {session_id}",
            )
    except Exception as e:
        return _err(
            error_code="DESTROY_SESSION_FAILED",
            error_message=str(e),
        )


# ---------------------------------------------------------------------------
# Tools: 批量操作
# ---------------------------------------------------------------------------

@mcp.tool()
async def stu_batch_import_accounts(
    file_path: Annotated[str, Field(description="账号文件路径（CSV 或 JSON）")],
    file_format: Annotated[
        str,
        Field(default="auto", description="文件格式: auto(自动检测), csv, json"),
    ] = "auto",
) -> str:
    """从文件导入账号列表.

    触发条件：需要批量导入多个账号时使用。
    前置依赖：无。
    副作用：无（只读查询）。

    CSV 格式: username,password[,nickname]
    JSON 格式: [{"username": "...", "password": "..."}, ...]

    返回结果中密码会被脱敏，不会明文显示。
    """
    try:
        source = None if file_format == "auto" else AccountSource(file_format)
        accounts = AccountImporter.import_accounts(file_path, source)

        return _ok(
            data={
                "total": len(accounts),
                "accounts": [
                    {
                        "username": a.username,
                        "nickname": a.nickname,
                    }
                    for a in accounts
                ],
            },
            next_action="proceed",
            suggested_action="确认账号列表后调用 stu_batch_complete_course",
        )
    except Exception as e:
        return _err(
            error_code="IMPORT_ACCOUNTS_FAILED",
            error_message=str(e),
            suggested_action="检查文件路径和格式是否正确",
        )


@mcp.tool()
async def stu_complete_course(
    course_identifier: Annotated[
        str,
        Field(description="课程标识（访问码/短域名/URL）"),
    ],
    skip_exam: Annotated[
        bool,
        Field(default=True, description="是否跳过考试小节"),
    ] = True,
    skip_questionnaire: Annotated[
        bool,
        Field(default=True, description="是否跳过问卷小节"),
    ] = True,
    questionnaire_answers: Annotated[
        str | None,
        Field(
            default=None,
            description="问卷答案配置，格式: 单选字母;多选字母(连续);开放文本;数值。"
            "提供后不再跳过问卷小节。"
            '示例: "A;BCD;我的观点是...;5"',
        ),
    ] = None,
    exam_answers: Annotated[
        str | None,
        Field(
            default=None,
            description="考试答案配置，格式同问卷。"
            "提供后不再跳过考试小节。"
            '示例: "A;BCD;我的观点是..."',
        ),
    ] = None,
    questionnaire_answers_map: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节指定问卷答案，JSON 格式: {"element_id": "A;B;C", ...}。'
            "优先于 questionnaire_answers。"
            '示例: \'{"61645039": "A;B;C"}\'',
        ),
    ] = None,
    exam_answers_map: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节指定考试答案，JSON 格式: {"element_id": "A;B;C", ...}。'
            "优先于 exam_answers。"
            '示例: \'{"61645043": "A;B;C"}\'',
        ),
    ] = None,
    questionnaire_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按问卷序号指定答案，JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "序号对应 stu_get_course_structure 返回的 questionnaire_index。"
            '示例: \'{"1": "A;B;C", "2": "D;E"}\'',
        ),
    ] = None,
    exam_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按考试序号指定答案，JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "序号对应 stu_get_course_structure 返回的 exam_index。"
            '示例: \'{"1": "A;B;C", "2": "D;E"}\'',
        ),
    ] = None,
    lesson_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节顺序指定答案（不区分类型），JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "优先级最高，覆盖所有其他答案配置方式。"
            "适合用户按课程小节顺序提供答案的场景，无需区分问卷/考试。"
            '示例: \'{"1": ";A;;;开放式答案;2;", "2": "C;ABC;开放式答案", "3": ";A;;;开放式答案;2;", "4": "C;ABC;开放式答案"}\'',
        ),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """一键完成单门课程的所有未完成小节.

    触发条件：用户想要快速完成一门课程的所有小节时调用。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：会自动完成所有可自动完成的小节（浏览、签到、问卷、考试）。

    执行流程：
    1. 解析课程标识，获取课程结构
    2. 报名课程（如需要）
    3. 逐个完成未完成小节
    4. 生成完成报告

    注意：
    - 问卷和考试默认跳过（需要答案配置）
    - 提供答案配置后，对应类型不再跳过
    - 操作完成后自动验证课程进度
    """
    client = _get_client(session_id)
    details: list[dict[str, Any]] = []
    completed = 0
    total = 0

    try:
        # 解析课程标识
        group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)

        # 检查是否需要报名
        needs_enroll, enroll_id = _check_needs_enroll(client, group_id, s_key)
        if needs_enroll and enroll_id:
            try:
                r = client.post(
                    client.mobile_url("/ajax/verify/auto"),
                    {"enroll_id": str(enroll_id)},
                )
                if r.get("error_code") == 0 or r.get("status") is True:
                    details.append({"action": "enroll", "success": True})
                else:
                    details.append({"action": "enroll", "success": False, "error": r.get("message", "报名失败")})
                    return _err(
                        error_code="ENROLL_FAILED",
                        error_message=f"课程需要报名但报名失败: {r.get('message', '未知错误')}",
                        data={"details": details},
                    )
                time.sleep(1)
            except Exception as e:
                details.append({"action": "enroll", "success": False, "error": str(e)})
                return _err(
                    error_code="ENROLL_ERROR",
                    error_message=f"报名异常: {e}",
                    data={"details": details},
                )

        # 获取课程结构
        r = client.get(
            client.mobile_url(
                f"/uapi/v2/element/list?"
                f"page=1&size=100&parent_id={group_id}&get_draft=0"
            )
        )
        if r.get("error_code") != 0:
            return _err(
                error_code="COURSE_NOT_ACCESSIBLE",
                error_message=f"无法获取课程: {r.get('message', '未知错误')}",
            )

        elements = r.get("data", {}).get("list", [])
        total = len(elements)

        # 构建序号到 element_id 的映射
        q_index_to_id: dict[int, str] = {}
        e_index_to_id: dict[int, str] = {}
        for el in elements:
            etype = el.get("type", 0)
            eid = str(el.get("element_id", ""))
            if etype == 1:
                q_index_to_id[len(q_index_to_id) + 1] = eid
            elif etype == 10:
                e_index_to_id[len(e_index_to_id) + 1] = eid

        # 解析答案配置映射
        q_answers_map: dict[str, str] | None = None
        e_answers_map: dict[str, str] | None = None
        if questionnaire_answers_map:
            try:
                parsed = json.loads(questionnaire_answers_map)
                if isinstance(parsed, dict):
                    q_answers_map = parsed
            except json.JSONDecodeError:
                pass
        if exam_answers_map:
            try:
                parsed = json.loads(exam_answers_map)
                if isinstance(parsed, dict):
                    e_answers_map = parsed
            except json.JSONDecodeError:
                pass

        # 将 by_index 参数转换为 element_id 映射
        if questionnaire_answers_by_index and q_index_to_id:
            try:
                by_index = json.loads(questionnaire_answers_by_index)
                if isinstance(by_index, dict):
                    for idx_str, cfg in by_index.items():
                        try:
                            idx = int(idx_str)
                            if idx in q_index_to_id:
                                if q_answers_map is None:
                                    q_answers_map = {}
                                q_answers_map[q_index_to_id[idx]] = cfg
                        except ValueError:
                            pass
            except json.JSONDecodeError:
                pass
        if exam_answers_by_index and e_index_to_id:
            try:
                by_index = json.loads(exam_answers_by_index)
                if isinstance(by_index, dict):
                    for idx_str, cfg in by_index.items():
                        try:
                            idx = int(idx_str)
                            if idx in e_index_to_id:
                                if e_answers_map is None:
                                    e_answers_map = {}
                                e_answers_map[e_index_to_id[idx]] = cfg
                        except ValueError:
                            pass
            except json.JSONDecodeError:
                pass

        # 解析 lesson_answers_by_index（按小节顺序，不区分类型）
        lesson_answers_map: dict[str, str] | None = None
        if lesson_answers_by_index:
            try:
                parsed = json.loads(lesson_answers_by_index)
                if isinstance(parsed, dict):
                    lesson_answers_map = parsed
            except json.JSONDecodeError:
                pass

        # 构建小节序号映射（用于 lesson_answers_by_index）
        lesson_index_map = _build_lesson_index_map(elements)

        # 逐个完成小节
        for lesson_idx, el in enumerate(elements, start=1):
            eid = str(el.get("element_id", ""))
            etype = el.get("type", 0)
            extend = el.get("extend", {}) or {}
            learn_status = extend.get("learn_status", 0)

            if learn_status == 2:
                completed += 1
                continue

            lesson_detail: dict[str, Any] = {
                "element_id": eid,
                "title": el.get("title", ""),
                "type": etype,
                "lesson_index": lesson_idx,
                "action": "",
                "success": False,
            }

            # 解析该小节的答案配置（支持 lesson_answers_by_index 优先）
            cfg, cfg_source = _resolve_lesson_answers_config(
                lesson_idx, eid, etype,
                lesson_answers_map,
                q_answers_map, e_answers_map,
                questionnaire_answers, exam_answers,
            )
            if cfg_source != "none":
                lesson_detail["config_source"] = cfg_source

            try:
                if etype in (11, 13, 15):  # 视频/文章/图文
                    _makeweikestatus_sequence(client, eid)
                    lesson_detail["action"] = "browse"
                    lesson_detail["success"] = True
                    completed += 1

                elif etype == 14:  # 文档
                    setup = el.get("setup", {}) or {}
                    vlt_min = setup.get("vlt_min", 0)
                    extras: dict[str, dict] = {}
                    if vlt_min:
                        extras["playing"] = {"left_time": str(vlt_min)}
                        extras["achieve"] = {"left_time": str(vlt_min), "vlt_status": "1"}
                    _makeweikestatus_sequence(client, eid, extras)
                    lesson_detail["action"] = "browse"
                    lesson_detail["success"] = True
                    completed += 1

                elif etype == 6:  # 签到
                    setup = el.get("setup", {}) or {}
                    advance = setup.get("advance", "0")
                    q_payload = {
                        "answerList": [],
                        "answerInfo": [{"id": eid, "text": "学员"}],
                        "answerNumber": {},
                        "enrollId": 0,
                        "sessionId": eid,
                    }
                    client.post(
                        client.mobile_url("/ajax/insertAnswer"),
                        {"q": json.dumps(q_payload, ensure_ascii=False)},
                    )
                    _makeweikestatus_sequence(client, eid)
                    lesson_detail["action"] = "checkin_with_rating" if advance == "1" else "checkin"
                    lesson_detail["success"] = True
                    completed += 1

                elif etype == 1:  # 问卷
                    if cfg:
                        lesson_detail["action"] = "questionnaire_with_config"
                        # 获取题目
                        r = client.get(
                            client.desktop_url(
                                f"/uapi/v1/poll/question-list?"
                                f"element_id={eid}&page=1&size=999"
                            )
                        )
                        questions = r.get("data", {}).get("list", [])
                        if not questions:
                            raise RuntimeError("该问卷没有题目")

                        # 解析并验证
                        answers = _parse_answers_config(cfg)
                        is_valid, error_msg = _validate_answers_against_questions(
                            questions, answers, for_exam=False
                        )
                        if not is_valid:
                            raise RuntimeError(f"问卷答案验证失败: {error_msg}")

                        # 提交
                        answers_json_list = _build_questionnaire_answers_json(
                            questions, answers
                        )
                        answers_json = json.dumps(answers_json_list, ensure_ascii=False)

                        r = client.post(
                            client.mobile_url("/megrez/poll/v1/user-save-poll-result"),
                            {
                                "answers": "[]",
                                "session_id": eid,
                                "submit_id": "0",
                            },
                        )
                        submit_id = r.get("data", {}).get("submit_id", "0")

                        r = client.post(
                            client.mobile_url("/megrez/poll/v1/save-poll-result"),
                            {
                                "submit_id": str(submit_id),
                                "element_id": eid,
                                "is_anonymous": "0",
                                "answer": answers_json,
                            },
                        )
                        if r.get("error_code") != 0:
                            raise RuntimeError(f"提交问卷失败: {r.get('message', '')}")

                        _makeweikestatus_sequence(client, eid)
                        lesson_detail["success"] = True
                        completed += 1
                    elif not skip_questionnaire:
                        lesson_detail["action"] = "questionnaire"
                        lesson_detail["skipped"] = True

                elif etype == 10:  # 考试
                    if cfg:
                        lesson_detail["action"] = "exam_with_config"
                        exam_submit_id = _get_exam_submit_id(client, eid)

                        student_id = ""
                        try:
                            r = client.get(
                                client.desktop_url("/uapi/v1/user/get")
                            )
                            student_id = r.get("data", {}).get("student_id", "")
                        except Exception:
                            pass

                        if not exam_submit_id or not student_id:
                            raise RuntimeError("无法获取 exam_submit_id 或 student_id")

                        # startExam
                        r = client.post(
                            client.mobile_url("/megrez/exam/v1/startExam"),
                            {
                                "session_id": eid,
                                "student_id": str(student_id),
                                "exam_submit_id": str(exam_submit_id),
                            },
                        )
                        if r.get("error_code") != 0:
                            raise RuntimeError(f"开始考试失败: {r.get('message', '')}")

                        # 获取考试题目
                        r = client.get(
                            client.mobile_url(
                                f"/napi/v1/quiz/question-list"
                                f"?_type=1&element_id={eid}&page=1&size=999"
                            )
                        )
                        questions = r.get("data", {}).get("list", [])
                        if not questions:
                            raise RuntimeError("该考试没有获取到题目")

                        # 解析并验证
                        answers = _parse_answers_config(cfg)
                        is_valid, error_msg = _validate_answers_against_questions(
                            questions, answers, for_exam=True
                        )
                        if not is_valid:
                            raise RuntimeError(f"考试答案验证失败: {error_msg}")

                        # 保存答案
                        exam_answers_list = _build_exam_answers_json(
                            questions, answers
                        )
                        for answer in exam_answers_list:
                            try:
                                client.post(
                                    client.mobile_url("/megrez/exam/v1/saveAnswer"),
                                    {
                                        "session_id": eid,
                                        "answer_list": json.dumps(
                                            [answer], ensure_ascii=False
                                        ),
                                        "student_id": str(student_id),
                                        "exam_submit_id": str(exam_submit_id),
                                    },
                                )
                            except Exception:
                                pass
                            time.sleep(0.3)

                        # 提交考试
                        r = client.post(
                            client.mobile_url("/megrez/exam/v1/submitExam"),
                            {
                                "session_id": eid,
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
                            raise RuntimeError(f"提交考试失败: {r.get('message', '')}")

                        _makeweikestatus_sequence(client, eid)
                        lesson_detail["success"] = True
                        completed += 1
                    elif not skip_exam:
                        lesson_detail["action"] = "exam"
                        lesson_detail["skipped"] = True

                else:
                    lesson_detail["action"] = "skipped"

            except Exception as e:
                lesson_detail["error"] = str(e)

            details.append(lesson_detail)
            time.sleep(0.5)

        # 检查最终进度
        try:
            r = client.get(
                client.desktop_url(f"/uapi/v1/course/get-learning-progress?group_id={group_id}")
            )
            progress_data = r.get("data", {})
            final_rate = progress_data.get("complete_rate", 0) * 100
        except Exception:
            final_rate = round(completed / total * 100) if total > 0 else 0

        return _ok(
            data={
                "group_id": group_id,
                "total_lessons": total,
                "completed_lessons": completed,
                "progress_percentage": round(final_rate, 1),
                "is_fully_completed": final_rate >= 100,
                "details": details,
            },
            next_action="lesson_completed" if final_rate >= 100 else "proceed",
            suggested_action="课程已完成" if final_rate >= 100 else "继续完成剩余小节",
        )

    except Exception as e:
        return _err(
            error_code="COMPLETE_COURSE_ERROR",
            error_message=str(e),
            data={"completed_lessons": completed, "total_lessons": total, "details": details},
        )


@mcp.tool()
async def stu_batch_complete_course(
    file_path: Annotated[str, Field(description="账号文件路径（CSV 或 JSON）")],
    course_identifier: Annotated[str, Field(description="课程标识（访问码/短域名/URL）")],
    file_format: Annotated[
        str,
        Field(default="auto", description="文件格式: auto, csv, json"),
    ] = "auto",
    max_concurrency: Annotated[
        int,
        Field(default=3, ge=1, le=10, description="最大并发数（1-10）"),
    ] = 3,
    delay_between_accounts: Annotated[
        float,
        Field(default=1.0, ge=0, description="账号间启动延迟（秒）"),
    ] = 1.0,
    skip_exam: Annotated[
        bool,
        Field(default=True, description="是否跳过考试小节"),
    ] = True,
    skip_questionnaire: Annotated[
        bool,
        Field(default=True, description="是否跳过问卷小节"),
    ] = True,
    questionnaire_answers: Annotated[
        str | None,
        Field(
            default=None,
            description="问卷答案配置，格式: 单选字母;多选字母(连续);开放文本;数值。"
            "提供后不再跳过问卷小节，所有账号使用相同答案。"
            '示例: "A;BCD;我的观点是...;5"',
        ),
    ] = None,
    exam_answers: Annotated[
        str | None,
        Field(
            default=None,
            description="考试答案配置，格式同问卷。"
            "提供后不再跳过考试小节，所有账号使用相同答案。"
            '示例: "A;BCD;我的观点是..."',
        ),
    ] = None,
    questionnaire_answers_map: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节指定问卷答案，JSON 格式: {"element_id": "A;B;C", ...}。'
            "优先于 questionnaire_answers，可为不同问卷小节配置不同答案。"
            '示例: \'{"37848837": "A;B;C", "37848838": "D;E;F"}\'',
        ),
    ] = None,
    exam_answers_map: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节指定考试答案，JSON 格式: {"element_id": "A;B;C", ...}。'
            "优先于 exam_answers，可为不同考试小节配置不同答案。"
            '示例: \'{"37848837": "A;B;C", "37848838": "D;E;F"}\'',
        ),
    ] = None,
    questionnaire_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按问卷序号指定答案，JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "序号对应 stu_get_course_structure 返回的 questionnaire_index。"
            "与 questionnaire_answers_map 合并使用，优先级低于 map。"
            '示例: \'{"1": "A;B;C", "2": "D;E"}\'',
        ),
    ] = None,
    exam_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按考试序号指定答案，JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "序号对应 stu_get_course_structure 返回的 exam_index。"
            "与 exam_answers_map 合并使用，优先级低于 map。"
            '示例: \'{"1": "A;B;C", "2": "D;E"}\'',
        ),
    ] = None,
    lesson_answers_by_index: Annotated[
        str | None,
        Field(
            default=None,
            description='按小节顺序指定答案（不区分类型），JSON 格式: {"1": "A;B;C", "2": "D;E;F", ...}。'
            "优先级最高，覆盖所有其他答案配置方式。"
            "适合按课程小节顺序提供答案，无需区分问卷/考试。"
            '示例: \'{"1": ";A;;;开放式答案;2;", "2": "C;ABC;开放式答案"}\'',
        ),
    ] = None,
) -> str:
    """批量完成课程 — 为多个账号自动完成指定课程.

    触发条件：需要为多个学员账号批量完成同一门课程时调用。
    前置依赖：账号文件已准备好。
    副作用：会为每个账号独立登录并完成可自动完成的小节。

    执行流程：
    1. 从文件导入账号列表
    2. 每个账号独立登录（独立会话，互不干扰）
    3. 解析课程标识，获取课程结构
    4. 报名课程（如需要）
    5. 逐个完成可自动完成的小节（浏览、签到、问卷、考试）
    6. 生成执行报告

    注意：
    - 问卷和考试默认跳过（需要用户交互）
    - 提供 questionnaire_answers / exam_answers 后，对应类型不再跳过
    - 提供 questionnaire_answers_map / exam_answers_map 可为不同小节指定不同答案
    - 每个账号使用独立会话，互不干扰
    - 并发数建议不超过 5，避免触发频率限制
    """
    # 导入账号
    try:
        source = None if file_format == "auto" else AccountSource(file_format)
        accounts = AccountImporter.import_accounts(file_path, source)
    except Exception as e:
        return _err(
            error_code="IMPORT_ACCOUNTS_FAILED",
            error_message=str(e),
        )

    if not accounts:
        return _err(
            error_code="NO_ACCOUNTS",
            error_message="账号列表为空",
        )

    # 获取基础 URL
    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")

    # 创建批量执行器
    executor = BatchExecutor(
        max_concurrency=max_concurrency,
        delay_between_accounts=delay_between_accounts,
    )

    # 解析答案配置映射（在任务函数外部解析一次）
    q_answers_map: dict[str, str] | None = None
    e_answers_map: dict[str, str] | None = None
    if questionnaire_answers_map:
        try:
            parsed = json.loads(questionnaire_answers_map)
            if isinstance(parsed, dict):
                q_answers_map = parsed
        except json.JSONDecodeError:
            pass
    if exam_answers_map:
        try:
            parsed = json.loads(exam_answers_map)
            if isinstance(parsed, dict):
                e_answers_map = parsed
        except json.JSONDecodeError:
            pass

    # 定义批量任务函数
    async def complete_course_task(client: UMUClient, course_id: str) -> dict[str, Any]:
        """单个账号的课程完成逻辑."""
        details: list[dict[str, Any]] = []
        completed = 0
        total = 0

        try:
            # 解析课程标识
            group_id, s_key, _ = _resolve_course_identifier(client, course_id)

            # 检查是否需要报名
            needs_enroll, enroll_id = _check_needs_enroll(client, group_id, s_key)
            if needs_enroll and enroll_id:
                try:
                    r = client.post(
                        client.mobile_url("/ajax/verify/auto"),
                        {"enroll_id": str(enroll_id)},
                    )
                    details.append({"action": "enroll", "success": r.get("error_code") == 0})
                    time.sleep(1)
                except Exception as e:
                    details.append({"action": "enroll", "success": False, "error": str(e)})

            # 获取课程结构
            r = client.get(
                client.mobile_url(
                    f"/uapi/v2/element/list?"
                    f"page=1&size=100&parent_id={group_id}&get_draft=0"
                )
            )
            if r.get("error_code") != 0:
                raise RuntimeError(f"无法获取课程: {r.get('message')}")

            elements = r.get("data", {}).get("list", [])
            total = len(elements)

            # 构建序号到 element_id 的映射（用于 by_index 参数）
            q_index_to_id: dict[int, str] = {}
            e_index_to_id: dict[int, str] = {}
            for el in elements:
                etype = el.get("type", 0)
                eid = str(el.get("element_id", ""))
                if etype == 1:
                    q_index_to_id[len(q_index_to_id) + 1] = eid
                elif etype == 10:
                    e_index_to_id[len(e_index_to_id) + 1] = eid

            # 将 by_index 参数转换为 element_id 映射，并与外部 map 合并
            if questionnaire_answers_by_index and q_index_to_id:
                try:
                    by_index = json.loads(questionnaire_answers_by_index)
                    if isinstance(by_index, dict):
                        for idx_str, cfg in by_index.items():
                            try:
                                idx = int(idx_str)
                                if idx in q_index_to_id:
                                    if q_answers_map is None:
                                        q_answers_map = {}
                                    q_answers_map[q_index_to_id[idx]] = cfg
                            except ValueError:
                                pass
                except json.JSONDecodeError:
                    pass
            if exam_answers_by_index and e_index_to_id:
                try:
                    by_index = json.loads(exam_answers_by_index)
                    if isinstance(by_index, dict):
                        for idx_str, cfg in by_index.items():
                            try:
                                idx = int(idx_str)
                                if idx in e_index_to_id:
                                    if e_answers_map is None:
                                        e_answers_map = {}
                                    e_answers_map[e_index_to_id[idx]] = cfg
                            except ValueError:
                                pass
                except json.JSONDecodeError:
                    pass

            # 解析 lesson_answers_by_index（按小节顺序，不区分类型）
            lesson_answers_map: dict[str, str] | None = None
            if lesson_answers_by_index:
                try:
                    parsed = json.loads(lesson_answers_by_index)
                    if isinstance(parsed, dict):
                        lesson_answers_map = parsed
                except json.JSONDecodeError:
                    pass

            # 构建小节序号映射
            lesson_index_map = _build_lesson_index_map(elements)

            # 逐个完成小节
            for lesson_idx, el in enumerate(elements, start=1):
                eid = str(el.get("element_id", ""))
                etype = el.get("type", 0)
                extend = el.get("extend", {}) or {}
                learn_status = extend.get("learn_status", 0)

                if learn_status == 2:
                    completed += 1
                    continue  # 已完成，跳过

                # 解析该小节的答案配置
                cfg, cfg_source = _resolve_lesson_answers_config(
                    lesson_idx, eid, etype,
                    lesson_answers_map,
                    q_answers_map, e_answers_map,
                    questionnaire_answers, exam_answers,
                )

                lesson_detail: dict[str, Any] = {
                    "element_id": eid,
                    "type": etype,
                    "lesson_index": lesson_idx,
                    "action": "",
                    "success": False,
                }
                if cfg_source != "none":
                    lesson_detail["config_source"] = cfg_source

                try:
                    if etype in (11, 13, 15):  # 视频/文章/图文
                        _makeweikestatus_sequence(client, eid)
                        lesson_detail["action"] = "browse"
                        lesson_detail["success"] = True
                        completed += 1

                    elif etype == 14:  # 文档
                        setup = el.get("setup", {}) or {}
                        vlt_min = setup.get("vlt_min", 0)
                        extras: dict[str, dict] = {}
                        if vlt_min:
                            extras["playing"] = {"left_time": str(vlt_min)}
                            extras["achieve"] = {"left_time": str(vlt_min), "vlt_status": "1"}
                        _makeweikestatus_sequence(client, eid, extras)
                        lesson_detail["action"] = "browse"
                        lesson_detail["success"] = True
                        completed += 1

                    elif etype == 6:  # 签到
                        setup = el.get("setup", {}) or {}
                        advance = setup.get("advance", "0")
                        q_payload = {
                            "answerList": [],
                            "answerInfo": [{"id": eid, "text": "学员"}],
                            "answerNumber": {},
                            "enrollId": 0,
                            "sessionId": eid,
                        }
                        client.post(
                            client.mobile_url("/ajax/insertAnswer"),
                            {"q": json.dumps(q_payload, ensure_ascii=False)},
                        )
                        _makeweikestatus_sequence(client, eid)
                        if advance == "1":
                            lesson_detail["action"] = "checkin_with_rating"
                        else:
                            lesson_detail["action"] = "checkin"
                        lesson_detail["success"] = True
                        completed += 1

                    elif etype == 1:  # 问卷
                        if cfg:
                            lesson_detail["action"] = "questionnaire_with_config"
                            # 获取题目
                            r = client.get(
                                client.desktop_url(
                                    f"/uapi/v1/poll/question-list?"
                                    f"element_id={eid}&page=1&size=999"
                                )
                            )
                            questions = r.get("data", {}).get("list", [])
                            if not questions:
                                raise RuntimeError("该问卷没有题目")

                            # 解析并验证
                            answers = _parse_answers_config(cfg)
                            is_valid, error_msg = _validate_answers_against_questions(
                                questions, answers, for_exam=False
                            )
                            if not is_valid:
                                raise RuntimeError(f"问卷答案验证失败: {error_msg}")

                            # 提交
                            answers_json_list = _build_questionnaire_answers_json(
                                questions, answers
                            )
                            answers_json = json.dumps(answers_json_list, ensure_ascii=False)

                            r = client.post(
                                client.mobile_url("/megrez/poll/v1/user-save-poll-result"),
                                {
                                    "answers": "[]",
                                    "session_id": eid,
                                    "submit_id": "0",
                                },
                            )
                            submit_id = r.get("data", {}).get("submit_id", "0")

                            r = client.post(
                                client.mobile_url("/megrez/poll/v1/save-poll-result"),
                                {
                                    "submit_id": str(submit_id),
                                    "element_id": eid,
                                    "is_anonymous": "0",
                                    "answer": answers_json,
                                },
                            )
                            if r.get("error_code") != 0:
                                raise RuntimeError(f"提交问卷失败: {r.get('message', '')}")

                            _makeweikestatus_sequence(client, eid)
                            lesson_detail["success"] = True
                            completed += 1

                        elif not skip_questionnaire:
                            lesson_detail["action"] = "questionnaire"
                            lesson_detail["skipped"] = True

                    elif etype == 10:  # 考试
                        if cfg:
                            lesson_detail["action"] = "exam_with_config"
                            # 获取 exam_submit_id
                            exam_submit_id = _get_exam_submit_id(client, eid)

                            # 获取 student_id
                            student_id = ""
                            try:
                                r = client.get(
                                    client.desktop_url("/uapi/v1/user/get")
                                )
                                student_id = r.get("data", {}).get("student_id", "")
                            except Exception:
                                pass

                            if not exam_submit_id or not student_id:
                                raise RuntimeError(
                                    "无法获取 exam_submit_id 或 student_id"
                                )

                            # startExam
                            r = client.post(
                                client.mobile_url("/megrez/exam/v1/startExam"),
                                {
                                    "session_id": eid,
                                    "student_id": str(student_id),
                                    "exam_submit_id": str(exam_submit_id),
                                },
                            )
                            if r.get("error_code") != 0:
                                raise RuntimeError(
                                    f"开始考试失败: {r.get('message', '')}"
                                )

                            # 获取考试题目
                            r = client.get(
                                client.mobile_url(
                                    f"/napi/v1/quiz/question-list"
                                    f"?_type=1&element_id={eid}&page=1&size=999"
                                )
                            )
                            questions = r.get("data", {}).get("list", [])
                            if not questions:
                                raise RuntimeError("该考试没有获取到题目")

                            # 解析并验证
                            answers = _parse_answers_config(cfg)
                            is_valid, error_msg = _validate_answers_against_questions(
                                questions, answers, for_exam=True
                            )
                            if not is_valid:
                                raise RuntimeError(f"考试答案验证失败: {error_msg}")

                            # 保存答案
                            exam_answers_list = _build_exam_answers_json(
                                questions, answers
                            )
                            for answer in exam_answers_list:
                                try:
                                    client.post(
                                        client.mobile_url(
                                            "/megrez/exam/v1/saveAnswer"
                                        ),
                                        {
                                            "session_id": eid,
                                            "answer_list": json.dumps(
                                                [answer], ensure_ascii=False
                                            ),
                                            "student_id": str(student_id),
                                            "exam_submit_id": str(exam_submit_id),
                                        },
                                    )
                                except Exception:
                                    pass
                                time.sleep(0.3)

                            # 提交考试
                            r = client.post(
                                client.mobile_url("/megrez/exam/v1/submitExam"),
                                {
                                    "session_id": eid,
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
                                raise RuntimeError(
                                    f"提交考试失败: {r.get('message', '')}"
                                )

                            _makeweikestatus_sequence(client, eid)
                            lesson_detail["success"] = True
                            completed += 1

                        elif not skip_exam:
                            lesson_detail["action"] = "exam"
                            lesson_detail["skipped"] = True

                    else:
                        lesson_detail["action"] = "skipped"

                except Exception as e:
                    lesson_detail["error"] = str(e)

                details.append(lesson_detail)
                time.sleep(0.5)  # 小节间延迟

            return {
                "completed_lessons": completed,
                "total_lessons": total,
                "details": details,
            }

        except Exception as e:
            return {
                "completed_lessons": completed,
                "total_lessons": total,
                "details": details + [{"error": str(e)}],
            }

    # 执行批量任务
    try:
        report = await executor.execute(
            accounts=accounts,
            task_func=complete_course_task,
            course_identifier=course_identifier,
            base_url=base_url,
            environment=env_name,
        )

        return _ok(
            data={
                "total_accounts": report.total_accounts,
                "successful": report.successful,
                "failed": report.failed,
                "total_duration_seconds": round(report.total_duration_seconds, 2),
                "status": report.status.value,
                "results": [
                    {
                        "username": r.username,
                        "success": r.success,
                        "error_message": r.error_message,
                        "completed_lessons": r.completed_lessons,
                        "total_lessons": r.total_lessons,
                        "duration_seconds": round(r.duration_seconds, 2),
                    }
                    for r in report.results
                ],
            },
            next_action="proceed",
            suggested_action="查看结果中的 failed 项，处理失败的账号",
        )
    except Exception as e:
        return _err(
            error_code="BATCH_EXECUTE_FAILED",
            error_message=str(e),
        )


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def stu_course_completion_workflow() -> str:
    """课程完成的标准操作流程."""
    return prompts.course_completion_workflow()


@mcp.prompt()
def stu_lesson_type_guide() -> str:
    """根据小节类型选择对应操作的参考指南."""
    return prompts.lesson_type_guide()


@mcp.prompt()
def stu_error_recovery_guide() -> str:
    """常见错误和恢复策略."""
    return prompts.error_recovery_guide()


@mcp.prompt()
def stu_exam_workflow_guide() -> str:
    """考试小节专用操作流程指引."""
    return prompts.exam_workflow_guide()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    """MCP 服务入口."""
    import asyncio

    print("=" * 60)
    print("UMU 学员端 MCP Server")
    print("=" * 60)
    print()
    print("支持的传输方式:")
    print("  - stdio:  标准输入输出（推荐用于本地 AI 助手）")
    print()
    print("环境变量:")
    print("  UMU_BASE_URL         - UMU 基础 URL（默认: https://www.umu.cn）")
    print("  UMU_STUDENT_USERNAME - 学生登录用户名")
    print("  UMU_STUDENT_PASSWORD - 学生登录密码")
    print()
    print("可用 Tools:")
    print("  认证: stu_login, stu_check_auth")
    print("  会话: stu_create_session, stu_list_sessions, stu_destroy_session")
    print("  解析: stu_resolve_course_url")
    print("  查结构: stu_get_my_courses, stu_get_course_structure, stu_get_learning_progress")
    print("  操作: stu_enroll_course, stu_browse_lesson,")
    print("        stu_get_questionnaire_questions, stu_submit_questionnaire,")
    print("        stu_check_in, stu_check_in_with_rating,")
    print("        stu_start_exam, stu_submit_exam")
    print("  批量: stu_batch_import_accounts, stu_batch_complete_course")
    print("  完成: stu_complete_course")
    print("  验证: stu_get_lesson_status")
    print("  讲师端: 请使用 umu-mcp-teacher")
    print()
    print("可用 Prompts:")
    print("  - stu_course_completion_workflow")
    print("  - stu_lesson_type_guide")
    print("  - stu_error_recovery_guide")
    print("  - stu_exam_workflow_guide")
    print()

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        print(f"不支持的传输方式: {transport}")
        print("支持: stdio, sse")


if __name__ == "__main__":
    main()
