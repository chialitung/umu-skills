# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""腾讯云 COS 上传核心模块.

封装 SCORM 课程包上传的完整流程，支持：
- 阶段化执行（每个阶段可独立测试和重试）
- 分片上传并发 + 流式读取
- 分片级重试（指数退避）
- 失败时自动清理 COS 分片
- 进度追踪
- 路径安全检查
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("umu.mcp.teacher.cos")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class COSCredentials:
    """COS 临时凭证."""

    resource_id: str
    object_name: str
    bucket_url: str
    session_token: str
    tmp_ak: str
    tmp_sk: str
    start_time: str
    expire_time: str
    region: str = "ap-beijing"

    def is_expiring_soon(self, buffer_seconds: int = 300) -> bool:
        """检查凭证是否在 buffer_seconds 内过期."""
        try:
            return int(time.time()) + buffer_seconds > int(self.expire_time)
        except (ValueError, TypeError):
            return False


@dataclass
class UploadProgress:
    """上传进度信息."""

    stage: str = ""
    current_part: int = 0
    total_parts: int = 0
    bytes_uploaded: int = 0
    bytes_total: int = 0
    percent: float = 0.0
    estimated_seconds_remaining: int = 0


@dataclass
class UploadResult:
    """上传结果."""

    resource_id: str
    file_url: str
    scorm_url: str = ""
    task_id: str = ""
    status: str = ""
    name: str = ""
    file_size: int = 0
    task_result: dict[str, Any] = field(default_factory=dict)
    progress: UploadProgress = field(default_factory=UploadProgress)


# ---------------------------------------------------------------------------
# 路径安全检查
# ---------------------------------------------------------------------------


