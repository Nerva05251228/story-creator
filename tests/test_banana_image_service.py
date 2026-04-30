import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import TEST_IMAGE_PLATFORM_BASE_URL, apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import image_generation_service as image_service


class _FakeBinaryResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code
        self.text = ""


class ImagePlatformServiceCompatibilityTests(unittest.TestCase):
    image_output_url = "https://image.example.test/image/output/ComfyUI_00001.png"

    def test_legacy_banana_keys_resolve_to_unified_platform_models(self):
        self.assertEqual(image_service.normalize_image_model_key("banana2"), "nano-banana-2")
        self.assertEqual(image_service.normalize_image_model_key("banana2-moti"), "nano-banana-2")
        self.assertEqual(image_service.normalize_image_model_key("banana-pro"), "nano-banana-pro")
        self.assertEqual(image_service._resolve_image_provider(model_name="banana-pro"), "momo")
        self.assertEqual(image_service._resolve_image_provider(model_name="banana2-moti"), "momo")

    def test_submit_and_status_urls_use_unified_platform(self):
        self.assertEqual(
            image_service.get_image_submit_api_url(model_name="banana-pro"),
            f"{TEST_IMAGE_PLATFORM_BASE_URL}/tasks",
        )
        self.assertEqual(
            image_service.get_image_status_api_url(task_id="task_123", model_name="banana-pro"),
            f"{TEST_IMAGE_PLATFORM_BASE_URL}/tasks/task_123",
        )

    @patch("image_generation_service.image_platform_client.resolve_image_route")
    @patch("image_generation_service.image_platform_client.submit_image_task")
    def test_submit_image_generation_uses_unified_platform_task_api(self, mock_submit, mock_resolve):
        mock_resolve.return_value = {
            "key": "nano-banana-pro",
            "model": "Nano Banana Pro",
            "provider": "momo",
            "supports_reference": True,
            "ratios": ["16:9"],
            "resolutions": ["1K", "2K"],
        }
        mock_submit.return_value = {
            "id": "task-text-1",
            "status": "queued",
            "provider": "momo",
            "model": "Nano Banana Pro",
            "cost": 0,
        }

        task_id = image_service.submit_image_generation(
            prompt="city night product shot",
            model="banana-pro",
            size="16:9",
            resolution="1K",
            n=1,
            reference_images=None,
        )

        self.assertEqual(task_id, "task-text-1")
        mock_resolve.assert_called_once_with("banana-pro", provider=None)
        mock_submit.assert_called_once_with(
            prompt="city night product shot",
            model="Nano Banana Pro",
            username="story_creator",
            provider="momo",
            action="text2image",
            ratio="16:9",
            resolution="1K",
            reference_images=None,
            extra={"n": 1},
            metadata={"source": "story_creator", "requested_model": "banana-pro"},
        )

    @patch("image_generation_service.image_platform_client.resolve_image_route")
    @patch("image_generation_service.image_platform_client.submit_image_task")
    def test_submit_image_generation_uses_image2image_when_references_present(self, mock_submit, mock_resolve):
        mock_resolve.return_value = {
            "key": "nano-banana-2",
            "model": "Nano Banana 2",
            "provider": "momo",
            "supports_reference": True,
            "ratios": ["9:16"],
            "resolutions": ["2K"],
        }
        mock_submit.return_value = {"id": "task-image-1", "status": "queued"}

        task_id = image_service.submit_image_generation(
            prompt="restyle the scene",
            model="banana2",
            size="9:16",
            resolution="2K",
            n=1,
            reference_images=["https://example.com/ref.png"],
        )

        self.assertEqual(task_id, "task-image-1")
        mock_submit.assert_called_once_with(
            prompt="restyle the scene",
            model="Nano Banana 2",
            username="story_creator",
            provider="momo",
            action="image2image",
            ratio="9:16",
            resolution="2K",
            reference_images=["https://example.com/ref.png"],
            extra={"n": 1},
            metadata={"source": "story_creator", "requested_model": "banana2"},
        )

    @patch("image_generation_service.image_platform_client.get_image_task")
    def test_get_image_task_status_reads_unified_platform_response(self, mock_get_task):
        mock_get_task.return_value = {
            "id": "task-query-1",
            "provider": "momo",
            "model": "Nano Banana Pro",
            "status": "completed",
            "progress": 100,
            "cost": 0.12,
            "resolution": "1K",
            "final_image_urls": [
                self.image_output_url,
            ],
        }

        result = image_service.get_image_task_status("task-query-1", model_name="banana-pro")

        self.assertEqual(
            result,
            {
                "status": "completed",
                "progress": 100,
                "images": [self.image_output_url],
                "cost": 0.12,
                "provider": "momo",
                "model": "Nano Banana Pro",
                "resolution": "1K",
                "raw_status": "completed",
                "raw_response": mock_get_task.return_value,
            },
        )

    @patch("image_generation_service.image_platform_client.get_image_task")
    def test_get_image_task_status_returns_query_failed_on_transient_error(self, mock_get_task):
        import requests

        mock_get_task.side_effect = requests.exceptions.SSLError("EOF during handshake")

        result = image_service.get_image_task_status("task-query-ssl", model_name="banana2")

        self.assertEqual(result["status"], "query_failed")
        self.assertFalse(result["query_ok"])
        self.assertTrue(result["query_transient"])
        self.assertIn("查询异常", result["error_message"])

    @patch("image_generation_service.image_platform_client.get_image_task")
    def test_query_image_task_status_raw_returns_provider_payload(self, mock_get_task):
        payload = {
            "id": "task-query-raw",
            "status": "processing",
            "progress": 20,
        }
        mock_get_task.return_value = payload

        result = image_service.query_image_task_status_raw("task-query-raw", model_name="banana-pro")

        self.assertEqual(result, payload)

    @patch("image_generation_service.os.remove")
    @patch("image_generation_service.os.path.exists", return_value=True)
    @patch("image_generation_service.upload_to_cdn", return_value="https://cdn.example.com/fixed.png")
    @patch("image_generation_service.requests.get")
    def test_download_and_upload_image_normalizes_duplicate_port_url(
        self,
        mock_get,
        _mock_upload,
        _mock_exists,
        _mock_remove,
    ):
        mock_get.return_value = _FakeBinaryResponse(b"image-bytes", status_code=200)

        with patch("builtins.open", unittest.mock.mock_open()):
            result = image_service.download_and_upload_image(
                "http://47.236.112.10:10000:10000/tmp/demo.jpg",
                123,
            )

        self.assertEqual(result, "https://cdn.example.com/fixed.png")
        mock_get.assert_called_once_with(
            "http://47.236.112.10:10000/tmp/demo.jpg",
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
