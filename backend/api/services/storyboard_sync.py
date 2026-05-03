import json
import traceback
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

import models
from storyboard_variant import build_storyboard_sync_variant_payload


ALLOWED_CARD_TYPES = ("\u89d2\u8272", "\u573a\u666f", "\u9053\u5177")

SUBJECT_MATCH_STOP_FRAGMENTS = {
    "\u4faf\u5e9c",
    "\u738b\u5e9c",
    "\u5e9c\u4e2d",
    "\u5e9c\u5185",
    "\u5bab\u4e2d",
    "\u5bab\u5185",
    "\u53e4\u4ee3",
    "\u73b0\u4ee3",
    "\u5ba4\u5185",
    "\u5ba4\u5916",
}


def normalize_subject_detail_entry(subject: dict, fallback: Optional[dict] = None) -> Optional[dict]:
    if not isinstance(subject, dict):
        return None

    fallback = fallback or {}
    name = (subject.get("name") or fallback.get("name") or "").strip()
    subject_type = (subject.get("type") or fallback.get("type") or "\u89d2\u8272").strip() or "\u89d2\u8272"
    if not name or subject_type not in ALLOWED_CARD_TYPES:
        return None

    alias = subject.get("alias")
    if alias is None:
        alias = fallback.get("alias")

    ai_prompt = subject.get("ai_prompt")
    if ai_prompt is None:
        ai_prompt = fallback.get("ai_prompt")

    role_personality = subject.get("role_personality")
    if role_personality is None:
        role_personality = subject.get("role_personality_en")
    if role_personality is None:
        role_personality = subject.get("personality_en")
    if role_personality is None:
        role_personality = fallback.get("role_personality")
    if role_personality is None:
        role_personality = fallback.get("role_personality_en")
    if role_personality is None:
        role_personality = fallback.get("personality_en")

    return {
        "name": name,
        "type": subject_type,
        "alias": (alias or "").strip(),
        "ai_prompt": (ai_prompt or "").strip(),
        "role_personality": (role_personality or "").strip() if subject_type == "\u89d2\u8272" else "",
    }


def build_subject_detail_map(subjects: Optional[list]) -> dict:
    subject_map = {}
    if not isinstance(subjects, list):
        return subject_map

    for subject in subjects:
        normalized = normalize_subject_detail_entry(subject)
        if not normalized:
            continue
        subject_map[(normalized["name"], normalized["type"])] = normalized
    return subject_map


def normalize_storyboard_generation_subjects(subjects: Optional[list]) -> list:
    normalized_subjects = []
    if not isinstance(subjects, list):
        return normalized_subjects

    for subject in subjects:
        if not isinstance(subject, dict):
            continue

        name = (subject.get("name") or "").strip()
        if not name:
            continue

        subject_type = (subject.get("type") or "\u89d2\u8272").strip() or "\u89d2\u8272"
        if subject_type not in ALLOWED_CARD_TYPES:
            continue

        normalized_subjects.append({
            "name": name,
            "type": subject_type,
        })

    deduped_subjects = []
    seen_subjects = set()
    for subject in normalized_subjects:
        subject_key = (subject["name"], subject["type"])
        if subject_key in seen_subjects:
            continue
        seen_subjects.add(subject_key)
        deduped_subjects.append(subject)

    return deduped_subjects


def find_meaningful_common_fragment(
    left_text: str,
    right_text: str,
    stop_fragments: Optional[set] = None,
) -> str:
    left_value = (left_text or "").strip()
    right_value = (right_text or "").strip()
    if not left_value or not right_value:
        return ""

    ignored_fragments = stop_fragments or set()
    max_length = min(len(left_value), len(right_value))
    for fragment_length in range(max_length, 1, -1):
        seen_fragments = set()
        for start_index in range(len(left_value) - fragment_length + 1):
            fragment = left_value[start_index:start_index + fragment_length].strip()
            if not fragment or fragment in seen_fragments or fragment in ignored_fragments:
                continue
            seen_fragments.add(fragment)
            if fragment in right_value:
                return fragment
    return ""


