"""Current-tab Tata invocation checks; no paid model calls or real UI clicks."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from offerly.tata_program import TataProgram, TataError, CDPTataLauncher, PANEL, FILLED


class Page:
    _target_id = "owned"

    def __init__(self):
        self.url = "https://example.invalid/application"
        self.clicks = 0
        self.ready = True
        self.visible = True
        self.uncertain = False

    async def evaluate(self, script, *args):
        if script == "() => location.href":
            return self.url
        if script == "() => document.visibilityState":
            return "visible" if self.visible else "hidden"
        if script == PANEL:
            return json.dumps({"ready": self.ready, "found": self.ready})
        if script == FILLED:
            return "2"
        if script.startswith("(expected)"):
            self.clicks += 1
            if self.uncertain:
                raise ConnectionError("Click response was lost")
            return json.dumps({"clicked": True})
        raise AssertionError(script)


class Connection:
    browser = "edge"

    def __init__(self):
        self.owned_targets = {"owned"}
        self.calls = []
        self.changed = False
        self.unsupported = False
        self.tabs = [
            {
                "targetId": "tab-owned",
                "type": "tab",
                "url": "https://example.invalid/application",
                "embedderData": {"tabActive": True},
            }
        ]

    def status(self):
        return {"connected": True}

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "Target.activateTarget":
            return {}
        if method == "Target.getTargets":
            return {"targetInfos": self.tabs}
        if method == "Target.getTargetInfo":
            return {
                "targetInfo": {
                    "targetId": "owned",
                    "url": "changed"
                    if self.changed
                    else "https://example.invalid/application",
                }
            }
        if method == "Extensions.triggerAction":
            if self.unsupported:
                raise RuntimeError("Method unavailable")
            return {}
        raise AssertionError("Unexpected browser action: " + method)


class TataProgramTests(unittest.IsolatedAsyncioTestCase):
    async def test_triggers_tab_target_and_fills_current_page_without_navigation(self):
        connection = Connection()
        page = Page()
        program = TataProgram()
        result = await program.run(connection, page)
        self.assertTrue(result.ok)
        self.assertEqual(result.steps, ["tata", "panel", "fill"])
        action = next(p for m, p in connection.calls if m == "Extensions.triggerAction")
        self.assertEqual(
            action, {"id": "jekomlapnmbblkcfcjkfnagdnnhcnbdo", "targetId": "tab-owned"}
        )
        self.assertEqual(page.clicks, 1)
        again = await program.run(connection, page)
        self.assertFalse(again.ok)
        self.assertEqual(page.clicks, 1)
        self.assertEqual(
            sum(m == "Extensions.triggerAction" for m, p in connection.calls), 1
        )

    async def test_dry_run_does_not_fill(self):
        page = Page()
        result = await TataProgram().run(Connection(), page, dry_run=True)
        self.assertTrue(result.ok)
        self.assertFalse(result.fill_attempted)
        self.assertEqual(page.clicks, 0)

    async def test_unowned_page_never_triggers(self):
        page = Page()
        page._target_id = "personal"
        connection = Connection()
        result = await TataProgram().run(connection, page)
        self.assertFalse(result.ok)
        self.assertEqual(connection.calls, [])

    async def test_duplicate_urls_use_only_active_tab(self):
        connection = Connection()
        other = dict(
            connection.tabs[0], targetId="inactive", embedderData={"tabActive": False}
        )
        connection.tabs.append(other)
        await CDPTataLauncher().open(connection, "owned", Page().url)
        self.assertEqual(connection.calls[-1][1]["targetId"], "tab-owned")

    async def test_ambiguous_active_tabs_stop_without_triggering(self):
        connection = Connection()
        connection.tabs.append(dict(connection.tabs[0], targetId="other"))
        with self.assertRaises(TataError):
            await CDPTataLauncher().open(connection, "owned", Page().url)
        self.assertFalse(
            any(m == "Extensions.triggerAction" for m, p in connection.calls)
        )

    async def test_page_navigation_stops_before_trigger(self):
        connection = Connection()
        connection.changed = True
        result = await TataProgram().run(connection, Page())
        self.assertFalse(result.ok)
        self.assertFalse(
            any(m == "Extensions.triggerAction" for m, p in connection.calls)
        )

    async def test_unsupported_browser_does_not_fall_back_to_mouse(self):
        connection = Connection()
        connection.unsupported = True
        page = Page()
        result = await TataProgram().run(connection, page)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "tata")
        self.assertEqual(page.clicks, 0)

    async def test_tab_switch_stops_before_fill(self):
        page = Page()
        page.visible = False
        result = await TataProgram().run(Connection(), page)
        self.assertFalse(result.ok)
        self.assertEqual(page.clicks, 0)

    async def test_missing_fill_button_stops(self):
        page = Page()
        page.ready = False
        clock = iter([0, 0, 30])
        fake_time = SimpleNamespace(monotonic=lambda: next(clock))
        with patch("offerly.tata_program.time", fake_time):
            result = await TataProgram().run(Connection(), page)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "panel")
        self.assertEqual(page.clicks, 0)

    async def test_uncertain_click_is_not_repeated(self):
        page = Page()
        page.uncertain = True
        program = TataProgram()
        result = await program.run(Connection(), page)
        self.assertFalse(result.ok)
        self.assertTrue(result.fill_attempted)
        await program.run(Connection(), page)
        self.assertEqual(page.clicks, 1)

    async def test_cancellation_prevents_fill(self):
        started = asyncio.Event()

        class BlockingLauncher:
            async def open(self, *args):
                started.set()
                await asyncio.Future()

        page = Page()
        task = asyncio.create_task(
            TataProgram(BlockingLauncher()).run(Connection(), page)
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(page.clicks, 0)


class BrowserConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_target_is_removed_with_its_sessions(self):
        from offerly.browser_connection import UserBrowserConnection

        connection = UserBrowserConnection("edge")
        connection.owned_targets.add("closed")
        connection.sessions.add("session-closed")
        connection.session_targets["session-closed"] = "closed"

        class Socket:
            async def __aiter__(self):
                yield json.dumps(
                    {
                        "method": "Target.targetDestroyed",
                        "params": {"targetId": "closed"},
                    }
                )

        connection.socket = Socket()
        await connection._read()
        self.assertNotIn("closed", connection.owned_targets)
        self.assertNotIn("session-closed", connection.sessions)

    async def test_reconciliation_does_not_treat_stale_or_personal_tabs_as_owned_pages(
        self,
    ):
        from offerly.browser_connection import UserBrowserConnection
        from unittest.mock import AsyncMock

        connection = UserBrowserConnection("edge")
        connection.owned_targets.update(["gone", "frame"])
        connection.call = AsyncMock(
            return_value={
                "targetInfos": [
                    {
                        "targetId": "personal",
                        "type": "page",
                        "url": "https://example.invalid",
                    },
                    {
                        "targetId": "frame",
                        "type": "iframe",
                        "url": "https://example.invalid/frame",
                    },
                ]
            }
        )
        self.assertEqual(await connection.reconcile_targets(), [])
        self.assertNotIn("gone", connection.owned_targets)
        self.assertNotIn("personal", connection.owned_targets)

    async def test_screencast_ack_frame_token_is_not_a_target_session_id(self):
        from offerly.browser_connection import UserBrowserConnection
        from unittest.mock import AsyncMock

        connection = UserBrowserConnection("edge")
        connection.sessions.add("owned-session")
        connection.call = AsyncMock(return_value={})
        await connection._forward(
            {
                "method": "Page.screencastFrameAck",
                "params": {"sessionId": 7},
                "sessionId": "owned-session",
            }
        )
        connection.call.assert_awaited_once_with(
            "Page.screencastFrameAck", {"sessionId": 7}, "owned-session"
        )
        with self.assertRaises(ValueError):
            await connection._forward(
                {
                    "method": "Target.detachFromTarget",
                    "params": {"sessionId": "personal-session"},
                    "sessionId": "owned-session",
                }
            )
        with self.assertRaises(ValueError):
            await connection._forward(
                {
                    "method": "Page.screencastFrameAck",
                    "params": {"sessionId": 7},
                    "sessionId": "personal-session",
                }
            )

    async def test_extension_popup_does_not_steal_owned_application_focus(self):
        from offerly.browser_connection import UserBrowserConnection

        connection = UserBrowserConnection("edge")
        connection.owned_targets.add("owned")

        class Socket:
            async def __aiter__(self):
                for target, url in [
                    ("popup", "chrome-extension://tata/popup.html"),
                    ("application", "https://example.invalid/apply"),
                ]:
                    yield json.dumps(
                        {
                            "method": "Target.targetCreated",
                            "params": {
                                "targetInfo": {
                                    "targetId": target,
                                    "url": url,
                                    "openerId": "owned",
                                }
                            },
                        }
                    )

        connection.socket = Socket()
        await connection._read()
        self.assertNotIn("popup", connection.owned_targets)
        self.assertIn("application", connection.owned_targets)


if __name__ == "__main__":
    unittest.main()
