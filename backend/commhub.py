"""CommHub (commhub.cloud) push-notification gateway (Firebase/FCM based).

Open app, no login: each device registers its FCM token and we push directly to
that device's token. Credentials come from the environment only."""
import os
import logging
import httpx

logger = logging.getLogger("auktion.commhub")

API_KEY = os.environ.get("COMMHUB_API_KEY")
APP_ID = os.environ.get("COMMHUB_APP_ID")
BASE_URL = (os.environ.get("COMMHUB_BASE_URL") or "").rstrip("/")
# Environment guard: actual push SENDS only happen where PUSH_SEND_ENABLED is true
# (set it to true in production, leave it off in preview). Token registration and
# web-config still work everywhere so the PWA can be installed/tested in preview.
SEND_ENABLED = (os.environ.get("PUSH_SEND_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return bool(API_KEY and APP_ID and BASE_URL)


def send_enabled() -> bool:
    return enabled() and SEND_ENABLED


def _headers():
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


async def get_web_config() -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{BASE_URL}/api/push/web-config", headers=_headers(),
                        params={"app_id": APP_ID})
        r.raise_for_status()
        return r.json()


async def register_token(device_token: str, user_id: str, platform: str = "web") -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{BASE_URL}/api/push/tokens", headers=_headers(), json={
            "app_id": APP_ID, "user_id": user_id,
            "device_token": device_token, "platform": platform,
        })
        r.raise_for_status()
        return r.json()


async def send_to_token(token: str, title: str, body: str, data: dict | None = None) -> dict:
    if not SEND_ENABLED:
        logger.info("push send disabled (PUSH_SEND_ENABLED not true) — skipping: %s", title)
        return {"skipped": True, "reason": "push_send_disabled"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{BASE_URL}/api/push/send", headers=_headers(), json={
            "app_id": APP_ID, "token": token, "title": title, "body": body,
            "data": {k: str(v) for k, v in (data or {}).items()},
        })
        r.raise_for_status()
        return r.json()
