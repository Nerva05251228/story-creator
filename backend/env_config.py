import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def _strip_optional_quotes(value: str) -> str:
    stripped = str(value or "").strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _load_env_manually(env_path: Path, *, override: bool = False) -> bool:
    loaded = False
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if not override and os.getenv(key):
            continue
        os.environ[key] = _strip_optional_quotes(value)
        loaded = True
    return loaded


def load_app_env(env_path: Optional[os.PathLike | str] = None, *, override: bool = False) -> bool:
    path = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not path.exists():
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:
        return _load_env_manually(path, override=override)

    return bool(load_dotenv(dotenv_path=path, override=override))


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def get_first_env(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = get_env(name)
        if value is not None:
            return value
    return default


def get_int_env(name: str, default: int) -> int:
    value = get_env(name)
    if value is None:
        return int(default)
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def get_bool_env(name: str, default: bool = False) -> bool:
    value = get_env(name)
    if value is None:
        return bool(default)
    return value.lower() in {"1", "true", "yes", "on"}


def is_placeholder_env_value(value: Optional[str]) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text.startswith("<set-local-") or "example.invalid" in text


def require_env(*names: str) -> str:
    value = get_first_env(*names)
    if value is None:
        joined = " or ".join(names)
        raise RuntimeError(f"{joined} is required. Set it in .env or the process environment.")
    return value


def require_real_env(*names: str) -> str:
    value = require_env(*names)
    if is_placeholder_env_value(value):
        joined = " or ".join(names)
        raise RuntimeError(f"{joined} contains a placeholder value. Set a real local value in .env.")
    return value


def mask_secret(value: Optional[str]) -> str:
    if not value:
        return "[missing]"
    return "[set]"


def mask_url(value: Optional[str]) -> str:
    if not value:
        return "[missing]"
    try:
        parts = urlsplit(value)
        if not parts.password:
            return value
        username = parts.username or ""
        host = parts.hostname or ""
        netloc = f"{username}:***@{host}"
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "[set]"


load_app_env()
