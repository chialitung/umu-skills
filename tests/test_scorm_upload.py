"""讲师端 SCORM 上传功能单元测试."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """创建模拟的 UMUClient."""
    client = MagicMock()
    client.base_url = "https://www.umu.cn"
    client.desktop_url = lambda path: f"https://www.umu.cn{path}"
    client.auth = MagicMock()
    client.auth.credentials = MagicMock()
    client.auth.credentials.username = "test_teacher"
    return client


@pytest.fixture
def temp_zip_file():
    """创建临时 zip 文件用于测试."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(b"PK\x03\x04" + b"fake zip content" * 100)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def mock_preobject_response():
    """模拟 preObject 响应."""
    return {
        "error_code": 0,
        "data": {
            "resource_id": "test-resource-id-123",
            "object_name": "resource/S2c/test/1234567890.zip",
            "bucket_info": {
                "id": "umu-cn-1303248253",
                "name": "umu-cn-1303248253",
                "region": "ap-beijing",
                "os_type": "COS",
                "bucket_url": "https://umu-cn.umucdn.cn",
            },
            "credential_info": {
                "session_token": "test-session-token-xyz",
                "start_time": "1780580368",
                "expire_time": "1780587568",
                "tmp_ak": "AKIDtest",
                "tmp_sk": "test-secret-key",
            },
        },
    }


@pytest.fixture
def mock_scorm_add_response():
    """模拟 scorm/add 响应."""
    return {
        "error_code": 0,
        "data": {
            "task_id": "test-task-id-abc",
        },
    }


@pytest.fixture
def mock_task_status_response():
    """模拟 task-status 响应."""
    return {
        "error_code": 0,
        "data": {
            "url": "https://example.com/scorm/launch",
        },
    }


@pytest.fixture
def mock_rename_response():
    """模拟 renameresource 响应."""
    return {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": {
            "result": 1,
        },
    }


@pytest.fixture
def mock_delete_response():
    """模拟 deleteresource 响应."""
    return {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
    }


