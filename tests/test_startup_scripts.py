import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class StartupScriptTests(unittest.TestCase):
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

        self.assertIn("$pythonExe = Join-Path $projectRoot 'venv\\Scripts\\python.exe'", source)
        self.assertIn("$env:APP_ROLE = 'web'", source)
        self.assertIn("$env:ENABLE_BACKGROUND_POLLER = '0'", source)
        self.assertIn("& $pythonExe .\\preflight.py migrate", source)
        self.assertIn("& $pythonExe -m uvicorn main:app", source)
        self.assertLess(
            source.index("& $pythonExe .\\preflight.py migrate"),
            source.index("& $pythonExe -m uvicorn main:app"),
        )

    def test_poller_script_runs_preflight_migrate_and_enables_poller_role(self):
        source = (ROOT_DIR / "start_poller.ps1").read_text(encoding="utf-8")

        self.assertIn("$pythonExe = Join-Path $projectRoot 'venv\\Scripts\\python.exe'", source)
        self.assertIn("$env:APP_ROLE = 'poller'", source)
        self.assertIn("$env:ENABLE_BACKGROUND_POLLER = '1'", source)
        self.assertIn("& $pythonExe .\\preflight.py migrate", source)
        self.assertIn("& $pythonExe .\\run_pollers.py", source)
        self.assertLess(
            source.index("& $pythonExe .\\preflight.py migrate"),
            source.index("& $pythonExe .\\run_pollers.py"),
        )

    def test_poller_script_uses_migrate_instead_of_check(self):
        source = (ROOT_DIR / "start_poller.ps1").read_text(encoding="utf-8")

        self.assertIn("& $pythonExe .\\preflight.py migrate", source)
        self.assertNotIn("python .\\preflight.py check", source)

    def test_run_pollers_runs_preflight_migrate_before_starting_threads(self):
        source = (ROOT_DIR / "backend" / "run_pollers.py").read_text(encoding="utf-8")

        self.assertIn('run_startup_preflight(mode="migrate")', source)
        self.assertLess(
            source.index('run_startup_preflight(mode="migrate")'),
            source.index("start_background_pollers(force=True)"),
        )

    def test_cmd_web_script_runs_preflight_before_uvicorn_workers(self):
        source = (ROOT_DIR / "start_server.cmd").read_text(encoding="utf-8")

        self.assertIn('set "APP_ROLE=web"', source)
        self.assertIn('set "ENABLE_BACKGROUND_POLLER=0"', source)
        self.assertIn("preflight.py migrate", source)
        self.assertIn("-m uvicorn main:app", source)
        self.assertLess(
            source.index("preflight.py migrate"),
            source.index("-m uvicorn main:app"),
        )


if __name__ == "__main__":
    unittest.main()
