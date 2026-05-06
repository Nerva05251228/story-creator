import os
import time

os.environ["ENABLE_BACKGROUND_POLLER"] = "1"


def main():
    from preflight import run_startup_preflight

    print("[poller-runner] running preflight migrate")
    run_startup_preflight(mode="migrate")

    from main import start_background_pollers, stop_background_pollers

    print("[poller-runner] starting background pollers")
    start_background_pollers(force=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[poller-runner] received interrupt, stopping")
    finally:
        stop_background_pollers()


if __name__ == "__main__":
    main()
