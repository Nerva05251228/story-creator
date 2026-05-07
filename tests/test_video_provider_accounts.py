import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import video_api_config  # noqa: E402
import video_provider_accounts  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "fake-response"

    def json(self):
        return self._payload


class FakeFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(self.payload)


class VideoProviderAccountsTests(unittest.TestCase):
    def test_provider_accounts_url_targets_provider_accounts_endpoint(self):
        self.assertEqual(
            video_api_config.get_video_provider_accounts_url("moti"),
            "https://ne.mocatter.cn/api/video/providers/moti/accounts",
        )

    def test_provider_stats_url_targets_stats_endpoint(self):
        self.assertEqual(
            video_api_config.get_video_provider_stats_url(),
            "https://ne.mocatter.cn/api/video/stats/providers",
        )

    def test_cache_refresh_fetches_once_and_returns_records(self):
        fetcher = FakeFetcher(
            {
                "total": 2,
                "records": [
                    {"account_id": "罗西剧场", "status": "OPEN"},
                    {"account_id": "cococo", "status": "CLOSED"},
                ],
            }
        )
        cache = video_provider_accounts.VideoProviderAccountsCache(fetcher=fetcher)

        refreshed = cache.refresh("moti")
        cached = cache.get("moti")

        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(
            fetcher.calls[0]["url"],
            "https://ne.mocatter.cn/api/video/providers/moti/accounts",
        )
        self.assertEqual(fetcher.calls[0]["timeout"], 180)
        self.assertEqual(refreshed["total"], 2)
        self.assertEqual(cached["records"][0]["account_id"], "罗西剧场")

    def test_cache_keeps_empty_shape_when_refresh_fails(self):
        class FailingFetcher:
            def get(self, *_args, **_kwargs):
                raise RuntimeError("network down")

        cache = video_provider_accounts.VideoProviderAccountsCache(fetcher=FailingFetcher())

        refreshed = cache.refresh("moti")

        self.assertEqual(refreshed["total"], 0)
        self.assertEqual(refreshed["records"], [])
        self.assertIn("error", refreshed)

    def test_resolve_video_provider_account_robot_id_prefers_robot_id(self):
        payload = {
            "records": [
                {"account_id": "罗西剧场", "robot_id": "2429291451132548"},
                {"account_id": "cococo", "robot_id": "1852023378305080"},
            ]
        }

        self.assertEqual(
            video_provider_accounts.resolve_video_provider_account_robot_id(payload, "罗西剧场"),
            "2429291451132548",
        )
        self.assertEqual(
            video_provider_accounts.resolve_video_provider_account_robot_id(payload, "1852023378305080"),
            "1852023378305080",
        )
        self.assertEqual(
            video_provider_accounts.resolve_video_provider_account_robot_id(payload, "missing"),
            "",
        )


if __name__ == "__main__":
    unittest.main()
