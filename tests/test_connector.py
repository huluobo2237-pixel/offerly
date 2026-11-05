"""Real native helper relay, file transport and single-use pairing regression."""

import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from aiohttp.test_utils import TestClient, TestServer
from frontend.backend import make_app
from offerly.browser_connection import browser_executable, native_endpoint
from offerly.services import ApplicationServices


class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_executable_remote_browser_upload_and_reconnect(self):
        executable = browser_executable("chrome")
        helper = (
            Path(__file__).resolve().parents[1]
            / "frontend/public/downloads/OfferlyConnect.exe"
        )
        if os.name != "nt" or not executable or not helper.is_file():
            self.skipTest("Windows, Chrome and built native helper required")
        with tempfile.TemporaryDirectory(prefix="offerly-helper-test-") as folder:
            root = Path(folder)
            profile = root / "browser"
            process = subprocess.Popen(
                [
                    str(executable),
                    "--headless=new",
                    "--remote-debugging-port=0",
                    "--user-data-dir=" + str(profile),
                    "--no-first-run",
                    "--disable-background-networking",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            helpers = []
            manager = ApplicationServices(root / "data")
            try:
                for _ in range(150):
                    if (profile / "DevToolsActivePort").is_file():
                        break
                    await asyncio.sleep(0.1)
                endpoint = native_endpoint(profile)
                async with TestClient(TestServer(make_app(manager))) as client:
                    headers = {"X-Offerly-Token": manager.token}
                    download = await client.get("/downloads/OfferlyConnect.exe")
                    self.assertEqual(download.status, 200)
                    self.assertTrue((await download.read()).startswith(b"MZ"))
                    response = await client.post(
                        "/api/browser-helper/pair",
                        json={"browser": "chrome"},
                        headers=headers,
                    )
                    paired = await response.json()
                    self.assertEqual(response.status, 200)
                    self.assertNotIn("127.0.0.1:9222", paired["launchUrl"])
                    code = parse_qs(urlsplit(paired["launchUrl"]).query)["code"][0]
                    native = subprocess.Popen(
                        [str(helper), "--verify", paired["launchUrl"], endpoint],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    helpers.append(native)
                    connection = manager.browsers.connections["chrome"]
                    for _ in range(150):
                        if connection.status()["connected"]:
                            break
                        await asyncio.sleep(0.1)
                    self.assertTrue(connection.status()["connected"], connection.error)
                    # Reusing the consumed pairing code never obtains a WebSocket.
                    replay = await client.get(
                        "/browser-helper/relay",
                        headers={
                            "X-Offerly-Pair-Code": code,
                            "X-Offerly-Browser": "chrome",
                        },
                    )
                    self.assertEqual(replay.status, 403)
                    target = await connection.create_tab(
                        "data:text/html,<title>Helper check</title><input type=file id=upload><input id=name>"
                    )
                    session = (
                        await connection.call(
                            "Target.attachToTarget",
                            {"targetId": target, "flatten": True},
                        )
                    )["sessionId"]
                    await connection.call(
                        "Runtime.evaluate",
                        {
                            "expression": "document.querySelector('#name').value='Relay works'"
                        },
                        session,
                    )
                    document = await connection.call("DOM.getDocument", {}, session)
                    node = (
                        await connection.call(
                            "DOM.querySelector",
                            {
                                "nodeId": document["root"]["nodeId"],
                                "selector": "#upload",
                            },
                            session,
                        )
                    )["nodeId"]
                    resume = manager.root / "姓名-软件开发.pdf"
                    content = b"%PDF-1.4\nlocal helper upload verification"
                    resume.write_bytes(content)
                    await connection.call(
                        "DOM.setFileInputFiles",
                        {"nodeId": node, "files": [str(resume)]},
                        session,
                    )
                    result = await connection.call(
                        "Runtime.evaluate",
                        {
                            "expression": "(async()=>{const f=document.querySelector('#upload').files[0];return {name:f.name,text:await f.text(),value:document.querySelector('#name').value}})()",
                            "awaitPromise": True,
                            "returnByValue": True,
                        },
                        session,
                    )
                    value = result["result"]["value"]
                    self.assertEqual(value["name"], resume.name)
                    self.assertEqual(value["text"], content.decode())
                    self.assertEqual(value["value"], "Relay works")
                    staged = next(iter(connection.staged.values()))
                    self.assertTrue(Path(staged).is_file())
                    outside = root / "not-authorized.pdf"
                    outside.write_bytes(content)
                    with self.assertRaises(ValueError):
                        await connection.call(
                            "DOM.setFileInputFiles",
                            {"nodeId": node, "files": [str(outside)]},
                            session,
                        )
                    self.assertEqual(
                        (
                            await client.post(
                                "/api/browser/disconnect",
                                json={"browser": "chrome"},
                                headers=headers,
                            )
                        ).status,
                        200,
                    )
                    for _ in range(100):
                        if not Path(staged).exists():
                            break
                        await asyncio.sleep(0.1)
                    self.assertFalse(
                        Path(staged).exists(),
                        "Helper session files must be cleaned after disconnect",
                    )
                    self.assertFalse(connection.status()["connected"])
                    # A fresh request works after explicit disconnect.
                    paired = await (
                        await client.post(
                            "/api/browser-helper/pair",
                            json={"browser": "chrome"},
                            headers=headers,
                        )
                    ).json()
                    native = subprocess.Popen(
                        [str(helper), "--verify", paired["launchUrl"], endpoint],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    helpers.append(native)
                    for _ in range(150):
                        if connection.status()["connected"]:
                            break
                        await asyncio.sleep(0.1)
                    self.assertTrue(connection.status()["connected"], connection.error)
                    self.assertEqual(
                        (await connection.call("Browser.getVersion"))[
                            "protocolVersion"
                        ],
                        "1.3",
                    )
                    await manager.browsers.disconnect("chrome")
            finally:
                await manager.close()
                for native in helpers:
                    if native.poll() is None:
                        native.kill()
                    native.wait(timeout=5)
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=15)
                # Chromium may briefly hold its test profile after parent exit.
                await asyncio.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
