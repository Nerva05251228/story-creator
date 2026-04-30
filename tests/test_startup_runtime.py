import importlib
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_startup_runtime():
    try:
        return importlib.import_module("startup_runtime")
    except ModuleNotFoundError as exc:
        if exc.name == "startup_runtime":
            raise AssertionError("startup_runtime module should exist") from exc
        raise


class StartupRuntimeTests(unittest.TestCase):
    def test_importing_startup_runtime_does_not_import_main(self):
        sys.modules.pop("startup_runtime", None)
        sys.modules.pop("main", None)

        startup_runtime = _load_startup_runtime()

        self.assertNotIn("main", sys.modules)
        self.assertTrue(hasattr(startup_runtime, "should_enable_background_pollers"))

    def test_is_env_truthy_uses_default_only_when_value_is_missing(self):
        startup_runtime = _load_startup_runtime()

        self.assertFalse(startup_runtime.is_env_truthy(None))
        self.assertTrue(startup_runtime.is_env_truthy(None, default=True))
        self.assertFalse(startup_runtime.is_env_truthy("off", default=True))

    def test_is_env_truthy_accepts_enabled_values(self):
        startup_runtime = _load_startup_runtime()

        for value in ("1", "true", "yes", "on", " TRUE "):
            with self.subTest(value=value):
                self.assertTrue(startup_runtime.is_env_truthy(value))

    def test_is_env_truthy_rejects_disabled_and_unknown_values(self):
        startup_runtime = _load_startup_runtime()

        for value in ("0", "false", "no", "off", "", "maybe"):
            with self.subTest(value=value):
                self.assertFalse(startup_runtime.is_env_truthy(value))

    def test_background_pollers_are_disabled_by_default(self):
        startup_runtime = _load_startup_runtime()

        self.assertFalse(startup_runtime.should_enable_background_pollers({}))

    def test_explicit_background_poller_truthy_values_enable_pollers(self):
        startup_runtime = _load_startup_runtime()

        for value in ("1", "true", "yes", "on"):
            with self.subTest(value=value):
                self.assertTrue(
                    startup_runtime.should_enable_background_pollers(
                        {"ENABLE_BACKGROUND_POLLER": value}
                    )
                )

    def test_explicit_background_poller_falsey_values_disable_pollers(self):
        startup_runtime = _load_startup_runtime()

        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                self.assertFalse(
                    startup_runtime.should_enable_background_pollers(
                        {"ENABLE_BACKGROUND_POLLER": value}
                    )
                )

    def test_app_role_poller_enables_background_pollers(self):
        startup_runtime = _load_startup_runtime()

        self.assertTrue(
            startup_runtime.should_enable_background_pollers({"APP_ROLE": "poller"})
        )

    def test_app_role_web_disables_background_pollers(self):
        startup_runtime = _load_startup_runtime()

        self.assertFalse(
            startup_runtime.should_enable_background_pollers({"APP_ROLE": "web"})
        )

    def test_explicit_background_poller_setting_takes_priority_over_app_role(self):
        startup_runtime = _load_startup_runtime()

        self.assertFalse(
            startup_runtime.should_enable_background_pollers(
                {"ENABLE_BACKGROUND_POLLER": "0", "APP_ROLE": "poller"}
            )
        )
        self.assertTrue(
            startup_runtime.should_enable_background_pollers(
                {"ENABLE_BACKGROUND_POLLER": "1", "APP_ROLE": "web"}
            )
        )


if __name__ == "__main__":
    unittest.main()
