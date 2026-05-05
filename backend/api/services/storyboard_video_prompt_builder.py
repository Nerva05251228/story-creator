import json
import re

from sqlalchemy.orm import Session

import models


DEFAULT_SORA_RULE = "准则：不要出现字幕"


def extract_scene_description(shot: models.StoryboardShot, db: Session) -> str:
    scene_desc = ""
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")

        if selected_ids:
            scene_cards_dict = {}
            all_scene_cards = db.query(models.SubjectCard).filter(
                models.SubjectCard.id.in_(selected_ids),
                models.SubjectCard.card_type == "场景",
            ).all()

            for card in all_scene_cards:
                scene_cards_dict[card.id] = card

            scene_prompts = []
            for card_id in selected_ids:
                card = scene_cards_dict.get(card_id)
                if card and card.ai_prompt and card.ai_prompt.strip():
                    clean_prompt = card.ai_prompt
                    clean_prompt = re.sub(r'生成图片的风格是：[^\n]*\n?', '', clean_prompt)
                    clean_prompt = re.sub(r'生成图片中场景的是：', '', clean_prompt)
                    clean_prompt = clean_prompt.strip()
                    if clean_prompt:
                        scene_prompts.append(f"{card.name}{clean_prompt}")

            if scene_prompts:
                scene_desc = "；".join(scene_prompts)
    except Exception as e:
        print(f"提取场景描述失败: {str(e)}")

    return scene_desc


def default_storyboard_video_prompt_template() -> str:
    return (
        "视频风格:逐帧动画，2d手绘动漫风格，强调帧间的手绘/精细绘制属性，而非3D渲染/CG动画的光滑感。"
        "画面整体呈现传统2D动画的逐帧绘制特征，包括但不限于：帧间微妙的线条变化、色彩的手工涂抹感、阴影的平面化处理。"
        "角色动作流畅但保留手绘的自然波动，背景元素展现水彩或厚涂等传统绘画技法的质感。"
        "整体视觉效果追求温暖、有机的手工艺术感，避免数字化的过度精确与机械感。"
    )


def build_sora_prompt(shot: models.StoryboardShot, db: Session = None) -> str:
    print("\n" + "=" * 80)
    print(f"[构建Sora提示词] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
    print("=" * 80)

    if bool(getattr(shot, "sora_prompt_is_full", False)) and str(getattr(shot, "sora_prompt", "") or "").strip():
        direct_prompt = str(shot.sora_prompt or "").strip()
        print("[构建Sora提示词] 检测到一次性完整提示词，直接返回，不再二次拼接")
        print(f"[拼接结果] 最终 prompt 长度: {len(direct_prompt)}")
        print("=" * 80 + "\n")
        return direct_prompt

    parts = []

    video_style_template = None
    episode = None
    if db:
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
            if episode and episode.video_style_template_id:
                video_style_template = db.query(models.VideoStyleTemplate).filter(
                    models.VideoStyleTemplate.id == episode.video_style_template_id
                ).first()
                if video_style_template:
                    print(f"[视频风格模板] 使用模板: {video_style_template.name} (id={video_style_template.id})")

            if not video_style_template:
                video_style_template = db.query(models.VideoStyleTemplate).filter(
                    models.VideoStyleTemplate.is_default == True
                ).first()
                if video_style_template:
                    print(f"[视频风格模板] 使用默认模板: {video_style_template.name} (id={video_style_template.id})")
        except Exception as e:
            print(f"[视频风格模板] 查询失败: {e}")

    if video_style_template and video_style_template.sora_rule and video_style_template.sora_rule.strip():
        sora_rule = video_style_template.sora_rule.strip()
        parts.append(sora_rule)
        print(f"[第0部分] ✅ 使用模板准则: {sora_rule[:80]}...")
    elif db:
        try:
            setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()
            if setting and setting.value:
                sora_rule = setting.value.strip()
                if sora_rule:
                    parts.append(sora_rule)
                    print(f"[第0部分] ✅ 使用全局Sora准则: {sora_rule}")
            else:
                sora_rule = DEFAULT_SORA_RULE
                parts.append(sora_rule)
                print(f"[第0部分] ⚠ 使用默认Sora准则: {sora_rule}")
        except Exception as e:
            print(f"[第0部分] ❌ 获取全局Sora准则失败: {str(e)}")
            sora_rule = DEFAULT_SORA_RULE
            parts.append(sora_rule)

    template = ""
    if episode and (getattr(episode, "video_prompt_template", "") or "").strip():
        template = episode.video_prompt_template.strip()
        print(f"[第1部分] ✅ 使用剧集提示词模板（长度: {len(template)}）")
    elif video_style_template and video_style_template.style_prompt and video_style_template.style_prompt.strip():
        template = video_style_template.style_prompt.strip()
        print(f"[第1部分] ✅ 使用模板风格: {template[:80]}...")
    elif db:
        try:
            setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "prompt_template").first()
            if setting and setting.value.strip():
                template = setting.value.strip()
                print(f"[第1部分] ✅ 使用全局提示词模板（长度: {len(template)}）")
            else:
                template = default_storyboard_video_prompt_template()
                print(f"[第1部分] ⚠ 使用默认提示词模板")
        except Exception as e:
            print(f"[第1部分] ❌ 获取全局提示词模板失败: {str(e)}")
            template = default_storyboard_video_prompt_template()
            print(f"[第1部分] ⚠ 使用默认提示词模板")

    if template:
        parts.append(template)
        print(f"[第1部分] ✅ 已添加视频风格模板")
    else:
        print(f"[第1部分] ❌ 模板为空，跳过")

    scene_desc = (shot.scene_override or "").strip()

    if scene_desc:
        print(f"[第2部分] 使用 scene_override: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[第2部分] 使用 scene_override: {scene_desc}")
    elif db:
        scene_desc = extract_scene_description(shot, db)
        if scene_desc:
            print(f"[第2部分] 从主体卡片提取场景: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[第2部分] 从主体卡片提取场景: {scene_desc}")
        else:
            print(f"[第2部分] 未找到场景描述")
    else:
        print(f"[第2部分] ❌ db 为 None，跳过场景查询")

    if scene_desc:
        parts.append(f"场景：{scene_desc}")
        print(f"[第2部分] ✅ 已添加场景描述")
    else:
        print(f"[第2部分] ❌ 场景描述为空，跳过")

    table_content = (shot.sora_prompt or shot.storyboard_video_prompt or "").strip()
    print(f"[第3部分] 使用字段: {'sora_prompt' if shot.sora_prompt else 'storyboard_video_prompt'}")
    print(f"[第3部分] 内容长度: {len(table_content)}")
    if table_content:
        parts.append(table_content)
        print(f"[第3部分] ✅ 已添加分镜表格")
    else:
        print(f"[第3部分] ❌ 分镜表格为空，跳过")

    final_prompt = "\n".join(parts).strip()
    print("-" * 80)
    print(f"[拼接结果] parts 数组长度: {len(parts)}")
    print(f"[拼接结果] 最终 prompt 长度: {len(final_prompt)}")
    print(f"[拼接结果] 最终 prompt 预览（前200字符）:")
    print(final_prompt[:200] + "..." if len(final_prompt) > 200 else final_prompt)
    print("=" * 80 + "\n")

    return final_prompt
