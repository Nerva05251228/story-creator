import os


TEST_LLM_RELAY_BASE_URL = "https://llm.example.test/api/llm"
TEST_IMAGE_PLATFORM_BASE_URL = "https://image.example.test/api/image"
TEST_VIDEO_API_BASE_URL = "https://video.example.test/api/video"
TEST_API_KEY = "test-api-token"


def apply_test_env_defaults() -> None:
    os.environ.setdefault("TEXT_RELAY_BASE_URL", TEST_LLM_RELAY_BASE_URL)
    os.environ.setdefault("TEXT_RELAY_API_KEY", TEST_API_KEY)
    os.environ.setdefault("IMAGE_PLATFORM_BASE_URL", TEST_IMAGE_PLATFORM_BASE_URL)
    os.environ.setdefault("IMAGE_PLATFORM_API_TOKEN", TEST_API_KEY)
    os.environ.setdefault("VIDEO_API_BASE_URL", TEST_VIDEO_API_BASE_URL)
    os.environ.setdefault("VIDEO_API_TOKEN", TEST_API_KEY)
