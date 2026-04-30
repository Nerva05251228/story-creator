import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import image_platform_client  # noqa: E402


def make_response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.side_effect = (
        Exception(f"HTTP {status_code}") if status_code >= 400 else None
    )
    return response


CATALOG_PAYLOAD = [
    {
        "key": "flux-pro",
        "model": "flux-pro",
        "display_name": "Flux Pro",
        "default_provider": "replicate",
        "fallback_providers": ["fal"],
        "ratios": ["1:1", "16:9"],
        "resolutions": ["1024x1024"],
        "supports_reference": True,
        "actions": ["text2image", "image2image"],
        "providers": [
            {
                "provider": "replicate",
                "upstream_model": "black-forest-labs/flux-pro",
                "ratios": ["1:1"],
                "resolutions": ["1024x1024"],
                "supports_reference": True,
                "actions": ["text2image"],
                "cost": 0.05,
                "priority": 1,
                "enabled": True,
                "internal_note": "drop me",
            },
            {
                "provider": "fal",
                "upstream_model": "fal-ai/flux-pro",
                "enabled": False,
            },
        ],
        "unexpected": "drop me",
    },
    {
        "key": "midjourney-v6",
        "model": "midjourney",
        "display_name": "Midjourney V6",
        "default_provider": "mj",
        "providers": [{"provider": "mj", "enabled": True}],
    },
    {
        "key": "gpt-image-1.5",
        "model": "gpt-image-1.5",
        "display_name": "GPT Image 1.5",
        "default_provider": "openai",
        "providers": [{"provider": "openai", "enabled": True}],
    },
]


