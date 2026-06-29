# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习流程共享辅助函数.

从 adapters/mcp/student.py 抽出的无状态辅助函数，供 learning operations 使用。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...core.client import UMUClient
from ...core.encrypt import decrypt_aes_base64

logger = logging.getLogger(__name__)


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
            logger.warning("[makeweikestatus] %s 失败: %s", action, e)
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


def _check_needs_enroll_form(
    client: UMUClient, group_id: str, s_key: str | None = None
) -> tuple[bool, dict[str, Any] | None]:
    """检测课程是否需要填写复杂报名表单。

    复用 course/pay 页面和报名表单解析逻辑，判断当前课程在报名后是否
    还需要学员填写联系信息或报名问题。

    Args:
        group_id: 课程组 ID
        s_key: 课程 URL 中的 sKey

    Returns:
        (是否需要表单, 表单摘要或 None)。表单摘要只包含用户需要填写的字段，
        用于快速提示用户准备答案。
    """
    try:
        enroll_id, enroll_url = _get_enroll_short_url(client, group_id, s_key)
        if not enroll_id or not enroll_url:
            return False, None

        page_data = _fetch_enroll_form_page(client, enroll_url)
        if not page_data:
            return False, None

        form = _parse_enroll_form(page_data)
        contact_fields = form.get("contact_fields", [])
        section_questions = form.get("section_questions", [])

        # 只统计真正需要用户填写的字段
        selected_contact = [f for f in contact_fields if f.get("selected")]
        answerable_questions = [
            q for q in section_questions if q.get("type") != "paragraph"
        ]

        if not selected_contact and not answerable_questions:
            return False, None

        summary: dict[str, Any] = {
            "enroll_id": str(enroll_id),
            "contact_fields": selected_contact,
            "section_questions": answerable_questions,
        }
        return True, summary
    except Exception:
        return False, None


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
            extend = qi.get("extend", {}) or {}
            min_val = extend.get("min")
            max_val = extend.get("max")
            if min_val is not None:
                question["min"] = min_val
            if max_val is not None:
                question["max"] = max_val
            default_value = setup.get("defaultValue")
            if default_value is not None:
                question["default_value"] = default_value
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
            if number is not None:
                try:
                    numeric_value: float = float(number)
                except (TypeError, ValueError):
                    return f"[{q.get('title', qid)}]必须是有效数字"
                min_val = q.get("min")
                max_val = q.get("max")
                if min_val is not None and numeric_value < float(min_val):
                    return f"[{q.get('title', qid)}]不能小于 {min_val}"
                if max_val is not None and numeric_value > float(max_val):
                    return f"[{q.get('title', qid)}]不能大于 {max_val}"

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
                answer_number[str(qid)] = str(number)

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

    优先级（从高到低):
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


def _extract_signin_page_data_json(html: str) -> dict[str, Any] | None:
    """从签到页面 HTML 中提取 pageData JSON 对象.

    页面通过 ``var pageData = {...};`` 或 ``pageData = {...};`` 注入初始数据。
    本函数使用简单的括号深度计数来定位 JSON 结束位置。
    """
    markers = ["var pageData = ", "pageData="]
    start = -1
    used_marker = ""
    for marker in markers:
        start = html.find(marker)
        if start != -1:
            used_marker = marker
            break
    if start == -1:
        return None
    start += len(used_marker)

    depth = 0
    in_string = False
    escape = False
    end = start
    for i in range(start, len(html)):
        ch = html[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not in_string:
            in_string = True
        elif ch == '"' and in_string:
            in_string = False
        elif not in_string:
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    json_str = html[start:end]
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _fetch_signin_questions(
    client: UMUClient,
    element_id: str,
) -> list[dict[str, Any]]:
    """通过签到页面 HTML 获取复杂签到的问题结构.

    返回 sectionArr 列表（每项包含 questionInfo 和 answerArr）。
    如果学员已经签到过，sectionArr 可能为空，此时抛出 RuntimeError。
    """
    # 1. 获取 element 信息，找到移动端分享 URL
    element_resp = client.get(client.desktop_url(f"/uapi/v1/element/{element_id}"))
    element_data = element_resp.get("data", {}) or {}
    share_url = element_data.get("share_url", "") or element_data.get("share_card_view", "")
    if not share_url:
        raise RuntimeError("无法获取签到小节分享链接")

    parsed = urlparse(share_url)
    key = parsed.path.rstrip("/").split("/")[-1]
    if not key:
        raise RuntimeError(f"无法从分享链接提取签到 key: {share_url}")
    # 移动端短链接形如 https://m.umu.cn/ssu_xxxx，需要去掉 ssu_ 前缀
    if key.startswith("ssu_"):
        key = key[4:]

    # 2. 获取签到页面 HTML
    sign_url = f"https://m.umu.cn/session/sign/{key}?sourceTitle="
    html = _get_html(client, sign_url)

    page_data = _extract_signin_page_data_json(html)
    if not page_data:
        raise RuntimeError("无法从签到页面解析 pageData")

    section_arr = page_data.get("info", {}).get("sessionArr", {}).get("sectionArr", [])
    if not isinstance(section_arr, list):
        section_arr = []

    # 过滤段落说明，只保留需要作答的题目；同时规范化字段名
    # 页面数据中的 question_id 字段名为 "id"，answer_id 字段名为 "id"/"answerId"
    questions = []
    for sec in section_arr:
        qinfo = sec.get("questionInfo", {})
        dom_type = qinfo.get("domType", "")
        if dom_type == "paragraph":
            continue
        qinfo.setdefault("questionId", qinfo.get("id", ""))
        for ans in sec.get("answerArr", []):
            ans.setdefault("answerId", ans.get("id", ""))
            ans.setdefault("questionId", qinfo.get("questionId", ""))
        questions.append(sec)

    if not questions:
        raise RuntimeError(
            "签到页面未返回题目数据（可能学员已完成签到，或页面结构已变更）。"
            "请提供显式 question_id 调用，或先调用 tch_get_section 获取题目 ID。"
        )

    return questions
