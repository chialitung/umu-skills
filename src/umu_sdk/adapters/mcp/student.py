# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

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

import asyncio
import base64
import json
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator
from urllib.parse import parse_qs, urlparse

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.credential_loader import load_credentials_with_source
from ...core.encrypt import decrypt_aes_base64
from .utils import (
    format_login_summary,
    fuzzy_filter_items,
    get_login_identity,
    report_pagination_progress,
)
from . import prompts
from .batch import AccountImporter, AccountSource, BatchExecutor
from .session import SessionManager
from .shared_session_tools import (
    SessionToolConfig,
    make_check_auth_tool,
    make_create_session_tool,
    make_destroy_session_tool,
    make_list_sessions_tool,
    make_login_tool,
)

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
def _setup_logging() -> None:
    """配置结构化日志."""
    level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = os.getenv(
        "MCP_LOG_FORMAT",
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger("umu.mcp.student")
    root.setLevel(level)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger("umu.mcp.student")


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
    # 每次启动都重新读取学生账号凭据；优先级：显式参数/环境变量 > .env > 加密凭证
    username, password, source = load_credentials_with_source("student")

    # 创建会话管理器
    _session_manager = SessionManager(
        base_url=base_url,
    )

    # 创建默认会话
    default_session = await _session_manager.create_session()
    _umu_client = default_session.client

    # 如果有凭据，自动登录默认会话；否则正常启动
    if username and password:
        try:
            await _session_manager.login_session(
                default_session.session_id, username, password, credential_source=source.value
            )
            default_session.credential_source = source.value
            identity = get_login_identity(_umu_client)
            summary = format_login_summary(username, source.value, identity)
            logger.info("[MCP Student] 默认会话已自动登录: %s", summary)
        except Exception as e:
            logger.warning("[MCP Student] 默认会话自动登录失败: %s", e)
    else:
        logger.info("[MCP Student] 未配置学生账号凭据，请调用 stu_login 或 stu_create_session")

    logger.info("[MCP Student] UMU 学员端服务已启动，目标: %s", base_url)

    yield {"client": _umu_client, "session_manager": _session_manager}

    # 清理所有会话
    if _session_manager:
        _session_manager.close_all()
        _session_manager = None
    _umu_client = None
    logger.info("[MCP Student] UMU 学员端服务已关闭")


# ---------------------------------------------------------------------------
# 创建 MCP 服务器
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "umu-student",
    instructions="""UMU 学习平台学员端 MCP 服务。

提供课程学习相关的原子化操作，包括：
- 课程结构查询（报名状态、小节列表）
- 学习进度查询
- 获取我参与的课程列表（支持按学习状态筛选：已学习/学习中/待学习）
- 课程报名（支持简单报名和复杂报名表单）
- 复杂报名表单查询与提交（联系信息、文本题、单选、多选）
- 小节完成（浏览、问卷、签到、考试）
- 状态验证

AI 使用本服务时，应先调用 stu_get_course_structure 获取课程结构。
若课程需要报名且 stu_enroll_course 无法直接完成（如需要填写姓名、公司、
职场地址、部门等），则调用 stu_get_enroll_form 获取表单结构，
再调用 stu_submit_enroll_form 提交答案，最后继续完成小节。
每次操作后建议验证状态。
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


def _require_auth(client: UMUClient) -> str | None:
    """检查客户端认证状态.

    Returns:
        None 表示认证正常；否则返回错误信息字符串.
    """
    if not client.auth.is_authenticated():
        return "当前未登录或 Token 已过期，请先调用 stu_login 登录"
    return None


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


_STUDENT_SESSION_CONFIG = SessionToolConfig(
    role="stu",
    role_label="学员",
    tool_domain_hint="学习相关 Tool",
    login_success_suffix="现在可以调用其他学习相关 Tool",
    check_auth_success_suffix="学习相关 Tool",
    create_session_suggested_action="保存 session_id，后续调用 tool 时传入此参数",
    create_session_with_password=True,
    include_is_authenticated_in_session=True,
)


def _get_html(client: UMUClient, url: str) -> str:
    """获取页面 HTML 内容（用于从页面提取 enroll_id 等）.

    自动跟随重定向（httpx.Client 初始化时已配置 follow_redirects=True）.
    """
    headers = client.auth.get_auth_headers()
    headers["Accept"] = "text/html,application/xhtml+xml"
    resp = client.http.get(url, headers=headers, timeout=client.timeout)
    return resp.text


def _extract_group_and_skey(url: str) -> tuple[str | None, str | None]:
    """从 URL 字符串中提取 group_id 和 s_key.

    支持从 access-denied 等中间页的 from_url 参数递归解析。
    """
    parsed_match = re.search(r"groupId[=:](\d+)", url, re.IGNORECASE)
    skey_match = re.search(r"sKey[=:]([a-zA-Z0-9]+)", url, re.IGNORECASE)
    if parsed_match:
        return parsed_match.group(1), skey_match.group(1) if skey_match else ""

    # 某些拦截页会把真实课程链接放在 from_url 查询参数中
    query = urlparse(url).query
    from_url = parse_qs(query).get("from_url", [""])[0]
    if from_url:
        return _extract_group_and_skey(from_url)

    return None, None


def _resolve_course_identifier(
    client: UMUClient, identifier: str
) -> tuple[str, str | None, str]:
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
    group_id, s_key = _extract_group_and_skey(url)
    if group_id:
        return group_id, s_key, url

    # 需要通过 HTTP 请求获取重定向后的真实 URL
    try:
        h = client.auth.get_auth_headers() if client.auth.is_authenticated() else {}
        h["Accept"] = "text/html"
        resp = client.http.get(url, headers=h, timeout=client.timeout)
        final_url = str(resp.url)

        group_id, s_key = _extract_group_and_skey(final_url)
        if group_id:
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


async def _makeweikestatus_sequence(
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
            logger.warning(f"[makeweikestatus] {action} 失败: {e}")
        await asyncio.sleep(0.3)

    failed = [r for r in results if r["status"] == "failed"]
    return {
        "all_succeeded": len(failed) == 0,
        "failed_actions": [r["action"] for r in failed],
        "results": results,
    }


def _format_scorm_total_time(seconds: int) -> str:
    """将秒数转换为 SCORM 1.2 cmi.core.total_time 格式 HHHH:MM:SS.SS."""
    seconds = max(0, seconds)
    hours, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    return f"{hours:04d}:{mins:02d}:{secs:02d}.00"


def _extract_page_data_json(html: str) -> dict[str, Any] | None:
    """从 HTML 中提取 `window.pageData = {...}` 或 `var pageData = {...}` 并解析为 dict。"""
    for marker in ("window.pageData=", "var pageData=", "window.pageData =", "var pageData ="):
        idx = html.find(marker)
        if idx >= 0:
            i = idx + len(marker)
            break
    else:
        return None

    # 跳过空白
    while i < len(html) and html[i] in " \t\n\r":
        i += 1
    if i >= len(html) or html[i] != "{":
        return None

    # 使用大括号深度计数提取完整 JSON 对象（忽略字符串内的大括号）
    depth = 0
    in_string = False
    escape = False
    for j in range(i, len(html)):
        c = html[j]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i : j + 1])
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# 复杂报名表单辅助函数
# ---------------------------------------------------------------------------
def _get_enroll_short_url(
    client: UMUClient, group_id: str, s_key: str | None = None
) -> tuple[str | None, str | None]:
    """访问 course/pay 页面，提取 enroll_id 和报名短链 enrollUrl。

    Args:
        group_id: 课程组 ID
        s_key: 课程 URL 中的 sKey

    Returns:
        (enroll_id, enroll_url) 或 (None, None)
    """
    pay_url = client.mobile_url(f"/course/pay?groupId={group_id}")
    if s_key:
        pay_url += f"&sKey={s_key}"

    headers = client.auth.get_auth_headers()
    headers["Accept"] = "text/html"
    try:
        resp = client.http.get(pay_url, headers=headers, timeout=client.timeout)
        page_data = _extract_page_data_json(resp.text)
        if not page_data:
            return None, None

        data = page_data.get("data", {})
        # 数据结构可能在 data.enroll 或 data.info.enroll
        enroll = data.get("enroll") or data.get("info", {}).get("enroll") or {}
        enroll_id = enroll.get("enrollId") or data.get("enrollId")
        enroll_url = data.get("enrollUrl") or enroll.get("shareUrl")
        return str(enroll_id) if enroll_id else None, enroll_url
    except Exception:
        return None, None


def _fetch_enroll_form_page(client: UMUClient, enroll_url: str) -> dict[str, Any] | None:
    """访问报名短链对应的 /model/{short_url}?enroll 页面，解析报名表单结构。"""
    parsed = urlparse(enroll_url)
    short_path = parsed.path.strip("/")
    if not short_path:
        return None

    form_url = client.mobile_url(f"/model/{short_path}?enroll")
    headers = client.auth.get_auth_headers()
    headers["Accept"] = "text/html"
    try:
        resp = client.http.get(form_url, headers=headers, timeout=client.timeout)
        return _extract_page_data_json(resp.text)
    except Exception:
        return None


def _parse_enroll_form(page_data: dict[str, Any]) -> dict[str, Any]:
    """从 pageData 解析 contactInfo 和 sectionArr，返回结构化表单。"""
    data = page_data.get("data", {})
    enroll = data.get("enrollData") or data.get("info", {}).get("enroll") or {}

    contact_info = enroll.get("contactInfo", []) or []
    section_arr = enroll.get("sectionArr", []) or []

    contact_fields: list[dict[str, Any]] = []
    for item in contact_info:
        field = {
            "key": item.get("key", ""),
            "title": item.get("questionTitle", ""),
            "type": item.get("domType", "text"),
            "required": str(item.get("isRequired", "0")) == "1",
            "selected": str(item.get("isSelected", "0")) == "1",
        }
        if field["type"] in ("radio", "checkbox"):
            field["options"] = [
                {"value": opt.get("value", ""), "text": opt.get("text", "")}
                for opt in item.get("questionDefaultValue", []) or []
            ]
        contact_fields.append(field)

    section_questions: list[dict[str, Any]] = []
    for idx, item in enumerate(section_arr):
        qi = item.get("questionInfo", {}) or {}
        setup = qi.get("setup", {}) or {}
        dom_type = qi.get("domType", "")

        # paragraph 为说明文字，不需要答案
        is_paragraph = dom_type == "paragraph"
        question: dict[str, Any] = {
            "index": idx,
            "question_id": str(qi.get("questionId", "")),
            "title": qi.get("questionTitle", ""),
            "type": dom_type,
            "desc": qi.get("desc", ""),
            # setup.required 码值反的："0"=必填，"1"=选填
            "required": (not is_paragraph) and str(setup.get("required", "1")) == "0",
        }

        answer_arr = item.get("answerArr", []) or []
        if dom_type in ("textarea", "text", "input"):
            if answer_arr:
                question["answer_id"] = str(answer_arr[0].get("answerId", ""))
        elif dom_type == "number":
            if answer_arr:
                question["answer_id"] = str(answer_arr[0].get("answerId", ""))
        elif dom_type in ("radio", "checkbox"):
            question["options"] = [
                {
                    "answer_id": str(opt.get("answerId", "")),
                    "text": opt.get("answerContent", ""),
                }
                for opt in answer_arr
            ]
            if dom_type == "checkbox":
                question["min_options"] = int(setup.get("limitOptionsMin", 0) or 0)
                question["max_options"] = int(setup.get("limitOptionsMax", 0) or 0)

        section_questions.append(question)

    return {
        "enroll_id": str(enroll.get("enrollId", "")),
        "enroll_url": enroll.get("shareUrl", ""),
        "contact_fields": contact_fields,
        "section_questions": section_questions,
    }


def _validate_enroll_form(
    contact_fields: list[dict[str, Any]],
    section_questions: list[dict[str, Any]],
    contact_answers: dict[str, str],
    section_answers: list[dict[str, Any]],
) -> str | None:
    """校验报名表单答案是否满足必填和格式要求。

    返回 None 表示校验通过，否则返回错误信息。
    """
    # 校验联系信息：selected=True 且 required=True 的字段必须填写
    for field in contact_fields:
        if field.get("selected") and field.get("required"):
            key = field["key"]
            value = contact_answers.get(key, "")
            if not str(value).strip():
                return f"联系信息[{field.get('title', key)}]为必填项，请提供"

    ans_map = {a.get("question_id", ""): a for a in section_answers}

    for q in section_questions:
        qid = q["question_id"]
        qtype = q.get("type", "")
        ans = ans_map.get(qid)

        if qtype == "paragraph":
            continue

        # 必填校验
        if q.get("required"):
            if not ans:
                return f"[{q.get('title', qid)}]为必填项，请提供答案"

        if not ans:
            continue

        if qtype in ("textarea", "text", "input"):
            text = str(ans.get("text", "")).strip()
            if q.get("required") and not text:
                return f"[{q.get('title', qid)}]为必填项，请提供文本答案"
        elif qtype == "radio":
            answer_id = str(ans.get("answer_id", "")).strip()
            if q.get("required") and not answer_id:
                return f"[{q.get('title', qid)}]为必填项，请选择一个选项"
        elif qtype == "checkbox":
            answer_ids = ans.get("answer_ids", []) or []
            count = len(answer_ids)
            min_opt = q.get("min_options", 0)
            max_opt = q.get("max_options", 0)
            if q.get("required") and count == 0:
                return f"[{q.get('title', qid)}]为必填项，请至少选择一项"
            if min_opt and count < min_opt:
                return f"[{q.get('title', qid)}]至少需要选择 {min_opt} 项"
            if max_opt and count > max_opt:
                return f"[{q.get('title', qid)}]最多只能选择 {max_opt} 项"
        elif qtype == "number":
            number = ans.get("number")
            if q.get("required") and number is None:
                return f"[{q.get('title', qid)}]为必填项，请提供数值"

    return None


def _build_insert_answer_payload(
    section_questions: list[dict[str, Any]],
    section_answers: list[dict[str, Any]],
    enroll_id: str,
) -> dict[str, Any]:
    """根据 section 答案构造 /ajax/insertAnswer 需要的 payload。"""
    q_map = {q["question_id"]: q for q in section_questions}

    answer_list: list[str] = []
    answer_info: list[dict[str, str]] = []
    answer_number: dict[str, Any] = {}

    for ans in section_answers:
        qid = ans.get("question_id", "")
        q = q_map.get(qid)
        if not q:
            continue
        qtype = q.get("type", "")

        if qtype in ("textarea", "text", "input"):
            answer_info.append(
                {"id": q.get("answer_id", ""), "text": str(ans.get("text", ""))}
            )
        elif qtype == "radio":
            answer_id = str(ans.get("answer_id", "")).strip()
            if answer_id:
                answer_list.append(answer_id)
        elif qtype == "checkbox":
            for aid in ans.get("answer_ids", []) or []:
                if aid:
                    answer_list.append(str(aid))
        elif qtype == "number":
            number = ans.get("number")
            if number is not None:
                answer_number[q.get("answer_id", "")] = number

    return {
        "answerList": answer_list,
        "answerInfo": answer_info,
        "answerNumber": answer_number,
        "sessionId": 0,
        "enrollId": str(enroll_id),
    }


def _extract_uscorm_runtime(
    client: UMUClient,
    element_id: str,
    share_url: str,
) -> dict[str, Any]:
    """从 SCORM 小节分享页提取 UMU 自研 wrapper 的运行时信息。

    UMU 对部分 SCORM 资源使用自研 wrapper（非 Moodle），运行时页由：
      https://www.umu.cn/scorm/{token}/element/{base64(element_id)}
    提供，commit 目标为 /napi/scorm/scorm12 或 /napi/scorm/scorm2004。

    返回 dict，包含：
      mode: "umu_wrapper"
      scorm_version: "1.2" | "2004"
      commit_url: 绝对提交地址
      launch_url: 启动页地址
      lms_data: 初始 CMI 数据
    失败时抛出 ValueError。
    """
    headers = client.auth.get_auth_headers()
    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    # 1. 访问分享页，拿到包含 resource_store 的 pageData
    resp = client.http.get(share_url, headers=headers, follow_redirects=True)
    page_data = _extract_page_data_json(resp.text or "")
    if not page_data:
        raise ValueError("分享页未包含 pageData")

    resource_store = page_data.get("data", {}).get("resource_store") or page_data.get("resource_store") or []
    if not resource_store:
        raise ValueError("pageData 中没有 resource_store")

    resource = resource_store[0]
    encrypted_url = resource.get("transcoding_url") or resource.get("url")
    if not encrypted_url:
        raise ValueError("resource_store 缺少加密 URL")

    decrypted = decrypt_aes_base64(str(encrypted_url))
    # decrypted 形如 https://umu.cn/scorm/{token}/element
    parsed = urlparse(decrypted)
    base_path = parsed.path.rstrip("/")
    if not base_path or not re.search(r"/scorm/[^/]+/element$", base_path):
        raise ValueError(f"解密后的 SCORM URL 不符合预期: {decrypted}")

    base64_id = base64.b64encode(str(element_id).encode()).decode()
    launch_path = f"{base_path}/{base64_id}"
    launch_url = f"https://www.umu.cn{launch_path}"

    # 2. 访问启动页，读取 commit endpoint 与初始 lms_data
    launch_resp = client.http.get(launch_url, headers=headers, follow_redirects=True)
    launch_html = launch_resp.text or ""
    launch_page_data = _extract_page_data_json(launch_html)
    if not launch_page_data:
        raise ValueError("SCORM 启动页未包含 pageData")

    scorm_version = launch_page_data.get("scorm_version", "1.2")
    lms_data = launch_page_data.get("lms_data", {}) or {}

    version_commit_suffix = {"1.2": "scorm12", "2004": "scorm2004"}.get(str(scorm_version), "")
    commit_urls = re.findall(r"settings\.lmsCommitUrl\s*=\s*\"([^\"]+)\"", launch_html)
    commit_candidates = [u for u in commit_urls if version_commit_suffix in u] if version_commit_suffix else []
    if commit_candidates:
        commit_path = commit_candidates[0]
    elif commit_urls:
        commit_path = commit_urls[0]
    else:
        raise ValueError("SCORM 启动页未找到 commit URL")
    if commit_path.startswith("http"):
        commit_url = commit_path
    else:
        commit_url = f"https://www.umu.cn{commit_path}"

    return {
        "mode": "umu_wrapper",
        "scorm_version": str(scorm_version),
        "commit_url": commit_url,
        "launch_url": launch_url,
        "lms_data": lms_data,
    }


def _post_uscorm_commit(
    client: UMUClient,
    runtime: dict[str, Any],
    cmi: dict[str, Any],
) -> dict[str, Any]:
    """向 UMU 自研 SCORM wrapper 提交 CMI 数据。"""
    headers = client.auth.get_auth_headers()
    headers["Content-Type"] = "application/json;charset=UTF-8"

    response = client.http.post(
        runtime["commit_url"],
        json={"cmi": cmi},
        headers=headers,
        follow_redirects=False,
    )
    text = (response.text or "").strip()
    try:
        result = json.loads(text)
    except Exception as e:
        raise RuntimeError(f"SCORM commit 返回非 JSON ({response.status_code}): {text[:200]} ({e})")

    if result.get("error_code") != 0:
        raise RuntimeError(f"SCORM commit 失败: {result.get('error_message') or text[:200]}")
    if result.get("data", {}).get("status") != 1:
        raise RuntimeError(f"SCORM commit 未生效: {result.get('data')}")
    return result


def _build_uscorm_12_cmi(
    runtime: dict[str, Any],
    status: str,
    score: int | None,
    duration_seconds: int,
    lesson_location: str,
    suspend_data_json: str,
) -> dict[str, Any]:
    """构造 UMU wrapper SCORM 1.2 需要的 cmi 对象。"""
    lms_data = runtime.get("lms_data", {}) or {}
    initial_cmi = lms_data.get("cmi", {}) or {}
    initial_core = initial_cmi.get("core", {}) or {}

    core: dict[str, Any] = {
        "entry": initial_core.get("entry", "ab-initio"),
        "student_id": initial_core.get("student_id", ""),
        "student_name": initial_core.get("student_name", ""),
        "lesson_location": lesson_location or initial_core.get("lesson_location", ""),
        "lesson_status": status,
        "credit": initial_core.get("credit", "credit"),
        "lesson_mode": initial_core.get("lesson_mode", "normal"),
        "exit": "",
    }
    if duration_seconds > 0:
        core["total_time"] = _format_scorm_total_time(duration_seconds)
    else:
        core["total_time"] = initial_core.get("total_time", "0000:00:00.00")

    score_obj: dict[str, Any] = {
        "min": "0",
        "max": "100",
    }
    if score is not None:
        score_obj["raw"] = str(score)
    else:
        score_obj["raw"] = initial_core.get("score", {}).get("raw", "")
    core["score"] = score_obj

    cmi: dict[str, Any] = {
        "comments": initial_cmi.get("comments", ""),
        "core": core,
        "suspend_data": suspend_data_json or initial_cmi.get("suspend_data", ""),
    }
    return cmi


def _parse_scorm_launch_url(url: str) -> dict[str, str]:
    """从 SCORM launch URL 解析 Moodle 运行时参数。

    返回 dict，包含 subdomain, a, scoid, course, sesskey, attempt。
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("launch url 缺少 host")

    parts = parsed.hostname.split(".")
    subdomain = parts[0] if parts else ""

    path_parts = parsed.path.strip("/").split("/")
    # 期望路径: /scorm/{a}/launch/{scoid}/course/{course}/element/{base64_id}
    if (
        len(path_parts) >= 7
        and path_parts[0] == "scorm"
        and path_parts[2] == "launch"
        and path_parts[4] == "course"
        and path_parts[6] == "element"
    ):
        params: dict[str, str] = {
            "a": path_parts[1],
            "scoid": path_parts[3],
            "course": path_parts[5],
        }
    else:
        params = {}

    qs = parse_qs(parsed.query)
    params.update({k: v[0] for k, v in qs.items()})
    params.setdefault("attempt", "1")
    params["subdomain"] = subdomain

    required = ["a", "scoid", "course", "sesskey"]
    missing = [k for k in required if not params.get(k)]
    if missing:
        raise ValueError(f"launch url 缺少必要参数: {missing}")

    return params


def _post_scorm_datamodel(
    client: UMUClient,
    launch_params: dict[str, str],
    extra_fields: dict[str, str],
) -> str:
    """向 Moodle datamodel.php 提交一条 CMI 记录。

    datamodel.php 返回 text/plain，成功时以 true 开头，因此不能复用 client.post。
    """
    url = f'https://{launch_params["subdomain"]}.m.umu.cn/mod/scorm/datamodel.php'
    form: dict[str, str] = {
        "id": "",
        "a": launch_params["a"],
        "sesskey": launch_params["sesskey"],
        "attempt": launch_params.get("attempt", "1"),
        "scoid": launch_params["scoid"],
    }
    form.update(extra_fields)

    headers = client.auth.get_auth_headers()
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    response = client.http.post(url, data=form, headers=headers, follow_redirects=False)
    text = (response.text or "").strip()
    if not text.startswith("true"):
        raise RuntimeError(f"datamodel.php 提交失败 ({response.status_code}): {text[:200]}")
    return text


def _resolve_scorm_launch_params(
    client: UMUClient,
    element_id: str,
    provided_url: str | None = None,
) -> dict[str, Any]:
    """解析 SCORM 启动参数。

    优先使用调用方显式提供的 launch URL；否则尝试从 element 详情中查找 launch URL。
    若检测到 UMU 自研 SCORM wrapper（非 Moodle），则返回 wrapper 运行时信息。
    自动发现失败时抛出 ValueError。
    """
    if provided_url:
        return _parse_scorm_launch_url(provided_url)

    # 1. 尝试从 element 详情中读取 launch 相关字段
    share_url = ""
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
        element_data = r.get("data", {}) or {}
        setup = element_data.get("setup", {}) or {}

        for key in ("scorm_launch_url", "launch_url", "resource_url"):
            url = element_data.get(key) or setup.get(key)
            if url and "scorm" in url:
                return _parse_scorm_launch_url(str(url))

        share_url = element_data.get("share_url") or setup.get("share_url")
        # 2. 跟踪 share_url 重定向，看是否会落到 Moodle launch URL
        if share_url:
            headers = client.auth.get_auth_headers()
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            resp = client.http.get(share_url, headers=headers, follow_redirects=False)
            if resp.status_code in (301, 302, 307, 308):
                location = resp.headers.get("location", "")
                if "scorm" in location and "launch" in location:
                    return _parse_scorm_launch_url(location)
    except Exception as e:
        print(f"[_resolve_scorm_launch_params] Moodle 自动发现失败: {e}", file=sys.stderr)

    # 3. 尝试 UMU 自研 SCORM wrapper
    if share_url:
        try:
            return _extract_uscorm_runtime(client, element_id, share_url)
        except Exception as e:
            print(f"[_resolve_scorm_launch_params] UMU wrapper 自动发现失败: {e}", file=sys.stderr)

    raise ValueError("无法自动发现 SCORM 启动参数，请提供 scorm_launch_url")


# ---------------------------------------------------------------------------
# Tools: 认证
# ---------------------------------------------------------------------------


mcp.tool()(
    make_login_tool(
        _STUDENT_SESSION_CONFIG,
        get_client=_get_client,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_check_auth_tool(
        _STUDENT_SESSION_CONFIG,
        get_client=_get_client,
        ok=_ok,
        err=_err,
    )
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
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    fuzzy_title: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的课程标题模糊匹配关键词。提供时会自动获取全量列表"
            "并筛选最匹配的候选，返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
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

    def _format_course(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "group_id": str(c.get("group_id", c.get("id", ""))),
            "title": c.get("group_title", c.get("title", "")),
            "cover_url": c.get("show_pic", c.get("cover_url", c.get("cover", ""))),
            "status": c.get("learn_status", c.get("status", "")),
            "is_finished": c.get("learn_status") == 3 or c.get("is_finished", False),
            "complete_rate": c.get("finish_ratio", c.get("complete_rate", 0)),
        }

    def _fetch_page(
        p: int, sz: int, preferred_endpoint: str | None = None
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Fetch a single page. Returns (courses, total_all, successful_endpoint)."""
        if preferred_endpoint:
            endpoints_to_try = [preferred_endpoint]
        else:
            endpoints_to_try = [
                client.desktop_url(f"/api/group/getmyparticipatedgrouplist?t={int(time.time()*1000)}&learn_status=0&page={p}&size={sz}"),
                client.desktop_url(f"/uapi/v1/course/list-my-course?page={p}&size={sz}"),
                client.desktop_url(f"/uapi/v1/course/my-courses?page={p}&size={sz}"),
            ]

        last_error = ""
        for url in endpoints_to_try:
            try:
                r = client.get(url)
                if r.get("error_code") == 0 or r.get("status") in (True, "true"):
                    data = r.get("data", {})
                    items = data.get("list", []) if isinstance(data, dict) else data
                    courses = [_format_course(c) for c in items]
                    page_info = data.get("page_info", {}) if isinstance(data, dict) else {}
                    total_all = int(page_info.get("list_total_num", 0) or 0)
                    return courses, total_all, url
                else:
                    last_error = r.get("message", r.get("error", "未知错误"))
            except Exception as e:
                last_error = str(e)
                continue

        raise RuntimeError(f"无法获取课程列表: {last_error}")

    effective_fetch_all = fetch_all or bool(fuzzy_title and fuzzy_title.strip())

    try:
        if effective_fetch_all:
            batch_size = 50
            all_courses: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1
            successful_endpoint: str | None = None

            while True:
                page_courses, total_all, successful_endpoint = _fetch_page(
                    current_page, batch_size, successful_endpoint
                )
                all_courses.extend(page_courses)

                report_pagination_progress(
                    "stu_get_my_courses",
                    current_page,
                    len(all_courses),
                    total_all,
                    batch_size,
                )

                if not page_courses or len(all_courses) >= total_all:
                    report_pagination_progress(
                        "stu_get_my_courses",
                        current_page,
                        len(all_courses),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "stu_get_my_courses",
                        current_page,
                        len(all_courses),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    break

                current_page += 1

            result_courses = all_courses
            if fuzzy_title and fuzzy_title.strip():
                result_courses = fuzzy_filter_items(
                    all_courses,
                    fuzzy_title,
                    key="title",
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )

            return _ok(
                data={
                    "total": len(result_courses),
                    "page": current_page,
                    "page_size": batch_size,
                    "courses": result_courses,
                },
                next_action="proceed",
                suggested_action="选择要学习的课程，调用 stu_get_course_structure 获取详情",
            )

        # Single-page mode (original behavior)
        courses, total_all, _ = _fetch_page(page, page_size)
        return _ok(
            data={
                "total": total_all,
                "page": page,
                "page_size": page_size,
                "courses": courses,
            },
            next_action="proceed",
            suggested_action="选择要学习的课程，调用 stu_get_course_structure 获取详情",
        )

    except Exception as e:
        return _err(
            error_code="FETCH_MY_COURSES_FAILED",
            error_message=str(e),
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
async def stu_get_enroll_form(
    course_identifier: Annotated[
        str,
        Field(description="课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）"),
    ],
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
        ),
    ] = None,
) -> str:
    """获取复杂报名表单的字段和题目结构。

    触发条件：当 stu_enroll_course 报名后仍无法学习，或 stu_get_course_structure
    提示需要报名但课程实际要求填写姓名、手机号、公司、职场地址、部门等信息时调用。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：无（只读查询）。

    返回的 contact_fields 中：
    - selected=true 的字段才会在表单中显示并提交
    - required=true 表示该字段必填

    返回的 section_questions 中：
    - type=paragraph 为说明文字，不需要答案
    - type=textarea/text/input 为文本题，提交时使用 {question_id, text}
    - type=radio 为单选题，提交时使用 {question_id, answer_id}
    - type=checkbox 为多选题，提交时使用 {question_id, answer_ids}
    - required=true 表示该题必填
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="请先调用 stu_login 登录",
            next_action="needs_user_input",
        )

    try:
        group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        return _err(
            error_code="INVALID_COURSE_IDENTIFIER",
            error_message=str(e),
            suggested_action="提供有效的课程链接、访问码或短域名",
        )

    try:
        enroll_id, enroll_url = _get_enroll_short_url(client, group_id, s_key)
        if not enroll_id or not enroll_url:
            return _err(
                error_code="ENROLL_FORM_NOT_FOUND",
                error_message="无法从课程页面提取报名信息，该课程可能不需要报名或访问受限",
                suggested_action="调用 stu_get_course_structure 确认课程报名状态",
            )

        page_data = _fetch_enroll_form_page(client, enroll_url)
        if not page_data:
            return _err(
                error_code="ENROLL_FORM_PARSE_FAILED",
                error_message="无法解析报名表单页面",
                suggested_action="稍后重试，或检查网络连接",
            )

        form = _parse_enroll_form(page_data)
        form["group_id"] = group_id
        form["s_key"] = s_key

        return _ok(
            data=form,
            next_action="proceed",
            suggested_action="根据返回的表单字段准备答案，调用 stu_submit_enroll_form 提交",
        )
    except Exception as e:
        return _err(
            error_code="GET_ENROLL_FORM_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接和课程访问权限",
        )


@mcp.tool()
async def stu_submit_enroll_form(
    course_identifier: Annotated[
        str,
        Field(description="课程标识。支持格式：访问码（如 aet504）、短域名（如 aet504.umu.cn）、完整URL（如 https://<domain>/course/?groupId=7324740&sKey=7fea）"),
    ],
    contact_answers: Annotated[
        dict[str, str],
        Field(
            default={},
            description='联系信息答案。key 对应 stu_get_enroll_form 返回的 contact_fields.key，例如 {"username": "张三", "mobile": "13800138000", "company": "腾讯"}',
        ),
    ] = None,
    section_answers: Annotated[
        list[dict[str, Any]],
        Field(
            default=None,
            description='报名问题答案列表。每题一个对象：文本题 {question_id, text}，单选题 {question_id, answer_id}，多选题 {question_id, answer_ids}',
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
    """提交复杂报名表单（联系信息 + 报名问题）。

    触发条件：调用 stu_get_enroll_form 获取表单结构并准备好答案后调用。
    前置依赖：需先调用 stu_login 完成登录，且已通过 stu_get_enroll_form 获取表单结构。
    副作用：会完成课程报名，提交后学员可正常学习课程小节。

    注意：
    - 只会提交 contact_fields 中 selected=true 的字段
    - section_questions 中 type=paragraph 的题目不需要提交答案
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="请先调用 stu_login 登录",
            next_action="needs_user_input",
        )

    contact_answers = contact_answers or {}
    section_answers = section_answers or []

    try:
        group_id, s_key, _ = _resolve_course_identifier(client, course_identifier)
    except ValueError as e:
        return _err(
            error_code="INVALID_COURSE_IDENTIFIER",
            error_message=str(e),
            suggested_action="提供有效的课程链接、访问码或短域名",
        )

    try:
        # 1. 获取表单结构和 enroll_id/short_url
        enroll_id, enroll_url = _get_enroll_short_url(client, group_id, s_key)
        if not enroll_id or not enroll_url:
            return _err(
                error_code="ENROLL_FORM_NOT_FOUND",
                error_message="无法从课程页面提取报名信息",
                suggested_action="调用 stu_get_enroll_form 确认表单结构",
            )

        page_data = _fetch_enroll_form_page(client, enroll_url)
        if not page_data:
            return _err(
                error_code="ENROLL_FORM_PARSE_FAILED",
                error_message="无法解析报名表单页面",
                suggested_action="稍后重试",
            )

        form = _parse_enroll_form(page_data)
        contact_fields = form.get("contact_fields", [])
        section_questions = form.get("section_questions", [])

        # 2. 校验答案
        error_msg = _validate_enroll_form(
            contact_fields, section_questions, contact_answers, section_answers
        )
        if error_msg:
            return _err(
                error_code="ENROLL_FORM_VALIDATION_FAILED",
                error_message=error_msg,
                suggested_action="根据 stu_get_enroll_form 返回的表单结构补充必填答案",
                data={"contact_fields": contact_fields, "section_questions": section_questions},
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
            return _ok(
                data={
                    "enroll_id": enroll_id,
                    "is_enrolled": is_enrolled,
                    "pay_status": pay_status,
                },
                next_action="enrollment_completed",
                suggested_action="报名成功，现在可以调用 stu_get_course_structure 获取课程结构并学习",
            )

        return _err(
            error_code="ENROLL_FORM_SUBMIT_INCOMPLETE",
            error_message=f"报名提交后状态未确认: is_enrolled={is_enrolled}, pay_status={pay_status}",
            suggested_action="调用 stu_get_enroll_form 或 stu_get_course_structure 检查当前报名状态",
            data={"is_enrolled": is_enrolled, "pay_status": pay_status},
        )
    except Exception as e:
        return _err(
            error_code="SUBMIT_ENROLL_FORM_ERROR",
            error_message=str(e),
            suggested_action="检查网络连接、答案格式和课程访问权限",
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

        await _makeweikestatus_sequence(client, element_id, extras)
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
async def stu_complete_scorm_section(
    element_id: Annotated[str, Field(description="小节元素 ID，来自 stu_get_course_structure 的 element_id")],
    group_id: Annotated[
        str,
        Field(default="", description="课程组 ID（可选），用于完成后验证进度"),
    ] = "",
    status: Annotated[
        str,
        Field(
            default="passed",
            description='SCORM 1.2 cmi.core.lesson_status，可选 passed/completed/failed/incomplete/browsed',
        ),
    ] = "passed",
    score: Annotated[
        int | None,
        Field(default=None, description="得分 0-100，提交到 cmi.core.score.raw"),
    ] = None,
    duration_seconds: Annotated[
        int,
        Field(
            default=0,
            description="本次学习时长（秒），格式化为 HHHH:MM:SS.SS 提交到 cmi.core.total_time",
        ),
    ] = 0,
    lesson_location: Annotated[
        str,
        Field(default="", description="可选：写入 cmi.core.lesson_location"),
    ] = "",
    suspend_data_json: Annotated[
        str,
        Field(default="", description="高级：自定义 cmi.suspend_data JSON 字符串"),
    ] = "",
    interactions_json: Annotated[
        str,
        Field(default="", description="高级：自定义 cmi.interactions 数组 JSON 字符串"),
    ] = "",
    scorm_launch_url: Annotated[
        str,
        Field(
            default="",
            description="可选：完整的 SCORM launch URL。自动发现失败时作为兜底",
        ),
    ] = "",
    session_id: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的会话 ID。如果提供，在指定会话中执行；否则使用默认会话。",
        ),
    ] = None,
) -> str:
    """完成 SCORM 1.2 格式小节。

    触发条件：当 stu_get_course_structure 返回 completion_type=scorm 时调用。
    前置依赖：学员已登录，且小节类型为 SCORM。
    副作用：向 Moodle 或 UMU 自研 SCORM wrapper 提交 CMI 数据，可能改变学习状态。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="请先调用 stu_login 登录",
            next_action="needs_user_input",
        )

    # 1. 校验元素类型
    try:
        r = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
        element_data = r.get("data", {}) or {}
        if element_data.get("type") != 11:
            return _err(
                error_code="INVALID_SECTION_TYPE",
                error_message=f'小节类型不是 SCORM/H5：type={element_data.get("type")}',
                suggested_action="请确认 element_id 对应 SCORM 小节",
                next_action="needs_user_input",
            )
        setup = element_data.get("setup", {}) or {}
        if setup.get("content_type") != "scorm":
            return _err(
                error_code="INVALID_SECTION_TYPE",
                error_message="小节不是 SCORM 类型（setup.content_type != scorm）",
                suggested_action="请使用 stu_browse_lesson 完成普通微课/视频小节",
                next_action="needs_user_input",
            )
    except Exception as e:
        return _err(
            error_code="ELEMENT_FETCH_FAILED",
            error_message=str(e),
            suggested_action="检查 element_id 是否正确",
            next_action="needs_user_input",
        )

    # 2. 解析 SCORM 启动参数
    try:
        launch_params = _resolve_scorm_launch_params(
            client, element_id, provided_url=scorm_launch_url or None
        )
    except Exception as e:
        return _err(
            error_code="SCORM_LAUNCH_RESOLVE_FAILED",
            error_message=str(e),
            suggested_action="请提供 scorm_launch_url，或确认学员有权限访问该小节",
            next_action="needs_user_input",
        )

    # 3. 初始化 makeweikestatus 状态机
    try:
        await _makeweikestatus_sequence(client, element_id)
    except Exception as e:
        print(f"[stu_complete_scorm_section] makeweikestatus 失败: {e}", file=sys.stderr)

    # 4. 提交 CMI 字段
    try:
        if launch_params.get("mode") == "umu_wrapper":
            if interactions_json:
                print(
                    "[stu_complete_scorm_section] UMU wrapper 暂不支持 interactions_json，已忽略",
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
                    return _err(
                        error_code="INVALID_INTERACTIONS_JSON",
                        error_message=f"interactions_json 解析失败: {e}",
                        suggested_action="请提供合法的 JSON 数组",
                        next_action="needs_user_input",
                    )

            _post_scorm_datamodel(client, launch_params, extra)
            # 空 commit，模拟 LMSCommit("")
            _post_scorm_datamodel(client, launch_params, {})
    except Exception as e:
        return _err(
            error_code="SCORM_DATAMODEL_ERROR",
            error_message=str(e),
            suggested_action="检查 scorm_launch_url 是否有效，或稍后重试",
            next_action="retry",
        )

    # 5. 验证小节状态
    try:
        status_result = await stu_get_lesson_status(element_id, group_id, session_id)
        parsed = json.loads(status_result)
        if parsed.get("success") and parsed.get("data", {}).get("is_completed"):
            return _ok(
                data=parsed["data"],
                next_action="lesson_completed",
                suggested_action="小节已完成",
            )
        return _ok(
            data=parsed.get("data"),
            next_action="proceed",
            suggested_action="已提交 SCORM 数据，请稍后再次确认完成状态",
        )
    except Exception as e:
        return _err(
            error_code="STATUS_CHECK_ERROR",
            error_message=str(e),
            suggested_action="已提交数据，但状态验证失败，请稍后调用 stu_get_lesson_status 检查",
            next_action="retry",
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
    - 考试: 0=单选, 1=多选, 2=文本/开放题, 3=文本/开放题
    """
    if is_exam:
        names = {0: "单选", 1: "多选", 2: "文本", 3: "文本"}
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

        # 问卷和考试的 type 码值不同，需分开判断
        # 问卷: 2=单选, 3=多选, 4=文本, 5=数值
        # 考试: 0=单选, 1=多选, 2=文本/开放题, 3=文本/开放题
        if for_exam:
            is_single = qtype == 0
            is_multi = qtype == 1
            is_open = qtype in (2, 3)
        else:
            is_single = qtype == 2
            is_multi = qtype == 3
            is_open = qtype == 4

        if is_single:  # 单选
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

        elif is_multi:  # 多选
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

        elif is_open:  # 开放题
            pass  # 非空已在上面检查

        elif qtype == 5 and not for_exam:  # 数值/评分（仅问卷）
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
    # 考试 API 的题目类型: 0=单选, 1=多选, 2=文本/开放题, 3=文本/开放题
    type_map = {0: "radio", 1: "checkbox", 2: "textarea", 3: "textarea"}
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

        elif qtype in (2, 3):  # 文本/开放题
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
        await _makeweikestatus_sequence(client, element_id)

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
        await _makeweikestatus_sequence(client, element_id)

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
            logger.warning(f"[stu_check_in] insertAnswer 失败（非致命）: {e}")

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
            logger.warning(f"[stu_check_in] insertWxAnswer 失败（非致命）: {e}")

        # 执行 makeweikestatus 序列
        await _makeweikestatus_sequence(client, element_id)

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
            logger.warning(f"[stu_check_in_with_rating] insertAnswer 失败（非致命）: {e}")

        # 执行 makeweikestatus 序列
        await _makeweikestatus_sequence(client, element_id)

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
                logger.warning(f"[stu_submit_exam_with_config] saveAnswer 失败（非致命）: {e}")
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
            return _err(
                error_code="SUBMIT_EXAM_FAILED",
                error_message=r.get("message", "提交考试失败"),
                suggested_action="检查 exam_submit_id 是否正确",
            )

        # 11. 执行 makeweikestatus 序列
        await _makeweikestatus_sequence(client, element_id)

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


mcp.tool()(
    make_create_session_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_list_sessions_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
)


mcp.tool()(
    make_destroy_session_tool(
        _STUDENT_SESSION_CONFIG,
        get_session_manager=lambda: _session_manager,
        ok=_ok,
        err=_err,
    )
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
                await asyncio.sleep(1)
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
                    await _makeweikestatus_sequence(client, eid)
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
                    await _makeweikestatus_sequence(client, eid, extras)
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
                    await _makeweikestatus_sequence(client, eid)
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

                        await _makeweikestatus_sequence(client, eid)
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
                            await asyncio.sleep(0.3)

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

                        await _makeweikestatus_sequence(client, eid)
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
            await asyncio.sleep(0.5)

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
        # 复制外部 map，避免并发任务共享修改同一字典
        local_q_answers_map = dict(q_answers_map) if q_answers_map else {}
        local_e_answers_map = dict(e_answers_map) if e_answers_map else {}
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
                    await asyncio.sleep(1)
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
                                    local_q_answers_map[q_index_to_id[idx]] = cfg
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
                                    local_e_answers_map[e_index_to_id[idx]] = cfg
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
                    local_q_answers_map, local_e_answers_map,
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
                        await _makeweikestatus_sequence(client, eid)
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
                        await _makeweikestatus_sequence(client, eid, extras)
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
                        await _makeweikestatus_sequence(client, eid)
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

                            await _makeweikestatus_sequence(client, eid)
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
                                await asyncio.sleep(0.3)

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

                            await _makeweikestatus_sequence(client, eid)
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
                await asyncio.sleep(0.5)  # 小节间延迟

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


@mcp.tool()
async def stu_list_participated_courses(
    page: Annotated[int, Field(default=1, ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100")] = 20,
    learn_status: Annotated[
        int,
        Field(default=0, ge=0, le=3, description="学习状态筛选：0=所有, 1=已学习, 2=学习中, 3=待学习"),
    ] = 0,
    fetch_all: Annotated[
        bool,
        Field(default=False, description="是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。"),
    ] = False,
    fuzzy_title: Annotated[
        str | None,
        Field(
            default=None,
            description="可选的课程标题模糊匹配关键词。提供时会自动获取全量列表"
            "并筛选最匹配的候选，返回相似度分数。",
        ),
    ] = None,
    top_k: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="模糊匹配时最多返回的候选数量"),
    ] = 10,
    similarity_threshold: Annotated[
        float,
        Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
        ),
    ] = 0.3,
    session_id: Annotated[
        str | None,
        Field(default=None, description="可选的会话 ID"),
    ] = None,
) -> str:
    """获取当前学员已参与学习的课程列表.

    触发条件：当需要查看我参与学习的所有课程时调用。
    前置依赖：需先调用 stu_login 完成登录。
    副作用：无（只读查询）。

    返回的课程列表包含 group_id、标题、学习状态、完成进度等信息。
    支持按学习状态筛选：0=所有, 1=已学习, 2=学习中, 3=待学习。
    获取 group_id 后，可调用 stu_get_course_structure 获取课程详情。
    """
    client = _get_client(session_id)

    auth_err = _require_auth(client)
    if auth_err:
        return _err(
            error_code="NOT_AUTHENTICATED",
            error_message=auth_err,
            suggested_action="调用 stu_login 完成登录后再重试",
        )

    status_map = {0: "all", 1: "pending", 2: "learning", 3: "completed"}

    def _format_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "group_id": item.get("group_id", ""),
            "title": item.get("group_title", ""),
            "learn_status": item.get("learn_status", 0),
            "learn_status_label": status_map.get(item.get("learn_status", 0), "unknown"),
            "finish_ratio": item.get("finish_ratio", 0),
            "cover_url": item.get("show_pic", ""),
            "access_code": item.get("access_code", ""),
            "group_url": item.get("group_url", ""),
            "share_pc_url": item.get("share_pc_url", ""),
            "session_num": item.get("session_num", 0),
            "participant_time": item.get("participant_time", ""),
        }

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getmyparticipatedgrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "learn_status": str(learn_status),
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取已参与课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        formatted_list = [_format_item(item) for item in course_list]
        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    effective_fetch_all = fetch_all or bool(fuzzy_title and fuzzy_title.strip())

    try:
        if effective_fetch_all:
            batch_size = 50
            all_items: list[dict[str, Any]] = []
            total_all = 0
            current_page = 1

            while True:
                page_items, total_all = _fetch_page(current_page, batch_size)
                all_items.extend(page_items)

                report_pagination_progress(
                    "stu_list_participated_courses",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                )

                if not page_items or len(all_items) >= total_all:
                    report_pagination_progress(
                        "stu_list_participated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_complete=True,
                    )
                    break

                if current_page >= 50:
                    report_pagination_progress(
                        "stu_list_participated_courses",
                        current_page,
                        len(all_items),
                        total_all,
                        batch_size,
                        is_safety_limit=True,
                    )
                    break

                current_page += 1

            result_items = all_items
            if fuzzy_title and fuzzy_title.strip():
                result_items = fuzzy_filter_items(
                    all_items,
                    fuzzy_title,
                    key="title",
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                )

            return _ok(
                data={
                    "courses": result_items,
                    "filter": {
                        "learn_status": learn_status,
                        "learn_status_label": status_map.get(learn_status, "unknown"),
                    },
                    "pagination": {
                        "total_all": total_all,
                        "current_page": current_page,
                        "page_size": batch_size,
                    },
                },
                next_action="proceed",
                suggested_action="选择要学习的课程，调用 stu_get_course_structure 获取详情",
            )

        # Single-page mode (original behavior)
        formatted_list, total_all = _fetch_page(page, page_size)

        return _ok(
            data={
                "courses": formatted_list,
                "filter": {
                    "learn_status": learn_status,
                    "learn_status_label": status_map.get(learn_status, "unknown"),
                },
                "pagination": {
                    "total": total_all,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": page_size,
                },
            },
            next_action="proceed",
            suggested_action="选择要学习的课程，调用 stu_get_course_structure 获取详情",
        )

    except Exception as e:
        return _err(
            error_code="LIST_PARTICIPATED_COURSES_ERROR",
            error_message=str(e),
            suggested_action="请检查网络连接后重试",
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

    print("=" * 60, file=sys.stderr)
    print("UMU 学员端 MCP Server", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(file=sys.stderr)
    print("支持的传输方式:", file=sys.stderr)
    print("  - stdio:  标准输入输出（推荐用于本地 AI 助手）", file=sys.stderr)
    print(file=sys.stderr)
    print("环境变量:", file=sys.stderr)
    print("  UMU_BASE_URL         - UMU 基础 URL（默认: https://www.umu.cn）", file=sys.stderr)
    print("  UMU_STUDENT_USERNAME - 学生登录用户名", file=sys.stderr)
    print("  UMU_STUDENT_PASSWORD - 学生登录密码", file=sys.stderr)
    print(file=sys.stderr)
    print("可用 Tools:", file=sys.stderr)
    print("  认证: stu_login, stu_check_auth", file=sys.stderr)
    print("  会话: stu_create_session, stu_list_sessions, stu_destroy_session", file=sys.stderr)
    print("  解析: stu_resolve_course_url", file=sys.stderr)
    print("  查结构: stu_get_my_courses, stu_list_participated_courses,", file=sys.stderr)
    print("          stu_get_course_structure, stu_get_learning_progress", file=sys.stderr)
    print("  操作: stu_enroll_course, stu_browse_lesson,", file=sys.stderr)
    print("        stu_get_questionnaire_questions, stu_submit_questionnaire,", file=sys.stderr)
    print("        stu_check_in, stu_check_in_with_rating,", file=sys.stderr)
    print("        stu_start_exam, stu_submit_exam", file=sys.stderr)
    print("  批量: stu_batch_import_accounts, stu_batch_complete_course", file=sys.stderr)
    print("  完成: stu_complete_course", file=sys.stderr)
    print("  验证: stu_get_lesson_status", file=sys.stderr)
    print("  讲师端: 请使用 umu-mcp-teacher", file=sys.stderr)
    print(file=sys.stderr)
    print("可用 Prompts:", file=sys.stderr)
    print("  - stu_course_completion_workflow", file=sys.stderr)
    print("  - stu_lesson_type_guide", file=sys.stderr)
    print("  - stu_error_recovery_guide", file=sys.stderr)
    print("  - stu_exam_workflow_guide", file=sys.stderr)
    print(file=sys.stderr)

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        print(f"不支持的传输方式: {transport}", file=sys.stderr)
        print("支持: stdio, sse", file=sys.stderr)


if __name__ == "__main__":
    main()
