import os
from typing import Mapping, Optional


TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def is_env_truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in TRUTHY_ENV_VALUES


def should_enable_background_pollers(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env

    configured = source.get("ENABLE_BACKGROUND_POLLER")
    if configured is not None:
        return is_env_truthy(configured, default=False)

    role = source.get("APP_ROLE")
    return str(role).strip().lower() == "poller" if role is not None else False
