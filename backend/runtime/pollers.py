from threading import Lock
from typing import Callable, Iterable, Protocol

from startup_runtime import should_enable_background_pollers


class BackgroundPoller(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


_background_poller_lock = Lock()
_background_pollers_started = False


def start_background_pollers(
    *,
    pollers: Iterable[BackgroundPoller],
    recover_storyboard2_video_polling: Callable[[], None],
    force: bool = False,
    should_enable_pollers: Callable[[], bool] = should_enable_background_pollers,
    print_fn: Callable[[str], None] = print,
) -> bool:
    global _background_pollers_started

    enabled = force or should_enable_pollers()
    if not enabled:
        print_fn("[startup] background pollers disabled for this process")
        return False

    with _background_poller_lock:
        if _background_pollers_started:
            return True

        for poller in pollers:
            poller.start()
        recover_storyboard2_video_polling()
        _background_pollers_started = True
        print_fn("[startup] background pollers enabled for this process")
        return True


def stop_background_pollers(*, pollers: Iterable[BackgroundPoller]) -> None:
    global _background_pollers_started

    with _background_poller_lock:
        if not _background_pollers_started:
            return

        for poller in pollers:
            poller.stop()
        _background_pollers_started = False
