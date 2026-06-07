"""音视频上传核心模块.

封装"我的音视频"资源上传的完整流程，支持：
- 视频格式：3gp, 3gpp, avi, flv, f4v, mkv, mov, mp4, m4a, mpeg, mpg, ts, mts,
  wmv, rm, rmvb, webm, dv, m2v, m4v, ogv, 3g2
- 音频格式：mp3, mp1, mp2, aac, ac3, flac, au, 3ga, amr, wav, wma, ra, ogg, dsf
- 小文件直接 PUT 上传
- 大文件分片并发上传（复用 DocumentUploader 的 COS 机制）
- 自动注册到音视频资源列表

与 DocumentUploader 的区别：
- media_type 为 "videoweike"（非 "docweike"）
- 支持的文件扩展名为音视频格式
- 文件大小限制 1024MB
- Content-Type 根据音视频格式设置
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from .cos_upload import (
    COSCredentials,
    UploadProgress,
    UploadResult,
    _cos_auth_header,
    read_file_chunks,
    validate_file_path,
)

logger = logging.getLogger("umu.mcp.teacher.video")

# ---------------------------------------------------------------------------
# 支持的音视频类型
# ---------------------------------------------------------------------------

SUPPORTED_VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    # 视频格式
    ".3gp", ".3gpp", ".avi", ".flv", ".f4v", ".mkv", ".mov", ".mp4", ".m4a",
    ".mpeg", ".mpg", ".ts", ".mts", ".wmv", ".rm", ".rmvb", ".webm", ".dv",
    ".m2v", ".m4v", ".ogv", ".3g2",
    # 音频格式
    ".mp3", ".mp1", ".mp2", ".aac", ".ac3", ".flac", ".au", ".3ga", ".amr",
    ".wav", ".wma", ".ra", ".ogg", ".dsf",
})

VIDEO_MEDIA_TYPE: str = "videoweike"

# 最大文件大小 1024MB
MAX_VIDEO_SIZE_BYTES: int = 1024 * 1024 * 1024


def validate_video_path(file_path: str) -> str:
    """验证音视频文件路径.

    Args:
        file_path: 文件路径

    Returns:
        解析后的绝对路径

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件格式不支持或路径不安全
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

    if file_size > MAX_VIDEO_SIZE_BYTES:
        raise ValueError(
            f"文件大小 {file_size / (1024 * 1024):.2f}MB 超过限制 1024MB: {abs_path}"
        )

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError(
            f"不支持的音视频格式 '{ext}'。支持的格式: "
            f"{', '.join(sorted(SUPPORTED_VIDEO_EXTENSIONS))}"
        )

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
# 音视频上传器
# ---------------------------------------------------------------------------

