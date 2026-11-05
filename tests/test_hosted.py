"""Public gateway identity, helper pairing and phone routing without paid APIs."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import DummyCookieJar
from aiohttp.test_utils import TestClient, TestServer

from frontend.hosted import COOKIE, HostedSite
from frontend.server_store import ServerStore


class HostedTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_visitors_public_download_helper_and_phone(self):
        with tempfile.TemporaryDirectory(prefix="offerly-hosted-test-") as folder:
            root = Path(folder)
            ServerStore(root / "model.json").write(
                {
                    "apiKey": "offline-not-real",
                    "model": "offline-model",
                    "baseUrl": "https://api.deepseek.com",
                }
            )
            hosted = HostedSite(root, "https://offerly.test")
            async with TestClient(
                TestServer(hosted.app()), cookie_jar=DummyCookieJar()
            ) as client:
                base = {"Host": "offerly.test", "Origin": "https://offerly.test"}
                self.assertEqual((await client.get("/", headers=base)).status, 200)
                self.assertEqual(
                    (await client.get("/plugin-check", headers=base)).status, 200
                )
                self.assertEqual(
                    (
                        await client.get("/downloads/OfferlyConnect.exe", headers=base)
                    ).status,
                    200,
                )
                self.assertEqual(
                    (
                        await client.get(
                            "/api/bootstrap",
                            headers={**base, "Origin": "https://other.test"},
                        )
                    ).status,
                    403,
                )
                first = await client.get("/api/bootstrap", headers=base)
                one = await first.json()
                cookie_one = COOKIE + "=" + first.cookies[COOKIE].value
                second = await client.get("/api/bootstrap", headers=base)
                two = await second.json()
                cookie_two = COOKIE + "=" + second.cookies[COOKIE].value
                self.assertNotEqual(one["token"], two["token"])
                self.assertTrue(one["browserHelperRequired"])
                self.assertTrue(one["modelConfigured"])
                self.assertNotIn("offline-not-real", str(one))
                h1 = {**base, "Cookie": cookie_one, "X-Offerly-Token": one["token"]}
                h2 = {**base, "Cookie": cookie_two, "X-Offerly-Token": two["token"]}
                mismatch = await client.get(
                    "/api/workspace", headers={**h1, "Cookie": cookie_two}
                )
                self.assertEqual(mismatch.status, 403)
                paired = await (
                    await client.post(
                        "/api/browser-helper/pair",
                        headers=h1,
                        json={"browser": "chrome"},
                    )
                ).json()
                query = parse_qs(urlsplit(paired["launchUrl"]).query)
                self.assertEqual(query["site"], ["https://offerly.test"])
                self.assertEqual(
                    (await (await client.get("/api/browsers", headers=h2)).json())[
                        "chrome"
                    ]["state"],
                    "not_connected",
                )
                socket = await client.ws_connect(
                    "/browser-helper/relay",
                    headers={
                        "Host": "offerly.test",
                        "X-Offerly-Pair-Code": query["code"][0],
                        "X-Offerly-Browser": "chrome",
                    },
                )
                message = await socket.receive_json(timeout=5)
                self.assertEqual(message["method"], "Browser.getVersion")
                await socket.send_json(
                    {
                        "id": message["id"],
                        "result": {
                            "product": "Chrome/test",
                            "userAgent": "Chrome/test",
                            "protocolVersion": "1.3",
                        },
                    }
                )
                for _ in range(30):
                    status = await (
                        await client.get("/api/browsers", headers=h1)
                    ).json()
                    if status["chrome"]["connected"]:
                        break
                    await asyncio.sleep(0.05)
                self.assertTrue(status["chrome"]["connected"])
                self.assertFalse(
                    (await (await client.get("/api/browsers", headers=h2)).json())[
                        "chrome"
                    ]["connected"]
                )
                await client.post(
                    "/api/browser/disconnect", headers=h1, json={"browser": "chrome"}
                )
                await socket.close()
                pair = await (
                    await client.post("/api/phone/pair", headers=h1, json={})
                ).json()
                ticket = urlsplit(pair["url"]).fragment
                exchange = await client.post(
                    "/phone-api/pair", headers=base, json={"ticket": ticket}
                )
                self.assertEqual(exchange.status, 200)
                phone = await exchange.json()
                status = await client.get(
                    "/phone-api/status",
                    headers={**base, "X-Phone-Token": phone["token"]},
                )
                self.assertEqual(status.status, 200)
                self.assertEqual((await status.json())["iceServers"], [])
                self.assertEqual(
                    (await client.get("/phone-api/status", headers=base)).status, 403
                )


if __name__ == "__main__":
    unittest.main()
