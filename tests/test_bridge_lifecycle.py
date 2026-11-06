"""Closing phone/Agent clients must not break the helper's shared CDP reader."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, Mock
from pathlib import Path
import tempfile

from offerly.browser_connection import UserBrowserConnection
from offerly.services import ApplicationServices
from offerly.model import AgentDeepSeek
from browser_use.llm.messages import UserMessage
from browser_use.llm.exceptions import ModelProviderError


class BridgeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disappearing_viewer_does_not_lose_browser_responses(self):
        for page in (True, False):
            with self.subTest(phone_viewer=page):
                connection = UserBrowserConnection("edge")
                connection.state = "connected"
                connection.sessions.add("session")
                departed = Mock(closed=False)
                departed.send_json = AsyncMock(
                    side_effect=ConnectionResetError(
                        "Cannot write to closing transport"
                    )
                )
                if page:
                    connection.page_clients[departed] = "session"
                else:
                    connection.clients.add(departed)
                    connection.client_sessions[departed] = {"session"}
                result = asyncio.get_running_loop().create_future()
                connection.pending[123] = result
                gate = asyncio.Event()

                class Socket:
                    async def __aiter__(self):
                        yield json.dumps(
                            {
                                "method": "Page.screencastFrame",
                                "sessionId": "session",
                                "params": {},
                            }
                        )
                        yield json.dumps({"id": 123, "result": {"alive": True}})
                        await gate.wait()

                connection.socket = Socket()
                reader = asyncio.create_task(connection._read())
                try:
                    self.assertEqual(await asyncio.wait_for(result, 1), {"alive": True})
                    self.assertFalse(reader.done())
                    self.assertEqual(connection.state, "connected")
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

    async def test_disconnected_browser_never_spends_another_model_request(self):
        with tempfile.TemporaryDirectory() as folder:
            owner = ApplicationServices(Path(folder)).agent
            owner.connection = SimpleNamespace(status=lambda: {"connected": False})
            owner.agent = Mock()
            model = owner.make_model({"model": "unchanged", "apiKey": "offline"})
            with patch.object(
                AgentDeepSeek, "ainvoke", new_callable=AsyncMock
            ) as provider:
                for _ in range(2):
                    with self.assertRaisesRegex(ModelProviderError, "浏览器连接已断开"):
                        await model.ainvoke([UserMessage(content="Continue")])
                provider.assert_not_awaited()
                self.assertEqual(owner.request_count, 0)
                owner.agent.stop.assert_called_once()
