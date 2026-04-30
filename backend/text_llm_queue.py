import requests
from threading import Lock, Semaphore
from typing import Any, Dict, Optional


TEXT_LLM_GLOBAL_MAX_CONCURRENT = 5

_text_llm_global_semaphore = Semaphore(TEXT_LLM_GLOBAL_MAX_CONCURRENT)
_text_llm_global_state_lock = Lock()
_text_llm_global_waiting = 0
_text_llm_global_running = 0


def _build_log_context(stage: str, provider_key: str, model: str, request_tag: str) -> str:
    parts = [
        f"stage={stage or ''}",
        f"provider={provider_key or ''}",
        f"model={model or ''}",
    ]
    if str(request_tag or "").strip():
        parts.append(f"request_tag={request_tag}")
    return ", ".join(parts)


def get_text_llm_queue_state() -> Dict[str, int]:
    with _text_llm_global_state_lock:
        return {
            "running": int(_text_llm_global_running),
            "waiting": int(_text_llm_global_waiting),
            "max": int(TEXT_LLM_GLOBAL_MAX_CONCURRENT),
        }


def reset_text_llm_queue_state_for_tests() -> None:
    global _text_llm_global_semaphore
    global _text_llm_global_waiting
    global _text_llm_global_running

    with _text_llm_global_state_lock:
        _text_llm_global_semaphore = Semaphore(TEXT_LLM_GLOBAL_MAX_CONCURRENT)
        _text_llm_global_waiting = 0
        _text_llm_global_running = 0


def run_text_llm_request(
    *,
    stage: str,
    url: str,
    headers: Dict[str, Any],
    json: Dict[str, Any],
    timeout: Any,
    provider_key: str = "",
    model: str = "",
    request_tag: str = "",
    proxies: Optional[Dict[str, Any]] = None,
):
    global _text_llm_global_waiting
    global _text_llm_global_running

    acquired_slot = False
    log_context = _build_log_context(stage, provider_key, model, request_tag)

    with _text_llm_global_state_lock:
        _text_llm_global_waiting += 1
        waiting_now = _text_llm_global_waiting
        running_now = _text_llm_global_running
    print(
        f"[文本LLM并发控制] 任务进入排队: {log_context}, "
        f"running={running_now}, waiting={waiting_now}, max={TEXT_LLM_GLOBAL_MAX_CONCURRENT}"
    )

    try:
        _text_llm_global_semaphore.acquire()
        acquired_slot = True
        with _text_llm_global_state_lock:
            _text_llm_global_waiting = max(0, _text_llm_global_waiting - 1)
            _text_llm_global_running += 1
            waiting_now = _text_llm_global_waiting
            running_now = _text_llm_global_running
        print(
            f"[文本LLM并发控制] 获取执行槽位: {log_context}, "
            f"running={running_now}, waiting={waiting_now}, max={TEXT_LLM_GLOBAL_MAX_CONCURRENT}"
        )

        request_kwargs: Dict[str, Any] = {
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        }
        if proxies is not None:
            request_kwargs["proxies"] = proxies
        response = requests.post(**request_kwargs)
        return response
    finally:
        if acquired_slot:
            with _text_llm_global_state_lock:
                _text_llm_global_running = max(0, _text_llm_global_running - 1)
                waiting_now = _text_llm_global_waiting
                running_now = _text_llm_global_running
            _text_llm_global_semaphore.release()
            print(
                f"[文本LLM并发控制] 释放执行槽位: {log_context}, "
                f"running={running_now}, waiting={waiting_now}, max={TEXT_LLM_GLOBAL_MAX_CONCURRENT}"
            )
        else:
            with _text_llm_global_state_lock:
                _text_llm_global_waiting = max(0, _text_llm_global_waiting - 1)
