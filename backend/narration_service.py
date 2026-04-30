"""
文本转解说剧后台任务服务
"""
import requests
import json
import time
import os
import uuid
from threading import Thread
from datetime import datetime
from database import SessionLocal
import models
import billing_service
from ai_config import build_ai_debug_config, get_ai_config
from text_llm_queue import run_text_llm_request


def save_narration_debug(input_data: dict, output_data: dict, episode_id: int, success: bool):
    """保存debug文件到backend/ai_debug"""
    try:
        # 创建debug目录
        debug_dir = os.path.join("ai_debug", f"narration_episode_{episode_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(debug_dir, exist_ok=True)

        # 保存输入数据
        with open(os.path.join(debug_dir, "input.json"), "w", encoding="utf-8") as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)

        # 保存输出数据
        with open(os.path.join(debug_dir, "output.json"), "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        # 保存元数据
        metadata = {
            "episode_id": episode_id,
            "timestamp": datetime.now().isoformat(),
            "success": success
        }
        with open(os.path.join(debug_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        try:
            from dashboard_service import log_file_task_event
            task_folder = os.path.basename(debug_dir)
            log_file_task_event(task_folder=task_folder, file_name="input.json", payload=input_data, task_type="narration", stage="narration", episode_id=episode_id)
            log_file_task_event(task_folder=task_folder, file_name="output.json", payload=output_data, task_type="narration", stage="narration", status="completed" if success else "failed", episode_id=episode_id)
        except Exception as dashboard_error:
            print(f"[narration][dashboard] sync failed: {str(dashboard_error)}")

        print(f"[文本转解说剧] Debug文件已保存到: {debug_dir}")
    except Exception as e:
        print(f"[文本转解说剧] 保存debug文件失败: {str(e)}")


def convert_text_to_narration_task(episode_id: int, custom_template: str = None):
    """
    后台任务：将文本转换为解说剧

    Args:
        episode_id: 片段ID
        custom_template: 可选的自定义模板（前端临时传入）
    """
    db = SessionLocal()

    try:
        # 获取episode
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            print(f"[文本转解说剧] Episode {episode_id} 不存在")
            return

        content = episode.content.strip()
        if not content:
            episode.narration_converting = False
            episode.narration_error = "文本内容为空"
            db.commit()
            print(f"[文本转解说剧] Episode {episode_id} 文本内容为空")
            return

        # 读取提示词模板：优先使用传入的临时模板，其次使用剧本级别，最后使用全局级别
        template = ""

        # 1. 优先使用前端传入的临时模板
        if custom_template is not None:
            template = custom_template.strip()
            print(f"[文本转解说剧] 使用前端临时模板")

        # 2. 如果没有临时模板，尝试读取剧本级别的模板
        if not template and episode.script and episode.script.narration_template:
            template = episode.script.narration_template.strip()
            print(f"[文本转解说剧] 使用剧本级别模板")

        # 3. 如果剧本级别为空，读取全局模板
        if not template:
            template_setting = db.query(models.GlobalSettings).filter(
                models.GlobalSettings.key == "narration_conversion_template"
            ).first()

            if template_setting and template_setting.value:
                template = template_setting.value.strip()
                print(f"[文本转解说剧] 使用全局默认模板")

        # 4. 如果都没有配置，报错
        if not template:
            episode.narration_converting = False
            episode.narration_error = "提示词模板未配置（临时、剧本和全局级别均为空）"
            db.commit()
            print(f"[文本转解说剧] 提示词模板未配置")
            return
        full_prompt = f"{template}\n\n原文本：\n{content}"

        # 准备debug输入数据
        input_data = {
            "episode_id": episode_id,
            "content_length": len(content),
            "content_preview": content[:200] + "..." if len(content) > 200 else content,
            "template": template,
            "full_prompt_length": len(full_prompt)
        }

        # 获取AI配置
        config = get_ai_config("narration")
        request_data = {
            "model": config['model'],
            "messages": [
                {
                    "role": "user",
                    "content": full_prompt
                }
            ],
            "stream": False
        }
        input_data["config"] = build_ai_debug_config(config)
        input_data["request_data"] = request_data

        print(f"[文本转解说剧] 开始转换 Episode {episode_id}，内容长度: {len(content)}")

        # 调用AI API
        response = run_text_llm_request(
            stage="narration",
            url=config['api_url'],
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=request_data,
            timeout=config['timeout'],
            provider_key=str(config.get("provider_key") or ""),
            model=str(config.get("model_id") or config.get("model") or ""),
            request_tag=f"episode={episode_id}"
        )

        if response.status_code == 200:
            billing_service.record_text_request_success(
                episode_id=episode_id,
                stage="narration",
                provider=str(config.get("provider_key") or ""),
                model_name=str(config.get("model_id") or config.get("model") or ""),
                billing_key=f"text:narration:{episode_id}:{uuid.uuid4().hex[:8]}",
                operation_key=f"text:narration:{episode_id}",
                attempt_index=1,
                detail_json=json.dumps({
                    "episode_id": episode_id,
                    "content_length": len(content),
                }, ensure_ascii=False),
            )

        if response.status_code != 200:
            error_msg = f"AI请求失败: {response.status_code} - {response.text[:200]}"
            episode.narration_converting = False
            episode.narration_error = error_msg
            db.commit()

            # 保存失败的debug
            output_data = {
                "success": False,
                "status_code": response.status_code,
                "error": response.text[:500]
            }
            save_narration_debug(input_data, output_data, episode_id, False)

            print(f"[文本转解说剧] Episode {episode_id} 失败: {error_msg}")
            return

        result = response.json()
        converted_text = result['choices'][0]['message']['content']

        # 保存转换后的文本到content
        episode.content = converted_text
        episode.narration_converting = False
        episode.narration_error = ""
        db.commit()

        # 保存成功的debug
        output_data = {
            "success": True,
            "converted_text_length": len(converted_text),
            "converted_text_preview": converted_text[:200] + "..." if len(converted_text) > 200 else converted_text,
            "raw_response": result
        }
        save_narration_debug(input_data, output_data, episode_id, True)

        print(f"[文本转解说剧] Episode {episode_id} 转换成功，新文本长度: {len(converted_text)}")

    except Exception as e:
        import traceback
        error_msg = f"转换失败: {str(e)}"

        try:
            episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
            if episode:
                episode.narration_converting = False
                episode.narration_error = error_msg
                db.commit()
        except:
            pass

        # 保存异常的debug
        output_data = {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        try:
            fallback_input = {"episode_id": episode_id}
            if "config" in locals():
                fallback_input["config"] = build_ai_debug_config(config)
            save_narration_debug(fallback_input, output_data, episode_id, False)
        except:
            pass

        print(f"[文本转解说剧] Episode {episode_id} 异常: {error_msg}")
        print(traceback.format_exc())

    finally:
        try:
            db.close()
        except:
            pass


def start_narration_conversion_async(episode_id: int, custom_template: str = None):
    """
    异步启动文本转解说剧任务

    Args:
        episode_id: 片段ID
        custom_template: 可选的自定义模板（前端临时传入）
    """
    thread = Thread(target=convert_text_to_narration_task, args=(episode_id, custom_template), daemon=True)
    thread.start()
    print(f"[文本转解说剧] 已启动后台任务 Episode {episode_id}")
