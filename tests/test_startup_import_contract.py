import ast
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
MAIN_PATH = BACKEND_DIR / "main.py"


def _top_level_calls(source: str) -> list[str]:
    tree = ast.parse(source)
    calls: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            calls.append(func.id)
        elif isinstance(func, ast.Attribute):
            parts = []
            current = func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            calls.append(".".join(reversed(parts)))
    return calls


class StartupImportContractTests(unittest.TestCase):
    def test_main_import_does_not_run_database_bootstrap(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        top_level_calls = _top_level_calls(source)

        self.assertNotIn("run_startup_bootstrap", top_level_calls)
        self.assertNotIn("models.Base.metadata.create_all", source)

    def test_main_uses_startup_runtime_for_poller_policy(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")

        self.assertIn("from startup_runtime import", source)
        self.assertNotIn("def should_enable_background_pollers", source)

    def test_web_startup_event_does_not_run_external_network_prewarms_directly(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        startup_body = source.split("async def startup_event():", 1)[1].split("# 关闭事件", 1)[0]

        self.assertIn("start_background_pollers", startup_body)
        self.assertNotIn("refresh_image_model_catalog", startup_body)
        self.assertNotIn("refresh_video_provider_accounts", startup_body)


if __name__ == "__main__":
    unittest.main()
