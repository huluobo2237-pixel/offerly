"""Actual Browser Use/CDP integration with a local form and unpaid scripted LLM."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["BROWSER_USE_VERSION_CHECK"] = "false"

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import ImageStat
from websockets.asyncio.client import connect
from frontend.backend import make_app
from frontend.workspace import Attachment, Workspace, WorkspaceRecord
from frontend.phone import PhoneHub
from aiortc import (
    RTCPeerConnection,
    RTCConfiguration,
    RTCSessionDescription,
    RTCRtpReceiver,
)
from offerly.browser_connection import (
    UserBrowserConnection,
    browser_executable,
    native_endpoint,
)
from offerly.services import ApplicationServices
from offerly.tata_program import TataProgram
from browser_use.llm.deepseek.chat import ChatDeepSeek
from browser_use.llm.views import ChatInvokeCompletion


class FixtureLauncher:
    """Only the extension action is simulated; Browser Use and the form remain real."""

    async def open(self, connection, target, url):
        return None


class FixtureModel(ChatDeepSeek):
    def __init__(self, owner):
        super().__init__(model="offline-test-model", api_key="not-a-real-key")
        self.owner, self.step = owner, 0
        self._verified_api_keys = True


class AutonomousDeepSeek(FixtureModel):
    async def ainvoke(self, messages, output_format=None, **kwargs):
        system = next(message.text for message in messages if message.role == "system")
        assert system.startswith("你是 Offerly 网申全流程 Agent")
        assert self.owner.agent.settings.extend_system_message is None
        assert self.owner.agent.settings.override_system_message is None
        self.owner.request_count += 1
        step, self.step = self.step, self.step + 1
        if step == 0:
            action = {"read_candidate_profile": {}}
        elif step == 1 and self.owner.task_options.source == "search":
            action = {"search_jobs": {"keywords": "developer", "limit": 2}}
        elif (
            self.owner.task_options.source == "search"
            and not self.owner.batch_context()["ready"]
        ):
            batch = self.owner.current_batch()
            job = self.owner.fixture_jobs[len(batch["jobs"])]
            page = await self.owner.browser.get_current_page()
            if not page or await page.evaluate("() => location.href") != job["url"]:
                action = {"navigate": {"url": job["url"], "new_tab": False}}
            else:
                action = {
                    "confirm_job": {
                        **{k: v for k, v in job.items() if k != "id"},
                        "reason": "Matches the supplied programming experience.",
                    }
                }
        else:
            index = self.owner.job["current"]
            offset = 6 if self.owner.task_options.source == "search" else 1
            phase = (step - offset) % (
                7 if self.owner.task_options.resumePlan == "upload" else 8
            )
            if self.owner.task_options.resumePlan == "upload" and phase >= 3:
                phase += 1
            job = self.owner.fixture_jobs[index]
            if phase == 0:
                action = {"navigate": {"url": job["url"], "new_tab": False}}
            elif phase == 1:
                action = {
                    "choose_job": {
                        **{k: v for k, v in job.items() if k != "id"},
                        "reason": "Matches the supplied programming experience.",
                    }
                }
            elif phase == 2:
                action = {"ask_user": {"reason": "Local fixture login assistance"}}
            elif phase == 3:
                action = {
                    "generate_resume": {
                        "rewrites": {
                            "summary:0": [
                                {
                                    "text": "Python API development experience for this role.",
                                    "sources": [
                                        {
                                            "id": "summary:0",
                                            "quote": "Python developer with API and project engineering experience.",
                                        }
                                    ],
                                }
                            ]
                        },
                        "focus": ["API development"],
                    }
                }
            elif phase == 5:
                page = await self.owner.application_page()
                assert (
                    int(
                        await page.evaluate(
                            '() => document.querySelector("#resume-file").files.length'
                        )
                    )
                    == 1
                )
                action = {"use_tata": {}}
            elif phase in (4, 6):
                nodes = await self.owner.browser.get_selector_map()
                element = "resume-file" if phase == 4 else "final-submit"
                node = next(
                    i for i, n in nodes.items() if n.attributes.get("id") == element
                )
                resume = next(
                    f for f in self.owner.files[index] if f["type"] == "resume"
                )
                action = (
                    {"upload_file": {"index": node, "path": resume["path"]}}
                    if phase == 4
                    else {"click": {"index": node}}
                )
            else:
                action = {
                    "request_review": {
                        "reason": "Check this local form and submit yourself."
                    }
                }
        result = output_format.model_validate(
            {
                "evaluation_previous_goal": "Local verification",
                "memory": "Only local test jobs.",
                "next_goal": "Continue the verified workflow.",
                "action": [action],
            }
        )
        return ChatInvokeCompletion(completion=result, usage=None)


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_led_search_pdf_generation_and_two_job_handoff(self):
        await self.check_workflow()

    async def test_website_targets_with_uploaded_resume_skip_pdf_generation(self):
        await self.check_workflow(upload=True)

    async def test_new_tab_click_reads_new_page_content(self):
        await self.check_workflow(startup_only=True)

    async def check_workflow(self, upload=False, startup_only=False):
        executable = browser_executable("chrome")
        if not executable:
            self.skipTest("System Chrome required for the local CDP integration test")
        with tempfile.TemporaryDirectory(prefix="offerly-agent-check-") as folder:
            root = Path(folder)
            browser_root = root / "browser"
            process = subprocess.Popen(
                [
                    str(executable),
                    "--headless=new",
                    "--remote-debugging-port=0",
                    "--user-data-dir=" + str(browser_root),
                    "--no-first-run",
                    "--disable-background-networking",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            connection = UserBrowserConnection("chrome", browser_root)
            submitted = []

            async def form(request):
                return web.Response(
                    text="""<!doctype html><title>Local recruitment fixture</title><div id="video-marker" style="position:fixed;right:0;top:0;width:160px;height:120px;background:rgb(255,0,0)"></div><h1>Local application form</h1><div><label><input id="privacy-check" type="checkbox" onchange="document.querySelector('#apply-entry').disabled=!this.checked"><span id="privacy-label">我同意隐私政策</span></label><button id="apply-entry" type="button" disabled>投递简历</button></div><form onsubmit="event.preventDefault();fetch('/submitted',{method:'POST'});"><label>Name <input id="name" required></label><label>Email <input id="email" type="email" required></label><label>PDF resume <input id="resume-file" type="file" accept=".pdf" required></label><button id="final-submit">提交申请</button></form><div id="floating-div"><button id="filling" type="button" onclick="this.disabled=true;setTimeout(()=>{document.querySelector('#name').value='Local Tester';document.querySelector('#email').value='local@example.invalid';this.disabled=false},3000)">一键填入</button></div><div style="height:1800px">Scroll fixture</div>""".replace(
                        "<h1>Local application form</h1>",
                        "<h1>New recruitment detail</h1>"
                        if request.match_info["id"] == "popup"
                        else "<h1>Local application form</h1>",
                    ),
                    content_type="text/html",
                )

            async def submit(request):
                submitted.append(True)
                return web.json_response({"ok": True})

            fixture = web.Application()
            fixture.router.add_get("/form/{id}", form)
            fixture.router.add_post("/submitted", submit)
            services = ApplicationServices(root / "data")
            services.browsers.connections["chrome"] = connection
            services.phone = PhoneHub(services, "127.0.0.1", 0)
            services.agent.model_factory = AutonomousDeepSeek
            services.agent.tata_program = TataProgram(FixtureLauncher())
            services.model_store.write(
                {
                    "apiKey": "offline-test-only",
                    "model": "offline-test-model",
                    "baseUrl": "https://example.invalid",
                }
            )
            workspace = Workspace(services.root / "workspace", services.model_store)
            from reportlab.pdfgen.canvas import Canvas

            pdf = io.BytesIO()
            canvas = Canvas(pdf)
            canvas.drawString(
                40,
                750,
                "Computer Science graduate. Python API developer and engineering projects.",
            )
            canvas.save()
            contents = pdf.getvalue()
            attachment = Attachment(
                id="a" * 32,
                type="resume",
                name="测试简历-AI 方向.pdf",
                size=len(contents),
                sha256=hashlib.sha256(contents).hexdigest(),
            )
            workspace.path(attachment).parent.mkdir(parents=True)
            workspace.path(attachment).write_bytes(contents)
            workspace.write(
                WorkspaceRecord(
                    revision=1,
                    resumePlan="template" if not upload else "upload",
                    resumeSections=["summary"],
                    profile={
                        "basic": {"name": "Local Tester"},
                        "contact": {
                            "phone": "00000000000",
                            "email": "local@example.invalid",
                        },
                        "extra": {
                            "evaluation": "Python developer with API and project engineering experience."
                        },
                    },
                    attachments=[attachment],
                )
            )
            try:
                for _ in range(100):
                    if (browser_root / "DevToolsActivePort").is_file():
                        break
                    await asyncio.sleep(0.1)
                await connection.begin()
                for _ in range(100):
                    if connection.status()["connected"]:
                        break
                    await asyncio.sleep(0.1)
                self.assertTrue(connection.status()["connected"])
                original = (await connection.call("Target.getTargets"))["targetInfos"]
                personal = next(t["targetId"] for t in original if t["type"] == "page")
                async with (
                    TestClient(TestServer(fixture)) as site,
                    TestClient(TestServer(make_app(services))) as app,
                    TestClient(TestServer(services.phone.app())) as mobile,
                ):
                    if not upload:
                        # Repeat the startup pattern from the reported failures:
                        # check browser, close the owned tab, then start again.
                        probe = await connection.browser_session()
                        closed = (await probe.get_current_page())._target_id
                        await probe.stop()
                        await connection.call(
                            "Target.closeTarget", {"targetId": closed}
                        )
                        await asyncio.sleep(0.1)
                        connection.owned_targets.add(closed)
                        recovered = await connection.browser_session()
                        self.assertNotEqual(
                            (await recovered.get_current_page())._target_id, closed
                        )
                        self.assertNotIn(closed, connection.owned_targets)
                        await recovered.navigate_to(
                            str(site.make_url("/form/recovery")), new_tab=True
                        )
                        self.assertTrue(
                            (await recovered.get_current_page_url()).endswith(
                                "/form/recovery"
                            )
                        )
                        try:
                            popup_url = str(site.make_url("/form/popup"))
                            opener = await recovered.get_current_page()
                            await opener.evaluate(
                                "(url) => { const a=document.createElement('a'); a.id='popup-link'; a.href=url; a.target='_blank'; a.textContent='Open recruitment'; document.body.prepend(a); return true; }",
                                popup_url,
                            )
                            await recovered.get_browser_state_summary()
                            nodes = await recovered.get_selector_map()
                            index = next(
                                i
                                for i, n in nodes.items()
                                if n.attributes.get("id") == "popup-link"
                            )
                            from browser_use import Tools

                            click_tools = Tools()
                            actions = click_tools.registry.create_action_model()
                            await click_tools.act(
                                actions(click={"index": index}),
                                browser_session=recovered,
                            )
                            await asyncio.sleep(2)
                            native = (await connection.call("Target.getTargets"))[
                                "targetInfos"
                            ]
                            popup = next(
                                t["targetId"]
                                for t in native
                                if t.get("type") == "page" and t.get("url") == popup_url
                            )
                            self.assertIn(popup, connection.owned_targets)
                            self.assertIn(
                                popup,
                                recovered.session_manager._targets,
                                "网页打开了新标签，但 Agent 的 SessionManager 没有接收到它",
                            )
                            self.assertEqual(
                                (await recovered.get_current_page())._target_id,
                                popup,
                                "点击后应切换到新标签，不能继续读取搜索页",
                            )
                            state = await recovered.get_browser_state_summary()
                            self.assertEqual(state.url, popup_url)
                            self.assertIn(
                                "New recruitment detail",
                                state.dom_state.llm_representation(),
                            )
                            self.assertNotIn(
                                "Open recruitment", state.dom_state.llm_representation()
                            )
                            self.assertNotIn(
                                personal, recovered.session_manager._targets
                            )
                        finally:
                            await recovered.stop()
                    if startup_only:
                        return
                    bootstrap = await (await app.get("/api/bootstrap")).json()
                    app.session.headers["X-Offerly-Token"] = bootstrap["token"]
                    self.assertTrue(bootstrap["agentAvailable"])
                    services.phone.port = mobile.port
                    pair = await (await app.post("/api/phone/pair", json={})).json()
                    token = await (
                        await mobile.post(
                            "/phone-api/pair",
                            json={"ticket": urlsplit(pair["url"]).fragment},
                        )
                    ).json()
                    mobile.session.headers["X-Phone-Token"] = token["token"]

                    async def wait(predicate):
                        for _ in range(900):
                            if predicate():
                                return
                            if (
                                not services.agent.job["active"]
                                or (services.agent.job.get("pending") or {}).get("kind")
                                == "blocked"
                            ):
                                self.fail(str(services.agent.public()))
                            await asyncio.sleep(0.1)
                        self.fail(str(services.agent.public()))

                    async def act(action, current, **extra):
                        state = services.agent.public()
                        return await mobile.post(
                            "/phone-api/tasks/" + state["id"] + "/" + action,
                            json={"id": state["id"], "current": current, **extra},
                        )

                    jobs = [
                        {
                            "id": str(i),
                            "title": "Local developer",
                            "company": "Local fixture " + str(i),
                            "url": str(site.make_url("/form/" + str(i))),
                            "jd": "Local workflow test only. Responsibilities include Python API development and reliable project engineering.",
                        }
                        for i in range(2)
                    ]
                    from frontend.job_catalog import CatalogResult, CatalogSummary

                    async def catalog_query(query):
                        return CatalogResult(
                            jobs=jobs, catalog=CatalogSummary(jobs=2, companies=1)
                        )

                    services.job_search.query_catalog = catalog_query
                    services.agent.fixture_jobs = jobs
                    r = await app.post(
                        "/api/agent/tasks",
                        json={
                            "browser": "chrome",
                            "pluginConfirmed": True,
                            "workspaceRevision": 1,
                            "resumePlan": "upload" if upload else "template",
                            "source": "websites" if upload else "search",
                            "count": 2,
                            "requirements": {"keywords": "developer"},
                            "targets": [{"url": j["url"]} for j in jobs]
                            if upload
                            else [],
                        },
                    )
                    self.assertEqual(r.status, 201, await r.text())
                    for index in range(2):
                        await wait(
                            lambda: (
                                services.agent.job["current"] == index
                                and (services.agent.job.get("pending") or {}).get(
                                    "kind"
                                )
                                == "login"
                            )
                        )
                        self.assertEqual((await act("complete", index)).status, 400)
                        self.assertEqual((await act("resume", index + 1)).status, 409)
                        self.assertEqual(
                            (
                                await act(
                                    "resume", index, feedback="Local login completed"
                                )
                            ).status,
                            200,
                        )
                        await wait(
                            lambda: (
                                services.agent.job["current"] == index
                                and (services.agent.job.get("pending") or {}).get(
                                    "kind"
                                )
                                == "review"
                            )
                        )
                        self.assertEqual(
                            len(submitted), index, "Agent must not submit the form"
                        )
                        self.assertEqual((await act("complete", index)).status, 400)
                        page = await services.agent.browser.get_current_page()
                        if index == 0:
                            from offerly.checkbox import set_checkbox

                            nodes = await services.agent.browser.get_selector_map()
                            checkbox = next(
                                i
                                for i, n in nodes.items()
                                if n.attributes.get("id") == "privacy-check"
                            )
                            first = await set_checkbox(
                                services.agent.browser, checkbox, True
                            )
                            second = await set_checkbox(
                                services.agent.browser, checkbox, True
                            )
                            self.assertTrue(first["checked"])
                            self.assertTrue(first["changed"])
                            self.assertTrue(second["checked"])
                            self.assertFalse(second["changed"])
                            self.assertFalse(
                                next(
                                    b["disabled"]
                                    for b in second["buttons"]
                                    if b["text"] == "投递简历"
                                )
                            )
                        data = json.loads(
                            await page.evaluate(
                                '() => ({name:document.querySelector("#name").value,files:document.querySelector("#resume-file").files.length,fileName:document.querySelector("#resume-file").files[0]?.name})'
                            )
                        )
                        self.assertEqual(data["name"], "Local Tester")
                        self.assertEqual(data["files"], 1)
                        resume = next(
                            f
                            for f in services.agent.files[index]
                            if f["type"] == "resume"
                        )
                        self.assertEqual(data["fileName"], resume["name"])
                        if upload:
                            self.assertEqual(data["fileName"], attachment.name)
                        if index == 0:
                            original_get_page = services.agent.browser.get_current_page
                            lost_focus = True

                            async def recovered_page(browser_instance):
                                nonlocal lost_focus
                                if lost_focus:
                                    lost_focus = False
                                    return None
                                return await original_get_page()

                            with patch.object(
                                type(services.agent.browser),
                                "get_current_page",
                                new=recovered_page,
                            ):
                                recovered = await services.agent.application_page()
                                self.assertEqual(recovered._target_id, page._target_id)
                            peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
                            received = asyncio.get_running_loop().create_future()
                            layout_ready = asyncio.get_running_loop().create_future()
                            acknowledgements = asyncio.Queue()
                            video_colors = asyncio.Queue()

                            @peer.on("track")
                            def track_received(track):
                                async def receive():
                                    try:
                                        while True:
                                            frame = await track.recv()
                                            if not received.done():
                                                received.set_result(
                                                    (frame.width, frame.height)
                                                )
                                            sample = frame.to_image().crop(
                                                (
                                                    frame.width - 35,
                                                    10,
                                                    frame.width - 10,
                                                    25,
                                                )
                                            )
                                            color = ImageStat.Stat(sample).mean
                                            video_colors.put_nowait(
                                                (color[0], color[2])
                                            )
                                    except Exception as exc:
                                        if not received.done():
                                            received.set_exception(exc)

                                asyncio.create_task(receive())

                            transceiver = peer.addTransceiver(
                                "video", direction="recvonly"
                            )
                            codecs = RTCRtpReceiver.getCapabilities("video").codecs
                            transceiver.setCodecPreferences(
                                [c for c in codecs if c.mimeType == "video/H264"]
                                + [c for c in codecs if c.mimeType != "video/H264"]
                            )
                            channel = peer.createDataChannel("input", ordered=True)

                            @channel.on("open")
                            def input_open():
                                channel.send(json.dumps({"kind": "ping", "at": 0}))

                            @channel.on("message")
                            def input_result(raw):
                                data = json.loads(raw)
                                if (
                                    data.get("kind") == "layout"
                                    and not layout_ready.done()
                                ):
                                    layout_ready.set_result(data)
                                if data.get("kind") in ("applied", "error"):
                                    acknowledgements.put_nowait(data)

                            try:
                                offer = await peer.createOffer()
                                await peer.setLocalDescription(offer)
                                state = services.agent.public()
                                r = await mobile.post(
                                    "/phone-api/remote/offer",
                                    json={
                                        "id": state["id"],
                                        "current": index,
                                        "type": "offer",
                                        "sdp": peer.localDescription.sdp,
                                    },
                                )
                                self.assertEqual(r.status, 200, await r.text())
                                answer = await r.json()
                                await peer.setRemoteDescription(
                                    RTCSessionDescription(
                                        sdp=answer["sdp"], type=answer["type"]
                                    )
                                )
                                dimensions = await asyncio.wait_for(received, 20)
                                self.assertGreater(dimensions[0], 0)
                                self.assertGreater(dimensions[1], 0)
                                layout = await asyncio.wait_for(layout_ready, 5)
                                coords = json.loads(
                                    await page.evaluate(
                                        '() => {const r=document.querySelector("#name").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};}'
                                    )
                                )
                                sequence = 0

                                async def send_input(event):
                                    nonlocal sequence
                                    sequence += 1
                                    channel.send(json.dumps({**event, "seq": sequence}))
                                    ack = await asyncio.wait_for(
                                        acknowledgements.get(), 5
                                    )
                                    self.assertEqual(ack.get("kind"), "applied", ack)
                                    self.assertEqual(ack["seq"], sequence)

                                point = {
                                    "nx": coords["x"]
                                    * layout["scale"]
                                    / layout["width"],
                                    "ny": (
                                        coords["y"] * layout["scale"] + layout["top"]
                                    )
                                    / layout["height"],
                                    "epoch": layout["epoch"],
                                }
                                await send_input(
                                    {"kind": "pointer", "phase": "down", **point}
                                )
                                await send_input(
                                    {"kind": "pointer", "phase": "up", **point}
                                )
                                await send_input({"kind": "key", "key": "Control+A"})
                                await send_input(
                                    {"kind": "text", "text": "Remote input checked"}
                                )
                                self.assertEqual(
                                    await page.evaluate(
                                        '() => document.querySelector("#name").value'
                                    ),
                                    "Remote input checked",
                                )
                                await send_input(
                                    {"kind": "scroll", **point, "dx": 0, "dy": 450}
                                )
                                await asyncio.sleep(0.5)
                                self.assertGreater(
                                    int(await page.evaluate("() => window.scrollY")), 0
                                )
                                await page.evaluate(
                                    "() => document.querySelector('#video-marker').style.background='rgb(0,0,255)'"
                                )
                                colors_seen = []

                                async def blue_frame():
                                    while True:
                                        red, blue = await video_colors.get()
                                        colors_seen.append((round(red), round(blue)))
                                        if blue > 170 and red < 70:
                                            return

                                try:
                                    await asyncio.wait_for(blue_frame(), 8)
                                except TimeoutError:
                                    from frontend.remote import decode_frame

                                    track = services.agent.remote.video
                                    image, w, h = decode_frame(track.latest[1])
                                    color = ImageStat.Stat(
                                        image.to_image().crop((w - 35, 10, w - 10, 25))
                                    ).mean
                                    self.fail(
                                        str(
                                            {
                                                "capture": track.frames,
                                                "decoded": track.decoded,
                                                "layout": track.layout,
                                                "sourcePixel": color,
                                                "receivedColors": colors_seen[-8:],
                                            }
                                        )
                                    )

                            finally:
                                await peer.close()
                                await mobile.post("/phone-api/remote/close", json={})

                        await page.evaluate(
                            '() => {document.querySelector("form").requestSubmit();return true;}'
                        )
                        for _ in range(20):
                            if len(submitted) > index:
                                break
                            await asyncio.sleep(0.1)
                        self.assertEqual(len(submitted), index + 1)
                        self.assertEqual(
                            (
                                await act("complete", index, confirmSubmitted=True)
                            ).status,
                            200,
                        )
                    await wait(lambda: not services.agent.job["active"])
                    self.assertEqual(
                        [c["state"] for c in services.agent.job["companies"]],
                        ["USER_CONFIRMED", "USER_CONFIRMED"],
                    )
                    remaining = (await connection.call("Target.getTargets"))[
                        "targetInfos"
                    ]
                    self.assertTrue(any(t["targetId"] == personal for t in remaining))
                    self.assertEqual(services.agent.request_count, 15 if upload else 22)
                    if not upload:
                        self.assertEqual(len(services.resumes.read()), 2)
                        self.assertNotEqual(
                            services.agent.files[0][-1]["path"],
                            services.agent.files[1][-1]["path"],
                        )
                        self.assertTrue(
                            all(
                                c.get("resumeId")
                                for c in services.agent.job["companies"]
                            )
                        )
                    if upload:
                        self.assertEqual(services.resumes.read(), [])
                        self.assertEqual(
                            services.agent.candidate["source"], "resume_attachment"
                        )
                        self.assertIn("Python API", services.agent.candidate["text"])
                    self.assertFalse(
                        any(
                            t["targetId"] == personal
                            for t in (
                                await connection._forward(
                                    {"method": "Target.getTargets"}
                                )
                            )["targetInfos"]
                        )
                    )
                    with self.assertRaises(ValueError):
                        await connection._forward({"method": "Browser.close"})
            finally:
                await services.agent.close()
                try:
                    async with connect(native_endpoint(browser_root)) as ws:
                        await ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                except Exception:
                    pass
                await connection.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                await asyncio.sleep(1)


if __name__ == "__main__":
    unittest.main()
