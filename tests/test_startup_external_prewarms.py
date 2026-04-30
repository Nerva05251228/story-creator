import sys
import threading
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class StartupExternalPrewarmTests(unittest.TestCase):
    def test_run_external_cache_prewarms_logs_and_continues_after_failures(self):
        import startup_external_prewarms

        calls = []
        logs = []

        def fail_image_catalog():
            calls.append("image")
            raise RuntimeError("image unavailable")

        def refresh_accounts(provider):
            calls.append(("video", provider))

        startup_external_prewarms.run_external_cache_prewarms(
            image_catalog_refresh=fail_image_catalog,
            video_accounts_refresh=refresh_accounts,
            print_fn=logs.append,
        )

        self.assertEqual(calls, ["image", ("video", "moti")])
        self.assertTrue(any("refresh image model catalog failed" in item for item in logs))

    def test_start_external_cache_prewarms_returns_without_waiting_for_refresh(self):
        import startup_external_prewarms

        started = threading.Event()
        release = threading.Event()

        def blocking_image_catalog():
            started.set()
            release.wait(timeout=5)

        thread = startup_external_prewarms.start_external_cache_prewarms(
            image_catalog_refresh=blocking_image_catalog,
            video_accounts_refresh=lambda provider: None,
            print_fn=lambda _: None,
        )

        self.assertTrue(started.wait(timeout=1))
        self.assertTrue(thread.is_alive())
        release.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
