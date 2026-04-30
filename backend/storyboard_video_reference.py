from typing import Iterable, List, Sequence, Tuple


def normalize_first_frame_candidate_url(value) -> str:
    return str(value or "").strip()


def collect_first_frame_candidate_urls(
    storyboard_image_url: str = "",
    detail_image_urls: Iterable[str] = (),
    uploaded_first_frame_image_url: str = "",
) -> List[str]:
    resolved = []
    seen = set()

    for raw_url in [storyboard_image_url, *(detail_image_urls or []), uploaded_first_frame_image_url]:
        image_url = normalize_first_frame_candidate_url(raw_url)
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        resolved.append(image_url)

    return resolved


def is_allowed_first_frame_candidate_url(
    target_url: str,
    storyboard_image_url: str = "",
    detail_image_urls: Iterable[str] = (),
    uploaded_first_frame_image_url: str = "",
) -> bool:
    normalized_target = normalize_first_frame_candidate_url(target_url)
    if not normalized_target:
        return False
    return normalized_target in collect_first_frame_candidate_urls(
        storyboard_image_url=storyboard_image_url,
        detail_image_urls=detail_image_urls,
        uploaded_first_frame_image_url=uploaded_first_frame_image_url,
    )


def build_seedance_prompt(prompt: str, first_frame_image_url: str = "") -> str:
    clean_prompt = str(prompt or "").strip()
    if not normalize_first_frame_candidate_url(first_frame_image_url):
        return clean_prompt
    return f"首帧[图片1]{clean_prompt}"


def _append_seedance_reference_items(
    *,
    image_prefix_parts: List[str],
    image_urls: List[str],
    image_index: int,
    label: str,
    image_url: str,
) -> int:
    normalized_url = normalize_first_frame_candidate_url(image_url)
    if not normalized_url:
        return image_index
    normalized_label = str(label or "").strip()
    if not normalized_label:
        return image_index
    image_prefix_parts.append(f"{normalized_label}[图片{image_index}]")
    image_urls.append(normalized_url)
    return image_index + 1


def build_seedance_reference_images(
    first_frame_image_url: str = "",
    scene_image_url: str = "",
    prop_reference_items: Sequence[Tuple[str, str]] = (),
    role_reference_items: Sequence[Tuple[str, str]] = (),
) -> dict:
    image_prefix_parts = []
    image_urls = []
    image_index = 1

    image_index = _append_seedance_reference_items(
        image_prefix_parts=image_prefix_parts,
        image_urls=image_urls,
        image_index=image_index,
        label="首帧",
        image_url=first_frame_image_url,
    )
    image_index = _append_seedance_reference_items(
        image_prefix_parts=image_prefix_parts,
        image_urls=image_urls,
        image_index=image_index,
        label="场景",
        image_url=scene_image_url,
    )

    for raw_name, raw_url in prop_reference_items or ():
        image_index = _append_seedance_reference_items(
            image_prefix_parts=image_prefix_parts,
            image_urls=image_urls,
            image_index=image_index,
            label=str(raw_name or "").strip() or "道具",
            image_url=raw_url,
        )

    for raw_name, raw_url in role_reference_items or ():
        image_index = _append_seedance_reference_items(
            image_prefix_parts=image_prefix_parts,
            image_urls=image_urls,
            image_index=image_index,
            label=str(raw_name or "").strip() or "参考图",
            image_url=raw_url,
        )

    return {
        "image_prefix_parts": image_prefix_parts,
        "image_urls": image_urls,
    }


def build_seedance_content_text(
    prompt: str,
    image_prefix_parts: Sequence[str] = (),
    audio_prefix_parts: Sequence[str] = (),
) -> str:
    clean_prompt = " ".join(str(prompt or "").replace("\r", " ").replace("\n", " ").split())
    prefix = "".join(str(part or "").strip() for part in (image_prefix_parts or ()))
    prefix += "".join(str(part or "").strip() for part in (audio_prefix_parts or ()))
    return f"{prefix}{clean_prompt}" if prefix else clean_prompt


def resolve_scene_reference_image_url(
    selected_scene_card_image_url: str = "",
    uploaded_scene_image_url: str = "",
    use_uploaded_scene_image: bool = False,
) -> str:
    normalized_uploaded_url = normalize_first_frame_candidate_url(uploaded_scene_image_url)
    if use_uploaded_scene_image and normalized_uploaded_url:
        return normalized_uploaded_url
    return normalize_first_frame_candidate_url(selected_scene_card_image_url)


def should_autofill_scene_override(
    current_scene_override: str = "",
    scene_override_locked: bool = False,
) -> bool:
    if scene_override_locked:
        return False
    return not str(current_scene_override or "").strip()