class VideoUploader:
    """音视频上传器，支持小文件直传和大文件分片上传.

    阶段：
        1. validate — 文件校验
        2. get_credentials — 获取 COS 临时凭证
        3. upload — COS 上传（单文件 / 分片并发）
        4. add_log — 记录上传日志
        5. callback — resourceCallback 注册到音视频列表

    复用 cos_upload.py 中的 COS 签名和分片上传机制。
    """

    # 小文件直接 PUT 阈值（MB）
    SINGLE_UPLOAD_THRESHOLD_MB: int = 50
    CHUNK_SIZE: int = 5 * 1024 * 1024  # 5MB
    # 分片并发数
    CONCURRENT_PARTS: int = 3
    # 分片上传重试次数
    PART_MAX_RETRIES: int = 3

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
        self.file_ext: str = ""
        self.teacher_id: str = ""
        self.creds: COSCredentials | None = None
        self.cos_url: str = ""
        self.upload_id: str = ""
        self.parts: list[dict[str, Any]] = []

        # 进度追踪
        self.progress = UploadProgress()
        self._upload_start_time: float = 0.0

        # 进度回调（供 CLI 脚本实时打印进度）
        self.on_progress: Any = None

    # ------------------------------------------------------------------
    # Stage 1: 文件验证
    # ------------------------------------------------------------------

    def stage_1_validate(self, file_path: str, name: str | None = None) -> None:
        """验证文件并初始化上传器状态."""
        logger.info("[Stage 1] 验证音视频文件: %s", file_path)

        self.file_path = validate_video_path(file_path)
        self.file_size = os.path.getsize(self.file_path)
        self.file_name = os.path.basename(self.file_path)
        self.file_ext = os.path.splitext(self.file_name)[1].lstrip(".").lower()
        self.display_name = name or self.file_name

        logger.info(
            "[Stage 1] 音视频文件验证通过: %s, size=%d bytes, ext=%s",
            self.file_name,
            self.file_size,
            self.file_ext,
        )

    # ------------------------------------------------------------------
    # Stage 2: 获取 COS 凭证
    # ------------------------------------------------------------------

    def stage_2_get_credentials(self) -> COSCredentials:
        """获取腾讯云 COS 临时上传凭证."""
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
                f'{{"key":"media_type","value":"{VIDEO_MEDIA_TYPE}"}},'
                '{"key":"id_prefix","value":"teacher"},'
                f'{{"key":"id","value":"{teacher_id}"}},'
                f'{{"key":"ext","value":"{self.file_ext}"}}'
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
            if self.upload_id:
                await self._abort_multipart_upload()
            raise
        finally:
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

        # 根据文件扩展名设置 Content-Type
        content_type = _get_video_content_type(self.file_ext)

        cos_headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": content_type,
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
        logger.info(
            "[Stage 3] 分片上传: %s (%d bytes)", self.file_name, self.file_size
        )

        assert self.creds is not None

        # 初始化分片上传
        await self._init_multipart_upload()

        # 并发上传分片
        chunks = read_file_chunks(self.file_path, self.CHUNK_SIZE)
        total_parts = len(chunks)

        logger.info(
            "[Stage 3] 共 %d 个分片，并发数 %d", total_parts, self.CONCURRENT_PARTS
        )

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
                # 触发进度回调
                if callable(self.on_progress):
                    try:
                        self.on_progress(self.progress)
                    except Exception:
                        pass
                return {"PartNumber": part_num, "ETag": etag}

        self.parts = await asyncio.gather(*[
            upload_one(n, c) for n, c in chunks
        ])
        self.parts.sort(key=lambda p: p["PartNumber"])

        # 完成分片上传
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

        content_type = _get_video_content_type(self.file_ext)
        headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": content_type,
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
        """上传单个分片，带重试."""
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
                await asyncio.sleep(2 ** attempt)

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
            f'<CompleteMultipartUpload>{"" .join(xml_parts)}</CompleteMultipartUpload>'
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
                    "file_ext": self.file_ext,
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
    # Stage 5: resourceCallback 注册
    # ------------------------------------------------------------------

    def stage_5_callback(self) -> dict[str, Any]:
        """调用 resourceCallback 将资源注册到音视频列表.

        这是资源能在前端"我的音视频"页面中显示的关键步骤。

        Returns:
            callback 响应数据
        """
        logger.info("[Stage 5] 注册资源到音视频列表...")

        if not self.creds:
            logger.warning("[Stage 5] 跳过: 无凭证")
            return {}

        callback_data = {
            "media_type": VIDEO_MEDIA_TYPE,
            "resource_id": self.creds.resource_id,
            "file_name": self.display_name,
            "file_size": self.file_size,
            "path": self.cos_url,
            "ext": self.file_ext,
            "extend_info": "",
        }

        try:
            callback_resp = self.client.post(
                self.client.desktop_url("/microapi/resourcemgt/resourceCallback"),
                data={"data": json.dumps(callback_data, ensure_ascii=False)},
            )

            if (
                callback_resp.get("status") is True
                or callback_resp.get("error_code") == 0
            ):
                logger.info("[Stage 5] 资源注册成功")
                return callback_resp.get("data", {})
            else:
                logger.warning(
                    "[Stage 5] 资源注册返回非成功状态: %s",
                    callback_resp.get("error", "unknown"),
                )
                return callback_resp.get("data", {})
        except Exception as e:
            logger.warning("[Stage 5] 资源注册失败（非致命）: %s", e)
            return {}

    # ------------------------------------------------------------------
    # 完整流水线
    # ------------------------------------------------------------------

    async def run(
        self,
        file_path: str,
        name: str | None = None,
    ) -> UploadResult:
        """执行完整的音视频上传流程.

        Args:
            file_path: 本地音视频文件路径
            name: 显示名称（可选，默认使用原文件名）

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

        # Stage 5: 注册到资源列表
        callback_data = self.stage_5_callback()

        return UploadResult(
            resource_id=self.creds.resource_id if self.creds else "",
            file_url=self.cos_url,
            scorm_url="",
            task_id="",
            status="done",
            name=self.display_name,
            file_size=self.file_size,
            task_result={"callback": callback_data},
            progress=self.progress,
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_video_content_type(ext: str) -> str:
    """根据音视频文件扩展名获取 Content-Type."""
    mapping = {
        # 视频
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "m4a": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "wmv": "video/x-ms-wmv",
        "flv": "video/x-flv",
        "f4v": "video/x-f4v",
        "mkv": "video/x-matroska",
        "webm": "video/webm",
        "3gp": "video/3gpp",
        "3gpp": "video/3gpp",
        "3g2": "video/3gpp2",
        "mpeg": "video/mpeg",
        "mpg": "video/mpeg",
        "ts": "video/mp2t",
        "mts": "video/mp2t",
        "rm": "application/vnd.rn-realmedia",
        "rmvb": "application/vnd.rn-realmedia-vbr",
        "dv": "video/dv",
        "m2v": "video/mpeg",
        "ogv": "video/ogg",
        # 音频
        "mp3": "audio/mpeg",
        "mp1": "audio/mpeg",
        "mp2": "audio/mpeg",
        "aac": "audio/aac",
        "ac3": "audio/ac3",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "wma": "audio/x-ms-wma",
        "ogg": "audio/ogg",
        "ra": "audio/vnd.rn-realaudio",
        "amr": "audio/amr",
        "au": "audio/basic",
        "3ga": "audio/3gpp",
        "dsf": "audio/dsd",
    }
    return mapping.get(ext.lower(), "application/octet-stream")
