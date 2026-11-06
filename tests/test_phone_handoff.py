"""Phone decisions advance the selected job without inventing a submission."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web
from offerly.services import ApplicationServices
from offerly.agent import Reason
from offerly.orchestration import TaskLaunch
from offerly.orchestration import SearchJobs, ChooseJob
from offerly.history import ApplicationHistory
from frontend.phone import PhoneHub
from frontend.wechat import WeChatNotifications
from frontend.backend import make_app
from aiohttp.test_utils import TestClient, TestServer


class Request(dict):
    def __init__(self, action, data):
        super().__init__()
        self.match_info = {"id": "round", "action": action}
        self.data = data
        self.method = "POST"

    async def read(self):
        return json.dumps(self.data).encode()

    async def json(self):
        return self.data


class PhoneDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_human_tools_use_assistance_timeout_without_changing_other_tools(
        self,
    ):
        from unittest.mock import patch
        from browser_use import ActionResult

        tools = self.agent.tools()
        model = tools.registry.create_action_model()

        async def wait_budget(task, timeout):
            return await task

        for name, params in (
            ("ask_user", {"reason": "Login"}),
            ("request_review", {"reason": "Review"}),
            ("read_candidate_profile", {}),
        ):
            action = model.model_validate({name: params})
            with (
                patch.object(
                    tools.registry,
                    "execute_action",
                    new=AsyncMock(return_value=ActionResult(extracted_content="ok")),
                ),
                patch(
                    "browser_use.tools.service.asyncio.wait_for",
                    new=AsyncMock(side_effect=wait_budget),
                ) as waiting,
            ):
                # Execute normally below; this test only captures the outer budget.
                result = await tools.act(action, browser_session=None)
                self.assertEqual(
                    waiting.call_args.kwargs["timeout"],
                    1950 if name != "read_candidate_profile" else 180,
                )
                self.assertEqual(result.extracted_content, "ok")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.services = ApplicationServices(Path(self.temp.name))
        self.agent = self.services.agent
        a = self.agent
        a.connection = SimpleNamespace(agent_mode=True)
        a.task_options = TaskLaunch.model_validate(
            {
                "browser": "edge",
                "pluginConfirmed": True,
                "workspaceRevision": 0,
                "resumePlan": "upload",
                "source": "search",
                "count": 2,
                "requirements": {"keywords": "Python"},
            }
        )
        a.candidate = {}
        a.files = [[], []]
        a.discovered = {}
        a.selected_urls = set()
        a.job = {
            "id": "round",
            "active": True,
            "state": "RUNNING",
            "current": 0,
            "pending": None,
            "companies": [
                {
                    "name": "Company A",
                    "job": {"title": "Developer", "url": "https://a.invalid/job/1"},
                    "target": {},
                    "state": "RUNNING",
                },
                {"name": "Company B", "job": {}, "target": {}, "state": "QUEUED"},
            ],
            "progress": {"timeline": []},
        }

    async def respond(self, action, feedback="", confirm=False):
        a = self.agent
        tool = a.tools().registry.registry.actions["ask_user"].function
        waiting = asyncio.create_task(
            tool(params=Reason(reason="Please check the filled form"))
        )
        await asyncio.sleep(0)
        request = Request(
            action, {"current": 0, "feedback": feedback, "confirmSubmitted": confirm}
        )
        response = await a.action(request)
        self.assertEqual(response.status, 200)
        output = await waiting
        self.assertEqual(a.job["current"], 1)
        self.assertTrue(a.connection.agent_mode)
        self.assertFalse(output.is_done)
        return a.application_history.records()[0]

    async def test_abandon_during_information_assistance_advances_without_submission(
        self,
    ):
        record = await self.respond("skip")
        self.assertEqual(record["state"], "abandoned")
        self.assertEqual(self.agent.job["companies"][0]["state"], "SKIPPED")
        self.assertIsNone(
            self.agent.application_history.find_duplicate(
                "Company A", "https://a.invalid/job/1"
            )
        )

    async def test_explicit_continue_next_reply_is_skip_not_completed(self):
        record = await self.respond("resume", "没事继续投下一个")
        self.assertEqual(record["state"], "abandoned")

    async def test_user_can_confirm_completed_job_from_any_assistance(self):
        record = await self.respond("complete", confirm=True)
        self.assertEqual(record["state"], "user_confirmed")
        history = ApplicationHistory(self.services.history_path)
        self.assertIsNotNone(
            history.find_duplicate("Company A", "https://a.invalid/job/another")
        )
        self.assertIsNotNone(
            history.find_duplicate(
                "Different display name",
                "https://a.invalid/job/1?fromPage=job&userId=123",
            )
        )
        history.record_outcome(
            "Company A", "Developer", "https://a.invalid/job/1", "incomplete"
        )
        self.assertEqual(history.records()[0]["state"], "user_confirmed")

    async def test_confirmation_requires_explicit_flag_and_real_selected_job(self):
        a = self.agent
        a.job.update(state="WAITING_HUMAN", pending={"kind": "login"})
        for selected, confirm in [(True, False), (False, True)]:
            if not selected:
                a.job["companies"][0]["job"] = {}
            with self.assertRaises(web.HTTPBadRequest):
                await a.action(
                    Request("complete", {"current": 0, "confirmSubmitted": confirm})
                )

    async def test_skip_rejects_stale_job_and_running_stage(self):
        a = self.agent
        for current in [1, 0]:
            with self.assertRaises(web.HTTPConflict):
                await a.action(Request("skip", {"current": current}))

    async def test_search_excludes_abandoned_jobs_and_other_jobs_at_processed_company(
        self,
    ):
        a = self.agent
        a.job["companies"][0]["state"] = "SKIPPED"
        a.job["current"] = 1
        self.services.job_search = SimpleNamespace(
            key=lambda: "",
            query_catalog=AsyncMock(
                return_value=SimpleNamespace(
                    jobs=[
                        {
                            "company": "Company A",
                            "title": "Developer",
                            "url": "https://a.invalid/job/1?fromPage=job",
                        },
                        {
                            "company": "Company A",
                            "title": "Another job",
                            "url": "https://a.invalid/job/2",
                        },
                        {
                            "company": "Company C",
                            "title": "Fresh job",
                            "url": "https://c.invalid/job/3",
                        },
                    ]
                )
            ),
        )
        result = await a.search_positions(SearchJobs(keywords="Python"))
        self.assertEqual([j["company"] for j in result["jobs"]], ["Company C"])
        self.assertEqual(result["total"], 1)

    async def test_blacklist_filters_search_and_rejects_registration(self):
        from unittest.mock import patch
        from frontend.job_discovery import WebHit
        from offerly.orchestration import ChooseJob

        a = self.agent
        a.task_options.requirements.companyBlacklist = "Excluded Corp"
        self.services.job_search = SimpleNamespace(
            key=lambda: "test-only",
            query_catalog=AsyncMock(
                return_value=SimpleNamespace(
                    jobs=[
                        {
                            "company": "Excluded Corp Ltd",
                            "title": "Developer",
                            "url": "https://excluded.invalid/job",
                        },
                        {
                            "company": "Allowed",
                            "title": "Developer",
                            "url": "https://allowed.invalid/job",
                        },
                    ]
                )
            ),
        )
        with patch(
            "frontend.job_discovery.WebDiscovery.search",
            new=AsyncMock(
                return_value=[
                    WebHit(
                        title="Excluded Corp hiring",
                        url="https://excluded.invalid",
                        snippet="Jobs",
                    ),
                    WebHit(
                        title="Allowed hiring",
                        url="https://allowed.invalid",
                        snippet="Jobs",
                    ),
                ]
            ),
        ):
            result = await a.search_positions(SearchJobs(keywords="Python"))
        self.assertEqual([j["company"] for j in result["jobs"]], ["Allowed"])
        self.assertEqual(len(result["leads"]), 1)
        self.assertIn("discovery", a.job)
        with self.assertRaisesRegex(ValueError, "黑名单"):
            await a.choose_position(
                ChooseJob(
                    company="Excluded Corp",
                    title="Developer",
                    url="https://excluded.invalid/job",
                    jd="Test",
                    reason="Test",
                )
            )

    async def test_batch_overfetches_distinct_employers_and_filters_brand_before_return(
        self,
    ):
        a = self.agent
        a.task_options.count = 5
        a.task_options.requirements.companyBlacklist = "阿里，哔哩哔哩"
        jobs = (
            [
                {
                    "company": "高德",
                    "title": "Engineer",
                    "url": "https://talent.amap.com/job/1",
                },
                {
                    "company": "未知名称",
                    "title": "Engineer",
                    "url": "https://jobs.bilibili.com/job/1",
                },
            ]
            + [
                {
                    "company": "Same employer",
                    "title": "Engineer",
                    "url": f"https://same.invalid/{i}",
                }
                for i in range(9)
            ]
            + [
                {
                    "company": f"Employer {i}",
                    "title": "Engineer",
                    "url": f"https://company{i}.invalid/job",
                }
                for i in range(20)
            ]
        )
        self.services.job_search = SimpleNamespace(
            key=lambda: "",
            query_catalog=AsyncMock(return_value=SimpleNamespace(jobs=jobs)),
        )
        result = await a.search_positions(SearchJobs(keywords="AI", limit=5))
        self.assertEqual(result["requested"], 15)
        self.assertEqual(len(result["jobs"]), 15)
        names = [j["company"] for j in result["jobs"]]
        self.assertEqual(len(set(names)), 15)
        self.assertNotIn("高德", names)
        self.assertNotIn("未知名称", names)

    async def test_known_blacklist_url_is_blocked_before_browser_navigation(self):
        a = self.agent
        a.task_options.requirements.companyBlacklist = "阿里"
        tools = a.tools()
        action = tools.registry.create_action_model().model_validate(
            {"navigate": {"url": "https://talent.amap.com/campus"}}
        )
        result = await tools.act(action, browser_session=None)
        self.assertIn("打开前拦截", result.error)

    async def test_interruption_explains_actual_model_error_and_missing_resume(self):
        a = self.agent
        a.task_options.resumePlan = "template"
        a.last_model_error = "模型动作格式错误：action.generate_resume.rewrites"
        history = SimpleNamespace(final_result=lambda: "未能生成简历，未提交申请")
        reason = a.interruption_reason(history)
        self.assertIn("模型动作格式错误", reason)
        self.assertIn("专属 PDF 简历尚未生成", reason)
        self.assertNotIn("请查看当前页面", reason)

    async def test_verified_catalog_jd_is_reused_without_repeating_full_text(self):
        a = self.agent
        a.task_options.source = "companies"
        url = "https://a.invalid/job/1"
        jd = "Full job responsibilities and Python API requirements. " * 100
        a.job["companies"][0]["job"] = {}
        a.discovered[url] = {"company": "Company A", "title": "Developer", "jd": jd}
        a.application_page = AsyncMock(
            return_value=SimpleNamespace(evaluate=AsyncMock(return_value=url))
        )
        result = await a.choose_position(
            ChooseJob(
                company="Company A",
                title="Developer",
                url=url,
                reason="Matches Python experience.",
            )
        )
        self.assertEqual(a.job["companies"][0]["job"]["jd"], jd.strip())
        self.assertTrue(result["jdStored"])
        self.assertNotIn("jd", result["job"])

    async def test_missing_jd_cannot_reuse_another_company_profile(self):
        a = self.agent
        a.task_options.source = "companies"
        url = "https://a.invalid/job/1"
        a.job["companies"][0]["job"] = {}
        a.discovered[url] = {
            "company": "Different company",
            "title": "Developer",
            "jd": "Not this job.",
        }
        a.application_page = AsyncMock(
            return_value=SimpleNamespace(evaluate=AsyncMock(return_value=url))
        )
        with self.assertRaisesRegex(ValueError, "没有可复用的 JD"):
            await a.choose_position(
                ChooseJob(
                    company="Company A",
                    title="Developer",
                    url=url,
                    reason="Matches Python experience.",
                )
            )

    async def test_phone_preview_requires_pairing_and_is_scoped_to_round(self):
        import time, hashlib, io
        from pypdf import PdfWriter

        make_app(self.services)
        service = self.services.resumes
        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.write(output)
        content = output.getvalue()
        record = {
            "id": "a" * 32,
            "name": "测试-开发岗位.pdf",
            "fileName": "测试-开发岗位.pdf",
            "pages": 1,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        service.path(record).parent.mkdir(parents=True, exist_ok=True)
        service.path(record).write_bytes(content)
        service.write([record])
        self.agent.job["companies"][0]["resumeId"] = record["id"]
        hub = PhoneHub(self.services, "127.0.0.1", 0)
        async with TestClient(TestServer(hub.app())) as client:
            hub.port = client.port
            url = "/phone-api/resumes/" + record["id"]
            self.assertEqual((await client.get(url)).status, 403)
            hub.session = "test-phone-token"
            hub.session_until = time.monotonic() + 60
            client.session.headers["X-Phone-Token"] = hub.session
            response = await client.get(url)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.read(), content)
            status = await (await client.get("/phone-api/status")).json()
            self.assertEqual(
                status["task"]["application"]["resume"]["name"], record["name"]
            )
            self.assertNotIn("path", status["task"]["application"]["resume"])
            image = await client.get(url + "/pages/1")
            self.assertEqual(image.status, 200)
            self.assertEqual(image.headers["Content-Type"], "image/png")
            self.assertTrue((await image.read()).startswith(b"\x89PNG"))
            self.assertEqual((await client.get(url + "/pages/2")).status, 404)
            self.assertEqual(
                (await client.get("/phone-api/resumes/" + "b" * 32)).status, 404
            )

    async def test_desktop_wechat_setup_uses_authenticated_api_without_phone_pairing(
        self,
    ):
        hub = PhoneHub(self.services, "127.0.0.1", 9190)
        hub.public_origin = "https://phone.example.invalid"
        self.services.phone = hub
        hub.wechat = WeChatNotifications()

        async def accepted(scope, origin):
            hub.wechat.test_accepted = True
            return True

        hub.wechat.test = AsyncMock(side_effect=accepted)
        async with TestClient(TestServer(make_app(self.services))) as client:
            response = await client.post(
                "/api/phone/wechat/setup-test", json={"token": "offlineTestToken12345"}
            )
            self.assertEqual(response.status, 403)
            client.session.headers["X-Offerly-Token"] = self.services.token
            response = await client.post(
                "/api/phone/wechat/setup-test", json={"token": "offlineTestToken12345"}
            )
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertTrue(data["enabled"])
            self.assertTrue(data["accepted"])
            self.assertNotIn("offlineTestToken12345", json.dumps(data))
            self.assertIsNone(hub.session)
            response = await client.post(
                "/api/phone/wechat/bind", json={"token": "otherUserTestToken67890"}
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(hub.wechat.token, "otherUserTestToken67890")
            self.assertTrue((await response.json())["enabled"])

            response = await client.post(
                "/api/phone/wechat/setup-test", json={"token": ""}
            )
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["enabled"])
            response = await client.post(
                "/api/phone/wechat/setup-test", json={"token": 123}
            )
            self.assertEqual(response.status, 400)
