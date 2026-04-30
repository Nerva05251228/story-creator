import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class StartupScriptTests(unittest.TestCase):
    def test_scripts_load_shared_env_helper(self):
        for filename in ("start_all.ps1", "start_web.ps1", "start_poller.ps1"):
            with self.subTest(filename=filename):
                source = (ROOT_DIR / filename).read_text(encoding="utf-8")
                self.assertIn("start_env.ps1", source)
                self.assertIn("Load-ProjectEnv", source)

    def test_scripts_do_not_embed_private_database_or_api_tokens(self):
        for filename in (
            "start_env.ps1",
            "start_all.ps1",
            "start_all.cmd",
            "start_web.ps1",
            "start_web.cmd",
            "start_poller.ps1",
            "start_poller.cmd",
            "start_server.cmd",
        ):
            with self.subTest(filename=filename):
                source = (ROOT_DIR / filename).read_text(encoding="utf-8")
                self.assertNotRegex(source, r"sk-[A-Za-z0-9_-]{16,}")
                self.assertNotRegex(source, r"postgresql://[^'\"\s]+:[^@'\"\s]+@")
                self.assertNotRegex(source, r"admin\d{4}")

    def test_cmd_wrappers_delegate_to_powershell_launchers(self):
        expected = {
            "start_all.cmd": "start_all.ps1",
            "start_web.cmd": "start_web.ps1",
            "start_poller.cmd": "start_poller.ps1",
        }
        for filename, target in expected.items():
            with self.subTest(filename=filename):
                source = (ROOT_DIR / filename).read_text(encoding="utf-8")
                self.assertIn("-NoProfile", source)
                self.assertIn(target, source)
                self.assertIn("%*", source)
                self.assertRegex(source, r"exit /b %(ERRORLEVEL|START_(ALL|WEB|POLLER|SERVER)_EXIT)%")

    def test_cmd_wrappers_keep_double_click_window_open_on_failure(self):
        expected = {
            "start_all.cmd": "START_ALL_EXIT",
            "start_web.cmd": "START_WEB_EXIT",
            "start_poller.cmd": "START_POLLER_EXIT",
            "start_server.cmd": "START_SERVER_EXIT",
        }
        for filename, exit_var in expected.items():
            with self.subTest(filename=filename):
                source = (ROOT_DIR / filename).read_text(encoding="utf-8")
                lower_source = source.lower()

                self.assertIn(f'set "{exit_var}=%ERRORLEVEL%"', source)
                self.assertIn(f'if not "%{exit_var}%"=="0"', source)
                self.assertIn("startup failed", lower_source)
                self.assertIn("pause", lower_source)
                self.assertIn(f"exit /b %{exit_var}%", source)

    def test_shared_env_helper_loads_dotenv_without_overriding_existing_values(self):
        source = (ROOT_DIR / "start_env.ps1").read_text(encoding="utf-8")

        self.assertIn("function Load-ProjectEnv", source)
        self.assertIn("GetEnvironmentVariable($Name, 'Process')", source)
        self.assertIn("IsNullOrWhiteSpace($current)", source)
        self.assertIn("SetEnvironmentVariable($Name, $Value, 'Process')", source)
        self.assertIn("continue", source)
        self.assertIn("Set-DefaultEnv", source)

    def test_start_all_runs_preflight_before_launching_windows(self):
        source = (ROOT_DIR / "start_all.ps1").read_text(encoding="utf-8")

        self.assertIn(".\\preflight.py migrate", source)
        self.assertIn("$env:APP_ROLE = 'preflight'", source)
        self.assertIn("$env:ENABLE_BACKGROUND_POLLER = '0'", source)
        self.assertLess(
            source.index(".\\preflight.py migrate"),
            source.index("Start-Process -FilePath 'powershell.exe'"),
        )

    def test_web_script_runs_preflight_before_uvicorn_workers(self):
        source = (ROOT_DIR / "start_web.ps1").read_text(encoding="utf-8")

        self.assertIn("$env:APP_ROLE = 'web'", source)
        self.assertIn("$env:ENABLE_BACKGROUND_POLLER = '0'", source)
        self.assertIn("python .\\preflight.py migrate", source)
        self.assertIn("python -m uvicorn main:app", source)
        self.assertLess(
            source.index("python .\\preflight.py migrate"),
            source.index("python -m uvicorn main:app"),
        )

    def test_poller_script_checks_preflight_and_enables_poller_role(self):
        source = (ROOT_DIR / "start_poller.ps1").read_text(encoding="utf-8")

        self.assertIn("$env:APP_ROLE = 'poller'", source)
        self.assertIn("$env:ENABLE_BACKGROUND_POLLER = '1'", source)
        self.assertIn("python .\\preflight.py check", source)
        self.assertIn("python .\\run_pollers.py", source)
        self.assertLess(
            source.index("python .\\preflight.py check"),
            source.index("python .\\run_pollers.py"),
        )

    def test_cmd_web_script_runs_preflight_before_uvicorn_workers(self):
        source = (ROOT_DIR / "start_server.cmd").read_text(encoding="utf-8")

        self.assertIn("start_web.ps1", source)
        self.assertNotIn("preflight.py migrate", source)
        self.assertNotIn("-m uvicorn main:app", source)


if __name__ == "__main__":
    unittest.main()
