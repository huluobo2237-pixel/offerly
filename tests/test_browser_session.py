"""State capture does not transfer unused images over the helper connection."""

import unittest
from unittest.mock import AsyncMock, patch

from browser_use import BrowserSession
from offerly.browser_session import ConnectedBrowserSession


class StateCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_dom_capture_preserves_options_without_unused_screenshot(self):
        browser = ConnectedBrowserSession(headless=False, no_viewport=True)
        with patch.object(
            BrowserSession, "get_browser_state_summary", new_callable=AsyncMock
        ) as capture:
            await browser.get_browser_state_summary(
                include_screenshot=True, cached=True, include_recent_events=True
            )
            capture.assert_awaited_once_with(
                include_screenshot=False, cached=True, include_recent_events=True
            )
