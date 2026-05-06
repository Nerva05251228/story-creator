import ast
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
MAIN_PATH = BACKEND_DIR / "main.py"
RUNTIME_POLLER_PATH = BACKEND_DIR / "runtime" / "pollers.py"


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


def _function_body_source(source: str, function_name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "\n".join(ast.get_source_segment(source, child) or "" for child in node.body)
    raise AssertionError(f"{function_name} not found")


def _function_node(source: str, function_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return node
    raise AssertionError(f"{function_name} not found")


def _dotted_call_name(node: ast.Call) -> str:
    parts = []
    current = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_metadata_create_all_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "create_all":
        return False
    metadata_attr = node.func.value
    return isinstance(metadata_attr, ast.Attribute) and metadata_attr.attr == "metadata"


def _function_call_leaf_names(source: str, function_name: str) -> set[str]:
    function = _function_node(source, function_name)
    names = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            call_name = _dotted_call_name(node)
            if call_name:
                names.add(call_name.rsplit(".", 1)[-1])
    return names


def _top_level_definition_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _imported_names_from_module(source: str, module_name: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != module_name:
            continue
        names.update(alias.name for alias in node.names)
    return names


def _router_decorator_paths(source: str) -> set[str]:
    paths = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                value = decorator.args[0].value
                if isinstance(value, str):
                    paths.add(value)
    return paths


class StartupImportContractTests(unittest.TestCase):
    def test_main_import_does_not_run_database_bootstrap(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        top_level_calls = _top_level_calls(source)

        self.assertNotIn("run_startup_bootstrap", top_level_calls)
        self.assertNotIn("models.Base.metadata.create_all", source)

    def test_runtime_pollers_uses_startup_runtime_for_poller_policy(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        runtime_source = RUNTIME_POLLER_PATH.read_text(encoding="utf-8-sig")

        self.assertIn("from startup_runtime import", runtime_source)
        self.assertNotIn("def should_enable_background_pollers", source)
        self.assertNotIn("background_poller_lock", source)
        self.assertNotIn("background_pollers_started", source)

    def test_web_startup_event_does_not_run_external_network_prewarms_directly(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        startup_body = _function_body_source(source, "startup_event")

        self.assertIn("start_background_pollers", startup_body)
        self.assertNotIn("refresh_image_model_catalog", startup_body)
        self.assertNotIn("refresh_video_provider_accounts", startup_body)

    def test_web_shutdown_event_delegates_background_poller_stop(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        shutdown_body = _function_body_source(source, "shutdown_event")

        self.assertIn("stop_background_pollers", shutdown_body)
        self.assertNotIn("poller.stop", shutdown_body)
        self.assertNotIn("image_poller.stop", shutdown_body)

    def test_storyboard2_core_helpers_are_not_redefined_in_main(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        top_level_defs = _top_level_definition_names(source)
        delegated_names = {
            "Storyboard2BatchGenerateSoraPromptsRequest",
            "Storyboard2UpdateShotRequest",
            "Storyboard2UpdateSubShotRequest",
            "_verify_episode_permission",
            "_parse_storyboard2_card_ids",
            "_collect_storyboard2_reference_images",
            "_clean_scene_ai_prompt_text",
            "_extract_scene_description_from_card_ids",
            "_resolve_storyboard2_scene_override_text",
            "_pick_storyboard2_source_shots",
            "_ensure_storyboard2_initialized",
            "_mark_storyboard2_image_task_active",
            "_mark_storyboard2_image_task_inactive",
            "_is_storyboard2_image_task_active",
            "_recover_orphan_storyboard2_image_tasks",
            "_serialize_storyboard2_board",
            "_get_storyboard2_sub_shot_with_permission",
            "_get_storyboard2_shot_with_permission",
            "_resolve_storyboard2_selected_card_ids",
            "_is_scene_subject_card_type",
            "_subject_type_sort_key",
            "_get_optional_prompt_config_content",
            "_save_storyboard2_image_debug",
            "_save_storyboard2_video_debug",
            "_normalize_storyboard2_video_status",
            "_is_storyboard2_video_processing",
            "_build_storyboard2_video_name_tag",
            "_process_storyboard2_video_cover_and_cdn",
            "_recover_storyboard2_video_polling",
            "_sync_storyboard2_processing_videos",
        }
        service_aliases = {
            "_parse_storyboard2_card_ids": "parse_storyboard2_card_ids",
            "_resolve_storyboard2_selected_card_ids": "resolve_storyboard2_selected_card_ids",
            "_is_scene_subject_card_type": "is_scene_subject_card_type",
            "_collect_storyboard2_reference_images": "collect_storyboard2_reference_images",
        }
        board_service_aliases = {
            "_clean_scene_ai_prompt_text": "clean_scene_ai_prompt_text",
            "_extract_scene_description_from_card_ids": "extract_scene_description_from_card_ids",
            "_resolve_storyboard2_scene_override_text": "resolve_storyboard2_scene_override_text",
            "_pick_storyboard2_source_shots": "pick_storyboard2_source_shots",
            "_ensure_storyboard2_initialized": "ensure_storyboard2_initialized",
            "_serialize_storyboard2_board": "serialize_storyboard2_board",
            "_subject_type_sort_key": "subject_type_sort_key",
        }
        media_service_aliases = {
            "_normalize_storyboard2_video_status": "normalize_storyboard2_video_status",
            "_is_storyboard2_video_processing": "is_storyboard2_video_processing",
        }
        permission_service_aliases = {
            "_verify_episode_permission": "verify_episode_permission",
            "_get_storyboard2_sub_shot_with_permission": "get_storyboard2_sub_shot_with_permission",
            "_get_storyboard2_shot_with_permission": "get_storyboard2_shot_with_permission",
        }
        image_task_state_service_aliases = {
            "_mark_storyboard2_image_task_active": "mark_storyboard2_image_task_active",
            "_mark_storyboard2_image_task_inactive": "mark_storyboard2_image_task_inactive",
            "_is_storyboard2_image_task_active": "is_storyboard2_image_task_active",
            "_recover_orphan_storyboard2_image_tasks": "recover_orphan_storyboard2_image_tasks",
        }
        video_task_service_aliases = {
            "_build_storyboard2_video_name_tag": "build_storyboard2_video_name_tag",
            "_process_storyboard2_video_cover_and_cdn": "process_storyboard2_video_cover_and_cdn",
        }
        video_polling_service_aliases = {
            "_recover_storyboard2_video_polling": "recover_storyboard2_video_polling",
            "_sync_storyboard2_processing_videos": "sync_storyboard2_processing_videos",
        }

        self.assertEqual(top_level_defs & delegated_names, set())
        self.assertIn("from api.services import storyboard2_reference_images", source)
        self.assertIn("from api.services import storyboard2_board", source)
        self.assertIn("from api.services import storyboard2_media", source)
        self.assertIn("from api.services import storyboard2_permissions", source)
        self.assertIn("from api.services import storyboard2_image_task_state", source)
        self.assertIn("from api.services import storyboard2_video_polling", source)
        self.assertIn("from api.services import storyboard2_video_tasks", source)
        for name in delegated_names - set(service_aliases) - set(board_service_aliases) - set(media_service_aliases) - set(permission_service_aliases) - set(image_task_state_service_aliases) - set(video_task_service_aliases) - set(video_polling_service_aliases):
            self.assertIn(f"{name} = storyboard2.{name}", source)
        for name, service_name in service_aliases.items():
            self.assertIn(f"{name} = storyboard2_reference_images.{service_name}", source)
        for name, service_name in board_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_board.{service_name}", source)
        for name, service_name in media_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_media.{service_name}", source)
        for name, service_name in permission_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_permissions.{service_name}", source)
        for name, service_name in image_task_state_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_image_task_state.{service_name}", source)
        for name, service_name in video_task_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_video_tasks.{service_name}", source)
        for name, service_name in video_polling_service_aliases.items():
            self.assertIn(f"{name} = storyboard2_video_polling.{service_name}", source)

    def test_storyboard2_reference_image_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_reference_images.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_parse_storyboard2_card_ids": "parse_storyboard2_card_ids",
            "_resolve_storyboard2_selected_card_ids": "resolve_storyboard2_selected_card_ids",
            "_is_scene_subject_card_type": "is_scene_subject_card_type",
            "_collect_storyboard2_reference_images": "collect_storyboard2_reference_images",
        }

        self.assertIn("from api.services import storyboard2_reference_images", main_source)
        self.assertIn("storyboard2_reference_images,", router_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"{name} = storyboard2_reference_images.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_reference_images.{service_name}", router_source)
            self.assertIn(f"def {service_name}", service_source)

    def test_storyboard2_board_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_board.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_clean_scene_ai_prompt_text": "clean_scene_ai_prompt_text",
            "_extract_scene_description_from_card_ids": "extract_scene_description_from_card_ids",
            "_resolve_storyboard2_scene_override_text": "resolve_storyboard2_scene_override_text",
            "_pick_storyboard2_source_shots": "pick_storyboard2_source_shots",
            "_ensure_storyboard2_initialized": "ensure_storyboard2_initialized",
            "_serialize_storyboard2_board": "serialize_storyboard2_board",
            "_subject_type_sort_key": "subject_type_sort_key",
        }

        self.assertIn("from api.services import storyboard2_board", main_source)
        self.assertIn("from api.services import storyboard2_board", episodes_source)
        self.assertIn("storyboard2_board,", router_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(episodes_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"{name} = storyboard2_board.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_board.{service_name}", episodes_source)
            self.assertIn(f"{name} = storyboard2_board.{service_name}", router_source)
            self.assertIn(f"def {service_name}", service_source)

    def test_episode_text_submission_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "episode_text_generation.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_resolve_narration_template": "resolve_narration_template",
            "_resolve_opening_template": "resolve_opening_template",
            "_submit_episode_text_relay_task": "submit_episode_text_relay_task",
            "_submit_detailed_storyboard_stage1_task": "submit_detailed_storyboard_stage1_task",
        }

        self.assertIn("from api.services import episode_text_generation", main_source)
        self.assertIn("from api.services import episode_text_generation", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(episodes_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"{name} = episode_text_generation.{service_name}", main_source)
            self.assertIn(f"{name} = episode_text_generation.{service_name}", episodes_source)
            self.assertNotIn(f"{name} = episodes.{name}", main_source)
            self.assertIn(f"def {service_name}", service_source)

    def test_episode_runtime_state_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "episode_runtime_state.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_refresh_episode_batch_sora_prompt_state": "refresh_episode_batch_sora_prompt_state",
            "_repair_stale_storyboard_prompt_generation": "repair_stale_storyboard_prompt_generation",
            "_reconcile_episode_runtime_flags": "reconcile_episode_runtime_flags",
        }

        self.assertIn("from api.services import episode_runtime_state", main_source)
        self.assertIn("from api.services import episode_runtime_state", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(episodes_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"{name} = episode_runtime_state.{service_name}", main_source)
            self.assertIn(f"{name} = episode_runtime_state.{service_name}", episodes_source)
            self.assertIn(f"def {service_name}", service_source)

    def test_storyboard2_media_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        board_source = (BACKEND_DIR / "api" / "services" / "storyboard2_board.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_media.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_normalize_jimeng_ratio": "normalize_jimeng_ratio",
            "_normalize_storyboard2_video_status": "normalize_storyboard2_video_status",
            "_is_storyboard2_video_processing": "is_storyboard2_video_processing",
        }

        self.assertIn("from api.services import storyboard2_media", main_source)
        self.assertIn("storyboard2_media,", router_source)
        self.assertIn("storyboard2_media", board_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(aliases), set())
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            if name in {"_normalize_storyboard2_video_status", "_is_storyboard2_video_processing"}:
                self.assertIn(f"{name} = storyboard2_media.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_media.{service_name}", router_source)

    def test_storyboard2_permission_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_permissions.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_verify_episode_permission": "verify_episode_permission",
            "_get_storyboard2_sub_shot_with_permission": "get_storyboard2_sub_shot_with_permission",
            "_get_storyboard2_shot_with_permission": "get_storyboard2_shot_with_permission",
        }

        self.assertIn("from api.services import storyboard2_permissions", main_source)
        self.assertIn("from api.services import storyboard2_permissions", episodes_source)
        self.assertIn("storyboard2_permissions,", router_source)
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard2_permissions.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_permissions.{service_name}", episodes_source)
            self.assertIn(f"{name} = storyboard2_permissions.{service_name}", router_source)

    def test_storyboard2_video_task_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_video_tasks.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_build_storyboard2_video_name_tag": "build_storyboard2_video_name_tag",
            "_process_storyboard2_video_cover_and_cdn": "process_storyboard2_video_cover_and_cdn",
        }

        self.assertIn("from api.services import storyboard2_video_tasks", main_source)
        self.assertIn("from api.services import storyboard2_video_tasks", episodes_source)
        self.assertIn("storyboard2_video_tasks,", router_source)
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard2_video_tasks.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_video_tasks.{service_name}", episodes_source)
            self.assertIn(f"{name} = storyboard2_video_tasks.{service_name}", router_source)

    def test_storyboard2_video_polling_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_video_polling.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_recover_storyboard2_video_polling": "recover_storyboard2_video_polling",
            "_sync_storyboard2_processing_videos": "sync_storyboard2_processing_videos",
        }

        self.assertIn("from api.services import storyboard2_video_polling", main_source)
        self.assertIn("from api.services import storyboard2_video_polling", episodes_source)
        self.assertIn("storyboard2_video_polling,", router_source)
        self.assertIn("def poll_storyboard2_sub_shot_video_status", service_source)
        self.assertNotIn("def _poll_storyboard2_sub_shot_video_status", router_source)
        self.assertEqual(_top_level_definition_names(router_source) & (set(aliases) | {"_poll_storyboard2_sub_shot_video_status"}), set())
        self.assertIn("storyboard2_video_polling.poll_storyboard2_sub_shot_video_status", router_source)
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard2_video_polling.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_video_polling.{service_name}", episodes_source)
            self.assertIn(f"{name} = storyboard2_video_polling.{service_name}", router_source)

    def test_storyboard2_image_task_state_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard2_image_task_state.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        aliases = {
            "_mark_storyboard2_image_task_active": "mark_storyboard2_image_task_active",
            "_mark_storyboard2_image_task_inactive": "mark_storyboard2_image_task_inactive",
            "_is_storyboard2_image_task_active": "is_storyboard2_image_task_active",
            "_recover_orphan_storyboard2_image_tasks": "recover_orphan_storyboard2_image_tasks",
        }

        self.assertIn("from api.services import storyboard2_image_task_state", main_source)
        self.assertIn("from api.services import storyboard2_image_task_state", episodes_source)
        self.assertIn("storyboard2_image_task_state,", router_source)
        self.assertEqual(_top_level_definition_names(router_source) & set(aliases), set())
        self.assertNotIn("storyboard2_active_image_tasks = set()", router_source)
        self.assertNotIn("storyboard2_active_image_tasks_lock = Lock()", router_source)
        self.assertIn("storyboard2_active_image_tasks = set()", service_source)
        self.assertIn("storyboard2_active_image_tasks_lock = Lock()", service_source)
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard2_image_task_state.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard2_image_task_state.{service_name}", episodes_source)
            self.assertIn(f"{name} = storyboard2_image_task_state.{service_name}", router_source)

    def test_episode_cleanup_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "episode_cleanup.py").read_text(encoding="utf-8-sig")
        shared_aliases = {
            "_normalize_storyboard_shot_ids": "normalize_storyboard_shot_ids",
            "_clear_storyboard_shot_dependencies": "clear_storyboard_shot_dependencies",
            "_delete_storyboard_shots_by_ids": "delete_storyboard_shots_by_ids",
            "_delete_episode_storyboard_shots": "delete_episode_storyboard_shots",
        }
        main_only_aliases = {
            "_clear_episode_dependencies": "clear_episode_dependencies",
        }

        self.assertIn("from api.services import episode_cleanup", main_source)
        self.assertIn("from api.services import episode_cleanup", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & (set(shared_aliases) | set(main_only_aliases)), set())
        self.assertEqual(_top_level_definition_names(episodes_source) & set(shared_aliases), set())
        for name, service_name in shared_aliases.items():
            self.assertIn(f"{name} = episode_cleanup.{service_name}", main_source)
            self.assertIn(f"{name} = episode_cleanup.{service_name}", episodes_source)
            self.assertIn(f"def {service_name}", service_source)
        for name, service_name in main_only_aliases.items():
            self.assertIn(f"{name} = episode_cleanup.{service_name}", main_source)
            self.assertIn(f"def {service_name}", service_source)

    def test_storyboard_excel_route_module_owns_storyboard_excel_routes(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        storyboard_excel_path = BACKEND_DIR / "api" / "routers" / "storyboard_excel.py"
        storyboard_excel_source = storyboard_excel_path.read_text(encoding="utf-8-sig")

        self.assertTrue(storyboard_excel_path.exists())
        self.assertIn("router = APIRouter()", storyboard_excel_source)
        self.assertIn("storyboard_excel,", main_source)
        self.assertIn("app.include_router(storyboard_excel.router)", main_source)
        self.assertEqual(
            {
                path
                for path in _router_decorator_paths(episodes_source)
                if "import-storyboard" in path or "export-storyboard" in path
            },
            set(),
        )
        self.assertEqual(
            {
                path
                for path in _router_decorator_paths(storyboard_excel_source)
                if "import-storyboard" in path or "export-storyboard" in path
            },
            {
                "/api/episodes/{episode_id}/import-storyboard",
                "/api/episodes/{episode_id}/export-storyboard",
            },
        )

    def test_episode_router_batch_video_helpers_are_defined(self):
        source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        top_level_defs = _top_level_definition_names(source)

        self.assertIn("_record_storyboard_video_charge", top_level_defs)
        self.assertIn(
            "_resolve_storyboard_video_model_by_provider = storyboard_video_settings.resolve_storyboard_video_model_by_provider",
            source,
        )
        self.assertIn(
            "_is_moti_storyboard_video_model = storyboard_video_settings.is_moti_storyboard_video_model",
            source,
        )

    def test_billing_charge_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        main_top_level_defs = _top_level_definition_names(main_source)
        episodes_top_level_defs = _top_level_definition_names(episodes_source)

        self.assertIn("from api.services import billing_charges", main_source)
        self.assertIn("from api.services import billing_charges", episodes_source)
        self.assertEqual(
            main_top_level_defs
            & {
                "_safe_json_dumps",
                "_record_card_image_charge",
                "_record_storyboard_image_charge",
                "_record_detail_image_charge",
                "_record_storyboard2_video_charge",
                "_record_storyboard2_image_charge",
            },
            set(),
        )
        self.assertEqual(
            episodes_top_level_defs
            & {
                "_safe_json_dumps",
                "_record_storyboard2_video_charge",
                "_record_storyboard2_image_charge",
            },
            set(),
        )

    def test_db_commit_retry_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        simple_storyboard_source = (
            BACKEND_DIR / "api" / "routers" / "simple_storyboard.py"
        ).read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "db_commit_retry.py"
        delegated_names = {
            "_rollback_quietly",
            "_is_sqlite_lock_error",
            "commit_with_retry",
        }

        self.assertTrue(service_path.exists())
        self.assertIn("from api.services import db_commit_retry", main_source)
        self.assertIn("from api.services import db_commit_retry", episodes_source)
        self.assertIn("from api.services import db_commit_retry", simple_storyboard_source)
        for source in (main_source, episodes_source, simple_storyboard_source):
            self.assertEqual(_top_level_definition_names(source) & delegated_names, set())
            self.assertIn(
                "SQLITE_LOCK_RETRY_DELAYS = db_commit_retry.SQLITE_LOCK_RETRY_DELAYS",
                source,
            )
            self.assertIn("_rollback_quietly = db_commit_retry.rollback_quietly", source)
            self.assertIn(
                "_is_sqlite_lock_error = db_commit_retry.is_sqlite_lock_error",
                source,
            )
            self.assertIn("commit_with_retry = db_commit_retry.commit_with_retry", source)

    def test_episode_metadata_helpers_are_not_redefined_in_main(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        top_level_defs = _top_level_definition_names(source)
        delegated_names = {
            "get_episode",
            "_build_episode_poll_status_payload",
            "_count_storyboard_items",
            "get_episode_poll_status",
            "get_episode_total_cost",
            "update_episode",
            "update_episode_storyboard2_duration",
        }

        self.assertEqual(top_level_defs & delegated_names, set())
        for name in delegated_names:
            self.assertIn(f"{name} = episodes.{name}", source)

    def test_storyboard_sync_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "storyboard_sync.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_normalize_subject_detail_entry",
            "_build_subject_detail_map",
            "_normalize_storyboard_generation_subjects",
            "_find_meaningful_common_fragment",
            "_infer_storyboard_role_name_from_shot",
            "_resolve_storyboard_subject_name",
            "_reconcile_storyboard_shot_subjects",
            "_create_shots_from_storyboard_data",
            "_sync_subjects_to_database",
            "_sync_storyboard_to_shots",
        }
        service_aliases = {
            "_normalize_subject_detail_entry": "normalize_subject_detail_entry",
            "_build_subject_detail_map": "build_subject_detail_map",
            "_normalize_storyboard_generation_subjects": "normalize_storyboard_generation_subjects",
            "_find_meaningful_common_fragment": "find_meaningful_common_fragment",
            "_infer_storyboard_role_name_from_shot": "infer_storyboard_role_name_from_shot",
            "_resolve_storyboard_subject_name": "resolve_storyboard_subject_name",
            "_reconcile_storyboard_shot_subjects": "reconcile_storyboard_shot_subjects",
            "_create_shots_from_storyboard_data": "create_shots_from_storyboard_data",
            "_sync_subjects_to_database": "sync_subjects_to_database",
            "_sync_storyboard_to_shots": "sync_storyboard_to_shots",
        }

        self.assertIn("from api.services import storyboard_sync", main_source)
        self.assertIn("from api.services import storyboard_sync", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        self.assertIn("_SUBJECT_MATCH_STOP_FRAGMENTS = storyboard_sync.SUBJECT_MATCH_STOP_FRAGMENTS", main_source)
        self.assertIn("_SUBJECT_MATCH_STOP_FRAGMENTS = storyboard_sync.SUBJECT_MATCH_STOP_FRAGMENTS", episodes_source)
        for name, service_name in service_aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard_sync.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard_sync.{service_name}", episodes_source)

    def test_storyboard_default_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        scripts_source = (BACKEND_DIR / "api" / "routers" / "scripts.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_get_pydantic_fields_set",
            "_normalize_detail_images_provider",
            "_resolve_episode_detail_images_provider",
            "_normalize_detail_images_model",
            "_normalize_storyboard2_video_duration",
            "_normalize_storyboard2_image_cw",
            "_get_first_episode_for_storyboard_defaults",
            "_build_episode_storyboard_sora_create_values",
        }

        self.assertIn("from api.services import storyboard_defaults", main_source)
        self.assertIn("from api.services import storyboard_defaults", episodes_source)
        self.assertIn("from api.services import storyboard_defaults", scripts_source)
        for source in (main_source, episodes_source, scripts_source):
            self.assertEqual(_top_level_definition_names(source) & delegated_names, set())
        self.assertIn("_get_pydantic_fields_set = storyboard_defaults.get_pydantic_fields_set", main_source)
        self.assertIn("_get_first_episode_for_storyboard_defaults = storyboard_defaults.get_first_episode_for_storyboard_defaults", main_source)
        self.assertIn("_build_episode_storyboard_sora_create_values = episodes._build_episode_storyboard_sora_create_values", main_source)
        self.assertIn("_get_pydantic_fields_set = storyboard_defaults.get_pydantic_fields_set", episodes_source)
        self.assertIn("_get_first_episode_for_storyboard_defaults = storyboard_defaults.get_first_episode_for_storyboard_defaults", episodes_source)
        self.assertIn("storyboard_defaults.build_episode_storyboard_sora_create_values", episodes_source)
        for name in {
            "_normalize_detail_images_provider",
            "_resolve_episode_detail_images_provider",
            "_normalize_detail_images_model",
            "_normalize_storyboard2_video_duration",
            "_normalize_storyboard2_image_cw",
        }:
            self.assertIn(f"{name} = storyboard_defaults.{name[1:]}", main_source)
            self.assertIn(f"{name} = storyboard_defaults.{name[1:]}", episodes_source)
            self.assertIn(f"{name} = storyboard_defaults.{name[1:]}", scripts_source)

    def test_storyboard_video_setting_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        scripts_source = (BACKEND_DIR / "api" / "routers" / "scripts.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_normalize_storyboard_video_appoint_account",
            "_normalize_storyboard_video_model",
            "_normalize_storyboard_video_aspect_ratio",
            "_normalize_storyboard_video_duration",
            "_normalize_storyboard_video_resolution_name",
            "_resolve_storyboard_video_provider",
            "_is_moti_storyboard_video_model",
            "_resolve_storyboard_video_model_by_provider",
            "_map_storyboard_prompt_template_duration",
            "_is_storyboard_shot_duration_override_enabled",
            "_is_storyboard_shot_model_override_enabled",
            "_get_episode_storyboard_video_settings",
            "_get_effective_storyboard_video_settings_for_shot",
        }

        self.assertIn("from api.services import storyboard_video_settings", main_source)
        self.assertIn("from api.services import storyboard_video_settings", episodes_source)
        self.assertIn("from api.services import storyboard_video_settings", scripts_source)
        self.assertNotIn("_STORYBOARD_VIDEO_MODEL_CONFIG = {", main_source)
        self.assertNotIn("_STORYBOARD_VIDEO_MODEL_CONFIG = {", episodes_source)
        for source in (main_source, episodes_source, scripts_source):
            self.assertEqual(_top_level_definition_names(source) & delegated_names, set())
        self.assertIn(
            "_STORYBOARD_VIDEO_MODEL_CONFIG = storyboard_video_settings.STORYBOARD_VIDEO_MODEL_CONFIG",
            main_source,
        )
        self.assertIn(
            "_STORYBOARD_VIDEO_MODEL_CONFIG = storyboard_video_settings.STORYBOARD_VIDEO_MODEL_CONFIG",
            episodes_source,
        )
        for name in delegated_names:
            service_name = name[1:]
            self.assertIn(f"{name} = storyboard_video_settings.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard_video_settings.{service_name}", episodes_source)
        for name in {
            "_normalize_storyboard_video_appoint_account",
            "_normalize_storyboard_video_model",
            "_normalize_storyboard_video_aspect_ratio",
            "_normalize_storyboard_video_duration",
            "_normalize_storyboard_video_resolution_name",
        }:
            self.assertIn(f"{name} = storyboard_video_settings.{name[1:]}", scripts_source)

    def test_storyboard_video_payload_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        main_delegated_names = {
            "_get_seedance_audio_validation_error",
            "_collect_moti_v2_reference_assets",
            "_build_moti_v2_content",
            "_build_storyboard_video_text_and_images_content",
            "_build_grok_video_content",
            "_build_unified_storyboard_video_task_payload",
        }
        main_aliases = {
            "_get_seedance_audio_validation_error": "get_seedance_audio_validation_error",
            "_collect_moti_v2_reference_assets": "_collect_moti_v2_reference_assets",
            "_build_moti_v2_content": "_build_moti_v2_content",
            "_build_storyboard_video_text_and_images_content": "build_storyboard_video_reference_content",
            "_build_grok_video_content": "_build_grok_video_content",
            "_build_unified_storyboard_video_task_payload": "_build_unified_storyboard_video_task_payload",
        }

        self.assertIn("from api.services import storyboard_video_payload", main_source)
        self.assertIn("from api.services import storyboard_video_payload", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & main_delegated_names, set())
        for name, service_name in main_aliases.items():
            self.assertIn(f"{name} = storyboard_video_payload.{service_name}", main_source)
        self.assertNotIn("def _build_unified_storyboard_video_task_payload", episodes_source)
        self.assertIn(
            "_build_unified_storyboard_video_task_payload = storyboard_video_payload._build_unified_storyboard_video_task_payload",
            episodes_source,
        )

    def test_storyboard_video_prompt_builder_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        settings_source = (BACKEND_DIR / "api" / "routers" / "settings.py").read_text(encoding="utf-8-sig")
        managed_source = (BACKEND_DIR / "managed_generation_service.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard_video_prompt_builder.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        service_exports = {
            "extract_scene_description",
            "default_storyboard_video_prompt_template",
            "build_sora_prompt",
        }
        main_aliases = {
            "extract_scene_description": "extract_scene_description",
            "_default_storyboard_video_prompt_template": "default_storyboard_video_prompt_template",
            "build_sora_prompt": "build_sora_prompt",
        }

        self.assertTrue(service_exports <= _top_level_definition_names(service_source))
        self.assertIn("from api.services import storyboard_video_prompt_builder", main_source)
        self.assertIn("from api.services import storyboard_video_prompt_builder", episodes_source)
        self.assertIn("from api.services import storyboard_video_prompt_builder", settings_source)
        self.assertEqual(_top_level_definition_names(main_source) & set(main_aliases), set())
        self.assertNotIn("def _default_storyboard_video_prompt_template", main_source)
        for name, service_name in main_aliases.items():
            self.assertIn(f"{name} = storyboard_video_prompt_builder.{service_name}", main_source)
        self.assertIn("build_sora_prompt = storyboard_video_prompt_builder.build_sora_prompt", episodes_source)
        self.assertNotIn("def _default_storyboard_video_prompt_template", settings_source)
        self.assertIn(
            "storyboard_video_prompt_builder.default_storyboard_video_prompt_template",
            settings_source,
        )
        self.assertIn(
            "build_sora_prompt",
            _imported_names_from_module(managed_source, "api.services.storyboard_video_prompt_builder"),
        )
        self.assertNotIn("build_sora_prompt", _imported_names_from_module(managed_source, "main"))

    def test_storyboard_sound_card_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        payload_source = (BACKEND_DIR / "api" / "services" / "storyboard_video_payload.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard_sound_cards.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        delegated_names = {
            "_parse_storyboard_sound_card_ids",
            "_get_episode_story_library",
            "_normalize_storyboard_selected_sound_card_ids",
            "_resolve_storyboard_selected_sound_cards",
        }
        service_aliases = {
            "_parse_storyboard_sound_card_ids": "parse_storyboard_sound_card_ids",
            "_get_episode_story_library": "get_episode_story_library",
            "_normalize_storyboard_selected_sound_card_ids": "normalize_storyboard_selected_sound_card_ids",
            "_resolve_storyboard_selected_sound_cards": "resolve_storyboard_selected_sound_cards",
        }

        self.assertIn("from api.services import storyboard_sound_cards", main_source)
        self.assertIn("from api.services import storyboard_sound_cards", payload_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(payload_source) & delegated_names, set())
        for name, service_name in service_aliases.items():
            self.assertIn(f"{name} = storyboard_sound_cards.{service_name}", main_source)
        for name, service_name in {
            "_parse_storyboard_sound_card_ids": "parse_storyboard_sound_card_ids",
            "_get_episode_story_library": "get_episode_story_library",
            "_resolve_storyboard_selected_sound_cards": "resolve_storyboard_selected_sound_cards",
        }.items():
            self.assertIn(f"{name} = storyboard_sound_cards.{service_name}", payload_source)
        for service_name in service_aliases.values():
            self.assertIn(f"def {service_name}", service_source)

    def test_storyboard_video_generation_limit_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard_video_generation_limits.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        delegated_names = {
            "_get_storyboard_shot_family_identity",
            "_get_storyboard_shot_family_filters",
            "_count_active_video_generations_for_shot_family",
            "_is_storyboard_shot_generation_active",
            "_build_active_video_generation_limit_message",
            "_ensure_storyboard_video_generation_slots_available",
        }
        service_aliases = {
            "_get_storyboard_shot_family_identity": "get_storyboard_shot_family_identity",
            "_get_storyboard_shot_family_filters": "get_storyboard_shot_family_filters",
            "_count_active_video_generations_for_shot_family": "count_active_video_generations_for_shot_family",
            "_is_storyboard_shot_generation_active": "is_storyboard_shot_generation_active",
            "_build_active_video_generation_limit_message": "build_active_video_generation_limit_message",
            "_ensure_storyboard_video_generation_slots_available": "ensure_storyboard_video_generation_slots_available",
        }

        self.assertIn("from api.services import storyboard_video_generation_limits", main_source)
        self.assertIn("from api.services import storyboard_video_generation_limits", episodes_source)
        self.assertIn(
            "ACTIVE_VIDEO_GENERATION_STATUSES = storyboard_video_generation_limits.ACTIVE_VIDEO_GENERATION_STATUSES",
            main_source,
        )
        self.assertIn(
            "ACTIVE_VIDEO_GENERATION_STATUSES = storyboard_video_generation_limits.ACTIVE_VIDEO_GENERATION_STATUSES",
            episodes_source,
        )
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        for name, service_name in service_aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            self.assertIn(f"{name} = storyboard_video_generation_limits.{service_name}", main_source)
            self.assertIn(f"{name} = storyboard_video_generation_limits.{service_name}", episodes_source)

    def test_storyboard_prompt_context_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        storyboard2_source = (BACKEND_DIR / "api" / "routers" / "storyboard2.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "storyboard_prompt_context.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        main_delegated_names = {
            "_debug_resolve_subject_names",
            "_build_subject_text_for_ai",
            "_build_storyboard2_subject_text",
            "_resolve_large_shot_template",
            "_append_sora_reference_prompt",
            "_resolve_sora_reference_prompt",
        }
        episodes_delegated_names = {
            "_build_subject_text_for_ai",
            "_resolve_large_shot_template",
            "_append_sora_reference_prompt",
            "_resolve_sora_reference_prompt",
        }
        storyboard2_delegated_names = {"_build_storyboard2_subject_text"}
        aliases = {
            "_debug_resolve_subject_names": "debug_resolve_subject_names",
            "_build_subject_text_for_ai": "build_subject_text_for_ai",
            "_build_storyboard2_subject_text": "build_storyboard2_subject_text",
            "_resolve_large_shot_template": "resolve_large_shot_template",
            "_append_sora_reference_prompt": "append_sora_reference_prompt",
            "_resolve_sora_reference_prompt": "resolve_sora_reference_prompt",
        }

        self.assertIn("from api.services import storyboard_prompt_context", main_source)
        self.assertIn("from api.services import storyboard_prompt_context", episodes_source)
        self.assertIn("storyboard_prompt_context", storyboard2_source)
        self.assertEqual(_top_level_definition_names(main_source) & main_delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & episodes_delegated_names, set())
        self.assertEqual(_top_level_definition_names(storyboard2_source) & storyboard2_delegated_names, set())
        self.assertIn(
            "SORA_REFERENCE_PROMPT_INSTRUCTION = storyboard_prompt_context.SORA_REFERENCE_PROMPT_INSTRUCTION",
            main_source,
        )
        self.assertIn(
            "SORA_REFERENCE_PROMPT_INSTRUCTION = storyboard_prompt_context.SORA_REFERENCE_PROMPT_INSTRUCTION",
            episodes_source,
        )
        for name, service_name in aliases.items():
            self.assertIn(f"def {service_name}", service_source)
            if name in main_delegated_names:
                self.assertIn(f"{name} = storyboard_prompt_context.{service_name}", main_source)
            if name in episodes_delegated_names:
                self.assertIn(f"{name} = storyboard_prompt_context.{service_name}", episodes_source)
            if name in storyboard2_delegated_names:
                self.assertIn(f"{name} = storyboard_prompt_context.{service_name}", storyboard2_source)

    def test_shot_image_generation_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        shots_source = (BACKEND_DIR / "api" / "routers" / "shots.py").read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "shot_image_generation.py"
        service_source = service_path.read_text(encoding="utf-8-sig")
        delegated_names = {
            "_DETAIL_IMAGES_MODEL_CONFIG",
            "_resolve_storyboard_sora_image_ratio",
            "_resolve_detail_images_actual_model",
            "_build_image_generation_debug_meta",
            "_build_image_generation_request_payload",
            "_submit_single_image_generation_task",
            "_save_detail_images_debug",
            "_process_detail_images_generation",
            "generate_detail_images",
            "get_shot_detail_images",
            "set_shot_detail_image_cover",
        }

        self.assertTrue(service_path.exists())
        self.assertIn("from api.services import shot_image_generation", main_source)
        self.assertIn("from api.services import shot_image_generation", shots_source)
        self.assertIn(
            "from api.schemas.shots import GenerateStoryboardImageRequest, GenerateDetailImagesRequest, SetDetailImageCoverRequest",
            main_source,
        )
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertIn("_DETAIL_IMAGES_MODEL_CONFIG = {", service_source)
        self.assertIn("def _resolve_storyboard_sora_image_ratio", service_source)
        self.assertIn("def _resolve_detail_images_actual_model", service_source)
        self.assertIn("def _build_image_generation_debug_meta", service_source)
        self.assertIn("def _build_image_generation_request_payload", service_source)
        self.assertIn("def _submit_single_image_generation_task", service_source)
        self.assertIn("def _save_detail_images_debug", service_source)
        self.assertIn("def _process_detail_images_generation", service_source)
        self.assertIn("def generate_detail_images", service_source)
        self.assertIn("def get_shot_detail_images", service_source)
        self.assertIn("def set_shot_detail_image_cover", service_source)
        self.assertIn("_DETAIL_IMAGES_MODEL_CONFIG = shot_image_generation._DETAIL_IMAGES_MODEL_CONFIG", main_source)
        self.assertIn(
            "_resolve_storyboard_sora_image_ratio = shot_image_generation._resolve_storyboard_sora_image_ratio",
            main_source,
        )
        self.assertIn(
            "_resolve_detail_images_actual_model = shot_image_generation._resolve_detail_images_actual_model",
            main_source,
        )
        self.assertIn(
            "_build_image_generation_debug_meta = shot_image_generation._build_image_generation_debug_meta",
            main_source,
        )
        self.assertIn(
            "_build_image_generation_request_payload = shot_image_generation._build_image_generation_request_payload",
            main_source,
        )

    def test_managed_generation_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        router_path = BACKEND_DIR / "api" / "routers" / "managed_generation.py"
        service_path = BACKEND_DIR / "api" / "services" / "managed_generation.py"
        router_source = router_path.read_text(encoding="utf-8-sig")
        service_source = service_path.read_text(encoding="utf-8-sig")
        delegated_names = {
            "_get_next_managed_reserved_variant_index",
            "_create_managed_reserved_shot",
            "_reserve_legacy_managed_session_slots",
            "stop_managed_generation",
            "get_managed_tasks",
            "get_managed_session_status",
        }

        self.assertTrue(router_path.exists())
        self.assertTrue(service_path.exists())
        self.assertIn("managed_generation,", main_source)
        self.assertIn("app.include_router(managed_generation.router)", main_source)
        self.assertIn("from api.services import managed_generation", episodes_source)
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        self.assertIn("router = APIRouter()", router_source)
        self.assertIn("def _get_next_managed_reserved_variant_index", service_source)
        self.assertIn("def _create_managed_reserved_shot", service_source)
        self.assertIn("def _reserve_legacy_managed_session_slots", service_source)
        self.assertIn("def stop_managed_generation", service_source)
        self.assertIn("def get_managed_tasks", service_source)
        self.assertIn("def get_managed_session_status", service_source)
        self.assertIn(
            "get_managed_tasks = managed_generation.get_managed_tasks",
            episodes_source,
        )
        self.assertIn(
            "get_managed_session_status = managed_generation.get_managed_session_status",
            episodes_source,
        )
        self.assertIn(
            "stop_managed_generation = managed_generation.stop_managed_generation",
            episodes_source,
        )

    def test_storyboard_reference_asset_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        payload_source = (BACKEND_DIR / "api" / "services" / "storyboard_video_payload.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "storyboard_reference_assets.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_debug_parse_card_ids",
            "_resolve_selected_cards",
            "_get_subject_card_reference_image_url",
            "_collect_storyboard_subject_reference_urls",
            "_get_selected_scene_card_image_url",
            "_resolve_selected_scene_reference_image_url",
        }

        self.assertIn("from api.services import storyboard_reference_assets", main_source)
        self.assertIn("from api.services import storyboard_reference_assets", episodes_source)
        self.assertIn("from api.services import storyboard_reference_assets", payload_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & {"_resolve_selected_cards"}, set())
        self.assertEqual(
            _top_level_definition_names(payload_source)
            & {
                "_resolve_selected_cards",
                "_debug_parse_card_ids",
                "_get_subject_card_reference_image_url",
                "_get_selected_scene_card_image_url",
                "_resolve_selected_scene_reference_image_url",
            },
            set(),
        )
        self.assertIn("def parse_card_ids", service_source)
        self.assertIn("def resolve_selected_cards", service_source)
        self.assertIn("def get_subject_card_reference_image_url", service_source)
        self.assertIn("def collect_storyboard_subject_reference_urls", service_source)
        self.assertIn("def get_selected_scene_card_image_url", service_source)
        self.assertIn("def resolve_selected_scene_reference_image_url", service_source)
        self.assertIn("_debug_parse_card_ids = storyboard_reference_assets.parse_card_ids", main_source)
        self.assertIn("_resolve_selected_cards = storyboard_reference_assets.resolve_selected_cards", main_source)
        self.assertIn(
            "_get_subject_card_reference_image_url = storyboard_reference_assets.get_subject_card_reference_image_url",
            main_source,
        )
        self.assertIn(
            "_collect_storyboard_subject_reference_urls = storyboard_reference_assets.collect_storyboard_subject_reference_urls",
            main_source,
        )
        self.assertIn(
            "_get_selected_scene_card_image_url = storyboard_reference_assets.get_selected_scene_card_image_url",
            main_source,
        )
        self.assertIn(
            "_resolve_selected_scene_reference_image_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url",
            main_source,
        )
        self.assertIn("_resolve_selected_cards = storyboard_reference_assets.resolve_selected_cards", episodes_source)
        self.assertIn("_resolve_selected_cards = storyboard_reference_assets.resolve_selected_cards", payload_source)
        self.assertIn("_debug_parse_card_ids = storyboard_reference_assets.parse_card_ids", payload_source)
        self.assertIn(
            "_resolve_selected_scene_reference_image_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url",
            payload_source,
        )

    def test_shot_reference_workflow_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        service_path = BACKEND_DIR / "api" / "services" / "shot_reference_workflow.py"

        self.assertTrue(service_path.exists())
        service_source = service_path.read_text(encoding="utf-8-sig")
        delegated_names = {
            "generate_storyboard_image",
            "set_shot_first_frame_reference",
            "upload_shot_first_frame_reference_image",
            "upload_shot_scene_image",
            "set_shot_scene_image_selection",
        }

        self.assertIn("from api.services import shot_reference_workflow", main_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        for name in delegated_names:
            self.assertIn(f"def {name}", service_source)
            self.assertIn(f"{name} = shot_reference_workflow.{name}", main_source)

    def test_shot_reference_request_schemas_live_in_api_schemas_shots(self):
        schema_path = BACKEND_DIR / "api" / "schemas" / "shots.py"
        schema_source = schema_path.read_text(encoding="utf-8-sig")
        schema_defs = _top_level_definition_names(schema_source)

        self.assertIn("SetFirstFrameReferenceRequest", schema_defs)
        self.assertIn("SetShotSceneImageSelectionRequest", schema_defs)

    def test_main_shot_and_video_workflow_schemas_are_schema_aliases(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        shot_schema_source = (BACKEND_DIR / "api" / "schemas" / "shots.py").read_text(encoding="utf-8-sig")
        episode_schema_source = (BACKEND_DIR / "api" / "schemas" / "episodes.py").read_text(encoding="utf-8-sig")
        main_defs = _top_level_definition_names(main_source)
        shot_schema_defs = _top_level_definition_names(shot_schema_source)
        episode_schema_defs = _top_level_definition_names(episode_schema_source)
        shot_schema_names = {
            "ShotCreate",
            "ShotUpdate",
            "ManualSoraPromptRequest",
            "ShotResponse",
            "ShotVideoResponse",
            "GenerateVideoRequest",
            "ThumbnailUpdate",
            "GenerateSoraPromptRequest",
            "GenerateLargeShotPromptRequest",
            "VideoStatusInfoResponse",
        }
        episode_schema_names = {
            "BatchGenerateSoraPromptsRequest",
            "BatchGenerateSoraPromptsResponse",
            "BatchGenerateSoraVideosRequest",
            "ManagedTaskResponse",
            "StartManagedGenerationRequest",
            "ManagedSessionStatusResponse",
        }

        self.assertTrue(shot_schema_names <= shot_schema_defs)
        self.assertTrue(episode_schema_names <= episode_schema_defs)
        self.assertEqual(main_defs & shot_schema_names, set())
        self.assertEqual(main_defs & episode_schema_names, set())
        self.assertIn("from api.schemas import shots as shot_schemas", main_source)
        self.assertIn("from api.schemas import episodes as episode_schemas", main_source)
        for name in shot_schema_names:
            self.assertIn(f"{name} = shot_schemas.{name}", main_source)
        for name in episode_schema_names:
            self.assertIn(f"{name} = episode_schemas.{name}", main_source)

    def test_voiceover_merge_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "voiceover_data.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_voiceover_shot_match_key",
            "_merge_voiceover_line_preserving_tts",
            "_merge_voiceover_dialogue_preserving_tts",
            "_merge_voiceover_shots_preserving_extensions",
        }
        service_aliases = {
            "_voiceover_shot_match_key": "voiceover_shot_match_key",
            "_merge_voiceover_line_preserving_tts": "merge_voiceover_line_preserving_tts",
            "_merge_voiceover_dialogue_preserving_tts": "merge_voiceover_dialogue_preserving_tts",
            "_merge_voiceover_shots_preserving_extensions": "merge_voiceover_shots_preserving_extensions",
        }

        self.assertIn("from api.services import voiceover_data", main_source)
        self.assertIn("from api.services import voiceover_data", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        self.assertIn("def voiceover_shot_match_key", service_source)
        self.assertIn("def merge_voiceover_line_preserving_tts", service_source)
        self.assertIn("def merge_voiceover_dialogue_preserving_tts", service_source)
        self.assertIn("def merge_voiceover_shots_preserving_extensions", service_source)
        for name, service_name in service_aliases.items():
            self.assertIn(f"{name} = voiceover_data.{service_name}", main_source)
            self.assertIn(f"{name} = voiceover_data.{service_name}", episodes_source)

    def test_voiceover_tts_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "voiceover_data.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_voiceover_default_vector_config",
            "_safe_float",
            "_normalize_voiceover_vector_config",
            "_normalize_voiceover_setting_template_payload",
            "_voiceover_default_line_tts",
            "_normalize_voiceover_line_tts",
            "_ensure_voiceover_shot_line_fields",
            "_normalize_voiceover_shots_for_tts",
            "_extract_voiceover_tts_line_states",
            "_find_voiceover_line_entry",
            "_parse_episode_voiceover_payload",
            "_voiceover_first_reference_id",
            "_iter_voiceover_lines",
        }
        service_aliases = {
            "_voiceover_default_vector_config": "voiceover_default_vector_config",
            "_safe_float": "safe_float",
            "_normalize_voiceover_vector_config": "normalize_voiceover_vector_config",
            "_normalize_voiceover_setting_template_payload": "normalize_voiceover_setting_template_payload",
            "_voiceover_default_line_tts": "voiceover_default_line_tts",
            "_normalize_voiceover_line_tts": "normalize_voiceover_line_tts",
            "_ensure_voiceover_shot_line_fields": "ensure_voiceover_shot_line_fields",
            "_normalize_voiceover_shots_for_tts": "normalize_voiceover_shots_for_tts",
            "_extract_voiceover_tts_line_states": "extract_voiceover_tts_line_states",
            "_find_voiceover_line_entry": "find_voiceover_line_entry",
            "_parse_episode_voiceover_payload": "parse_episode_voiceover_payload",
            "_voiceover_first_reference_id": "voiceover_first_reference_id",
            "_iter_voiceover_lines": "iter_voiceover_lines",
        }

        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        self.assertIn("VOICEOVER_TTS_VECTOR_KEYS = voiceover_data.VOICEOVER_TTS_VECTOR_KEYS", main_source)
        self.assertIn("VOICEOVER_TTS_VECTOR_KEYS = voiceover_data.VOICEOVER_TTS_VECTOR_KEYS", episodes_source)
        self.assertNotIn("VOICEOVER_TTS_VECTOR_KEYS = [", main_source)
        self.assertNotIn("VOICEOVER_TTS_VECTOR_KEYS = [", episodes_source)
        for service_name in service_aliases.values():
            self.assertIn(f"def {service_name}", service_source)
        for name, service_name in service_aliases.items():
            self.assertIn(f"{name} = voiceover_data.{service_name}", main_source)
            self.assertIn(f"{name} = voiceover_data.{service_name}", episodes_source)
        for constant_name in {
            "VOICEOVER_TTS_METHOD_SAME",
            "VOICEOVER_TTS_METHOD_VECTOR",
            "VOICEOVER_TTS_METHOD_EMO_TEXT",
            "VOICEOVER_TTS_METHOD_AUDIO",
            "VOICEOVER_TTS_ALLOWED_METHODS",
        }:
            self.assertIn(f"{constant_name} = voiceover_data.{constant_name}", main_source)
            self.assertIn(f"{constant_name} = voiceover_data.{constant_name}", episodes_source)

    def test_voiceover_shared_data_helpers_live_in_service_module(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        service_source = (BACKEND_DIR / "api" / "services" / "voiceover_data.py").read_text(encoding="utf-8-sig")
        delegated_names = {
            "_voiceover_default_test_mp3_path",
            "_voiceover_default_shared_data",
            "_voiceover_default_reference_item",
            "_normalize_voiceover_shared_data",
            "_load_script_voiceover_shared_data",
            "_save_script_voiceover_shared_data",
        }
        service_names = {
            "voiceover_default_test_mp3_path",
            "voiceover_default_shared_data",
            "voiceover_default_reference_item",
            "normalize_voiceover_shared_data",
            "load_script_voiceover_shared_data",
            "save_script_voiceover_shared_data",
        }

        self.assertIn("from api.services import voiceover_data", main_source)
        self.assertIn("from api.services import voiceover_data", episodes_source)
        self.assertIn("from functools import partial", main_source)
        self.assertIn("from functools import partial", episodes_source)
        self.assertEqual(_top_level_definition_names(main_source) & delegated_names, set())
        self.assertEqual(_top_level_definition_names(episodes_source) & delegated_names, set())
        for service_name in service_names:
            self.assertIn(f"def {service_name}", service_source)
        for source in (main_source, episodes_source):
            self.assertIn(
                "_voiceover_default_test_mp3_path = partial(voiceover_data.voiceover_default_test_mp3_path, __file__)",
                source,
            )
            self.assertIn("_voiceover_default_shared_data = voiceover_data.voiceover_default_shared_data", source)
            self.assertIn(
                "_voiceover_default_reference_item = partial(",
                source,
            )
            self.assertIn(
                "_normalize_voiceover_shared_data = partial(",
                source,
            )
            self.assertIn(
                "_load_script_voiceover_shared_data = partial(",
                source,
            )
            self.assertIn(
                "_save_script_voiceover_shared_data = partial(",
                source,
            )

    def test_voiceover_route_module_owns_voiceover_routes(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        voiceover_path = BACKEND_DIR / "api" / "routers" / "voiceover.py"
        voiceover_source = voiceover_path.read_text(encoding="utf-8-sig")

        self.assertTrue(voiceover_path.exists())
        self.assertIn("router = APIRouter()", voiceover_source)
        self.assertIn("voiceover,", main_source)
        self.assertIn("app.include_router(voiceover.router)", main_source)
        self.assertEqual(
            {path for path in _router_decorator_paths(episodes_source) if "/voiceover" in path},
            set(),
        )
        self.assertEqual(
            {path for path in _router_decorator_paths(voiceover_source) if "/voiceover" in path},
            {
                "/api/episodes/{episode_id}/voiceover",
                "/api/episodes/{episode_id}/voiceover/shared",
                "/api/episodes/{episode_id}/voiceover/shared/voice-references",
                "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}",
                "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview",
                "/api/episodes/{episode_id}/voiceover/shared/vector-presets",
                "/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}",
                "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets",
                "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}",
                "/api/episodes/{episode_id}/voiceover/shared/setting-templates",
                "/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}",
                "/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate",
                "/api/episodes/{episode_id}/voiceover/generate-all",
                "/api/episodes/{episode_id}/voiceover/tts-status",
            },
        )

    def test_simple_storyboard_route_module_owns_simple_storyboard_routes(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        simple_storyboard_path = BACKEND_DIR / "api" / "routers" / "simple_storyboard.py"
        simple_storyboard_source = simple_storyboard_path.read_text(encoding="utf-8-sig")

        self.assertTrue(simple_storyboard_path.exists())
        self.assertIn("router = APIRouter()", simple_storyboard_source)
        self.assertIn("simple_storyboard,", main_source)
        self.assertIn("app.include_router(simple_storyboard.router)", main_source)
        self.assertEqual(
            {
                path
                for path in _router_decorator_paths(episodes_source)
                if "simple-storyboard" in path or "generate-simple-storyboard" in path
            },
            set(),
        )
        self.assertEqual(
            {
                path
                for path in _router_decorator_paths(simple_storyboard_source)
                if "simple-storyboard" in path or "generate-simple-storyboard" in path
            },
            {
                "/api/episodes/{episode_id}/generate-simple-storyboard",
                "/api/episodes/{episode_id}/simple-storyboard",
                "/api/episodes/{episode_id}/simple-storyboard/status",
                "/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches",
            },
        )
        for name in {
            "generate_simple_storyboard_api",
            "get_simple_storyboard",
            "get_simple_storyboard_status",
            "retry_failed_simple_storyboard_batches_api",
            "update_simple_storyboard",
        }:
            self.assertIn(f"{name} = simple_storyboard.{name}", main_source)

    def test_storyboard2_route_module_owns_storyboard2_routes(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8-sig")
        episodes_source = (BACKEND_DIR / "api" / "routers" / "episodes.py").read_text(encoding="utf-8-sig")
        storyboard2_path = BACKEND_DIR / "api" / "routers" / "storyboard2.py"
        storyboard2_source = storyboard2_path.read_text(encoding="utf-8-sig")

        expected_paths = {
            "/api/episodes/{episode_id}/storyboard2",
            "/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts",
            "/api/storyboard2/shots/{storyboard2_shot_id}",
            "/api/storyboard2/subshots/{sub_shot_id}",
            "/api/storyboard2/subshots/{sub_shot_id}/generate-images",
            "/api/storyboard2/subshots/{sub_shot_id}/generate-video",
            "/api/storyboard2/subshots/{sub_shot_id}/current-image",
            "/api/storyboard2/images/{image_id}",
            "/api/storyboard2/videos/{video_id}",
        }

        self.assertTrue(storyboard2_path.exists())
        self.assertIn("router = APIRouter()", storyboard2_source)
        self.assertIn("storyboard2,", main_source)
        self.assertIn("app.include_router(storyboard2.router)", main_source)
        self.assertEqual(
            _router_decorator_paths(episodes_source) & expected_paths,
            set(),
        )
        self.assertEqual(
            {path for path in _router_decorator_paths(storyboard2_source) if "storyboard2" in path},
            expected_paths,
        )
        for name in {
            "get_storyboard2_data",
            "batch_generate_storyboard2_sora_prompts",
            "generate_storyboard2_sub_shot_images",
            "generate_storyboard2_sub_shot_video",
            "update_storyboard2_shot",
            "update_storyboard2_sub_shot",
            "delete_storyboard2_video",
            "set_storyboard2_current_image",
            "delete_storyboard2_image",
        }:
            self.assertIn(f"{name} = storyboard2.{name}", main_source)
            self.assertIn(f"{name} = storyboard2.{name}", episodes_source)

    def test_web_startup_event_excludes_schema_bootstrap_and_preflight_responsibilities(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        startup_node = _function_node(source, "startup_event")
        legacy_bootstrap_calls = _function_call_leaf_names(source, "run_startup_bootstrap")
        forbidden_calls = []
        forbidden_imports = []

        for node in ast.walk(startup_node):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "preflight":
                        forbidden_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "preflight":
                forbidden_imports.append(node.module)
            elif isinstance(node, ast.Call):
                call_name = _dotted_call_name(node)
                leaf_name = call_name.rsplit(".", 1)[-1]
                if (
                    call_name.startswith("preflight.")
                    or _is_metadata_create_all_call(node)
                    or leaf_name in {
                        "run_startup_preflight",
                        "run_startup_bootstrap",
                        "_ensure_runtime_directories",
                        "_ensure_function_model_configs",
                    }
                    or leaf_name in legacy_bootstrap_calls
                ):
                    forbidden_calls.append(call_name)

        self.assertEqual(forbidden_imports, [])
        self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
