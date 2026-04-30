"""Video model pricing poller - fetches prices daily at 00:00 Beijing time."""
import time
import requests
from threading import Thread
from datetime import datetime, timezone, timedelta
from database import SessionLocal
import models
from video_api_config import get_video_api_headers, get_video_models_url

# Beijing timezone offset: UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))


class ModelPricingPoller:
    """Polls video model pricing from mocatter.cn API every day at 00:00 Beijing time."""

    def __init__(self):
        self.running = False
        self.thread = None
        self._last_update_date = None  # Track last update date (Beijing time) to avoid duplicate

    def start(self):
        """Start the pricing poller thread."""
        if self.running:
            return

        self.running = True
        self.thread = Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("[pricing] model pricing poller started")

    def stop(self):
        """Stop the pricing poller thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[pricing] model pricing poller stopped")

    def _poll_loop(self):
        """Main polling loop. Fetches immediately on startup, then daily at 00:00 Beijing time."""
        # Immediate fetch on startup
        try:
            print("[pricing] initial fetch on startup...")
            self._update_pricing()
            now_bj = datetime.now(BEIJING_TZ)
            self._last_update_date = now_bj.date()
        except Exception as e:
            print(f"[pricing] initial fetch failed: {e}")

        while self.running:
            try:
                now_bj = datetime.now(BEIJING_TZ)
                today = now_bj.date()

                # Trigger at hour 0 if we haven't updated today yet
                if now_bj.hour == 0 and self._last_update_date != today:
                    print(f"[pricing] daily update at {now_bj.strftime('%Y-%m-%d %H:%M:%S')} Beijing time")
                    self._update_pricing()
                    self._last_update_date = today
            except Exception as e:
                print(f"[pricing] poll loop error: {e}")

            # Sleep 60 seconds between checks
            time.sleep(60)

    def _update_pricing(self):
        """Fetch pricing from API and update database."""
        try:
            response = requests.get(
                get_video_models_url(),
                headers=get_video_api_headers(),
                timeout=30
            )

            if response.status_code != 200:
                print(f"[pricing] fetch failed with status {response.status_code}")
                return

            data = response.json()
            providers = data.get("providers", [])

            if not providers:
                print("[pricing] no providers in response")
                return

            db = SessionLocal()
            try:
                now = datetime.utcnow()
                count = 0

                # Clear old records
                db.query(models.VideoModelPricing).delete()

                for provider_info in providers:
                    provider_name = provider_info.get("provider", "")
                    models_list = provider_info.get("models", [])

                    for model_info in models_list:
                        model_name = model_info.get("model", "")
                        configurations = model_info.get("configurations", [])

                        for config in configurations:
                            try:
                                duration = int(config.get("duration", 0))
                                aspect_ratio = str(config.get("aspect_ratio", "16:9"))
                                price_yuan = float(config.get("price", 0))

                                pricing = models.VideoModelPricing(
                                    provider=provider_name,
                                    model_name=model_name,
                                    duration=duration,
                                    aspect_ratio=aspect_ratio,
                                    price_yuan=price_yuan,
                                    updated_at=now
                                )
                                db.add(pricing)
                                count += 1
                            except (ValueError, TypeError) as e:
                                print(f"[pricing] skip invalid config: {config}, error: {e}")
                                continue

                db.commit()
                print(f"[pricing] updated {count} pricing records from {len(providers)} providers")

            finally:
                db.close()

        except Exception as e:
            print(f"[pricing] update failed: {e}")


# Create global poller instance
model_pricing_poller = ModelPricingPoller()