def infer_storyboard_role_name_from_shot(
    subject_name: str,
    shot_data: dict,
    canonical_subject_map: dict,
) -> Optional[str]:
    normalized_name = (subject_name or "").strip()
    if normalized_name not in {"\u6211", "\u81ea\u5df1", "\u672c\u4eba", "\u6211\u81ea\u5df1"}:
        return None

    narration = shot_data.get("narration")
    if isinstance(narration, dict):
        speaker = (narration.get("speaker") or "").strip()
        if speaker and (speaker, "\u89d2\u8272") in canonical_subject_map:
            return speaker

    dialogue = shot_data.get("dialogue")
    if isinstance(dialogue, list):
        speakers = []
        for item in dialogue:
            if not isinstance(item, dict):
                continue
            speaker = (item.get("speaker") or "").strip()
            if speaker and speaker not in speakers:
                speakers.append(speaker)
        if len(speakers) == 1 and (speakers[0], "\u89d2\u8272") in canonical_subject_map:
            return speakers[0]

    return None


def resolve_storyboard_subject_name(
    subject: dict,
    shot_data: dict,
    canonical_subject_map: dict,
    name_mappings: Optional[dict] = None,
) -> str:
    normalized_subject = normalize_subject_detail_entry(subject)
    if not normalized_subject:
        return ""

    subject_name = normalized_subject["name"]
    subject_type = normalized_subject["type"]

    mapped_name = (name_mappings or {}).get(subject_name)
    if mapped_name and (mapped_name, subject_type) in canonical_subject_map:
        return mapped_name

    if (subject_name, subject_type) in canonical_subject_map:
        return subject_name

    if subject_type == "\u89d2\u8272":
        inferred_role_name = infer_storyboard_role_name_from_shot(
            subject_name,
            shot_data,
            canonical_subject_map,
        )
        if inferred_role_name:
            return inferred_role_name
        return subject_name

    if subject_type not in {"\u573a\u666f", "\u9053\u5177"}:
        return subject_name

    candidate_details = [
        detail
        for detail in canonical_subject_map.values()
        if detail.get("type") == subject_type
    ]
    if not candidate_details:
        return subject_name

    candidate_texts = [subject_name]
    original_text = (shot_data.get("original_text") or "").strip()
    if original_text:
        candidate_texts.append(original_text)

    best_match_name = subject_name
    best_match_score = 0
    second_best_score = 0

    for candidate in candidate_details:
        current_score = 0
        candidate_name = candidate.get("name", "")
        candidate_alias = candidate.get("alias", "")
        for source_text in candidate_texts:
            current_score = max(
                current_score,
                len(find_meaningful_common_fragment(source_text, candidate_name, SUBJECT_MATCH_STOP_FRAGMENTS)),
                len(find_meaningful_common_fragment(source_text, candidate_alias, SUBJECT_MATCH_STOP_FRAGMENTS)),
            )

        if current_score > best_match_score:
            second_best_score = best_match_score
            best_match_score = current_score
            best_match_name = candidate_name
        elif current_score > second_best_score:
            second_best_score = current_score

    if best_match_score >= 2 and best_match_score > second_best_score:
        return best_match_name

    return subject_name


def reconcile_storyboard_shot_subjects(
    shot_data: dict,
    canonical_subjects: Optional[Any],
    name_mappings: Optional[dict] = None,
) -> list:
    if isinstance(canonical_subjects, dict):
        canonical_subject_map = canonical_subjects
    else:
        canonical_subject_map = build_subject_detail_map(canonical_subjects)

    reconciled_subjects = []
    seen_subjects = set()
    for subject in normalize_storyboard_generation_subjects(shot_data.get("subjects", [])):
        resolved_name = resolve_storyboard_subject_name(
            subject,
            shot_data,
            canonical_subject_map,
            name_mappings=name_mappings,
        )
        if not resolved_name:
            continue
        subject_key = (resolved_name, subject["type"])
        if subject_key in seen_subjects:
            continue
        seen_subjects.add(subject_key)
        reconciled_subjects.append({
            "name": resolved_name,
            "type": subject["type"],
        })

    return reconciled_subjects


