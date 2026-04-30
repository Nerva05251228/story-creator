import requests
import json
import time
import traceback
from typing import List, Any, Callable, Optional, Dict
from database import SessionLocal
import models
import billing_service
from ai_config import build_ai_debug_config, get_ai_config
from storyboard_prompt_templates import inject_large_shot_template_content
from text_llm_queue import run_text_llm_request


def get_prompt_by_key(key: str) -> str:
    """
    浠庢暟鎹簱璇诲彇鎻愮ず璇嶉厤缃?

    Args:
        key: 鎻愮ず璇嶇殑鍞竴鏍囪瘑绗?

    Returns:
        str: 鎻愮ず璇嶅唴瀹?

    Raises:
        Exception: 濡傛灉鎻愮ず璇嶄笉瀛樺湪鎴栨湭鍚敤
    """
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == key,
            models.PromptConfig.is_active == True
        ).first()

        if not config:
            raise Exception(f"鎻愮ず璇嶉厤缃笉瀛樺湪鎴栨湭鍚敤: {key}")

        return config.content
    finally:
        db.close()


def _extract_ai_response_content(result: Any) -> str:
    """
    Extract assistant text from different upstream response formats.
    Priority:
    1) OpenAI-compatible choices[0].message.content
    2) Gemini-like candidates[0].content.parts[*].text
    3) direct text fields: content/text/output_text/response
    """
    if not isinstance(result, dict):
        raise ValueError(f"AI response is not a JSON object: {type(result).__name__}")

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        txt = item.get("text")
                        if isinstance(txt, str) and txt.strip():
                            text_parts.append(txt)
                if text_parts:
                    return "\n".join(text_parts)
        for key in ("content", "text"):
            value = first.get(key)
            if isinstance(value, str) and value.strip():
                return value

    candidates = result.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content_obj = first.get("content")
        if isinstance(content_obj, dict):
            parts = content_obj.get("parts")
            if isinstance(parts, list):
                text_parts = []
                for part in parts:
                    if isinstance(part, dict):
                        txt = part.get("text")
                        if isinstance(txt, str) and txt.strip():
                            text_parts.append(txt)
                if text_parts:
                    return "\n".join(text_parts)
        for key in ("content", "text", "output_text"):
            value = first.get(key)
            if isinstance(value, str) and value.strip():
                return value

    for key in ("content", "text", "output_text", "response"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value

    if "error" in result:
        raise ValueError(f"Upstream returned error: {result.get('error')}")

    available_keys = sorted([str(k) for k in result.keys()])
    raise ValueError(f"No text content found in AI response. keys={available_keys}")


def _build_ai_raw_debug_payload(
    response: Any = None,
    response_json: Any = None,
    extracted_content: Any = None,
    parsed_json: Any = None,
    error: Exception = None,
) -> dict:
    """Build a complete debug payload for upstream AI responses."""
    payload = {}

    if response is not None:
        payload["status_code"] = getattr(response, "status_code", None)
        try:
            payload["response_headers"] = dict(response.headers or {})
        except Exception:
            payload["response_headers"] = {}
        try:
            payload["raw_response_text"] = response.text
        except Exception:
            payload["raw_response_text"] = None

    if response_json is not None:
        payload["response_json"] = response_json
    if extracted_content is not None:
        payload["extracted_content"] = extracted_content
    if parsed_json is not None:
        payload["parsed_json"] = parsed_json
    if error is not None:
        payload["error"] = str(error)
        payload["error_type"] = type(error).__name__
        payload["traceback"] = traceback.format_exc()

    return payload


def generate_storyboard_prompts(
    script_excerpt: str,
    subject_names: List[str],
    duration: int,
    prompt_style: str = None,
    shot_id: int = None,
    prompt_key: str = "generate_video_prompts",
    subject_text_override: str = None,
    scene_description: str = "",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: int = None,
    large_shot_template_content: str = "",
    large_shot_template_name: str = "",
) -> dict:
    """Generate Sora timeline prompts and return parsed JSON object."""
    from main import save_ai_debug
    from datetime import datetime

    storyboard2_prompt_key = "generate_storyboard2_video_prompts"
    override_text = (subject_text_override or "").strip()
    subject_text = override_text if override_text else ("、".join(subject_names) if subject_names else "无")
    print(
        f"[SoraSubjectDebug][ai_service_input] shot_id={shot_id} "
        f"prompt_key={prompt_key} subject_names={subject_names} "
        f"subject_text_override={override_text if override_text else None} "
        f"subject_text={subject_text}"
    )

    def resolve_template_duration(value: int) -> int:
        if value <= 15:
            return 15
        return 25

    try:
        safe_duration = max(1, int(duration or 15))
    except Exception:
        safe_duration = 15
    template_duration = resolve_template_duration(safe_duration)
    custom_style = (prompt_style or "").strip()
    scene_text = (scene_description or "").strip()
    template_field = (duration_template_field or "video_prompt_rule").strip() or "video_prompt_rule"
    template_source = "unknown"
    large_shot_content = (large_shot_template_content or "").strip()
    large_shot_name = (large_shot_template_name or "").strip()

    if custom_style:
        template_for_format = custom_style
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(template_for_format, large_shot_content)
        try:
            prompt = template_for_format.format(
                script_excerpt=script_excerpt,
                scene_description=scene_text,
                subject_text=subject_text,
                safe_duration=safe_duration,
                extra_style="",
                large_shot_template_content=large_shot_content
            )
        except KeyError:
            prompt = template_for_format
        template_source = "custom_style"
        print("[Sora提示词生成] 使用自定义模板")
    else:
        db = SessionLocal()
        try:
            use_duration_template = prompt_key != storyboard2_prompt_key
            if use_duration_template:
                template = db.query(models.ShotDurationTemplate).filter(
                    models.ShotDurationTemplate.duration == template_duration
                ).first()
                template_rule = ""
                if template:
                    template_rule = str(getattr(template, template_field, "") or "").strip()
                if template_rule:
                    prompt_template = template_rule
                    template_source = f"duration_template_{template_duration}s:{template_field}"
                    print(f"[Sora提示词生成] 使用时长 {template_duration} 秒模板 ({template_field})")
                else:
                    prompt_template = get_prompt_by_key(prompt_key)
                    template_source = f"prompt_config:{prompt_key}"
                    print(f"[Sora提示词生成] 未找到时长模板({template_duration}s/{template_field})，使用默认 key={prompt_key}")
            else:
                prompt_template = get_prompt_by_key(prompt_key)
                template_source = f"prompt_config:{prompt_key}"
                print(f"[Sora提示词生成] 使用独立模板 key={prompt_key}")
        finally:
            db.close()

        template_for_format = prompt_template
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(template_for_format, large_shot_content)
        prompt = template_for_format.format(
            script_excerpt=script_excerpt,
            scene_description=scene_text,
            subject_text=subject_text,
            safe_duration=safe_duration,
            extra_style="",
            large_shot_template_content=large_shot_content
        )

    config = get_ai_config("video_prompt")
    input_debug_data = {
        "script_excerpt": script_excerpt,
        "subject_names": subject_names,
        "subject_text_override": subject_text_override,
        "subject_text": subject_text,
        "scene_description": scene_text,
        "duration": duration,
        "template_duration": template_duration,
        "prompt_style": prompt_style,
        "prompt_key": prompt_key,
        "duration_template_field": template_field,
        "template_source": template_source,
        "large_shot_template_id": large_shot_template_id,
        "large_shot_template_name": large_shot_name,
        "large_shot_template_content": large_shot_content,
        "prompt": prompt,
        "config": build_ai_debug_config(config)
    }

    task_folder = None
    if shot_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_folder = f"sora_prompt_shot_{shot_id}_{timestamp}"

    response = None
    result = None
    content = None
    parsed = None

    try:
        if shot_id and task_folder:
            save_ai_debug(
                "sora_prompt",
                input_debug_data,
                shot_id=shot_id,
                task_folder=task_folder
            )
            print("  ✓ 已保存Sora提示词输入")

        request_payload = {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "response_format": {"type": "json_object"},
            "stream": False
        }

        response = run_text_llm_request(
            stage=str(prompt_key or "video_prompt"),
            url=config["api_url"],
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=config["timeout"],
            provider_key=str(config.get("provider_key") or ""),
            model=str(config.get("model_id") or config.get("model") or ""),
            request_tag=f"shot={shot_id or ''}|prompt_key={prompt_key or 'video_prompt'}",
            proxies={"http": None, "https": None}
        )

        if response.status_code == 200 and shot_id and task_folder:
            billing_service.record_text_request_success_for_shot(
                shot_id=shot_id,
                stage=str(prompt_key or "video_prompt"),
                provider=str(config.get("provider_key") or ""),
                model_name=str(config.get("model_id") or config.get("model") or ""),
                billing_key=f"text:video_prompt:{task_folder}",
                operation_key=f"text:video_prompt:{shot_id}:{prompt_key or 'video_prompt'}",
                attempt_index=1,
                detail_json=json.dumps({
                    "prompt_key": prompt_key,
                    "duration": safe_duration,
                    "task_folder": task_folder,
                }, ensure_ascii=False),
            )

        if response.status_code != 200:
            response_json = None
            try:
                response_json = response.json()
            except Exception:
                response_json = None

            if shot_id and task_folder:
                output_debug_data = {
                    "status_code": response.status_code,
                    "error": f"HTTP {response.status_code}",
                    "success": False
                }
                save_ai_debug(
                    "sora_prompt",
                    input_debug_data,
                    output_debug_data,
                    raw_response=_build_ai_raw_debug_payload(
                        response=response,
                        response_json=response_json
                    ),
                    shot_id=shot_id,
                    task_folder=task_folder
                )
                print(f"  ✓ 已保存失败输出 (HTTP {response.status_code})")

            raise Exception(f"AI请求失败: {response.status_code} - {response.text}")

        try:
            result = response.json()
        except json.JSONDecodeError as e:
            if shot_id and task_folder:
                output_debug_data = {
                    "error": f"response json decode error: {str(e)}",
                    "status_code": response.status_code,
                    "success": False
                }
                save_ai_debug(
                    "sora_prompt",
                    input_debug_data,
                    output_debug_data,
                    raw_response=_build_ai_raw_debug_payload(
                        response=response,
                        error=e
                    ),
                    shot_id=shot_id,
                    task_folder=task_folder
                )
                print("  ✓ 已保存失败输出 (响应JSON解析失败)")
            raise Exception(f"AI响应JSON解析失败: {str(e)}")

        content = _extract_ai_response_content(result)
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()

        parsed = json.loads(content)

        if "timeline" not in parsed:
            if shot_id and task_folder:
                output_debug_data = {
                    "raw_content": content,
                    "parsed": parsed,
                    "error": "missing timeline field",
                    "success": False
                }
                save_ai_debug(
                    "sora_prompt",
                    input_debug_data,
                    output_debug_data,
                    raw_response=_build_ai_raw_debug_payload(
                        response=response,
                        response_json=result,
                        extracted_content=content,
                        parsed_json=parsed
                    ),
                    shot_id=shot_id,
                    task_folder=task_folder
                )
                print("  ✓ 已保存失败输出 (缺少 timeline)")
            raise ValueError("AI返回格式不正确：缺少timeline字段")

        if not isinstance(parsed.get("timeline"), list) or len(parsed["timeline"]) == 0:
            if shot_id and task_folder:
                output_debug_data = {
                    "raw_content": content,
                    "parsed": parsed,
                    "error": "timeline must be a non-empty list",
                    "success": False
                }
                save_ai_debug(
                    "sora_prompt",
                    input_debug_data,
                    output_debug_data,
                    raw_response=_build_ai_raw_debug_payload(
                        response=response,
                        response_json=result,
                        extracted_content=content,
                        parsed_json=parsed
                    ),
                    shot_id=shot_id,
                    task_folder=task_folder
                )
                print("  ✓ 已保存失败输出 (timeline格式错误)")
            raise ValueError("AI返回格式不正确：timeline必须是非空数组")

        if shot_id and task_folder:
            output_debug_data = {
                "raw_content": content,
                "parsed": parsed,
                "success": True
            }
            save_ai_debug(
                "sora_prompt",
                input_debug_data,
                output_debug_data,
                shot_id=shot_id,
                task_folder=task_folder
            )
            print("  ✓ 已保存成功输出")

        return parsed

    except json.JSONDecodeError as e:
        if shot_id and task_folder:
            output_debug_data = {
                "raw_content": content,
                "error": f"content json decode error: {str(e)}",
                "success": False
            }
            save_ai_debug(
                "sora_prompt",
                input_debug_data,
                output_debug_data,
                raw_response=_build_ai_raw_debug_payload(
                    response=response,
                    response_json=result,
                    extracted_content=content,
                    error=e
                ),
                shot_id=shot_id,
                task_folder=task_folder
            )
            print("  ✓ 已保存失败输出 (内容JSON解析失败)")
        raise Exception(f"AI返回内容JSON格式错误: {str(e)}")

    except Exception as e:
        if shot_id and task_folder:
            output_debug_data = {
                "error": str(e),
                "error_type": type(e).__name__,
                "success": False
            }
            save_ai_debug(
                "sora_prompt",
                input_debug_data,
                output_debug_data,
                raw_response=_build_ai_raw_debug_payload(
                    response=response,
                    response_json=result,
                    extracted_content=content,
                    parsed_json=parsed,
                    error=e
                ),
                shot_id=shot_id,
                task_folder=task_folder
            )
            print("  ✓ 已保存失败输出 (异常)")
        raise Exception(f"AI生成失败: {str(e)}")


# ==================== 鏂颁袱闃舵鍒嗛暅鐢熸垚 ====================

def stage1_generate_initial_storyboard(content: str, episode_id: int = None, task_folder: str = None, batch_id: str = None) -> tuple:
    """
    闃舵1锛氬垵姝ュ垎闀滅敓鎴愶紙甯﹂噸璇曟満鍒讹級

    Args:
        content: 涓€鎵瑰墽鏈唴瀹癸紙绾?00瀛楋級
        episode_id: 鐗囨ID锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級
        task_folder: 浠诲姟鏂囦欢澶瑰悕锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級

    Returns:
        tuple: (parsed_result, debug_info)
        - parsed_result: dict with "shots" array
        - debug_info: dict with "input" and "output" for debugging
    """
    # 鏈湴瀵煎叆閬垮厤寰幆渚濊禆
    from main import save_ai_debug

    max_retries = 10
    last_error = None

    for attempt in range(max_retries):
        attempt_num = attempt + 1  # 1-indexed for display
        print(f"[stage1] attempt {attempt_num}/{max_retries}")

        # 鉁?鍦╰ry鍧椾箣鍓嶅垵濮嬪寲鎵€鏈夊彲鑳藉湪exception handler涓娇鐢ㄧ殑鍙橀噺
        input_debug_data = {
            "content": content,
            "attempt": attempt_num,
            "max_retries": max_retries
        }
        raw_response_text = None
        status_code = None
        result = None
        raw_content = None

        try:
            # 浠庢暟鎹簱璇诲彇prompt妯℃澘
            prompt_template = get_prompt_by_key("stage1_initial_storyboard")
            prompt = prompt_template.format(content=content)

            # 鑾峰彇AI閰嶇疆
            config = get_ai_config("detailed_storyboard_s1")

            # 鍑嗗璇锋眰鏁版嵁
            request_data = {
                "model": config['model'],
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "response_format": {"type": "json_object"},  # 鉁?寮哄埗杩斿洖 JSON 鏍煎紡
                "stream": False  # 鉁?绂佺敤娴佸紡鍝嶅簲锛岀洿鎺ヨ繑鍥炲畬鏁碕SON
            }

            # 鉁?鏇存柊input_debug_data锛屾坊鍔犲畬鏁寸殑璋冭瘯淇℃伅
            input_debug_data.update({
                "prompt": prompt,
                "request_data": request_data,
                "config": build_ai_debug_config(config)
            })

            # 鉁?淇濆瓨杈撳叆锛堟瘡娆″皾璇曢兘淇濆瓨锛?
            if episode_id and task_folder:
                save_ai_debug(
                    'stage1',
                    input_debug_data,
                    episode_id=episode_id,
                    batch_id=batch_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [stage1] saved input for attempt {attempt_num}")

            # 鍙戦€丄PI璇锋眰
            response = run_text_llm_request(
                stage="stage1",
                url=config['api_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=request_data,
                timeout=config['timeout'],
                provider_key=str(config.get("provider_key") or ""),
                model=str(config.get("model_id") or config.get("model") or ""),
                request_tag=f"episode={episode_id or ''}|task={task_folder or ''}|batch={batch_id or ''}|attempt={attempt_num}"
            )

            # 鉁?鍏堜繚瀛樺師濮嬪搷搴旀枃鏈?
            raw_response_text = response.text
            status_code = response.status_code

            if status_code == 200 and episode_id:
                billing_service.record_text_request_success(
                    episode_id=episode_id,
                    stage="detailed_storyboard_stage1",
                    provider=str(config.get("provider_key") or ""),
                    model_name=str(config.get("model_id") or config.get("model") or ""),
                    billing_key=f"text:stage1:{task_folder or episode_id}:{batch_id or ''}:attempt{attempt_num}",
                    operation_key=f"text:stage1:{task_folder or episode_id}:{batch_id or ''}",
                    attempt_index=attempt_num,
                    detail_json=json.dumps(
                        {
                            "task_folder": task_folder,
                            "batch_id": batch_id,
                            "attempt": attempt_num,
                        },
                        ensure_ascii=False,
                    ),
                )

            # 鉁?HTTP 閿欒澶勭悊
            if status_code != 200:
                try:
                    error_response = response.json()
                except:
                    error_response = {"raw_text": raw_response_text}

                output_debug_data = {
                    "error": f"HTTP {status_code}",
                    "raw_response": error_response,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage1',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        batch_id=batch_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage1] saved failed output for attempt {attempt_num} (HTTP {status_code})")

                last_error = f"HTTP {status_code}"
                raise Exception(f"AI request failed: {status_code} - {raw_response_text}")

            # 鉁?瑙ｆ瀽JSON鍝嶅簲
            try:
                result = response.json()
            except json.JSONDecodeError as json_err:
                output_debug_data = {
                    "error": f"failed to parse response JSON: {str(json_err)}",
                    "raw_response_text": raw_response_text,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨JSON瑙ｆ瀽澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage1',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        batch_id=batch_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage1] saved failed output for attempt {attempt_num} (response JSON parse error)")

                last_error = f"response JSON parse error: {str(json_err)}"
                raise json_err

            content_result = result['choices'][0]['message']['content']

            # 鎻愬彇JSON
            raw_content = content_result
            if '```json' in content_result:
                content_result = content_result.split('```json')[1].split('```')[0].strip()
            elif '```' in content_result:
                content_result = content_result.split('```')[1].split('```')[0].strip()

            # 鉁?灏濊瘯瑙ｆ瀽content涓殑JSON
            try:
                parsed = json.loads(content_result)
            except json.JSONDecodeError as json_err:
                output_debug_data = {
                    "error": f"failed to parse AI JSON content: {str(json_err)}",
                    "raw_content": raw_content,
                    "extracted_content": content_result,
                    "full_response": result,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨鍐呭JSON瑙ｆ瀽澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage1',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        batch_id=batch_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage1] saved failed output for attempt {attempt_num} (content JSON parse error)")

                last_error = f"content JSON parse error: {str(json_err)}"
                raise json_err

            # 楠岃瘉鏍煎紡
            if 'shots' not in parsed:
                output_debug_data = {
                    "error": "invalid AI response format: missing 'shots' field",
                    "parsed_json": parsed,
                    "raw_response": raw_content,
                    "full_response": result,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨鏍煎紡閿欒鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage1',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        batch_id=batch_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage1] saved failed output for attempt {attempt_num} (format error)")

                last_error = "invalid AI response format: missing 'shots' field"
                raise ValueError("invalid AI response format: missing 'shots' field")

            # 鉁呪渽 鎴愬姛锛佷繚瀛樻垚鍔熺殑杈撳嚭
            output_debug_data = {
                "raw_response": raw_content,
                "parsed_json": parsed,
                "full_response": result,
                "shots_count": len(parsed.get("shots", [])),
                "attempt": attempt_num,
                "success": True
            }

            if episode_id and task_folder:
                save_ai_debug(
                    'stage1',
                    input_debug_data,
                    output_debug_data,
                    episode_id=episode_id,
                    batch_id=batch_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [stage1] saved success output for attempt {attempt_num}")

            # 鏋勫缓杩斿洖鐨勮皟璇曚俊鎭紙淇濇寔鍚戝悗鍏煎锛?
            debug_info = {
                "input": input_debug_data,
                "output": output_debug_data
            }

            # 鉁?鎴愬姛杩斿洖
            return parsed, debug_info

        except Exception as e:
            last_error = str(e)
            print(f"[stage1] attempt {attempt_num}/{max_retries} failed: {last_error}")

            # 濡傛灉涓嶆槸鏈€鍚庝竴娆″皾璇曪紝绛夊緟鍚庨噸璇?
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 鎸囨暟閫€閬? 1绉? 2绉? 4绉?
                print(f"[stage1] waiting {wait_time}s before retry")
                time.sleep(wait_time)

    # 鎵€鏈夐噸璇曢兘澶辫触
    final_error = Exception(f"stage1 AI analysis failed (retried {max_retries} times): {last_error}")
    raise final_error


def stage2_generate_subject_prompts(full_storyboard_json: str, episode_id: int = None, task_folder: str = None) -> tuple:
    """
    闃舵2锛氱敓鎴愪富浣撶粯鐢绘彁绀鸿瘝锛堝甫閲嶈瘯鏈哄埗锛?

    Args:
        full_storyboard_json: 瀹屾暣鍒嗛暅琛↗SON瀛楃涓?
        episode_id: 鐗囨ID锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級
        task_folder: 浠诲姟鏂囦欢澶瑰悕锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級

    Returns:
        tuple: (parsed_result, debug_info)
        - parsed_result: dict with "subjects" array
        - debug_info: dict with "input" and "output" for debugging
    """
    # 鏈湴瀵煎叆閬垮厤寰幆渚濊禆
    from main import save_ai_debug

    max_retries = 10
    last_error = None

    for attempt in range(max_retries):
        attempt_num = attempt + 1  # 1-indexed for display
        print(f"[stage2] attempt {attempt_num}/{max_retries}")

        # 鉁?鍦╰ry鍧椾箣鍓嶅垵濮嬪寲鎵€鏈夊彲鑳藉湪exception handler涓娇鐢ㄧ殑鍙橀噺
        input_debug_data = {
            "full_storyboard_json": full_storyboard_json[:1000] + "..." if len(full_storyboard_json) > 1000 else full_storyboard_json,
            "attempt": attempt_num,
            "max_retries": max_retries
        }
        raw_response_text = None
        status_code = None
        result = None
        raw_content = None

        try:
            try:
                total_shots = len(json.loads(full_storyboard_json).get("shots", []))
            except Exception:
                total_shots = full_storyboard_json.count('"shot_number"')

            # 浠庢暟鎹簱璇诲彇prompt妯℃澘
            prompt_template = get_prompt_by_key("stage2_refine_shot")
            prompt = prompt_template.format(
                total_shots=total_shots,
                full_storyboard_json=full_storyboard_json
            )

            # 鑾峰彇AI閰嶇疆
            config = get_ai_config("detailed_storyboard_s2")

            # 鍑嗗璇锋眰鏁版嵁
            request_data = {
                "model": config['model'],
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "response_format": {"type": "json_object"},  # 寮哄埗杩斿洖 JSON 鏍煎紡
                "stream": False  # 绂佺敤娴佸紡鍝嶅簲锛岀洿鎺ヨ繑鍥炲畬鏁碕SON
            }

            # 鉁?鏇存柊input_debug_data锛屾坊鍔犲畬鏁寸殑璋冭瘯淇℃伅
            input_debug_data.update({
                "total_shots": total_shots,
                "prompt": prompt,
                "request_data": request_data,
                "config": build_ai_debug_config(config)
            })

            # 鉁?淇濆瓨杈撳叆锛堟瘡娆″皾璇曢兘淇濆瓨锛?
            if episode_id and task_folder:
                save_ai_debug(
                    'stage2',
                    input_debug_data,
                    episode_id=episode_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [stage2] saved input for attempt {attempt_num}")

            # 鍙戦€丄PI璇锋眰
            response = run_text_llm_request(
                stage="stage2",
                url=config['api_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=request_data,
                timeout=config['timeout'],
                provider_key=str(config.get("provider_key") or ""),
                model=str(config.get("model_id") or config.get("model") or ""),
                request_tag=f"episode={episode_id or ''}|task={task_folder or ''}|attempt={attempt_num}"
            )

            # 鉁?鍏堜繚瀛樺師濮嬪搷搴旀枃鏈?
            raw_response_text = response.text
            status_code = response.status_code

            if status_code == 200 and episode_id:
                billing_service.record_text_request_success(
                    episode_id=episode_id,
                    stage="detailed_storyboard_stage2",
                    provider=str(config.get("provider_key") or ""),
                    model_name=str(config.get("model_id") or config.get("model") or ""),
                    billing_key=f"text:stage2:{task_folder or episode_id}:attempt{attempt_num}",
                    operation_key=f"text:stage2:{task_folder or episode_id}",
                    attempt_index=attempt_num,
                    detail_json=json.dumps({
                        "task_folder": task_folder,
                        "attempt": attempt_num,
                    }, ensure_ascii=False),
                )

            # 鉁?HTTP 閿欒澶勭悊
            if status_code != 200:
                try:
                    error_response = response.json()
                except:
                    error_response = {"raw_text": raw_response_text}

                output_debug_data = {
                    "error": f"HTTP {status_code}",
                    "raw_response": error_response,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage2',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage2] saved failed output for attempt {attempt_num} (HTTP {status_code})")

                last_error = f"HTTP {status_code}"
                raise Exception(f"AI request failed: {status_code} - {raw_response_text}")

            # 鉁?瑙ｆ瀽JSON鍝嶅簲
            try:
                result = response.json()
            except json.JSONDecodeError as json_err:
                output_debug_data = {
                    "error": f"failed to parse response JSON: {str(json_err)}",
                    "raw_response_text": raw_response_text,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨JSON瑙ｆ瀽澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage2',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage2] saved failed output for attempt {attempt_num} (response JSON parse error)")

                last_error = f"response JSON parse error: {str(json_err)}"
                raise json_err

            content_result = result['choices'][0]['message']['content']

            # 鎻愬彇JSON
            raw_content = content_result
            if '```json' in content_result:
                content_result = content_result.split('```json')[1].split('```')[0].strip()
            elif '```' in content_result:
                content_result = content_result.split('```')[1].split('```')[0].strip()

            # 鉁?灏濊瘯瑙ｆ瀽content涓殑JSON
            try:
                parsed = json.loads(content_result)
            except json.JSONDecodeError as json_err:
                # 鎵撳嵃璇︾粏鐨勯敊璇俊鎭?
                print(f"  [stage2] JSON parse failed: {str(json_err)}")
                print(f"  [stage2] raw content length: {len(raw_content)}")
                print(f"  [stage2] extracted content length: {len(content_result)}")
                print(f"  [stage2] raw content first 200 chars: {raw_content[:200]}")
                print(f"  [stage2] extracted content first 200 chars: {content_result[:200]}")

                output_debug_data = {
                    "error": f"failed to parse AI JSON content: {str(json_err)}",
                    "json_error_msg": str(json_err),
                    "json_error_pos": getattr(json_err, 'pos', None),
                    "json_error_lineno": getattr(json_err, 'lineno', None),
                    "json_error_colno": getattr(json_err, 'colno', None),
                    "raw_content": raw_content,
                    "raw_content_preview": raw_content[:500],
                    "extracted_content": content_result,
                    "extracted_content_preview": content_result[:500],
                    "full_response": result,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨鍐呭JSON瑙ｆ瀽澶辫触鐨勮緭鍑?
                if episode_id and task_folder:
                    try:
                        save_ai_debug(
                            'stage2',
                            input_debug_data,
                            output_debug_data,
                            episode_id=episode_id,
                            task_folder=task_folder,
                            attempt=attempt_num
                        )
                        print(f"  [stage2] saved failed output for attempt {attempt_num} to ai_debug/{task_folder}")
                    except Exception as save_err:
                        print(f"  [stage2] failed to save debug info: {str(save_err)}")

                last_error = f"content JSON parse error: {str(json_err)}"
                raise json_err

            # 楠岃瘉鏍煎紡
            if 'subjects' not in parsed:
                output_debug_data = {
                    "error": "invalid AI response format: missing 'subjects' field",
                    "parsed_json": parsed,
                    "raw_response": raw_content,
                    "full_response": result,
                    "attempt": attempt_num
                }

                # 鉁?淇濆瓨鏍煎紡閿欒鐨勮緭鍑?
                if episode_id and task_folder:
                    save_ai_debug(
                        'stage2',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage2] saved failed output for attempt {attempt_num} (format error)")

                last_error = "invalid AI response format: missing 'subjects' field"
                raise ValueError("invalid AI response format: missing 'subjects' field")

            # 鉁呪渽 鎴愬姛锛佷繚瀛樻垚鍔熺殑杈撳嚭
            output_debug_data = {
                "raw_response": raw_content,
                "parsed_json": parsed,
                "full_response": result,
                "subjects_count": len(parsed.get("subjects", [])),
                "attempt": attempt_num,
                "success": True
            }

            if episode_id and task_folder:
                save_ai_debug(
                    'stage2',
                    input_debug_data,
                    output_debug_data,
                    episode_id=episode_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [stage2] saved success output for attempt {attempt_num}")

            # 鏋勫缓杩斿洖鐨勮皟璇曚俊鎭紙淇濇寔鍚戝悗鍏煎锛?
            debug_info = {
                "input": input_debug_data,
                "output": output_debug_data
            }

            # 鉁?鎴愬姛杩斿洖
            return parsed, debug_info

        except Exception as e:
            last_error = str(e)
            print(f"[stage2] attempt {attempt_num}/{max_retries} failed: {last_error}")

            # 鉁?纭繚寮傚父鎯呭喌涓嬩篃淇濆瓨鍘熷鍝嶅簲锛堝鏋滄湁鐨勮瘽锛?
            if episode_id and task_folder:
                try:
                    exception_debug_data = {
                        "error": f"exception: {last_error}",
                        "exception_type": type(e).__name__,
                        "raw_response_text": raw_response_text,
                        "status_code": status_code,
                        "result": result,
                        "raw_content": raw_content,
                        "attempt": attempt_num
                    }
                    save_ai_debug(
                        'stage2',
                        input_debug_data,
                        exception_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [stage2] saved exception debug info to ai_debug/{task_folder}")
                except Exception as save_err:
                    print(f"  [stage2] failed to save exception debug info: {str(save_err)}")

            # 濡傛灉涓嶆槸鏈€鍚庝竴娆″皾璇曪紝绛夊緟鍚庨噸璇?
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 鎸囨暟閫€閬? 1绉? 2绉? 4绉?
                print(f"[stage2] waiting {wait_time}s before retry")
                time.sleep(wait_time)

    # 鎵€鏈夐噸璇曢兘澶辫触
    final_error = Exception(f"stage2 AI analysis failed (retried {max_retries} times): {last_error}")
    raise final_error


# ==================== 鏂颁笁闃舵鍒嗛暅鐢熸垚 ====================

def generate_simple_storyboard(
    content: str,
    batch_size: int,
    duration: int = 15,
    episode_id: int = None,
    task_folder: str = None,
    batches: Optional[List[Dict[str, Any]]] = None,
    batch_retry_limit: int = 10,
    on_batch_result: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> tuple:
    """
    鏂伴樁娈?锛氱畝鍗曞垎闀?- 闀滃ご鍒掑垎
    灏嗗墽鏈枃鏈寜鍒嗘鍒掑垎涓哄涓暅澶达紝浠呰緭鍑洪暅鍙峰拰鍘熸枃鐗囨
    """
    from main import save_ai_debug
    from concurrent.futures import ThreadPoolExecutor

    prepared_batches: List[Dict[str, Any]] = []
    if batches:
        for item in batches:
            if not isinstance(item, dict):
                continue
            batch_index = int(item.get("batch_index") or 0)
            if batch_index <= 0:
                continue
            prepared_batches.append({
                "batch_index": batch_index,
                "content": str(item.get("content") or ""),
                "retry_count": int(item.get("retry_count") or 0),
            })
    else:
        paragraphs = [p.strip() for p in str(content or "").split('\n') if p.strip()]
        split_batches = []
        current_batch = []
        current_length = 0
        for para in paragraphs:
            para_length = len(para)
            if current_length + para_length >= batch_size and current_batch:
                split_batches.append('\n\n'.join(current_batch))
                current_batch = [para]
                current_length = para_length
            else:
                current_batch.append(para)
                current_length += para_length
        if current_batch:
            split_batches.append('\n\n'.join(current_batch))
        prepared_batches = [
            {
                "batch_index": index + 1,
                "content": batch_content,
                "retry_count": 0,
            }
            for index, batch_content in enumerate(split_batches)
        ]

    print(f"[simple_storyboard] split into {len(prepared_batches)} batches, batch_size={batch_size}")

    def emit_batch_result(payload: Dict[str, Any]):
        if not on_batch_result:
            return
        try:
            on_batch_result(dict(payload))
        except Exception as callback_error:
            print(f"[simple_storyboard] batch callback failed: {callback_error}")

    def process_batch(batch_meta: Dict[str, Any]):
        batch_index = int(batch_meta.get("batch_index") or 0)
        batch_content = str(batch_meta.get("content") or "")
        existing_retry_count = int(batch_meta.get("retry_count") or 0)
        max_retries = max(1, int(batch_retry_limit or 1))
        last_error = None

        emit_batch_result({
            "batch_index": batch_index,
            "status": "submitting",
            "last_attempt": 0,
            "retry_count": existing_retry_count,
        })

        for attempt in range(max_retries):
            attempt_num = attempt + 1
            input_debug_data = {
                "batch_idx": batch_index,
                "batch_content": batch_content,
                "batch_size": batch_size,
                "attempt": attempt_num,
                "max_retries": max_retries,
                "existing_retry_count": existing_retry_count,
            }
            raw_response_text = None
            status_code = None
            result = None
            raw_content = None
            failure_logged = False

            try:
                db = SessionLocal()
                try:
                    template = db.query(models.ShotDurationTemplate).filter(
                        models.ShotDurationTemplate.duration == duration
                    ).first()
                    if not template:
                        raise Exception(f"no duration template found: {duration}s")
                    prompt_template = template.simple_storyboard_rule
                finally:
                    db.close()

                prompt = prompt_template.format(content=batch_content)
                config = get_ai_config("simple_storyboard")
                request_data = {
                    "model": config['model'],
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "stream": False
                }
                input_debug_data.update({
                    "prompt": prompt,
                    "request_data": request_data,
                    "config": build_ai_debug_config(config)
                })

                if episode_id and task_folder:
                    save_ai_debug(
                        'simple_storyboard',
                        input_debug_data,
                        episode_id=episode_id,
                        batch_id=str(batch_index),
                        task_folder=task_folder,
                        attempt=attempt_num
                    )

                response = run_text_llm_request(
                    stage="simple_storyboard",
                    url=config['api_url'],
                    headers={
                        "Authorization": f"Bearer {config['api_key']}",
                        "Content-Type": "application/json",
                    },
                    json=request_data,
                    timeout=config['timeout'],
                    provider_key=str(config.get("provider_key") or ""),
                    model=str(config.get("model_id") or config.get("model") or ""),
                    request_tag=f"episode={episode_id or ''}|task={task_folder or ''}|batch={batch_index}|attempt={attempt_num}"
                )

                raw_response_text = response.text
                status_code = response.status_code

                if status_code == 200 and episode_id:
                    billing_service.record_text_request_success(
                        episode_id=episode_id,
                        stage="simple_storyboard",
                        provider=str(config.get("provider_key") or ""),
                        model_name=str(config.get("model_id") or config.get("model") or ""),
                        billing_key=f"text:simple_storyboard:{task_folder or episode_id}:batch{batch_index}:attempt{attempt_num}",
                        operation_key=f"text:simple_storyboard:{task_folder or episode_id}:batch{batch_index}",
                        attempt_index=attempt_num,
                        detail_json=json.dumps({
                            "batch_index": batch_index,
                            "task_folder": task_folder,
                            "attempt": attempt_num,
                            "retry_count": existing_retry_count,
                        }, ensure_ascii=False),
                    )

                if status_code != 200:
                    try:
                        error_response = response.json()
                    except Exception:
                        error_response = {"raw_text": raw_response_text}
                    output_debug_data = {
                        "error": f"HTTP {status_code}",
                        "raw_response": error_response,
                        "status_code": status_code,
                        "attempt": attempt_num
                    }
                    if episode_id and task_folder:
                        save_ai_debug(
                            'simple_storyboard',
                            input_debug_data,
                            output_debug_data,
                            episode_id=episode_id,
                            batch_id=str(batch_index),
                            task_folder=task_folder,
                            attempt=attempt_num
                        )
                        failure_logged = True
                    last_error = f"HTTP {status_code}"
                    raise Exception(f"AI request failed: {status_code}")

                result = response.json()
                raw_content = result['choices'][0]['message']['content']
                content_to_parse = raw_content.strip()
                if content_to_parse.startswith('```'):
                    lines = content_to_parse.split('\n')
                    if lines[0].startswith('```'):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == '```':
                        lines = lines[:-1]
                    content_to_parse = '\n'.join(lines)

                import re
                content_to_parse = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content_to_parse)
                parsed = json.loads(content_to_parse)
                if 'shots' not in parsed:
                    output_debug_data = {
                        "raw_response": raw_content,
                        "parsed_json": parsed,
                        "full_response": result,
                        "error": "missing 'shots' field",
                        "attempt": attempt_num,
                        "success": False
                    }
                    if episode_id and task_folder:
                        save_ai_debug(
                            'simple_storyboard',
                            input_debug_data,
                            output_debug_data,
                            episode_id=episode_id,
                            batch_id=str(batch_index),
                            task_folder=task_folder,
                            attempt=attempt_num
                        )
                        failure_logged = True
                    last_error = "invalid AI response format: missing 'shots' field"
                    raise ValueError(last_error)

                output_debug_data = {
                    "raw_response": raw_content,
                    "parsed_json": parsed,
                    "full_response": result,
                    "shots_count": len(parsed.get("shots", [])),
                    "attempt": attempt_num,
                    "success": True
                }
                if episode_id and task_folder:
                    save_ai_debug(
                        'simple_storyboard',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        batch_id=str(batch_index),
                        task_folder=task_folder,
                        attempt=attempt_num
                    )

                batch_shots = parsed.get("shots", [])
                emit_batch_result({
                    "batch_index": batch_index,
                    "status": "completed",
                    "shots": batch_shots,
                    "error_message": "",
                    "last_attempt": attempt_num,
                    "retry_count": existing_retry_count,
                })
                return (batch_index, batch_shots)

            except Exception as e:
                last_error = str(e)
                if episode_id and task_folder and not failure_logged:
                    exception_debug_data = {
                        "error": f"exception: {last_error}",
                        "exception_type": type(e).__name__,
                        "raw_response_text": raw_response_text,
                        "status_code": status_code,
                        "result": result,
                        "raw_content": raw_content,
                        "attempt": attempt_num,
                        "success": False,
                    }
                    try:
                        save_ai_debug(
                            'simple_storyboard',
                            input_debug_data,
                            exception_debug_data,
                            episode_id=episode_id,
                            batch_id=str(batch_index),
                            task_folder=task_folder,
                            attempt=attempt_num
                        )
                    except Exception as save_err:
                        print(f"[simple_storyboard] failed to save exception output: {str(save_err)}")

                if attempt < max_retries - 1:
                    emit_batch_result({
                        "batch_index": batch_index,
                        "status": "submitting",
                        "error_message": last_error,
                        "last_attempt": attempt_num,
                        "retry_count": existing_retry_count,
                    })
                    time.sleep(2 ** attempt)

        emit_batch_result({
            "batch_index": batch_index,
            "status": "failed",
            "shots": [],
            "error_message": last_error or "simple storyboard generation failed",
            "last_attempt": max_retries,
            "retry_count": existing_retry_count,
        })
        return (batch_index, [])

    if not prepared_batches:
        raise Exception("simple storyboard generation failed: no batches prepared")

    all_shots = []
    shot_counter = 1
    with ThreadPoolExecutor(max_workers=min(len(prepared_batches), 10)) as executor:
        futures = [executor.submit(process_batch, batch_meta) for batch_meta in prepared_batches]
        batch_results = {}
        for future in futures:
            batch_idx, batch_shots = future.result()
            batch_results[batch_idx] = batch_shots

    for batch_idx in sorted(batch_results.keys()):
        batch_shots = batch_results[batch_idx]
        for shot in batch_shots:
            shot["shot_number"] = shot_counter
            shot_counter += 1
        all_shots.extend(batch_shots)

    if not all_shots:
        raise Exception("simple storyboard generation failed: no shots generated")

    result = {"shots": all_shots}
    debug_info = {
        "input": {
            "content": content,
            "batch_size": batch_size,
            "batches_count": len(prepared_batches)
        },
        "output": {
            "shots_count": len(all_shots),
            "success": True
        }
    }

    print(f"[simple_storyboard] generation success, total shots={len(all_shots)}")
    return result, debug_info


def generate_detailed_storyboard(simple_shots: list, episode_id: int = None, task_folder: str = None) -> tuple:
    """
    鏂伴樁娈?锛氳缁嗗垎闀?- 鍐呭鍒嗘瀽
    瀵瑰凡鍒掑垎鐨勯暅澶磋繘琛岃缁嗗唴瀹瑰垎鏋愶紝鎻愬彇涓讳綋銆佸鐧姐€佹梺鐧界瓑淇℃伅

    Args:
        simple_shots: 绠€鍗曞垎闀滅殑shots鍒楄〃锛堟瘡涓寘鍚玸hot_number鍜宱riginal_text锛?
        episode_id: 鐗囨ID锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級
        task_folder: 浠诲姟鏂囦欢澶瑰悕锛堢敤浜庤皟璇曟枃浠朵繚瀛橈級

    Returns:
        tuple: (parsed_result, debug_info)
        - parsed_result: dict with "shots" array (鍖呭惈涓讳綋銆佸鐧界瓑璇︾粏淇℃伅)
        - debug_info: dict with "input" and "output" for debugging
    """
    # 鏈湴瀵煎叆閬垮厤寰幆渚濊禆
    from main import save_ai_debug

    max_retries = 10
    last_error = None

    # 鏋勫缓闀滃ご鍒楄〃鐨勬枃鏈牸寮?
    shots_content = ""
    for shot in simple_shots:
        shot_num = shot.get('shot_number', '?')
        original_text = shot.get('original_text', '')
        shots_content += f"镜头{shot_num}:\n{original_text}\n\n"

    for attempt in range(max_retries):
        attempt_num = attempt + 1
        print(f"[detailed_storyboard] attempt {attempt_num}/{max_retries}")

        input_debug_data = {
            "simple_shots": simple_shots,
            "shots_content": shots_content,
            "shots_count": len(simple_shots),
            "attempt": attempt_num,
            "max_retries": max_retries
        }
        raw_response_text = None
        status_code = None
        result = None
        raw_content = None

        try:
            # 浠庢暟鎹簱璇诲彇prompt妯℃澘
            prompt_template = get_prompt_by_key("detailed_storyboard_content_analysis")
            prompt = prompt_template.format(shots_content=shots_content)

            # 鑾峰彇AI閰嶇疆
            config = get_ai_config("detailed_storyboard_s1")

            # 鍑嗗璇锋眰鏁版嵁
            request_data = {
                "model": config['model'],
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "response_format": {"type": "json_object"},
                "stream": False
            }

            input_debug_data.update({
                "prompt": prompt,
                "request_data": request_data,
                "config": build_ai_debug_config(config)
            })

            # 淇濆瓨杈撳叆
            if episode_id and task_folder:
                save_ai_debug(
                    'detailed_storyboard',
                    input_debug_data,
                    episode_id=episode_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [detailed_storyboard] saved input for attempt {attempt_num}")

            # 鍙戦€丄PI璇锋眰
            response = run_text_llm_request(
                stage="detailed_storyboard",
                url=config['api_url'],
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json=request_data,
                timeout=config['timeout'],
                provider_key=str(config.get("provider_key") or ""),
                model=str(config.get("model_id") or config.get("model") or ""),
                request_tag=f"episode={episode_id or ''}|task={task_folder or ''}|attempt={attempt_num}"
            )

            raw_response_text = response.text
            status_code = response.status_code

            if status_code == 200 and episode_id:
                billing_service.record_text_request_success(
                    episode_id=episode_id,
                    stage="detailed_storyboard",
                    provider=str(config.get("provider_key") or ""),
                    model_name=str(config.get("model_id") or config.get("model") or ""),
                    billing_key=f"text:detailed_storyboard:{task_folder or episode_id}:attempt{attempt_num}",
                    operation_key=f"text:detailed_storyboard:{task_folder or episode_id}",
                    attempt_index=attempt_num,
                    detail_json=json.dumps({
                        "task_folder": task_folder,
                        "attempt": attempt_num,
                    }, ensure_ascii=False),
                )

            # HTTP 閿欒澶勭悊
            if status_code != 200:
                try:
                    error_response = response.json()
                except:
                    error_response = {"raw_text": raw_response_text}

                output_debug_data = {
                    "error": f"HTTP {status_code}",
                    "raw_response": error_response,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                if episode_id and task_folder:
                    save_ai_debug(
                        'detailed_storyboard',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [detailed_storyboard] saved HTTP-failure output for attempt {attempt_num} (HTTP {status_code})")

                last_error = f"HTTP {status_code}"
                raise Exception(f"AI request failed: {status_code} - {raw_response_text}")

            # 瑙ｆ瀽JSON鍝嶅簲
            try:
                result = response.json()
            except json.JSONDecodeError as json_err:
                output_debug_data = {
                    "error": f"failed to parse response JSON: {str(json_err)}",
                    "raw_response_text": raw_response_text,
                    "status_code": status_code,
                    "attempt": attempt_num
                }

                if episode_id and task_folder:
                    save_ai_debug(
                        'detailed_storyboard',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [detailed_storyboard] saved JSON-parse-failure output for attempt {attempt_num}")

                last_error = f"response JSON parse error: {str(json_err)}"
                raise json_err

            raw_content = result['choices'][0]['message']['content']

            # 娓呯悊markdown浠ｇ爜鍧?
            content_to_parse = raw_content.strip()
            if content_to_parse.startswith('```'):
                lines = content_to_parse.split('\n')
                if lines[0].startswith('```'):
                    lines = lines[1:]
                if lines and lines[-1].strip() == '```':
                    lines = lines[:-1]
                content_to_parse = '\n'.join(lines)

            # 娓呯悊鎺у埗瀛楃锛堜繚鐣欐崲琛岀鍜屽埗琛ㄧ锛?
            import re
            # 绉婚櫎闄や簡\n鍜孿t涔嬪鐨勬帶鍒跺瓧绗?
            content_to_parse = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content_to_parse)

            # 瑙ｆ瀽JSON
            try:
                parsed = json.loads(content_to_parse)
            except json.JSONDecodeError as json_err:
                output_debug_data = {
                    "error": f"failed to parse content JSON: {str(json_err)}",
                    "raw_content": raw_content,
                    "cleaned_content": content_to_parse,
                    "full_response": result,
                    "attempt": attempt_num
                }

                if episode_id and task_folder:
                    save_ai_debug(
                        'detailed_storyboard',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [detailed_storyboard] saved content-JSON-failure output for attempt {attempt_num}")

                last_error = f"content JSON parse error: {str(json_err)}"
                raise json_err

            # 楠岃瘉鏍煎紡
            if 'shots' not in parsed:
                output_debug_data = {
                    "error": "invalid AI response format: missing 'shots' field",
                    "parsed_json": parsed,
                    "raw_response": raw_content,
                    "full_response": result,
                    "attempt": attempt_num
                }

                if episode_id and task_folder:
                    save_ai_debug(
                        'detailed_storyboard',
                        input_debug_data,
                        output_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [detailed_storyboard] saved format-error output for attempt {attempt_num}")

                last_error = "invalid AI response format: missing 'shots' field"
                raise ValueError("invalid AI response format: missing 'shots' field")

            # 鎴愬姛锛佷繚瀛樻垚鍔熺殑杈撳嚭
            output_debug_data = {
                "raw_response": raw_content,
                "parsed_json": parsed,
                "full_response": result,
                "shots_count": len(parsed.get("shots", [])),
                "attempt": attempt_num,
                "success": True
            }

            if episode_id and task_folder:
                save_ai_debug(
                    'detailed_storyboard',
                    input_debug_data,
                    output_debug_data,
                    episode_id=episode_id,
                    task_folder=task_folder,
                    attempt=attempt_num
                )
                print(f"  [detailed_storyboard] saved success output for attempt {attempt_num}")

            debug_info = {
                "input": input_debug_data,
                "output": output_debug_data
            }

            return parsed, debug_info

        except Exception as e:
            last_error = str(e)
            print(f"[detailed_storyboard] attempt {attempt_num}/{max_retries} failed: {last_error}")

            if episode_id and task_folder:
                try:
                    exception_debug_data = {
                        "error": f"exception: {last_error}",
                        "exception_type": type(e).__name__,
                        "raw_response_text": raw_response_text,
                        "status_code": status_code,
                        "result": result,
                        "raw_content": raw_content,
                        "attempt": attempt_num
                    }
                    save_ai_debug(
                        'detailed_storyboard',
                        input_debug_data,
                        exception_debug_data,
                        episode_id=episode_id,
                        task_folder=task_folder,
                        attempt=attempt_num
                    )
                    print(f"  [detailed_storyboard] saved exception debug info")
                except Exception as save_err:
                    print(f"  [detailed_storyboard] failed to save exception debug info: {str(save_err)}")

            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"[detailed_storyboard] waiting {wait_time}s before retry")
                time.sleep(wait_time)

    # 鎵€鏈夐噸璇曢兘澶辫触
    final_error = Exception(f"detailed storyboard generation failed (retried {max_retries} times): {last_error}")
    raise final_error

