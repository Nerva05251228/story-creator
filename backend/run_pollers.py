import os
import time

from env_config import load_app_env

load_app_env()
os.environ["APP_ROLE"] = "poller"
os.environ["ENABLE_BACKGROUND_POLLER"] = "1"

from main import start_background_pollers, stop_background_pollers


def main():
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