def sync_subjects_to_database(episode_id: int, storyboard_data: dict, db: Session):
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if not script:
            return

        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()
        if not library:
            print(f"[\u540c\u6b65\u4e3b\u4f53] \u8b66\u544a\uff1a\u627e\u4e0d\u5230\u5267\u96c6 {episode.id} \u7684\u4e3b\u4f53\u5e93")
            return

        all_subjects = build_subject_detail_map(storyboard_data.get("subjects", []))
        shots = storyboard_data.get("shots", [])
        reconciled_shots = []

        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_copy = dict(shot)
            shot_copy["subjects"] = reconcile_storyboard_shot_subjects(
                shot_copy,
                all_subjects,
            )
            reconciled_shots.append(shot_copy)

        shots = reconciled_shots

        for shot in shots:
            subjects = shot.get("subjects", [])
            if not isinstance(subjects, list):
                continue

            for subj in subjects:
                if not isinstance(subj, dict):
                    continue

                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "\u89d2\u8272").strip() or "\u89d2\u8272"

                if not name:
                    continue

                if subject_type not in ALLOWED_CARD_TYPES:
                    continue

                key = (name, subject_type)
                if key not in all_subjects:
                    all_subjects[key] = {
                        "name": name,
                        "type": subject_type,
                        "alias": "",
                        "ai_prompt": "",
                        "role_personality": "",
                    }

        if not all_subjects:
            print("[\u540c\u6b65\u4e3b\u4f53] \u6ca1\u6709\u53d1\u73b0\u65b0\u4e3b\u4f53")
            return

        print(f"[\u540c\u6b65\u4e3b\u4f53] \u4ece\u5206\u955c\u8868\u4e2d\u63d0\u53d6\u5230 {len(all_subjects)} \u4e2a\u552f\u4e00\u4e3b\u4f53")

        existing_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        existing_card_map = {(card.name, card.card_type): card for card in existing_cards}
        existing_names = {(card.name, card.card_type): card.id for card in existing_cards}

        updated_count = 0
        for key, subject_info in all_subjects.items():
            existing_card = existing_card_map.get(key)
            if not existing_card:
                continue

            changed = False
            alias = (subject_info.get("alias") or "").strip()
            ai_prompt = (subject_info.get("ai_prompt") or "").strip()
            role_personality = (subject_info.get("role_personality") or "").strip()

            if alias and alias != (existing_card.alias or ""):
                existing_card.alias = alias
                changed = True
            if ai_prompt and ai_prompt != (existing_card.ai_prompt or ""):
                existing_card.ai_prompt = ai_prompt
                changed = True
            if existing_card.card_type == "\u89d2\u8272" and role_personality and role_personality != (getattr(existing_card, "role_personality", "") or ""):
                existing_card.role_personality = role_personality
                changed = True

            if changed:
                updated_count += 1

        created_count = 0
        for key, subject_info in all_subjects.items():
            if key in existing_names:
                continue

            new_card = models.SubjectCard(
                library_id=library.id,
                name=subject_info["name"],
                card_type=subject_info["type"],
                alias=subject_info.get("alias", ""),
                ai_prompt=subject_info.get("ai_prompt", ""),
                role_personality=subject_info.get("role_personality", "") if subject_info["type"] == "\u89d2\u8272" else "",
            )
            db.add(new_card)
            db.flush()
            existing_names[key] = new_card.id
            existing_card_map[key] = new_card
            created_count += 1
            print(f"[\u540c\u6b65\u4e3b\u4f53] \u521b\u5efa\u65b0\u4e3b\u4f53: {subject_info['name']} ({subject_info['type']}) - ID: {new_card.id}")

        if created_count > 0 or updated_count > 0:
            db.commit()
            print(f"[\u540c\u6b65\u4e3b\u4f53] \u6210\u529f\u521b\u5efa {created_count} \u4e2a\u65b0\u4e3b\u4f53\u5361\u7247\uff0c\u66f4\u65b0 {updated_count} \u4e2a\u4e3b\u4f53\u5361\u7247")
        else:
            print("[\u540c\u6b65\u4e3b\u4f53] \u6240\u6709\u4e3b\u4f53\u5df2\u5b58\u5728\uff0c\u65e0\u9700\u521b\u5efa")

        updated_shots = 0
        for shot in shots:
            shot_number = shot.get("shot_number")
            if not shot_number:
                continue

            subjects = shot.get("subjects", [])
            if not isinstance(subjects, list):
                continue

            card_ids = []
            for subj in subjects:
                if not isinstance(subj, dict):
                    continue

                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "\u89d2\u8272").strip() or "\u89d2\u8272"

                if not name:
                    continue

                key = (name, subject_type)
                if key in existing_names:
                    card_ids.append(existing_names[key])

            shot_record = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == episode_id,
                models.StoryboardShot.shot_number == shot_number,
                models.StoryboardShot.variant_index == 0,
            ).first()

            if shot_record:
                shot_record.selected_card_ids = json.dumps(card_ids)
                updated_shots += 1

        if updated_shots > 0:
            db.commit()
            print(f"[\u540c\u6b65\u4e3b\u4f53] \u6210\u529f\u66f4\u65b0 {updated_shots} \u4e2a\u955c\u5934\u7684 selected_card_ids")

    except Exception as exc:
        print(f"[\u540c\u6b65\u4e3b\u4f53] \u9519\u8bef: {str(exc)}")
        traceback.print_exc()
        db.rollback()


