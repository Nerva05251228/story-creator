import json
import os
import shutil
import traceback
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

import models
from api.services.episode_cleanup import delete_episode_storyboard_shots
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


def _safe_audio_duration_seconds(value: Any) -> float:
    try:
        duration_seconds = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return duration_seconds if duration_seconds > 0 else 0.0


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


def create_shots_from_storyboard_data(episode_id: int, db: Session):
    """
    从episode.storyboard_data JSON创建storyboard_shots表记录

    此函数被以下场景调用：
    1. AI生成分镜完成后自动调用
    2. 用户手动点击"创建镜头"按钮
    """
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode or not episode.storyboard_data:
        return

    # 解析JSON数据
    try:
        storyboard = json.loads(episode.storyboard_data)
        shots_data = storyboard.get("shots", [])
        subjects_data = storyboard.get("subjects", [])
    except Exception as e:
        print(f"解析storyboard_data失败: {e}")
        return

    if not shots_data:
        return

    canonical_subject_map = build_subject_detail_map(subjects_data)
    reconciled_shots_data = []
    combined_subject_map = dict(canonical_subject_map)
    for shot_data in shots_data:
        shot_copy = dict(shot_data)
        shot_copy["subjects"] = reconcile_storyboard_shot_subjects(
            shot_copy,
            canonical_subject_map,
        )
        for subject in shot_copy.get("subjects", []):
            subject_key = (subject["name"], subject["type"])
            if subject_key not in combined_subject_map:
                combined_subject_map[subject_key] = normalize_subject_detail_entry(subject)
        reconciled_shots_data.append(shot_copy)

    shots_data = reconciled_shots_data
    subjects_data = list(combined_subject_map.values())

    # 获取剧本和主体库
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script:
        return

    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()
    if not library:
        return

    # ========== 清理旧数据（避免孤儿记录） ==========
    # 获取当前主体库的所有旧主体
    old_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).all()

    # 先删除这些主体的所有图片（避免孤儿记录）
    for old_card in old_cards:
        # 删除手动上传的图片
        db.query(models.CardImage).filter(
            models.CardImage.card_id == old_card.id
        ).delete()
        # 删除AI生成的图片
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == old_card.id
        ).delete()
        # 删除声音素材
        db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == old_card.id
        ).delete()

    # 再删除主体卡片
    db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).delete()
    db.commit()
    print(f"[清理] 已清空当前主体库的所有旧主体、图片和声音素材")

    allowed_subject_types = set(ALLOWED_CARD_TYPES)
    existing_names_to_ids = {}

    # ========== 渐进式回退：从最新到最旧的剧集查找可复用主体 ==========
    # 获取当前剧集需要的所有主体
    needed_subjects = set()
    for subj in subjects_data:
        name = subj.get('name', '').strip()
        subject_type = (subj.get('type') or "角色").strip() or "角色"
        if name and subject_type in allowed_subject_types:
            needed_subjects.add((name, subject_type))

    # 获取同一剧本下其他剧集（按创建时间倒序：从新到旧）
    other_episodes = db.query(models.Episode).filter(
        models.Episode.script_id == script.id,
        models.Episode.id != episode.id
    ).order_by(models.Episode.created_at.desc()).all()

    # 已找到的主体字典：(name, card_type) -> SubjectCard
    found_subjects = {}

    # 遍历每个剧集（从新到旧）
    for ep in other_episodes:
        # 获取这个剧集的主体库
        ep_library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == ep.id
        ).first()

        if not ep_library:
            continue

        # 获取这个主体库的所有符合类型的主体
        ep_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == ep_library.id,
            models.SubjectCard.card_type.in_(allowed_subject_types)
        ).all()

        # 遍历这个剧集的主体
        for card in ep_cards:
            key = (card.name, card.card_type)
            # 如果需要这个主体 且 还没找到过，则记录
            if key in needed_subjects and key not in found_subjects:
                found_subjects[key] = card
                print(f"[素材查找] 从剧集 '{ep.name}' 找到可复用主体：{card.name}（{card.card_type}）")

        # 如果所有需要的主体都找到了，提前退出
        if len(found_subjects) >= len(needed_subjects):
            print(f"[素材查找] 所有需要的主体都已找到，停止查找")
            break

    print(f"[素材查找] 共找到 {len(found_subjects)}/{len(needed_subjects)} 个可复用主体")

    # 创建新主体卡片
    for subj in subjects_data:
        name = subj.get('name', '').strip()
        subject_type = (subj.get('type') or "角色").strip() or "角色"
        # 跳过空名字或已创建的名字（防止同批次重复）
        if not name or name in existing_names_to_ids:
            continue
        if subject_type not in allowed_subject_types:
            continue

        # ========== 检查是否有可复用的主体 ==========
        key = (name, subject_type)
        source_card = found_subjects.get(key)

        if source_card:
            # 找到可复用的主体，复制 SubjectCard
            new_card = models.SubjectCard(
                library_id=library.id,
                name=source_card.name,
                alias=source_card.alias,
                card_type=source_card.card_type,
                ai_prompt=source_card.ai_prompt,
                role_personality=(getattr(source_card, "role_personality", "") or ""),
                style_template_id=source_card.style_template_id
            )
            db.add(new_card)
            db.flush()

            # 复制所有图片记录
            source_images = db.query(models.CardImage).filter(
                models.CardImage.card_id == source_card.id
            ).order_by(models.CardImage.order).all()

            copied_count = 0
            for img in source_images:
                # 判断图片路径类型
                is_cdn_url = img.image_path.startswith(('http://', 'https://'))

                if is_cdn_url:
                    # CDN图片：直接复制记录，共享同一个URL
                    new_image = models.CardImage(
                        card_id=new_card.id,
                        image_path=img.image_path,  # 直接使用同一个CDN URL
                        order=img.order
                    )
                    db.add(new_image)
                    copied_count += 1
                else:
                    # 本地图片：检查文件是否存在，物理复制
                    if os.path.exists(img.image_path):
                        file_ext = os.path.splitext(img.image_path)[1]
                        new_filename = f"card_{new_card.id}_{uuid.uuid4().hex[:8]}{file_ext}"
                        new_path = os.path.join("uploads", new_filename)

                        try:
                            shutil.copy2(img.image_path, new_path)
                            new_image = models.CardImage(
                                card_id=new_card.id,
                                image_path=new_path,
                                order=img.order
                            )
                            db.add(new_image)
                            copied_count += 1
                        except Exception as e:
                            print(f"复制本地图片失败 {img.image_path}: {e}")

            # ========== 复制 GeneratedImage 记录 ==========
            source_generated_images = db.query(models.GeneratedImage).filter(
                models.GeneratedImage.card_id == source_card.id
            ).order_by(models.GeneratedImage.created_at).all()

            for gen_img in source_generated_images:
                new_generated_image = models.GeneratedImage(
                    card_id=new_card.id,
                    image_path=gen_img.image_path,  # CDN URL 直接复用
                    model_name=gen_img.model_name,
                    is_reference=gen_img.is_reference,
                    task_id=gen_img.task_id,
                    status=gen_img.status
                )
                db.add(new_generated_image)

            source_audios = db.query(models.SubjectCardAudio).filter(
                models.SubjectCardAudio.card_id == source_card.id
            ).order_by(models.SubjectCardAudio.created_at).all()
            for audio in source_audios:
                new_audio = models.SubjectCardAudio(
                    card_id=new_card.id,
                    audio_path=audio.audio_path,
                    file_name=audio.file_name,
                    duration_seconds=_safe_audio_duration_seconds(audio.duration_seconds),
                    is_reference=audio.is_reference
                )
                db.add(new_audio)

            existing_names_to_ids[name] = new_card.id
            print(f"[主体复用] 复用主体：{name}（{subject_type}），复制了 {copied_count} 张卡片图，{len(source_generated_images)} 张AI图，{len(source_audios)} 条声音素材")
        else:
            # 没有可复用的主体，创建空主体（原逻辑）
            new_card = models.SubjectCard(
                library_id=library.id,
                name=name,
                alias=subj.get('alias', '').strip(),
                card_type=subject_type,
                ai_prompt=subj.get('ai_prompt', '').strip(),
                role_personality=(subj.get('role_personality') or subj.get('role_personality_en') or subj.get('personality_en') or '').strip()
            )
            db.add(new_card)
            db.flush()
            existing_names_to_ids[name] = new_card.id

    db.commit()

    # 重新获取所有卡片
    all_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).all()
    all_cards = [card for card in all_cards if card.card_type in allowed_subject_types]
    card_name_to_id = {card.name: card.id for card in all_cards}

    # 删除旧镜头（替换模式）
    delete_episode_storyboard_shots(episode_id, db)
    db.commit()

    # 创建新镜头
    for shot_data in shots_data:
        shot_number = int(shot_data.get('shot_number', 0))
        if shot_number <= 0:
            continue

        # 解析主体ID
        selected_card_ids = []
        subjects = shot_data.get('subjects', [])
        if isinstance(subjects, list):
            for subj in subjects:
                if isinstance(subj, dict):
                    name = subj.get('name', '').strip()
                    if name and name in card_name_to_id:
                        selected_card_ids.append(card_name_to_id[name])

        # 处理新格式的 dialogue 和 narration - 格式化为可读文本
        def format_voice_content(shot_data: dict) -> str:
            """将narration或dialogue格式化为可读文本"""
            voice_type = shot_data.get('voice_type', 'none')

            if voice_type == 'narration':
                narration = shot_data.get('narration')
                if narration and isinstance(narration, dict):
                    speaker = narration.get('speaker', '')
                    gender = narration.get('gender', '')
                    emotion = narration.get('emotion', '')
                    text = narration.get('text', '')
                    return f"旁白（{speaker}/{gender}/{emotion}）：{text}"

            elif voice_type == 'dialogue':
                dialogue = shot_data.get('dialogue')
                if dialogue and isinstance(dialogue, list):
                    dialogue_lines = []
                    for d in dialogue:
                        speaker = d.get('speaker', '')
                        gender = d.get('gender', '')
                        target = d.get('target')
                        emotion = d.get('emotion', '')
                        text = d.get('text', '')

                        if target:
                            dialogue_lines.append(f"{speaker}（{gender}）对{target}说（{emotion}）：{text}")
                        else:
                            dialogue_lines.append(f"{speaker}（{gender}）说（{emotion}）：{text}")

                    return '\n'.join(dialogue_lines)

            return ""

        # 格式化语音内容
        formatted_voice = format_voice_content(shot_data)

        # 使用原剧本段落作为基础文本
        excerpt = shot_data.get('original_text', '')

        # 构建sora_prompt: 原剧本段落 + 旁白/对白
        if excerpt and formatted_voice:
            sora_prompt_value = f"{excerpt}\n{formatted_voice}"
        elif excerpt:
            sora_prompt_value = excerpt
        elif formatted_voice:
            sora_prompt_value = formatted_voice
        else:
            sora_prompt_value = ""

        # storyboard_dialogue保存格式化的语音内容
        storyboard_dialogue_value = formatted_voice

        for _ in [None]:
            new_shot = models.StoryboardShot(
                episode_id=episode_id,
                shot_number=shot_number,
                variant_index=0,
                prompt_template='',
                script_excerpt=shot_data.get('original_text', ''),
                storyboard_dialogue=storyboard_dialogue_value,  # ✅ 格式化的旁白/对白
                sora_prompt=sora_prompt_value,  # ✅ 原剧本段落 + 旁白/对白
                selected_card_ids=json.dumps(selected_card_ids),
                selected_sound_card_ids=None,
                aspect_ratio='16:9',
                duration=15,
                storyboard_video_model="",
                storyboard_video_model_override_enabled=False,
                duration_override_enabled=False
            )
            db.add(new_shot)

    db.commit()


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