def validate_file_path(file_path: str) -> str:
    """验证文件路径安全性.

    检查项：
    1. 路径存在且为文件
    2. 文件大小 > 0
    3. 扩展名为 .zip
    4. 不是符号链接（防止目录遍历）
    5. 在允许目录内（如配置了 UMU_UPLOAD_DIRS）

    Args:
        file_path: 文件路径（绝对或相对）

    Returns:
        解析后的绝对路径

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 路径不安全或文件无效
    """
    abs_path = os.path.abspath(file_path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")

    if os.path.islink(abs_path):
        raise ValueError(f"不支持符号链接: {abs_path}")

    if not os.path.isfile(abs_path):
        raise ValueError(f"路径不是文件: {abs_path}")

    file_size = os.path.getsize(abs_path)
    if file_size == 0:
        raise ValueError(f"文件大小为 0: {abs_path}")

    if not abs_path.lower().endswith(".zip"):
        raise ValueError(f"仅支持 .zip 格式: {abs_path}")

    # 可选：限制在特定目录内
    allowed_dirs = os.getenv("UMU_UPLOAD_DIRS", "").split(",")
    if allowed_dirs and allowed_dirs[0]:
        if not any(
            abs_path.startswith(d.strip()) for d in allowed_dirs if d.strip()
        ):
            raise ValueError(
                f"文件路径不在允许范围内。允许的目录: {allowed_dirs}"
            )

    return abs_path


# ---------------------------------------------------------------------------
# COS 签名
# ---------------------------------------------------------------------------


def _cos_auth_header(
    method: str,
    uri: str,
    params: dict[str, str],
    headers: dict[str, str],
    secret_id: str,
    secret_key: str,
    start_time: str,
    expire_time: str,
) -> str:
    """生成腾讯云 COS 请求签名 (v1).

    参考: https://cloud.tencent.com/document/product/436/7778
    关键发现：COS 服务器将参数名转为小写后再计算签名，
    因此签名时必须使用小写参数名。
    """
    key_time = f"{start_time};{expire_time}"

    # SignKey = HMAC-SHA1(KeyTime, SecretKey)
    sign_key = hmac.new(
        secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1
    ).hexdigest()

    # HttpParameters — COS 服务器将参数名转为小写，签名时必须使用小写
    param_list = sorted((k.lower(), v) for k, v in params.items())
    http_params = (
        urllib.parse.urlencode(param_list, quote_via=urllib.parse.quote, safe="")
        if param_list
        else ""
    )

    # HttpHeaders — 只签名 Host（必须）
    header_list = sorted([(k.lower(), v.strip()) for k, v in headers.items()])
    http_headers = "&".join(
        [f"{k}={urllib.parse.quote(v, safe='')}" for k, v in header_list]
    )

    # HttpString = Method + URI + HttpParameters + HttpHeaders
    http_string = f"{method.lower()}\n{uri}\n{http_params}\n{http_headers}\n"

    # StringToSign = sha1 + KeyTime + sha1(HttpString)
    sha1_http = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
    string_to_sign = f"sha1\n{key_time}\n{sha1_http}\n"

    # Signature = HMAC-SHA1(StringToSign, SignKey)
    signature = hmac.new(
        sign_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
    ).hexdigest()

    header_keys = ";".join([k for k, _ in header_list])
    param_keys = ";".join([k for k, _ in param_list])

    return (
        f"q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_keys}"
        f"&q-url-param-list={param_keys}"
        f"&q-signature={signature}"
    )


# ---------------------------------------------------------------------------
# 流式分片读取
# ---------------------------------------------------------------------------


def read_file_chunks(
    file_path: str,
    chunk_size: int = 5 * 1024 * 1024,
) -> list[tuple[int, bytes]]:
    """读取文件并按 chunk_size 分片，返回 (序号, 字节) 列表.

    序号从 1 开始（COS API 要求）。
    """
    chunks: list[tuple[int, bytes]] = []
    with open(file_path, "rb") as f:
        part_num = 0
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            part_num += 1
            chunks.append((part_num, chunk))
    return chunks


# ---------------------------------------------------------------------------
# ScormUploader — 阶段化上传器
# ---------------------------------------------------------------------------


class ScormUploader:
    """SCORM 课程包上传器，按阶段执行.

    阶段：
        1. validate — 文件校验
        2. get_credentials — 获取 COS 临时凭证
        3. upload — COS 上传（单文件 / 分片并发）
        4. add_log — 记录上传日志
        5. register — SCORM 注册
        6. poll — 轮询解析状态

    每阶段可独立调用，状态通过属性传递。
    """

    # 小文件直接 PUT 阈值（MB）
    SINGLE_UPLOAD_THRESHOLD_MB: int = 50
    CHUNK_SIZE: int = 5 * 1024 * 1024  # 5MB
    # 分片并发数
    CONCURRENT_PARTS: int = 3
    # 分片上传重试次数
    PART_MAX_RETRIES: int = 3
    # 任务状态轮询最大次数
    POLL_MAX_ROUNDS: int = 60
    POLL_INTERVAL_SECONDS: int = 2

    def __init__(self, client: Any, base_url: str):
        """初始化上传器.

        Args:
            client: UMUClient 实例
            base_url: UMU 基础 URL，用于 Origin/Referer 头
        """
        self.client = client
        self.base_url = base_url
        self.cos_origin = base_url

        # 阶段间共享状态
        self.file_path: str = ""
        self.file_size: int = 0
        self.file_name: str = ""
        self.display_name: str = ""
        self.teacher_id: str = ""
        self.creds: COSCredentials | None = None
        self.cos_url: str = ""
        self.upload_id: str = ""
        self.parts: list[dict[str, Any]] = []

        # 进度追踪
        self.progress = UploadProgress()
        self._upload_start_time: float = 0.0

    # ------------------------------------------------------------------
    # Stage 1: 文件验证
    # ------------------------------------------------------------------

    def stage_1_validate(self, file_path: str, name: str | None = None) -> None:
        """验证文件并初始化上传器状态.

        Args:
            file_path: 本地文件路径
            name: 显示名称（可选）

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件无效或不安全
        """
        logger.info("[Stage 1] 验证文件: %s", file_path)

        self.file_path = validate_file_path(file_path)
        self.file_size = os.path.getsize(self.file_path)
        self.file_name = os.path.basename(self.file_path)
        self.display_name = name or os.path.splitext(self.file_name)[0]

        logger.info(
            "[Stage 1] 文件验证通过: %s, size=%d bytes",
            self.file_name,
            self.file_size,
        )

    # ------------------------------------------------------------------
    # Stage 2: 获取 COS 凭证
    # ------------------------------------------------------------------

    def stage_2_get_credentials(self) -> COSCredentials:
        """获取腾讯云 COS 临时上传凭证.

        Returns:
            COSCredentials 实例

        Raises:
            RuntimeError: 获取凭证失败或响应格式无效
        """
        logger.info("[Stage 2] 获取 COS 上传凭证...")

        # 获取讲师 ID
        teacher_id = ""
        try:
            user_resp = self.client.get(
                self.client.desktop_url("/uapi/v1/user/get")
            )
            teacher_id = str(user_resp.get("data", {}).get("teacher_id", ""))
        except Exception as e:
            logger.warning("获取用户 info 失败: %s", e)

        if not teacher_id and self.client.auth.credentials:
            teacher_id = self.client.auth.credentials.username or ""

        self.teacher_id = teacher_id

        # 请求凭证
        pre_payload = {
            "data": (
                '{"opts":['
                '{"key":"media_type","value":"videoweike"},'
                '{"key":"id_prefix","value":"teacher"},'
                f'{{"key":"id","value":"{teacher_id}"}},'
                '{"key":"ext","value":"zip"}'
                "]}"
            )
        }

        pre_resp = self.client.post(
            self.client.desktop_url("/microapi/resourcemgt/preObject"),
            data=pre_payload,
        )

        if pre_resp.get("error_code") != 0:
            raise RuntimeError(
                f"获取上传凭证失败: {pre_resp.get('error_message', 'unknown')}"
            )

        pre_data = pre_resp.get("data", {})
        credential_info = pre_data.get("credential_info", {})

        self.creds = COSCredentials(
            resource_id=pre_data.get("resource_id", ""),
            object_name=pre_data.get("object_name", ""),
            bucket_url=pre_data.get("bucket_info", {}).get(
                "bucket_url", "https://umu-cn.umucdn.cn"
            ),
            session_token=credential_info.get("session_token", ""),
            tmp_ak=credential_info.get("tmp_ak", ""),
            tmp_sk=credential_info.get("tmp_sk", ""),
            start_time=str(credential_info.get("start_time", "")),
            expire_time=str(credential_info.get("expire_time", "")),
            region=pre_data.get("bucket_info", {}).get("region", "ap-beijing"),
        )

        if not self.creds.resource_id or not self.creds.object_name:
            raise RuntimeError("上传凭证响应缺少必要字段")

        if not self.creds.tmp_ak or not self.creds.tmp_sk:
            raise RuntimeError("COS 临时凭证缺少 tmp_ak 或 tmp_sk")

        self.cos_url = f"{self.creds.bucket_url}/{self.creds.object_name}"

        logger.info(
            "[Stage 2] 凭证获取成功: resource_id=%s, object_name=%s",
            self.creds.resource_id,
            self.creds.object_name,
        )
        return self.creds

    # ------------------------------------------------------------------
    # Stage 3: COS 上传
    # ------------------------------------------------------------------

    async def stage_3_upload(self) -> str:
        """上传文件到 COS.

        策略：
        - 小于 SINGLE_UPLOAD_THRESHOLD_MB：直接 PUT
        - 大于等于阈值：分片并发上传

        Returns:
            COS URL

        Raises:
            RuntimeError: 上传失败
        """
        logger.info("[Stage 3] 开始 COS 上传: %d bytes", self.file_size)
        self._upload_start_time = time.time()

        if self.creds is None:
            raise RuntimeError("必须先调用 stage_2_get_credentials")

        threshold = self.SINGLE_UPLOAD_THRESHOLD_MB * 1024 * 1024

        try:
            if self.file_size <= threshold:
                await self._upload_single()
            else:
                await self._upload_multipart()
        except Exception:
            # 上传失败时清理已初始化的分片
            if self.upload_id:
                await self._abort_multipart_upload()
            raise
        finally:
            # 清除敏感凭证
            if self.creds:
                self.creds.tmp_ak = ""
                self.creds.tmp_sk = ""
                self.creds.session_token = ""

        logger.info("[Stage 3] COS 上传完成: %s", self.cos_url)
        return self.cos_url

    async def _upload_single(self) -> None:
        """小文件直接 PUT 上传."""
        logger.info("[Stage 3] 小文件直传: %s", self.file_name)

        assert self.creds is not None

        auth = _cos_auth_header(
            method="PUT",
            uri=f"/{self.creds.object_name}",
            params={},
            headers={"Host": "umu-cn.umucdn.cn"},
            secret_id=self.creds.tmp_ak,
            secret_key=self.creds.tmp_sk,
            start_time=self.creds.start_time,
            expire_time=self.creds.expire_time,
        )

        cos_headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": "application/x-zip-compressed",
            "x-cos-security-token": self.creds.session_token,
            "Authorization": auth,
            "Origin": self.cos_origin,
            "Referer": f"{self.cos_origin}/",
        }

        with open(self.file_path, "rb") as f:
            content = f.read()

        resp = httpx.put(
            self.cos_url,
            content=content,
            headers=cos_headers,
            timeout=120,
        )

        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"COS 直传失败: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        self.progress = UploadProgress(
            stage="upload_complete",
            bytes_uploaded=self.file_size,
            bytes_total=self.file_size,
            percent=100.0,
        )

    async def _upload_multipart(self) -> None:
        """大文件分片上传（支持并发）."""
        logger.info("[Stage 3] 分片上传: %s (%d bytes)", self.file_name, self.file_size)

        assert self.creds is not None

        # 4a. 初始化分片上传
        await self._init_multipart_upload()

        # 4b. 并发上传分片
        chunks = read_file_chunks(self.file_path, self.CHUNK_SIZE)
        total_parts = len(chunks)

        logger.info("[Stage 3] 共 %d 个分片，并发数 %d", total_parts, self.CONCURRENT_PARTS)

        self.progress = UploadProgress(
            stage="uploading_parts",
            total_parts=total_parts,
            bytes_total=self.file_size,
        )

        semaphore = asyncio.Semaphore(self.CONCURRENT_PARTS)

        async def upload_one(part_num: int, chunk: bytes) -> dict[str, Any]:
            async with semaphore:
                etag = await self._upload_part_with_retry(part_num, chunk)
                self.progress.bytes_uploaded += len(chunk)
                self.progress.current_part = part_num
                self.progress.percent = round(
                    self.progress.bytes_uploaded / self.file_size * 100, 1
                )
                if self._upload_start_time > 0:
                    elapsed = time.time() - self._upload_start_time
                    if self.progress.bytes_uploaded > 0 and self.progress.percent > 0:
                        total_est = elapsed / (self.progress.percent / 100)
                        self.progress.estimated_seconds_remaining = int(
                            total_est - elapsed
                        )
                return {"PartNumber": part_num, "ETag": etag}

        self.parts = await asyncio.gather(*[
            upload_one(n, c) for n, c in chunks
        ])
        self.parts.sort(key=lambda p: p["PartNumber"])

        # 4c. 完成分片上传
        await self._complete_multipart_upload()

        self.progress = UploadProgress(
            stage="upload_complete",
            current_part=total_parts,
            total_parts=total_parts,
            bytes_uploaded=self.file_size,
            bytes_total=self.file_size,
            percent=100.0,
        )

    async def _init_multipart_upload(self) -> None:
        """初始化分片上传，获取 upload_id."""
        logger.info("[Stage 3] 初始化分片上传...")

        assert self.creds is not None

        auth = _cos_auth_header(
            method="POST",
            uri=f"/{self.creds.object_name}",
            params={"uploads": ""},
            headers={"Host": "umu-cn.umucdn.cn"},
            secret_id=self.creds.tmp_ak,
            secret_key=self.creds.tmp_sk,
            start_time=self.creds.start_time,
            expire_time=self.creds.expire_time,
        )

        headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": "application/x-zip-compressed",
            "x-cos-security-token": self.creds.session_token,
            "Authorization": auth,
            "Origin": self.cos_origin,
            "Referer": f"{self.cos_origin}/",
        }

        resp = httpx.post(
            f"{self.cos_url}?uploads",
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"初始化分片上传失败: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        import re

        match = re.search(r"<UploadId>([^<]+)</UploadId>", resp.text)
        if not match:
            raise RuntimeError("无法获取分片上传 ID")

        self.upload_id = match.group(1)
        logger.info(
            "[Stage 3] 分片上传已初始化: upload_id=%s...",
            self.upload_id[:20],
        )

    async def _upload_part_with_retry(
        self,
        part_number: int,
        chunk: bytes,
    ) -> str:
        """上传单个分片，带重试.

        Args:
            part_number: 分片序号（从1开始）
            chunk: 分片字节数据

        Returns:
            ETag 值

        Raises:
            RuntimeError: 所有重试都失败
        """
        assert self.creds is not None

        for attempt in range(self.PART_MAX_RETRIES):
            try:
                return await self._upload_part(part_number, chunk)
            except Exception as e:
                logger.warning(
                    "分片 %d 上传失败 (attempt %d/%d): %s",
                    part_number,
                    attempt + 1,
                    self.PART_MAX_RETRIES,
                    e,
                )
                if attempt == self.PART_MAX_RETRIES - 1:
                    raise RuntimeError(
                        f"分片 {part_number} 上传失败，已重试 {self.PART_MAX_RETRIES} 次"
                    ) from e
                # 指数退避
                await asyncio.sleep(2 ** attempt)

        # 不可达，但 type checker 需要
        raise RuntimeError(f"分片 {part_number} 上传失败")

    async def _upload_part(self, part_number: int, chunk: bytes) -> str:
        """上传单个分片（无重试）."""
        assert self.creds is not None

        auth = _cos_auth_header(
            method="PUT",
            uri=f"/{self.creds.object_name}",
            params={"partNumber": str(part_number), "uploadId": self.upload_id},
            headers={"Host": "umu-cn.umucdn.cn"},
            secret_id=self.creds.tmp_ak,
            secret_key=self.creds.tmp_sk,
            start_time=self.creds.start_time,
            expire_time=self.creds.expire_time,
        )

        headers = {
            "Host": "umu-cn.umucdn.cn",
            "x-cos-security-token": self.creds.session_token,
            "Authorization": auth,
            "Origin": self.cos_origin,
            "Referer": f"{self.cos_origin}/",
        }

        resp = httpx.put(
            f"{self.cos_url}?partNumber={part_number}&uploadId={self.upload_id}",
            content=chunk,
            headers=headers,
            timeout=120,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"HTTP {resp.status_code} - {resp.text[:200]}"
            )

        etag = resp.headers.get("ETag", "")
        logger.debug(
            "分片 %d 上传成功: ETag=%s...",
            part_number,
            etag[:20],
        )
        return etag

    async def _complete_multipart_upload(self) -> None:
        """完成分片上传，合并所有分片."""
        logger.info("[Stage 3] 完成分片上传，合并 %d 个分片...", len(self.parts))

        assert self.creds is not None

        xml_parts = []
        for part in self.parts:
            xml_parts.append(
                f'<Part><PartNumber>{part["PartNumber"]}</PartNumber>'
                f'<ETag>{part["ETag"]}</ETag></Part>'
            )
        complete_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<CompleteMultipartUpload>{"".join(xml_parts)}</CompleteMultipartUpload>'
        )

        auth = _cos_auth_header(
            method="POST",
            uri=f"/{self.creds.object_name}",
            params={"uploadId": self.upload_id},
            headers={"Host": "umu-cn.umucdn.cn"},
            secret_id=self.creds.tmp_ak,
            secret_key=self.creds.tmp_sk,
            start_time=self.creds.start_time,
            expire_time=self.creds.expire_time,
        )

        headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": "application/xml",
            "x-cos-security-token": self.creds.session_token,
            "Authorization": auth,
            "Origin": self.cos_origin,
            "Referer": f"{self.cos_origin}/",
        }

        resp = httpx.post(
            f"{self.cos_url}?uploadId={self.upload_id}",
            content=complete_xml,
            headers=headers,
            timeout=60,
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"完成分片上传失败: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        logger.info("[Stage 3] 分片上传合并成功")

    async def _abort_multipart_upload(self) -> None:
        """取消分片上传，清理 COS 上的临时分片."""
        if not self.upload_id or not self.creds:
            return

        logger.info("[Stage 3] 清理分片上传: upload_id=%s...", self.upload_id[:20])

        try:
            auth = _cos_auth_header(
                method="DELETE",
                uri=f"/{self.creds.object_name}",
                params={"uploadId": self.upload_id},
                headers={"Host": "umu-cn.umucdn.cn"},
                secret_id=self.creds.tmp_ak,
                secret_key=self.creds.tmp_sk,
                start_time=self.creds.start_time,
                expire_time=self.creds.expire_time,
            )

            headers = {
                "Host": "umu-cn.umucdn.cn",
                "x-cos-security-token": self.creds.session_token,
                "Authorization": auth,
                "Origin": self.cos_origin,
                "Referer": f"{self.cos_origin}/",
            }

            resp = httpx.delete(
                f"{self.cos_url}?uploadId={self.upload_id}",
                headers=headers,
                timeout=30,
            )

            if resp.status_code in (200, 204):
                logger.info("[Stage 3] 分片上传清理成功")
            else:
                logger.warning(
                    "[Stage 3] 分片上传清理失败: HTTP %d", resp.status_code
                )
        except Exception as e:
            logger.warning("[Stage 3] 分片上传清理异常: %s", e)

    # ------------------------------------------------------------------
    # Stage 4: 记录上传日志
    # ------------------------------------------------------------------

    def stage_4_add_log(self) -> None:
        """记录上传操作日志（非致命，失败不中断）."""
        logger.info("[Stage 4] 记录上传日志...")

        if self.creds is None:
            logger.warning("[Stage 4] 跳过：无凭证")
            return

        try:
            self.client.get(
                self.client.desktop_url("/uapi/v1/resource/add-log"),
                params={
                    "t": str(int(time.time() * 1000)),
                    "resource_id": self.creds.resource_id,
                    "file_url": self.cos_url,
                    "file_name": self.file_name,
                    "file_ext": "zip",
                    "refer": self.client.desktop_url("/videoDrive"),
                    "action_type": "upload",
                    "resource_type": "1",
                    "origin_type": "2",
                    "device_type": "1",
                    "os": "Windows 11",
                },
            )
            logger.info("[Stage 4] 日志记录成功")
        except Exception as e:
            logger.warning("[Stage 4] 日志记录失败（非致命）: %s", e)

    # ------------------------------------------------------------------
    # Stage 5: 注册 SCORM 课程包
    # ------------------------------------------------------------------

    def stage_5_register(self) -> str:
        """注册为 SCORM 课程包.

        Returns:
            task_id

        Raises:
            RuntimeError: 注册失败
        """
        logger.info("[Stage 5] 注册 SCORM 课程包...")

        scorm_resp = self.client.post(
            self.client.desktop_url("/napi/scorm/add"),
            data={"packageUrl": self.cos_url},
        )

        if scorm_resp.get("error_code") != 0:
            raise RuntimeError(
                f"SCORM 注册失败: {scorm_resp.get('error_message', 'unknown')}"
            )

        task_id = scorm_resp.get("data", {}).get("task_id", "")
        logger.info("[Stage 5] SCORM 注册成功: task_id=%s", task_id)
        return task_id

    # ------------------------------------------------------------------
    # Stage 6: 轮询解析状态
    # ------------------------------------------------------------------

    def stage_6_poll_status(self, task_id: str) -> dict[str, Any]:
        """轮询 SCORM 解析处理状态.

        Args:
            task_id: SCORM 任务 ID

        Returns:
            解析结果字典（包含 url 等）
        """
        logger.info("[Stage 6] 轮询 SCORM 解析状态: task_id=%s", task_id)

        for i in range(self.POLL_MAX_ROUNDS):
            try:
                status_resp = self.client.get(
                    self.client.desktop_url("/napi/scorm/task-status"),
                    params={
                        "task_id": task_id,
                        "t": str(int(time.time() * 1000)),
                    },
                )

                if status_resp.get("error_code") == 0:
                    task_data = status_resp.get("data", {})
                    if task_data and task_data.get("url"):
                        logger.info(
                            "[Stage 6] 解析完成: %s...",
                            task_data.get("url", "")[:60],
                        )
                        return task_data
            except Exception as e:
                logger.warning("[Stage 6] 状态查询失败: %s", e)

            time.sleep(self.POLL_INTERVAL_SECONDS)

        logger.warning(
            "[Stage 6] 轮询超时 (%d 秒)",
            self.POLL_MAX_ROUNDS * self.POLL_INTERVAL_SECONDS,
        )
        return {}

    # ------------------------------------------------------------------
    # Stage 7: 回调注册到资源列表
    # ------------------------------------------------------------------

    def stage_7_callback(self, task_result: dict[str, Any]) -> dict[str, Any]:
        """调用 resource callback 将资源注册到音视频库列表.

        这是资源能在前端"我的音视频"页面中显示的关键步骤。
        前端在 SCORM 解析完成后会调用此 API。

        Args:
            task_result: Stage 6 返回的解析结果（包含 url）

        Returns:
            callback 响应数据
        """
        logger.info("[Stage 7] 注册资源到列表...")

        if not self.creds:
            logger.warning("[Stage 7] 跳过: 无凭证")
            return {}

        scorm_url = task_result.get("url", "")
        if not scorm_url:
            logger.warning("[Stage 7] 跳过: 无 SCORM URL")
            return {}

        params = {
            "t": str(int(time.time() * 1000)),
            "ext": "zip",
            "file_name": self.display_name,
            "file_size": str(self.file_size),
            "media_type": "videoweike",
            "path": self.cos_url,
            "resource_id": self.creds.resource_id,
            "sp": "1",
            "transcoding_ext": "scorm",
            "transcoding_url": scorm_url,
        }

        try:
            callback_resp = self.client.get(
                self.client.desktop_url("/api/resource/callback"),
                params=params,
            )

            if callback_resp.get("status") is True or callback_resp.get("error_code") == 0:
                logger.info("[Stage 7] 资源注册成功")
                return callback_resp.get("data", {})
            else:
                logger.warning(
                    "[Stage 7] 资源注册返回非成功状态: %s",
                    callback_resp.get("error", "unknown"),
                )
                return callback_resp.get("data", {})
        except Exception as e:
            logger.warning("[Stage 7] 资源注册失败（非致命）: %s", e)
            return {}

    # ------------------------------------------------------------------
    # 完整流水线
    # ------------------------------------------------------------------

    async def run(
        self,
        file_path: str,
        name: str | None = None,
    ) -> UploadResult:
        """执行完整的上传流程.

        Args:
            file_path: 本地 SCORM zip 文件路径
            name: 显示名称（可选）

        Returns:
            UploadResult 结果
        """
        # Stage 1: 验证
        self.stage_1_validate(file_path, name)

        # Stage 2: 获取凭证
        self.stage_2_get_credentials()

        # Stage 3: COS 上传
        await self.stage_3_upload()

        # Stage 4: 记录日志
        self.stage_4_add_log()

        # Stage 5: 注册 SCORM
        task_id = self.stage_5_register()

        # Stage 6: 轮询状态
        task_result = self.stage_6_poll_status(task_id)

        # Stage 7: 注册到资源列表（让前端可见）
        callback_data = self.stage_7_callback(task_result)

        return UploadResult(
            resource_id=self.creds.resource_id if self.creds else "",
            file_url=self.cos_url,
            scorm_url=task_result.get("url", ""),
            task_id=task_id,
            status="done" if task_result.get("url") else "timeout",
            name=self.display_name,
            file_size=self.file_size,
            task_result={**task_result, "callback": callback_data},
            progress=self.progress,
        )
