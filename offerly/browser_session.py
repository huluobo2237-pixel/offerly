"""Attach to a user's visible browser without resizing it or capturing unused images."""

import logging
import time

from browser_use import BrowserSession

logger = logging.getLogger(__name__)


class ConnectedBrowserSession(BrowserSession):
    async def get_browser_state_summary(
        self, include_screenshot=True, cached=False, include_recent_events=False
    ):
        # The current DeepSeek agent is text-only. Upstream captures an image
        # even with use_vision=False for cloud history; Offerly does not use it.
        # Phone streaming and explicit screenshot tools use separate CDP paths.
        started = time.monotonic()
        state = await super().get_browser_state_summary(
            include_screenshot=False,
            cached=cached,
            include_recent_events=include_recent_events,
        )
        logger.info("Browser state received: elapsed=%.2fs", time.monotonic() - started)
        return state
