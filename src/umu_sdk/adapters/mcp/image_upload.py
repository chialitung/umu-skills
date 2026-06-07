"""通用图片/媒体文件上传器.

基于 cos_upload.py 中的 COS 签名逻辑，支持图片等小型媒体文件的上传。
流程：preObject 获取 COS 凭证 → PUT 直传到 COS → resourceCallback 注册到资源库.

Usage:
    from umu_sdk.adapters.mcp.image_upload import ImageUploader

    uploader = ImageUploader(client, client.base_url)
    result = uploader.upload("/path/to/cover.jpg")
    print(result.resource_id, result.file_url)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .cos_upload import COSCredentials, _cos_auth_header

logger = logging.getLogger("umu.mcp.teacher.image")

# 支持的图片格式
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
# 图片大小限制：10MB
MAX_IMAGE_SIZE = 10 * 1024 * 1024


@dataclass
class ImageUploadResult:
    """图片上传结果."""

    resource_id: str
    file_url: str
    cos_url: str
    file_size: int
    width: int = 0
    height: int = 0


class ImageUploader:
    """图片上传器 — 用于课程封面、小节封面、背景图等.

    与 ScormUploader 的区别：
    - 不需要分片上传（图片通常较小）
    - 不需要 SCORM 注册和转码轮询
    - media_type 使用 picweike/image，resource_type=6
    """

    def __init__(self, client: Any, base_url: str):
        """初始化上传器.

        Args:
            client: UMUClient 实例
            base_url: UMU 基础 URL，用于 Origin/Referer 头
        """
        self.client = client
        self.base_url = base_url
        self.cos_origin = base_url

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def _validate_image(self, file_path: str) -> tuple[str, str, int]:
        """验证图片文件.

        Args:
            file_path: 本地图片路径

        Returns:
            (绝对路径, 扩展名(不含点), 文件大小)

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 格式不支持或大小超限
        """
        abs_path = os.path.abspath(file_path)

        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"图片不存在: {abs_path}")

        if not os.path.isfile(abs_path):
            raise ValueError(f"路径不是文件: {abs_path}")

        ext = os.path.splitext(abs_path)[1].lower()
        if ext not in SUPPORTED_IMAGE_EXTS:
            raise ValueError(
                f"不支持的图片格式: {ext}，支持: {SUPPORTED_IMAGE_EXTS}"
            )

        file_size = os.path.getsize(abs_path)
        if file_size == 0:
            raise ValueError(f"图片文件大小为 0: {abs_path}")

        if file_size > MAX_IMAGE_SIZE:
            raise ValueError(
                f"图片过大: {file_size} bytes，最大支持 {MAX_IMAGE_SIZE // 1024 // 1024}MB"
            )

        ext_no_dot = ext.lstrip(".")
        if ext_no_dot == "jpg":
            ext_no_dot = "jpeg"  # 内部统一用 jpeg

        return abs_path, ext_no_dot, file_size

    # ------------------------------------------------------------------
    # 上传主流程
    # ------------------------------------------------------------------

    def upload(
        self,
        file_path: str,
        media_type: str = "picweike",
    ) -> ImageUploadResult:
        """上传图片到 UMU 资源库.

        Args:
            file_path: 本地图片路径
            media_type: 媒体类型标识，默认 picweike（课程封面/背景/小节封面通用）

        Returns:
            ImageUploadResult 实例

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件无效
            RuntimeError: 上传失败
        """
        # 1. 验证文件
        abs_path, ext, file_size = self._validate_image(file_path)
        file_name = os.path.basename(abs_path)

        logger.info("[ImageUpload] 开始上传: %s (%d bytes)", file_name, file_size)

        # 2. 获取 COS 凭证
        creds = self._get_credentials(ext, media_type)

        # 3. PUT 上传到 COS
        cos_url = self._upload_to_cos(abs_path, file_size, ext, creds)

        # 4. resourceCallback 注册到资源库
        self._resource_callback(creds, cos_url, file_name, file_size, media_type)

        # 5. 清理敏感凭证
        creds.tmp_ak = ""
        creds.tmp_sk = ""
        creds.session_token = ""

        logger.info(
            "[ImageUpload] 上传完成: resource_id=%s", creds.resource_id
        )

        return ImageUploadResult(
            resource_id=creds.resource_id,
            file_url=cos_url,
            cos_url=cos_url,
            file_size=file_size,
        )

    # ------------------------------------------------------------------
    # Stage 1: 获取 COS 凭证
    # ------------------------------------------------------------------

    def _get_credentials(self, ext: str, media_type: str) -> COSCredentials:
        """获取腾讯云 COS 临时上传凭证."""
        logger.info("[ImageUpload] 获取 COS 上传凭证...")

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

        # 请求凭证
        pre_payload = {
            "data": (
                '{"opts":['
                f'{{"key":"media_type","value":"{media_type}"}},'
                '{"key":"id_prefix","value":"teacher"},'
                f'{{"key":"id","value":"{teacher_id}"}},'
                f'{{"key":"ext","value":"{ext}"}}'
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

        creds = COSCredentials(
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

        if not creds.resource_id or not creds.object_name:
            raise RuntimeError("上传凭证响应缺少必要字段")

        if not creds.tmp_ak or not creds.tmp_sk:
            raise RuntimeError("COS 临时凭证缺少 tmp_ak 或 tmp_sk")

        logger.info(
            "[ImageUpload] 凭证获取成功: resource_id=%s",
            creds.resource_id,
        )
        return creds

    # ------------------------------------------------------------------
    # Stage 2: COS 直传
    # ------------------------------------------------------------------

    def _upload_to_cos(
        self,
        file_path: str,
        file_size: int,
        ext: str,
        creds: COSCredentials,
    ) -> str:
        """PUT 上传文件到 COS."""
        cos_url = f"{creds.bucket_url}/{creds.object_name}"

        auth = _cos_auth_header(
            method="PUT",
            uri=f"/{creds.object_name}",
            params={},
            headers={"Host": "umu-cn.umucdn.cn"},
            secret_id=creds.tmp_ak,
            secret_key=creds.tmp_sk,
            start_time=creds.start_time,
            expire_time=creds.expire_time,
        )

        content_type = f"image/{ext}"
        cos_headers = {
            "Host": "umu-cn.umucdn.cn",
            "Content-Type": content_type,
            "x-cos-security-token": creds.session_token,
            "Authorization": auth,
            "Origin": self.cos_origin,
            "Referer": f"{self.cos_origin}/",
        }

        with open(file_path, "rb") as f:
            content = f.read()

        resp = httpx.put(
            cos_url,
            content=content,
            headers=cos_headers,
            timeout=120,
        )

        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f"COS 上传失败: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        logger.info("[ImageUpload] COS 上传完成: %s", cos_url)
        return cos_url

    # ------------------------------------------------------------------
    # Stage 3: resourceCallback 注册
    # ------------------------------------------------------------------

    def _resource_callback(
        self,
        creds: COSCredentials,
        cos_url: str,
        file_name: str,
        file_size: int,
        media_type: str,
    ) -> None:
        """调用 resourceCallback 将资源注册到资源库列表."""
        logger.info("[ImageUpload] 注册资源到列表...")

        # 参照 HAR：后端期望 data={"media_type":"...",...} 格式的 form 字段
        callback_payload = {
            "media_type": media_type,
            "resource_id": creds.resource_id,
            "file_name": file_name,
            "file_size": file_size,
            "path": cos_url,
            "ext": os.path.splitext(file_name)[1].lower().lstrip("."),
            "extend_info": "",
        }

        try:
            callback_resp = self.client.post(
                self.client.desktop_url("/microapi/resourcemgt/resourceCallback"),
                data={"data": json.dumps(callback_payload, ensure_ascii=False)},
            )

            if callback_resp.get("status") is True or callback_resp.get("error_code") == 0:
                logger.info("[ImageUpload] 资源注册成功")
            else:
                err_msg = callback_resp.get("error", callback_resp.get("error_message", "unknown"))
                logger.error("[ImageUpload] 资源注册失败: %s", err_msg)
                raise RuntimeError(f"资源注册失败: {err_msg}")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error("[ImageUpload] 资源注册异常: %s", e)
            raise RuntimeError(f"资源注册异常: {e}") from e