@pytest.fixture
def mock_list_resources_response():
    """模拟 getresourcelist 响应."""
    return {
        "status": True,
        "errno": 0,
        "error_code": 0,
        "error": "success",
        "data": {
            "page_info": {
                "list_total_num": "1",
                "total_page_num": 1,
                "current_page": "1",
                "size": "15",
            },
            "list": [
                {
                    "id": "test-resource-id-123",
                    "file_name": "test_course.zip",
                    "file_size": "1024",
                    "file_duration": "0",
                    "url": "https://umu-cn.umucdn.cn/resource/test/123.zip",
                    "ext": "zip",
                    "media_type": "videoweike",
                    "transcoding_url": "",
                    "transcoding_ext": "scorm",
                    "create_time": "2025-12-17 09:12:51",
                    "status": "in_use",
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# ScormUploader 测试
# ---------------------------------------------------------------------------


class TestScormUploader:
    """测试 SCORM 上传器核心逻辑."""

    @pytest.mark.asyncio
    async def test_stage_1_validate_success(self, temp_zip_file):
        """测试文件验证成功."""
        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        uploader = ScormUploader(MagicMock(), "https://www.umu.cn")
        uploader.stage_1_validate(temp_zip_file, name="My Course")

        assert uploader.file_path == os.path.abspath(temp_zip_file)
        assert uploader.file_name.endswith(".zip")
        assert uploader.display_name == "My Course"
        assert uploader.file_size > 0

    @pytest.mark.asyncio
    async def test_stage_1_validate_file_not_found(self):
        """测试文件不存在."""
        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        uploader = ScormUploader(MagicMock(), "https://www.umu.cn")
        with pytest.raises(FileNotFoundError):
            uploader.stage_1_validate("/nonexistent/file.zip")

    @pytest.mark.asyncio
    async def test_stage_1_validate_not_zip(self):
        """测试非 zip 文件."""
        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a zip")
            path = f.name

        try:
            uploader = ScormUploader(MagicMock(), "https://www.umu.cn")
            with pytest.raises(ValueError, match="仅支持 .zip 格式"):
                uploader.stage_1_validate(path)
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_stage_1_validate_symlink(self):
        """测试符号链接被拒绝."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Windows 上创建符号链接需要管理员权限")

        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"PK\x03\x04")
            real_path = f.name

        symlink_path = real_path + "_link"
        try:
            os.symlink(real_path, symlink_path)
            uploader = ScormUploader(MagicMock(), "https://www.umu.cn")
            with pytest.raises(ValueError, match="不支持符号链接"):
                uploader.stage_1_validate(symlink_path)
        finally:
            if os.path.exists(symlink_path):
                os.unlink(symlink_path)
            os.unlink(real_path)

    @pytest.mark.asyncio
    async def test_stage_2_get_credentials(self, mock_preobject_response):
        """测试获取凭证."""
        import time
        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        # 更新 mock 响应中的时间戳为当前时间 + 1 小时，确保不过期
        future_time = int(time.time()) + 3600
        mock_preobject_response["data"]["credential_info"]["start_time"] = str(future_time - 7200)
        mock_preobject_response["data"]["credential_info"]["expire_time"] = str(future_time)

        client = MagicMock()
        client.desktop_url = lambda path: f"https://www.umu.cn{path}"
        client.get.return_value = {"data": {"teacher_id": "11872995"}}
        client.post.return_value = mock_preobject_response

        uploader = ScormUploader(client, "https://www.umu.cn")
        uploader.file_path = "/tmp/test.zip"
        uploader.file_name = "test.zip"
        uploader.file_size = 1000

        creds = uploader.stage_2_get_credentials()

        assert creds.resource_id == "test-resource-id-123"
        assert creds.object_name == "resource/S2c/test/1234567890.zip"
        assert creds.tmp_ak == "AKIDtest"
        assert creds.tmp_sk == "test-secret-key"
        assert not creds.is_expiring_soon()

    @pytest.mark.asyncio
    async def test_stage_2_preobject_failure(self):
        """测试 preObject 失败."""
        from umu_sdk.adapters.mcp.cos_upload import ScormUploader

        client = MagicMock()
        client.desktop_url = lambda path: f"https://www.umu.cn{path}"
        client.get.return_value = {"data": {"teacher_id": ""}}
        client.post.return_value = {"error_code": 1001, "error_message": "失败"}

        uploader = ScormUploader(client, "https://www.umu.cn")
        uploader.file_path = "/tmp/test.zip"
        uploader.file_name = "test.zip"
        uploader.file_size = 1000

        with pytest.raises(RuntimeError, match="获取上传凭证失败"):
            uploader.stage_2_get_credentials()

    @pytest.mark.asyncio
    async def test_credentials_expiry(self):
        """测试凭证过期检测."""
        from umu_sdk.adapters.mcp.cos_upload import COSCredentials

        import time

        now = int(time.time())
        # 5 分钟后过期的凭证
        creds = COSCredentials(
            resource_id="test",
            object_name="test",
            bucket_url="https://test.com",
            session_token="token",
            tmp_ak="ak",
            tmp_sk="sk",
            start_time=str(now - 3600),
            expire_time=str(now + 300),
        )
        assert creds.is_expiring_soon(buffer_seconds=600)  # 5 分钟后过期，10 分钟 buffer → 即将过期
        assert not creds.is_expiring_soon(buffer_seconds=60)  # 5 分钟后过期，1 分钟 buffer → 还没过期


# ---------------------------------------------------------------------------
# tch_upload_scorm 集成测试
# ---------------------------------------------------------------------------


class TestTchUploadScorm:
    """测试 tch_upload_scorm Tool."""

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """测试文件不存在时返回错误."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            result = await tch_upload_scorm("/nonexistent/file.zip")
            data = json.loads(result)

            assert data["success"] is False
            assert data["error_code"] == "FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_invalid_file_type(self, temp_zip_file):
        """测试非 zip 文件返回错误."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm

        # 创建一个非 zip 文件
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a zip")
            txt_path = f.name

        try:
            with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
                mock_client = MagicMock()
                mock_get_client.return_value = mock_client

                result = await tch_upload_scorm(txt_path)
                data = json.loads(result)

                assert data["success"] is False
                assert data["error_code"] == "INVALID_FILE"
        finally:
            os.unlink(txt_path)

    @pytest.mark.asyncio
    async def test_empty_file(self):
        """测试空文件返回错误."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"")  # 空文件
            empty_path = f.name

        try:
            with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
                mock_client = MagicMock()
                mock_get_client.return_value = mock_client

                result = await tch_upload_scorm(empty_path)
                data = json.loads(result)

                assert data["success"] is False
                assert data["error_code"] == "INVALID_FILE"
        finally:
            os.unlink(empty_path)

    @pytest.mark.asyncio
    async def test_successful_upload(self, temp_zip_file):
        """测试完整的成功上传流程."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm
        from umu_sdk.adapters.mcp.cos_upload import UploadResult, UploadProgress

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client, \
             patch("umu_sdk.tools.operations.resource_management.ScormUploader") as MockUploader:

            mock_client = MagicMock()
            mock_client.base_url = "https://www.umu.cn"
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            # Mock ScormUploader.run 返回成功结果
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=UploadResult(
                resource_id="test-resource-id-123",
                file_url="https://umu-cn.umucdn.cn/resource/test/123.zip",
                scorm_url="https://example.com/scorm/launch",
                task_id="test-task-id-abc",
                status="done",
                name="My SCORM Course",
                file_size=1024,
                task_result={"url": "https://example.com/scorm/launch"},
                progress=UploadProgress(
                    stage="upload_complete",
                    percent=100.0,
                    bytes_uploaded=1024,
                    bytes_total=1024,
                ),
            ))
            MockUploader.return_value = mock_instance

            result = await tch_upload_scorm(
                temp_zip_file,
                name="My SCORM Course",
                auto_rename=False,
            )
            data = json.loads(result)

            # 验证结果
            assert data["success"] is True
            assert data["data"]["resource_id"] == "test-resource-id-123"
            assert data["data"]["status"] == "done"
            assert data["data"]["scorm_url"] == "https://example.com/scorm/launch"
            assert "progress" in data["data"]
            assert data["data"]["progress"]["percent"] == 100.0
            assert data["data"]["rename_status"] == "skipped"

            # 验证 ScormUploader 被创建并调用
            MockUploader.assert_called_once_with(mock_client, "https://www.umu.cn")
            mock_instance.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_with_auto_rename(self, temp_zip_file):
        """测试上传后自动重命名."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm
        from umu_sdk.adapters.mcp.cos_upload import UploadResult

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client, \
             patch("umu_sdk.tools.operations.resource_management.ScormUploader") as MockUploader:

            mock_client = MagicMock()
            mock_client.base_url = "https://www.umu.cn"
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            # Mock 重命名成功
            mock_client.post.return_value = {
                "status": True,
                "error_code": 0,
            }

            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=UploadResult(
                resource_id="test-resource-id-123",
                file_url="https://umu-cn.umucdn.cn/resource/test/123.zip",
                scorm_url="https://example.com/scorm/launch",
                task_id="test-task-id-abc",
                status="done",
                name="Renamed Course",
                file_size=1024,
            ))
            MockUploader.return_value = mock_instance

            result = await tch_upload_scorm(
                temp_zip_file,
                name="Renamed Course",
                auto_rename=True,
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["data"]["rename_status"] == "success"
            assert data["data"]["name"] == "Renamed Course"

    @pytest.mark.asyncio
    async def test_preobject_failure(self, temp_zip_file):
        """测试 preObject 失败时返回错误."""
        from umu_sdk.adapters.mcp.teacher import tch_upload_scorm

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client, \
             patch("umu_sdk.tools.operations.resource_management.ScormUploader") as MockUploader:
            mock_client = MagicMock()
            mock_client.base_url = "https://www.umu.cn"
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("获取凭证失败"))
            MockUploader.return_value = mock_instance

            result = await tch_upload_scorm(temp_zip_file)
            data = json.loads(result)

            assert data["success"] is False
            assert data["error_code"] == "TCH_UPLOAD_SCORM_ERROR"


# ---------------------------------------------------------------------------
# tch_list_resources 测试
# ---------------------------------------------------------------------------


class TestTchListResources:
    """测试资源列表查询功能."""

    @pytest.mark.asyncio
    async def test_list_resources_success(self, mock_list_resources_response):
        """测试成功获取资源列表."""
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.get.return_value = mock_list_resources_response

            result = await tch_list_resources(page=1, page_size=15)
            data = json.loads(result)

            assert data["success"] is True
            assert len(data["data"]["resources"]) == 1
            assert data["data"]["resources"][0]["id"] == "test-resource-id-123"
            assert data["data"]["pagination"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_resources_empty(self):
        """测试空列表."""
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.get.return_value = {
                "status": True,
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": "0",
                        "total_page_num": 0,
                        "current_page": "1",
                        "size": "15",
                    },
                    "list": [],
                },
            }

            result = await tch_list_resources()
            data = json.loads(result)

            assert data["success"] is True
            assert len(data["data"]["resources"]) == 0
            assert data["data"]["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_resources_failure(self):
        """测试获取失败."""
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.get.return_value = {
                "status": False,
                "error_code": 500,
                "error": "服务器错误",
            }

            result = await tch_list_resources()
            data = json.loads(result)

            assert data["success"] is False
            assert data["error_code"] == "LIST_RESOURCES_FAILED"


# ---------------------------------------------------------------------------
# tch_rename_resource 测试
# ---------------------------------------------------------------------------


class TestTchRenameResource:
    """测试资源重命名功能."""

    @pytest.mark.asyncio
    async def test_rename_success(self, mock_rename_response):
        """测试成功重命名."""
        from umu_sdk.adapters.mcp.teacher import tch_rename_resource

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.post.return_value = mock_rename_response

            result = await tch_rename_resource(
                resource_id="test-resource-id-123",
                file_name="New Course Name",
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["data"]["new_name"] == "New Course Name"
            assert data["data"]["resource_id"] == "test-resource-id-123"

    @pytest.mark.asyncio
    async def test_rename_failure(self):
        """测试重命名失败."""
        from umu_sdk.adapters.mcp.teacher import tch_rename_resource

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.post.return_value = {
                "status": False,
                "error": "资源不存在",
            }

            result = await tch_rename_resource(
                resource_id="invalid-id",
                file_name="New Name",
            )
            data = json.loads(result)

            assert data["success"] is False
            assert data["error_code"] == "RENAME_FAILED"


# ---------------------------------------------------------------------------
# tch_delete_resource 测试
# ---------------------------------------------------------------------------


class TestTchDeleteResource:
    """测试资源删除功能."""

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_delete_response):
        """测试成功删除."""
        from umu_sdk.adapters.mcp.teacher import tch_delete_resource

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.post.return_value = mock_delete_response

            result = await tch_delete_resource(
                resource_id="test-resource-id-123",
            )
            data = json.loads(result)

            assert data["success"] is True
            assert data["data"]["deleted"] is True
            assert data["data"]["resource_id"] == "test-resource-id-123"

    @pytest.mark.asyncio
    async def test_delete_failure(self):
        """测试删除失败."""
        from umu_sdk.adapters.mcp.teacher import tch_delete_resource

        with patch("umu_sdk.adapters.mcp.teacher._get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.desktop_url = lambda path: f"https://www.umu.cn{path}"
            mock_get_client.return_value = mock_client

            mock_client.post.return_value = {
                "status": False,
                "error": "权限不足",
            }

            result = await tch_delete_resource(
                resource_id="invalid-id",
            )
            data = json.loads(result)

            assert data["success"] is False
            assert data["error_code"] == "DELETE_FAILED"


# ---------------------------------------------------------------------------
# 环境验证策略测试
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 路径安全测试
# ---------------------------------------------------------------------------


class TestPathValidation:
    """测试文件路径安全验证."""

    def test_symlink_rejected(self):
        """测试符号链接被拒绝."""
        import sys

        if sys.platform == "win32":
            pytest.skip("Windows 上创建符号链接需要管理员权限")

        from umu_sdk.adapters.mcp.cos_upload import validate_file_path

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"PK\x03\x04")
            real_path = f.name

        link_path = real_path + "_link"
        try:
            os.symlink(real_path, link_path)
            with pytest.raises(ValueError, match="不支持符号链接"):
                validate_file_path(link_path)
        finally:
            if os.path.exists(link_path):
                os.unlink(link_path)
            os.unlink(real_path)

    def test_directory_rejected(self):
        """测试目录路径被拒绝."""
        from umu_sdk.adapters.mcp.cos_upload import validate_file_path

        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(ValueError, match="路径不是文件"):
                validate_file_path(d)

    def test_empty_file_rejected(self):
        """测试空文件被拒绝."""
        from umu_sdk.adapters.mcp.cos_upload import validate_file_path

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"")
            path = f.name

        try:
            with pytest.raises(ValueError, match="文件大小为 0"):
                validate_file_path(path)
        finally:
            os.unlink(path)
