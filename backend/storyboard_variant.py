def build_base_variant_payload(source_shot, *, next_variant: int) -> dict:
    return {
        "episode_id": source_shot.episode_id,
        "shot_number": source_shot.shot_number,
        "stable_id": source_shot.stable_id,
        "variant_index": next_variant,
        "prompt_template": source_shot.prompt_template,
        "script_excerpt": source_shot.script_excerpt,
        "storyboard_video_prompt": source_shot.storyboard_video_prompt,
        "storyboard_audio_prompt": source_shot.storyboard_audio_prompt,
        "storyboard_dialogue": source_shot.storyboard_dialogue,
        "scene_override": getattr(source_shot, "scene_override", ""),
        "scene_override_locked": bool(getattr(source_shot, "scene_override_locked", False)),
        "sora_prompt": getattr(source_shot, "sora_prompt", ""),
        "sora_prompt_is_full": bool(getattr(source_shot, "sora_prompt_is_full", False)),
        "sora_prompt_status": getattr(source_shot, "sora_prompt_status", "idle"),
        "selected_card_ids": source_shot.selected_card_ids,
        "selected_sound_card_ids": getattr(source_shot, "selected_sound_card_ids", None),
        "first_frame_reference_image_url": getattr(source_shot, "first_frame_reference_image_url", ""),
        "uploaded_scene_image_url": getattr(source_shot, "uploaded_scene_image_url", ""),
        "use_uploaded_scene_image": bool(getattr(source_shot, "use_uploaded_scene_image", False)),
        "aspect_ratio": source_shot.aspect_ratio,
        "duration": source_shot.duration,
        "storyboard_video_model": getattr(source_shot, "storyboard_video_model", ""),
        "storyboard_video_appoint_account": getattr(source_shot, "storyboard_video_appoint_account", ""),
        "storyboard_video_model_override_enabled": bool(getattr(source_shot, "storyboard_video_model_override_enabled", False)),
        "duration_override_enabled": bool(getattr(source_shot, "duration_override_enabled", False)),
        "provider": getattr(source_shot, "provider", ""),
        "timeline_json": getattr(source_shot, "timeline_json", ""),
        "detail_image_prompt_overrides": getattr(source_shot, "detail_image_prompt_overrides", "{}"),
        "storyboard_image_path": getattr(source_shot, "storyboard_image_path", ""),
        "storyboard_image_status": getattr(source_shot, "storyboard_image_status", "idle"),
        "storyboard_image_task_id": "",
        "storyboard_image_model": getattr(source_shot, "storyboard_image_model", ""),
        "video_path": "",
        "thumbnail_video_path": "",
        "video_status": "idle",
        "task_id": "",
        "video_error_message": "",
        "video_submitted_at": None,
        "cdn_uploaded": False,
    }


def build_duplicate_shot_payload(source_shot, *, next_variant: int) -> dict:
    return build_base_variant_payload(source_shot, next_variant=next_variant)


def build_storyboard_sync_variant_payload(
    source_shot,
    *,
    next_variant: int,
    script_excerpt: str,
    storyboard_dialogue: str,
    selected_card_ids: str,
    sora_prompt: str,
) -> dict:
    payload = build_base_variant_payload(source_shot, next_variant=next_variant)
    payload.update(
        {
            "script_excerpt": script_excerpt,
            "storyboard_dialogue": storyboard_dialogue,
            "selected_card_ids": selected_card_ids,
            "sora_prompt": sora_prompt,
            "sora_prompt_status": "idle",
        }
    )
    return payload


def build_storyboard_image_variant_payload(source_shot, *, next_variant: int) -> dict:
    payload = build_base_variant_payload(source_shot, next_variant=next_variant)
    payload.update(
        {
            "storyboard_image_status": "processing",
        }
    )
    return payload


def choose_storyboard_reference_source(target_shot, family_shots):
    target_id = getattr(target_shot, "id", None)
    selected_first_frame = str(getattr(target_shot, "first_frame_reference_image_url", "") or "").strip()

    candidates = []
    for shot in family_shots or []:
        if getattr(shot, "id", None) == target_id:
            continue
        image_path = str(getattr(shot, "storyboard_image_path", "") or "").strip()
        if not image_path or image_path.startswith("error:"):
            continue
        variant_index = int(getattr(shot, "variant_index", 0) or 0)
        matches_first_frame = bool(selected_first_frame and image_path == selected_first_frame)
        candidates.append(
            (
                0 if matches_first_frame else 1,
                0 if variant_index == 0 else 1,
                variant_index,
                getattr(shot, "id", 0) or 0,
                shot,
            )
        )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[:4])
    return candidates[0][4]