class ImagePlatformClientTests(unittest.TestCase):
    def setUp(self):
        image_platform_client._MODEL_CATALOG_CACHE = None

    def tearDown(self):
        image_platform_client._MODEL_CATALOG_CACHE = None

    @patch.dict(os.environ, {}, clear=True)
    @patch("image_platform_client.requests.get")
    def test_fetch_filters_models_and_preserves_enabled_provider_fields(self, mock_get):
        mock_get.return_value = make_response(CATALOG_PAYLOAD)

        catalog = image_platform_client.fetch_image_model_catalog(timeout=12)

        self.assertEqual(len(catalog), 1)
        self.assertEqual(
            catalog[0],
            {
                "key": "flux-pro",
                "model": "flux-pro",
                "display_name": "Flux Pro",
                "default_provider": "replicate",
                "fallback_providers": ["fal"],
                "ratios": ["1:1", "16:9"],
                "resolutions": ["1024x1024"],
                "supports_reference": True,
                "actions": ["text2image", "image2image"],
                "providers": [
                    {
                        "provider": "replicate",
                        "upstream_model": "black-forest-labs/flux-pro",
                        "ratios": ["1:1"],
                        "resolutions": ["1024x1024"],
                        "supports_reference": True,
                        "actions": ["text2image"],
                        "cost": 0.05,
                        "priority": 1,
                        "enabled": True,
                    }
                ],
            },
        )
        mock_get.assert_called_once_with(
            "https://ne.mocatter.cn/api/image/models",
            headers={
                "Authorization": (
                    "Bearer sk-PhyClrwsJ4OPRff-0xr306P4uwA0kYKam_RL_GxKLtI"
                )
            },
            timeout=12,
        )

    @patch("image_platform_client.requests.get")
    def test_fetch_raises_for_empty_non_list_and_http_errors(self, mock_get):
        mock_get.return_value = make_response([])
        with self.assertRaises(ValueError):
            image_platform_client.fetch_image_model_catalog()

        mock_get.return_value = make_response({"models": []})
        with self.assertRaises(ValueError):
            image_platform_client.fetch_image_model_catalog()

        mock_get.return_value = make_response([], status_code=500)
        with self.assertRaises(Exception):
            image_platform_client.fetch_image_model_catalog()

    @patch("image_platform_client.requests.get")
    def test_resolve_route_accepts_aliases_and_provider_override(self, mock_get):
        mock_get.return_value = make_response(
            [
                {
                    "key": "story-art",
                    "model": "story-art-v1",
                    "display_name": "Story Art",
                    "default_provider": "disabled-default",
                    "providers": [
                        {"provider": "disabled-default", "enabled": False},
                        {
                            "provider": "enabled-a",
                            "upstream_model": "upstream-a",
                            "enabled": True,
                        },
                        {
                            "provider": "enabled-b",
                            "upstream_model": "upstream-b",
                            "enabled": True,
                        },
                    ],
                }
            ]
        )
        image_platform_client.refresh_image_model_catalog()

        by_key = image_platform_client.resolve_image_route("story-art")
        by_model = image_platform_client.resolve_image_route("story-art-v1")
        by_display = image_platform_client.resolve_image_route("Story Art")
        by_provider = image_platform_client.resolve_image_route(
            "story-art", provider="enabled-b"
        )

        self.assertEqual(by_key["provider"], "enabled-a")
        self.assertEqual(by_model["provider"], "enabled-a")
        self.assertEqual(by_display["provider"], "enabled-a")
        self.assertEqual(by_provider["upstream_model"], "upstream-b")
        with self.assertRaises(ValueError):
            image_platform_client.resolve_image_route("story-art", provider="missing")
        with self.assertRaises(ValueError):
            image_platform_client.resolve_image_route("missing-model")

    @patch.dict(
        os.environ,
        {
            "IMAGE_PLATFORM_BASE_URL": "https://example.test/api/image/",
            "IMAGE_PLATFORM_API_TOKEN": "platform-token",
        },
        clear=True,
    )
    @patch("image_platform_client.requests.post")
    def test_submit_image_task_posts_standard_payload_with_auth(self, mock_post):
        mock_post.return_value = make_response({"id": "task-1", "status": "queued"})

        result = image_platform_client.submit_image_task(
            prompt="a neon fox",
            model="flux-pro",
            username="alice",
            provider="replicate",
            action="image2image",
            ratio="16:9",
            resolution="1024x576",
            reference_images=["https://cdn.test/ref.png"],
            extra={"seed": 42},
            metadata={"source": "unit-test"},
            timeout=33,
        )

        self.assertEqual(result, {"id": "task-1", "status": "queued"})
        mock_post.assert_called_once_with(
            "https://example.test/api/image/tasks",
            json={
                "prompt": "a neon fox",
                "model": "flux-pro",
                "username": "alice",
                "provider": "replicate",
                "action": "image2image",
                "ratio": "16:9",
                "resolution": "1024x576",
                "reference_images": ["https://cdn.test/ref.png"],
                "extra": {"seed": 42},
                "metadata": {"source": "unit-test"},
            },
            headers={"Authorization": "Bearer platform-token"},
            timeout=33,
        )

    def test_normalize_task_status_response_prefers_final_images_and_error_message(self):
        normalized = image_platform_client.normalize_task_status_response(
            {
                "status": "failed",
                "progress": 75,
                "final_image_urls": ["https://cdn.test/final.png"],
                "upstream_image_urls": ["https://cdn.test/upstream.png"],
                "cost": 0.12,
                "provider": "replicate",
                "model": "flux-pro",
                "raw_response": {"id": "upstream-task"},
                "error": {"message": "quota exceeded"},
            }
        )

        self.assertEqual(normalized["status"], "failed")
        self.assertEqual(normalized["progress"], 75)
        self.assertEqual(normalized["images"], ["https://cdn.test/final.png"])
        self.assertEqual(normalized["cost"], 0.12)
        self.assertEqual(normalized["provider"], "replicate")
        self.assertEqual(normalized["model"], "flux-pro")
        self.assertEqual(normalized["raw_response"], {"id": "upstream-task"})
        self.assertEqual(normalized["error"], {"message": "quota exceeded"})
        self.assertEqual(normalized["error_message"], "quota exceeded")

    def test_normalize_task_status_response_falls_back_to_upstream_images(self):
        normalized = image_platform_client.normalize_task_status_response(
            {"upstream_image_urls": ["https://cdn.test/upstream.png"]}
        )

        self.assertEqual(normalized["images"], ["https://cdn.test/upstream.png"])


if __name__ == "__main__":
    unittest.main()
