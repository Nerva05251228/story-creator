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
            "_sync_storyboard2_processing_videos",
        }

        self.assertEqual(top_level_defs & delegated_names, set())
        for name in delegated_names:
            self.assertIn(f"{name} = episodes.{name}", source)

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

    def test_storyboard_subject_helpers_are_not_redefined_in_main(self):
        source = MAIN_PATH.read_text(encoding="utf-8-sig")
        top_level_defs = _top_level_definition_names(source)
        delegated_names = {
            "_normalize_subject_detail_entry",
            "_build_subject_detail_map",
            "_normalize_storyboard_generation_subjects",
            "_find_meaningful_common_fragment",
            "_infer_storyboard_role_name_from_shot",
            "_resolve_storyboard_subject_name",
            "_reconcile_storyboard_shot_subjects",
        }

        self.assertEqual(top_level_defs & delegated_names, set())
        for name in delegated_names:
            self.assertIn(f"{name} = episodes.{name}", source)
        self.assertIn("_SUBJECT_MATCH_STOP_FRAGMENTS = episodes._SUBJECT_MATCH_STOP_FRAGMENTS", source)

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
