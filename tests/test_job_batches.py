"""Batch readiness and public catalog accumulation, without model/search charges."""

import json
import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from frontend.job_catalog import JobCatalog
from frontend.job_discovery import (
    WebDiscovery,
    WebHit,
    DiscoveryResult,
    WebPosting,
    DiscoveryError,
)
from frontend.job_search import JobSearchService, SearchInput
from offerly.orchestration import TaskLaunch, ConfirmJob, ChooseJob, SearchJobs
from offerly.services import ApplicationServices


class JobBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_application_is_replaced_and_remaining_count_is_refilled(
        self,
    ):
        self.configure(3)
        a = self.a
        for i in range(3):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        job = self.job(0)
        self.visit(job["url"])
        await a.choose_position(ChooseJob(**job))
        with patch.object(
            a,
            "application_page",
            AsyncMock(
                return_value=SimpleNamespace(
                    evaluate=AsyncMock(
                        return_value={
                            "url": job["url"],
                            "title": job["title"],
                            "status": "已投递",
                        }
                    )
                )
            ),
        ):
            result = await a.skip_existing_application()
        self.assertIsNotNone(result)
        self.assertEqual(a.job["current"], 0)
        self.assertEqual(len(a.job["companies"]), 3)
        self.assertEqual(a.job["companies"][0]["job"]["url"], self.job(1)["url"])
        self.assertEqual(
            a.application_history.find_duplicate(job["company"], job["url"])["state"],
            "already_applied",
        )
        a.advance_target("USER_CONFIRMED", "Offline user confirmation")
        a.advance_target("USER_CONFIRMED", "Offline user confirmation")
        self.assertEqual(a.job["current"], 2)
        self.assertEqual(a.batch_context()["required"], 1)
        self.assertFalse(a.batch_context()["ready"])
        job = self.job(3)
        self.visit(job["url"])
        await a.confirm_position(ConfirmJob(**job))
        await a.choose_position(ChooseJob(**job))
        self.assertTrue(a.advance_target("USER_CONFIRMED", "Offline user confirmation"))
        self.assertEqual(
            sum(c["state"] == "USER_CONFIRMED" for c in a.job["companies"]), 3
        )

    async def test_existing_application_evidence_must_match_current_job(self):
        self.configure(1)
        a = self.a
        job = self.job(0)
        self.visit(job["url"])
        await a.confirm_position(ConfirmJob(**job))
        await a.choose_position(ChooseJob(**job))
        for evidence in [
            {"url": job["url"], "title": job["title"], "status": ""},
            {"url": self.job(8)["url"], "title": job["title"], "status": "已投递"},
        ]:
            with patch.object(
                a,
                "application_page",
                AsyncMock(
                    return_value=SimpleNamespace(
                        evaluate=AsyncMock(return_value=evidence)
                    )
                ),
            ):
                self.assertIsNone(await a.skip_existing_application())
        self.assertEqual(a.job["companies"][0]["job"]["url"], job["url"])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.services = ApplicationServices(Path(self.temp.name))
        self.services.job_search = JobSearchService(Path(self.temp.name))
        self.addAsyncCleanup(self.services.job_search.close)
        self.a = self.services.agent
        self.configure(7)

    def configure(self, count):
        a = self.a
        a.task_options = TaskLaunch(
            browser="edge",
            pluginConfirmed=True,
            workspaceRevision=0,
            resumePlan="upload",
            source="search",
            count=count,
            requirements={"keywords": "Python"},
        )
        a.job = {
            "id": "batch-test",
            "current": 0,
            "companies": [
                {"name": f"Target {i}", "job": {}, "target": {}, "state": "QUEUED"}
                for i in range(count)
            ],
            "progress": {"timeline": []},
        }
        a.candidate = {}
        a.discovered = {}
        a.selected_urls = set()
        a.files = [[] for _ in range(count)]

    def job(self, index, **extra):
        return dict(
            company=f"Employer {index}",
            title="2027届 Python Engineer",
            url=f"https://employer{index}.invalid/jobs/{index}",
            jd="岗位职责：开发Python API服务与数据处理系统。任职要求：熟悉Python、数据库及软件工程，具备实际项目开发经验。",
            reason="Matches Python experience.",
            **extra,
        )

    def visit(self, url):
        self.a.application_page = AsyncMock(
            return_value=SimpleNamespace(evaluate=AsyncMock(return_value=url))
        )

    async def test_seven_targets_require_five_then_two(self):
        a = self.a
        for i in range(4):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        with self.assertRaisesRegex(ValueError, "当前只有 4 个"):
            await a.choose_position(ChooseJob(**self.job(0)))
        self.assertFalse(a.job["companies"][0]["job"])
        job = self.job(4)
        self.visit(job["url"])
        await a.confirm_position(ConfirmJob(**job))
        self.assertTrue(a.batch_context()["ready"])
        # No further search request is issued in the middle of an active batch.
        self.services.job_search.key = lambda: self.fail("Unexpected Tavily request")
        result = await a.search_positions(SearchJobs(keywords="Python"))
        self.services.job_search.key = lambda: ""
        self.assertTrue(result["batch"]["ready"])
        for i in range(5):
            job = self.job(i)
            self.visit(job["url"])
            job.pop("jd")  # Reuse the verified full JD, not another model transcript.
            await a.choose_position(ChooseJob(**job))
            a.advance_target("SKIPPED", "Offline batch verification")
        self.assertEqual(a.job["current"], 5)
        self.assertEqual(a.batch_context()["required"], 2)
        self.assertFalse(a.batch_context()["ready"])
        for i in (5, 6):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        self.assertTrue(a.batch_context()["ready"])

    async def test_small_rounds_require_exact_requested_count(self):
        for count in (1, 3, 5):
            self.configure(count)
            self.assertEqual(self.a.batch_context()["required"], count)

    async def test_catalog_batch_is_confirmed_without_browser_or_paid_search(self):
        a = self.a
        jobs = [self.job(i) for i in range(5)]
        a.application_page = AsyncMock(
            side_effect=AssertionError("Do not open pages while collecting")
        )
        with (
            patch.object(
                self.services.job_search,
                "query_catalog",
                new=AsyncMock(return_value=SimpleNamespace(jobs=jobs)),
            ),
            patch.object(
                self.services.job_search,
                "key",
                side_effect=AssertionError("Catalog is sufficient"),
            ),
        ):
            result = await a.search_positions(SearchJobs(keywords="Python"))
        self.assertEqual(len(result["jobs"]), 5)
        self.assertTrue(result["catalogChecked"])
        for job in jobs:
            await a.confirm_position(ConfirmJob(**job))
        self.assertTrue(a.batch_context()["ready"])
        a.application_page.assert_not_awaited()

    async def test_search_lead_is_selected_without_claiming_verified_jd(self):
        a = self.a
        job = self.job(0)
        a.job["discovery"] = {
            "leads": [
                {"url": job["url"], "title": job["title"], "summary": "Python role"}
            ]
        }
        a.application_page = AsyncMock(
            side_effect=AssertionError("No browser navigation")
        )
        job.pop("jd")
        result = await a.confirm_position(ConfirmJob(**job))
        self.assertTrue(result["addedToBatch"])
        self.assertFalse(result["catalogStored"])
        self.assertEqual(a.current_batch()["jobs"][0]["jd"], "")

    async def test_third_party_entries_cannot_be_confirmed_or_selected(self):
        for url in (
            "https://www.nowcoder.com/jobs/detail/123",
            "https://www.zhipin.com/job_detail/123",
            "https://career.cuhk.edu.cn/job/view/id/123",
        ):
            job = {**self.job(0), "url": url}
            with self.assertRaisesRegex(ValueError, "企业官网"):
                await self.a.confirm_position(ConfirmJob(**job))
            with self.assertRaisesRegex(ValueError, "企业官网"):
                await self.a.choose_position(ChooseJob(**job))

    async def test_handoff_binds_next_job_and_upload_uses_correct_material(self):
        from browser_use import ActionResult
        from browser_use.tools.service import Tools

        self.configure(3)
        a = self.a
        for i in range(3):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        job = self.job(0)
        self.visit(job["url"])
        await a.choose_position(ChooseJob(**job))
        a.advance_target("USER_CONFIRMED", "User confirmed")
        self.assertEqual(a.job["companies"][1]["job"]["company"], "Employer 1")
        a.files[1] = [
            {
                "type": "resume",
                "name": "candidate-role.pdf",
                "path": "/local/candidate-role.pdf",
            }
        ]
        tools = a.tools()
        action = tools.registry.create_action_model().model_validate(
            {"upload_file": {"index": 5, "path": "/local/candidate-role.pdf"}}
        )
        node = SimpleNamespace(
            attributes={"accept": ".pdf"},
            get_meaningful_text_for_llm=lambda: "上传简历",
        )
        browser = SimpleNamespace(get_selector_map=AsyncMock(return_value={5: node}))
        with patch.object(
            Tools,
            "act",
            new=AsyncMock(
                return_value=ActionResult(extracted_content="file input set")
            ),
        ):
            result = await tools.act(action, browser_session=browser)
        self.assertFalse(result.error)
        self.assertEqual(a.job["uploads"][-1]["file"], "candidate-role.pdf")
        self.assertEqual(a.job["uploads"][-1]["target"], "上传简历")
        self.assertEqual(a.job["uploads"][-1]["state"], "sent")
        a.advance_target("SKIPPED", "User abandoned Employer 1")
        self.assertEqual(a.job["companies"][1]["state"], "SKIPPED")
        self.assertEqual(a.job["companies"][2]["job"]["company"], "Employer 2")
        self.assertEqual(
            next(
                r
                for r in a.application_history.records()
                if r["company"] == "Employer 2"
            )["state"],
            "selected",
        )

    async def test_abandon_repairs_missing_current_job_before_advancing(self):
        self.configure(3)
        a = self.a
        for i in range(3):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        job = self.job(0)
        self.visit(job["url"])
        await a.choose_position(ChooseJob(**job))
        a.job["companies"][0]["state"] = "USER_CONFIRMED"
        a.job["current"] = 1  # Reproduce the previous missing activation at handoff.
        a.advance_target("SKIPPED", "User abandoned")
        self.assertEqual(a.job["companies"][1]["job"]["company"], "Employer 1")
        self.assertEqual(a.job["companies"][1]["state"], "SKIPPED")
        self.assertEqual(a.job["companies"][2]["job"]["company"], "Employer 2")

    async def test_abandon_after_blocked_upload_restarts_loop_at_next_job(self):
        from pydantic import TypeAdapter

        self.configure(3)
        a = self.a
        for i in range(3):
            job = self.job(i)
            self.visit(job["url"])
            await a.confirm_position(ConfirmJob(**job))
        job = self.job(0)
        self.visit(job["url"])
        await a.choose_position(ChooseJob(**job))
        a.advance_target("USER_CONFIRMED", "User submitted first job")
        a.job["companies"][1][
            "job"
        ] = {}  # The missing activation seen in the real log.
        a.job.update(active=True, state="RUNNING", pending=None)
        a.candidate = {"text": "Python API experience 🚀"}
        browser = SimpleNamespace(navigate_to=AsyncMock(), stop=AsyncMock())
        a.connection = SimpleNamespace(
            agent_mode=True, browser_session=AsyncMock(return_value=browser)
        )
        a.make_model = lambda _: None
        tasks = []

        class LocalAgent:
            def __init__(self, **options):
                TypeAdapter(str).validate_python(options["task"]).encode("utf-8")
                tasks.append(json.loads(options["task"].split("\n", 1)[1]))

            async def run(self, **kwargs):
                a.request_count += 1
                if len(tasks) == 2:
                    a.job["companies"][2]["state"] = "USER_CONFIRMED"
                return SimpleNamespace(
                    final_result=lambda: "Local upload blocked", errors=lambda: []
                )

        class Request:
            match_info = {"id": "batch-test", "action": "skip"}

            async def read(self):
                return json.dumps({"current": 1}).encode()

        with patch("browser_use.Agent", LocalAgent):
            running = asyncio.create_task(a.run({}))
            try:
                for _ in range(100):
                    if a.job["state"] == "WAITING_HUMAN":
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(a.job["state"], "WAITING_HUMAN")
                await a.action(Request())
                await asyncio.wait_for(running, 3)
            finally:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
        self.assertEqual(tasks[-1]["current"], 2)
        self.assertEqual(tasks[-1]["selectedJob"]["company"], "Employer 2")
        self.assertEqual(a.job["state"], "FINISHED")

    async def test_nonmatching_blacklisted_job_is_saved_without_joining_batch(self):
        a = self.a
        a.task_options.requirements.companyBlacklist = "Employer 99"
        job = self.job(99)
        self.visit(job["url"])
        result = await a.confirm_position(ConfirmJob(**job, forBatch=False))
        self.assertTrue(result["catalogStored"])
        self.assertEqual(a.batch_context()["confirmed"], 0)
        self.assertEqual(self.services.job_search.catalog.summary().jobs, 1)
        with self.assertRaisesRegex(ValueError, "黑名单"):
            await a.confirm_position(ConfirmJob(**job))

    async def test_tavily_requests_twenty_results_for_one_credit(self):
        sent = []

        class Response:
            status = 200

            async def json(self):
                return {"results": [], "usage": {"credits": 1}}

        class Session:
            @asynccontextmanager
            async def post(self, url, **kwargs):
                sent.append(kwargs["json"])
                yield Response()

        search = WebDiscovery("offline-test")
        await search.search(Session(), "Python campus")
        self.assertEqual(sent[0]["max_results"], 20)
        self.assertEqual(search.result.credits, 1)

    async def test_background_collects_all_valid_jobs_without_another_search(self):
        service = self.services.job_search
        hit = WebHit(
            title="Public career page",
            url="https://example.invalid/jobs",
            snippet="Positions",
        )
        posting = WebPosting(
            id="one",
            title="2027届 Python Engineer",
            company="Other employer",
            url="https://example.invalid/jobs/one",
            description=self.job(1)["jd"],
            nature="campus",
            natureEvidence="官网职责与要求",
        )
        with (
            patch.object(
                WebDiscovery,
                "verify_hits",
                new=AsyncMock(return_value=DiscoveryResult(postings=[posting])),
            ),
            patch.object(
                WebDiscovery,
                "search",
                side_effect=AssertionError("No new search credits"),
            ),
        ):
            await service.remember_search_hits([hit])
            await __import__("asyncio").gather(*service.harvest_tasks)
        self.assertEqual(service.catalog.summary().jobs, 1)
        with service.catalog.connect() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM discovery_leads").fetchone()[0], 1
            )
        # Reopening the database retains the public job.
        reopened = JobCatalog(service.root)
        self.assertEqual(
            len(reopened.search(SearchInput(keywords="Python", category="不限")).jobs),
            1,
        )

    async def test_search_persists_hits_before_applying_user_blacklist(self):
        a = self.a
        a.task_options.requirements.companyBlacklist = "Excluded"
        hit = WebHit(
            title="Excluded hiring",
            url="https://excluded.invalid/jobs/1",
            snippet="Python role",
        )
        allowed = WebHit(
            title="Allowed hiring",
            url="https://allowed.invalid/jobs/2",
            snippet="Python role",
        )
        service = self.services.job_search
        with (
            patch.object(service, "key", return_value="offline-only"),
            patch.object(service, "remember_search_hits", new=AsyncMock()) as remember,
            patch.object(
                WebDiscovery, "search", new=AsyncMock(return_value=[hit, allowed])
            ) as search,
        ):
            result = await a.search_positions(SearchJobs(keywords="Python"))
            self.assertEqual(search.await_count, 1)
            self.assertEqual(len(remember.await_args.args[0]), 2)
        self.assertEqual(len(result["leads"]), 1)
        self.assertIn("allowed.invalid", result["leads"][0]["url"])

    async def test_search_body_alone_is_not_enough_to_mark_a_job_verified(self):
        hit = WebHit(
            title="Python工程师 - Example公司",
            url="https://example.jobs.feishu.cn/campus/position/123/detail",
            rawContent="岗位职责："
            + "开发Python服务。" * 12
            + "任职要求："
            + "掌握Python与数据库。" * 12,
        )
        with patch.object(
            WebDiscovery,
            "fetch_page",
            new=AsyncMock(side_effect=DiscoveryError("Not available")),
        ):
            result = await WebDiscovery("").verify_hits([hit])
        self.assertFalse(result.postings)
        posting = {
            "@type": "JobPosting",
            "title": "2027届 Python工程师",
            "description": self.job(1)["jd"],
            "hiringOrganization": {
                "name": "Example公司",
                "url": "https://example.jobs.feishu.cn",
            },
        }
        html = (
            '<html><title>2027届 Python工程师 - Example公司</title><script type="application/ld+json">'
            + json.dumps(posting)
            + "</script></html>"
        )
        with patch.object(
            WebDiscovery, "fetch_page", new=AsyncMock(return_value=(hit.url, html))
        ):
            result = await WebDiscovery("").verify_hits([hit])
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.searchCalls, 0)

    async def test_tracking_aliases_update_existing_record_and_keep_metadata(self):
        catalog = self.services.job_search.catalog
        job = self.job(0)
        catalog.ingest(
            "collector",
            "Employer 0",
            [
                {
                    "id": "original",
                    "title": job["title"],
                    "url": job["url"] + "?fromPage=job&userId=1",
                    "description": job["jd"],
                    "nature": "campus",
                    "updatedAt": "2026-10-20",
                    "salary": "10-20K",
                }
            ],
        )
        catalog.store_verified(job["company"], job["title"], job["url"], job["jd"])
        self.assertEqual(catalog.summary().jobs, 1)
        with catalog.connect() as db:
            raw = json.loads(db.execute("SELECT record FROM postings").fetchone()[0])[
                "raw"
            ]
        self.assertEqual(raw["salary"], "10-20K")
        self.assertEqual(raw["updatedAt"], "2026-10-20")
