from __future__ import annotations

import logging
import time

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class CallbackClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, payload: dict) -> tuple[bool, str | None]:
        if not self.settings.callback_url:
            return True, None
        headers = {"Content-Type": "application/json"}
        if self.settings.callback_token:
            headers["Authorization"] = f"Bearer {self.settings.callback_token}"
        last_error = None
        for attempt in range(1, self.settings.callback_max_attempts + 1):
            try:
                response = httpx.post(
                    self.settings.callback_url, json=payload, headers=headers,
                    timeout=self.settings.callback_timeout_seconds, trust_env=False,
                )
                response.raise_for_status()
                return True, None
            except Exception as exc:
                last_error = str(exc)[:500]
                logger.warning("Callback attempt %d failed: %s", attempt, last_error)
                if attempt < self.settings.callback_max_attempts:
                    time.sleep(min(2 ** (attempt - 1), 5))
        return False, last_error
