from threading import Thread
from typing import Any, Callable, Optional


ImageCatalogRefresh = Callable[[], Any]
VideoAccountsRefresh = Callable[[str], Any]
PrintFn = Callable[[str], None]


def _default_image_catalog_refresh() -> Any:
    import image_platform_client

    return image_platform_client.refresh_image_model_catalog()


def _default_video_accounts_refresh(provider: str) -> Any:
    from video_provider_accounts import refresh_video_provider_accounts

    return refresh_video_provider_accounts(provider)


def run_external_cache_prewarms(
    image_catalog_refresh: Optional[ImageCatalogRefresh] = None,
    video_accounts_refresh: Optional[VideoAccountsRefresh] = None,
    print_fn: PrintFn = print,
) -> None:
    refresh_image_catalog = image_catalog_refresh or _default_image_catalog_refresh
    refresh_video_accounts = video_accounts_refresh or _default_video_accounts_refresh

    try:
        refresh_image_catalog()
    except Exception as exc:
        print_fn(f"[startup] refresh image model catalog failed: {exc}")

    try:
        refresh_video_accounts("moti")
    except Exception as exc:
        print_fn(f"[startup] refresh moti video accounts failed: {exc}")


def start_external_cache_prewarms(
    image_catalog_refresh: Optional[ImageCatalogRefresh] = None,
    video_accounts_refresh: Optional[VideoAccountsRefresh] = None,
    print_fn: PrintFn = print,
) -> Thread:
    thread = Thread(
        target=run_external_cache_prewarms,
        kwargs={
            "image_catalog_refresh": image_catalog_refresh,
            "video_accounts_refresh": video_accounts_refresh,
            "print_fn": print_fn,
        },
        name="startup-external-cache-prewarms",
        daemon=True,
    )
    thread.start()
    return thread