def sync_storyboard_to_shots(episode_id: int, new_storyboard_data: dict, old_storyboard_data: dict, db: Session):
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if not script:
            return

        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()
        if not library:
            return

        old_shots_dict_by_id = {}
        if old_storyboard_data:
            old_shots = old_storyboard_data.get("shots", [])
            for old_shot in old_shots:
                shot_id = old_shot.get("id")
                if shot_id:
                    old_shots_dict_by_id[shot_id] = old_shot

        existing_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        card_name_to_id = {(card.name, card.card_type): card.id for card in existing_cards}

        existing_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        ).all()

        existing_shots_by_id = {shot.id: shot for shot in existing_shots}
        existing_shots_by_stable_id = {}
        for shot in existing_shots:
            if shot.stable_id:
                if shot.stable_id not in existing_shots_by_stable_id:
                    existing_shots_by_stable_id[shot.stable_id] = []
                existing_shots_by_stable_id[shot.stable_id].append(shot)

        new_shots = new_storyboard_data.get("shots", [])
        processed_ids = set()

        for new_shot in new_shots:
            shot_number_str = new_shot.get("shot_number", "")
            try:
                shot_number = int(shot_number_str)
            except Exception:
                continue

            shot_id = new_shot.get("id")
            stable_id = new_shot.get("stable_id")

            if shot_id:
                processed_ids.add(shot_id)

            new_subjects = new_shot.get("subjects", [])
            selected_card_ids = []
            for subj in new_subjects:
                if not isinstance(subj, dict):
                    continue
                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "\u89d2\u8272").strip() or "\u89d2\u8272"
                if name:
                    key = (name, subject_type)
                    if key in card_name_to_id:
                        selected_card_ids.append(card_name_to_id[key])

            new_script_excerpt = (new_shot.get("original_text") or "").strip()
            new_dialogue = (new_shot.get("dialogue_text") or "").strip()
            new_sora_prompt = new_script_excerpt

            if shot_id and shot_id in existing_shots_by_id:
                db_record = existing_shots_by_id[shot_id]

                old_shot = old_shots_dict_by_id.get(shot_id)

                is_modified = False
                if old_shot:
                    old_original_text = (old_shot.get("original_text") or "").strip()
                    old_dialogue = (old_shot.get("dialogue_text") or "").strip()

                    if new_script_excerpt != old_original_text or new_dialogue != old_dialogue:
                        is_modified = True

                has_video = db_record.video_status in ["processing", "completed"]

                if is_modified and has_video:
                    variants = existing_shots_by_stable_id.get(db_record.stable_id, [])

                    existing_variant_with_same_content = None
                    for variant in variants:
                        if variant.variant_index > 0:
                            variant_excerpt = (variant.script_excerpt or "").strip()
                            variant_dialogue = (variant.storyboard_dialogue or "").strip()
                            if variant_excerpt == new_script_excerpt and variant_dialogue == new_dialogue:
                                existing_variant_with_same_content = variant
                                break

                    if existing_variant_with_same_content:
                        print(f"[\u540c\u6b65\u955c\u5934] \u955c\u5934{shot_number}\u5df2\u6709\u76f8\u540c\u5185\u5bb9\u7684\u53d8\u4f53 (id={existing_variant_with_same_content.id})\uff0c\u4e0d\u91cd\u590d\u521b\u5efa")
                    else:
                        max_variant = max((variant.variant_index for variant in variants), default=0)

                        new_variant = models.StoryboardShot(
                            **build_storyboard_sync_variant_payload(
                                db_record,
                                next_variant=max_variant + 1,
                                script_excerpt=new_script_excerpt,
                                storyboard_dialogue=new_dialogue,
                                selected_card_ids=json.dumps(selected_card_ids),
                                sora_prompt=new_sora_prompt,
                            )
                        )
                        db.add(new_variant)
                        print(f"[\u540c\u6b65\u955c\u5934] \u955c\u5934{shot_number}\u5df2\u6709\u89c6\u9891\uff0c\u521b\u5efa\u65b0\u53d8\u4f53 (id={shot_id})")
                else:
                    db_record.shot_number = shot_number
                    db_record.script_excerpt = new_script_excerpt
                    db_record.storyboard_dialogue = new_dialogue
                    db_record.selected_card_ids = json.dumps(selected_card_ids)
                    if is_modified:
                        db_record.sora_prompt = new_sora_prompt
                        db_record.sora_prompt_status = "idle"
                    print(f"[\u540c\u6b65\u955c\u5934] \u66f4\u65b0\u955c\u5934{shot_number} (id={shot_id})")

                    if db_record.stable_id and db_record.stable_id in existing_shots_by_stable_id:
                        for variant in existing_shots_by_stable_id[db_record.stable_id]:
                            if variant.id != db_record.id:
                                variant.shot_number = shot_number
                                print(f"[\u540c\u6b65\u955c\u5934] \u66f4\u65b0\u53d8\u4f53\u955c\u5934{shot_number}_{variant.variant_index} (id={variant.id})")
            else:
                if not stable_id:
                    stable_id = str(uuid.uuid4())

                new_record = models.StoryboardShot(
                    episode_id=episode_id,
                    shot_number=shot_number,
                    stable_id=stable_id,
                    variant_index=0,
                    script_excerpt=new_script_excerpt,
                    storyboard_dialogue=new_dialogue,
                    selected_card_ids=json.dumps(selected_card_ids),
                    selected_sound_card_ids=None,
                    sora_prompt=new_sora_prompt,
                    aspect_ratio="16:9",
                    duration=15,
                    storyboard_video_model="",
                    storyboard_video_model_override_enabled=False,
                    duration_override_enabled=False,
                    prompt_template="",
                    video_status="idle",
                    sora_prompt_status="idle",
                )
                db.add(new_record)
                print(f"[\u540c\u6b65\u955c\u5934] \u521b\u5efa\u65b0\u955c\u5934{shot_number} (stable_id={stable_id})")

        for shot in existing_shots:
            should_delete = False

            if shot.variant_index == 0:
                if shot.id not in processed_ids:
                    should_delete = True

                    if should_delete:
                        has_video = shot.video_status in ["processing", "completed"]

                        if not has_video:
                            db.delete(shot)
                            print(f"[\u540c\u6b65\u955c\u5934] \u5220\u9664\u955c\u5934{shot.shot_number} (id={shot.id}\uff0c\u672a\u751f\u6210\u89c6\u9891)")

                            if shot.stable_id and shot.stable_id in existing_shots_by_stable_id:
                                for variant in existing_shots_by_stable_id[shot.stable_id]:
                                    if variant.id != shot.id:
                                        db.delete(variant)
                                        print(f"[\u540c\u6b65\u955c\u5934] \u5220\u9664\u53d8\u4f53\u955c\u5934{variant.shot_number}_{variant.variant_index} (id={variant.id})")
                        else:
                            print(f"[\u540c\u6b65\u955c\u5934] \u4fdd\u7559\u955c\u5934{shot.shot_number} (id={shot.id}\uff0c\u5df2\u751f\u6210\u89c6\u9891)")

        db.commit()
        print("[\u540c\u6b65\u955c\u5934] \u540c\u6b65\u5b8c\u6210")

    except Exception as exc:
        print(f"[\u540c\u6b65\u955c\u5934] \u9519\u8bef: {str(exc)}")
        traceback.print_exc()
        db.rollback()
