import importlib
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


POLLER_NAMES = ("video", "image", "managed", "text_relay", "voiceover_tts", "pricing")


def _load_runtime_pollers():
    sys.modules.pop("runtime.pollers", None)
    sys.modules.pop("main", None)
    try:
        return importlib.import_module("runtime.pollers")
    except ModuleNotFoundError as exc:
        if exc.name in {"runtime", "runtime.pollers"}:
            raise AssertionError("runtime.pollers module should exist") from exc
        raise


class FakePoller:
    def __init__(self, name: str, events: list[str]):
        self.name = name
        self.events = events
        self.start_count = 0
        self.stop_count = 0

    def start(self):
        self.start_count += 1
        self.events.append(f"start:{self.name}")

    def stop(self):
        self.stop_count += 1
        self.events.append(f"stop:{self.name}")


def _fake_pollers(events: list[str]) -> tuple[FakePoller, ...]:
    return tuple(FakePoller(name, events) for name in POLLER_NAMES)


class RuntimePollerTests(unittest.TestCase):
    def test_importing_runtime_pollers_does_not_import_main(self):
        runtime_pollers = _load_runtime_pollers()

        self.assertNotIn("main", sys.modules)
        self.assertTrue(hasattr(runtime_pollers, "start_background_pollers"))
        self.assertTrue(hasattr(runtime_pollers, "stop_background_pollers"))

    def test_disabled_policy_does_not_start_pollers(self):
        runtime_pollers = _load_runtime_pollers()
        events: list[str] = []
        logs: list[str] = []

        result = runtime_pollers.start_background_pollers(
            pollers=_fake_pollers(events),
            recover_storyboard2_video_polling=lambda: events.append("recover"),
            should_enable_pollers=lambda: False,
            print_fn=logs.append,
        )

        self.assertFalse(result)
        self.assertEqual(events, [])
        self.assertTrue(any("disabled" in item for item in logs))

    def test_force_start_ignores_disabled_policy_and_preserves_order(self):
        runtime_pollers = _load_runtime_pollers()
        events: list[str] = []

        result = runtime_pollers.start_background_pollers(
            pollers=_fake_pollers(events),
            recover_storyboard2_video_polling=lambda: events.append("recover"),
            force=True,
            should_enable_pollers=lambda: False,
            print_fn=lambda _: None,
        )

        self.assertTrue(result)
        self.assertEqual(
            events,
            [*(f"start:{name}" for name in POLLER_NAMES), "recover"],
        )

    def test_start_is_idempotent_and_recovery_runs_once(self):
        runtime_pollers = _load_runtime_pollers()
        events: list[str] = []
        pollers = _fake_pollers(events)

        first = runtime_pollers.start_background_pollers(
            pollers=pollers,
            recover_storyboard2_video_polling=lambda: events.append("recover"),
            should_enable_pollers=lambda: True,
            print_fn=lambda _: None,
        )
        second = runtime_pollers.start_background_pollers(
            pollers=pollers,
            recover_storyboard2_video_polling=lambda: events.append("recover"),
            should_enable_pollers=lambda: True,
            print_fn=lambda _: None,
        )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(
            events,
            [*(f"start:{name}" for name in POLLER_NAMES), "recover"],
        )
        self.assertTrue(all(poller.start_count == 1 for poller in pollers))

    def test_stop_is_idempotent_and_preserves_order(self):
        runtime_pollers = _load_runtime_pollers()
        events: list[str] = []
        pollers = _fake_pollers(events)

        runtime_pollers.stop_background_pollers(pollers=pollers)
        runtime_pollers.start_background_pollers(
            pollers=pollers,
            recover_storyboard2_video_polling=lambda: events.append("recover"),
            should_enable_pollers=lambda: True,
            print_fn=lambda _: None,
        )
        runtime_pollers.stop_background_pollers(pollers=pollers)
        runtime_pollers.stop_background_pollers(pollers=pollers)

        self.assertEqual(
            events,
            [
                *(f"start:{name}" for name in POLLER_NAMES),
                "recover",
                *(f"stop:{name}" for name in POLLER_NAMES),
            ],
        )
        self.assertTrue(all(poller.stop_count == 1 for poller in pollers))


if __name__ == "__main__":
    unittest.main()
