import threading
import time


class RequestLoadTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._active_requests = 0
        self._last_request_finished_at = 0.0

    def request_started(self, path: str = "") -> None:
        with self._lock:
            self._active_requests += 1

    def request_finished(self, path: str = "") -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_request_finished_at = time.monotonic()

    def active_request_count(self) -> int:
        with self._lock:
            return int(self._active_requests)

    def had_recent_request(self, within_seconds: float = 2.0) -> bool:
        cutoff = time.monotonic() - float(within_seconds)
        with self._lock:
            return self._last_request_finished_at >= cutoff

    def should_throttle_background_tasks(
        self,
        active_threshold: int = 1,
        recent_window_seconds: float = 2.0,
    ) -> bool:
        with self._lock:
            active = self._active_requests
            recent = self._last_request_finished_at >= (time.monotonic() - float(recent_window_seconds))
        return active >= int(active_threshold) or recent

    def choose_batch_size(self, normal_size: int, busy_size: int) -> int:
        if self.should_throttle_background_tasks():
            return max(1, int(busy_size))
        return max(1, int(normal_size))

    def choose_interval(self, normal_seconds: float, busy_seconds: float) -> float:
        if self.should_throttle_background_tasks():
            return max(float(normal_seconds), float(busy_seconds))
        return max(0.5, float(normal_seconds))


request_load_tracker = RequestLoadTracker()
