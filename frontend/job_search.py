"""Bounded, read-only official recruitment search; no LLM or profile data is sent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from .job_catalog import JobCatalog
from .job_discovery import DiscoveryError, DiscoveryLead, WebDiscovery
from .private_store import PrivateStore

RUNNER = Path(__file__).parent / "job-search" / "runner.mjs"
CACHE_SECONDS = 1800
MAX_RESULTS = 200
NATURES = {
    "校园招聘": "campus",
    "实习招聘": "intern",
    "社会招聘": "social",
    "不限": "all",
}


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    keywords: str = Field(min_length=1, max_length=160)
    cities: str = Field(default="", max_length=160)
    category: Literal["校园招聘", "实习招聘", "社会招聘", "不限"] = "校园招聘"
    year: str = Field(default="", pattern=r"^(?:20\d{2})?$")
    notes: str = Field(default="", max_length=1000)
    mode: Literal["catalog", "web", "direct"] = "catalog"


class SearchConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    apiKey: str = Field(min_length=8, max_length=1000, repr=False)


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=200, ge=1, le=500)
    pages: int = Field(default=5, ge=1, le=10)
    searchCredits: int = Field(default=0, ge=0, le=12)


class BuiltinSource(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    name: str
    natures: list[Literal["campus", "intern", "social"]]
    url: str = ""


class SearchJob(BaseModel):
    id: str
    title: str
    company: str
    city: str
    url: str
    jd: str
    source: str
    nature: Literal["campus", "intern", "social"]
    natureLabel: str
    yearLabel: str
    checkedAt: float
    updatedAt: str = ""
    selected: bool = False
    score: int = 0
    searchMode: Literal["catalog", "web", "direct"] = "direct"


class SourceReport(BaseModel):
    source: str
    company: str
    status: Literal["ok", "partial", "failed"]
    warning: str = ""
    found: int = 0
    matched: int = 0


class SearchState(BaseModel):
    engineVersion: int = 2
    id: str
    query: SearchInput
    status: Literal["running", "complete", "partial", "failed", "cancelled"] = "running"
    startedAt: float
    finishedAt: float = 0
    total: int = 0
    done: int = 0
    requests: int = 0
    cacheHit: bool = False
    error: str = ""
    jobs: list[SearchJob] = Field(default_factory=list)
    sources: list[SourceReport] = Field(default_factory=list)
    phase: Literal["discovering", "verifying", "reading", "done"] = "reading"
    searchCalls: int = 0
    searchCredits: int = 0
    discoveredPages: int = 0
    leads: list[DiscoveryLead] = Field(default_factory=list)
    catalog: dict[str, JsonValue] = Field(default_factory=dict)
    closedJobIds: list[str] = Field(default_factory=list)
    batchCollect: bool = False
    skippedSources: int = 0


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def text(value: str) -> str:
    parser = PlainText()
    parser.feed(str(value or ""))
    return re.sub(r"\n[\s\n]*\n", "\n", unescape("".join(parser.parts))).strip()


def search_terms(keywords: str) -> list[str]:
    """Small deterministic synonym expansion, without an inference request."""
    terms = [s.strip() for s in re.split(r"[,，;；、\n]+", keywords) if s.strip()]
    if len(terms) == 1 and re.search(
        r"(?<![A-Za-z])AI(?:GC)?(?![A-Za-z])|人工智能|大模型|agent|LLM", keywords, re.I
    ):
        terms = ["AI", "大模型", "Agent"]
    return list(dict.fromkeys(terms))[:4]


def keyword_matches(term: str, value: str) -> bool:
    if term.casefold() == "ai":
        # Avoid matching common words such as email, detail and retail.
        return bool(re.search(r"(?<![a-z])ai(?:gc)?(?![a-z])|openai", value, re.I))
    return term.casefold() in value.casefold()


def normalize_job(
    raw: dict, source: str, company: str, query: SearchInput, now: float
) -> SearchJob | None:
    title = text(raw.get("title", ""))[:160]
    from offerly.job_links import application_job_url

    url = application_job_url(str(raw.get("url", ""))[:4096])
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (
        not title
        or not raw.get("id")
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    description, requirement = (
        text(raw.get("description", "")),
        text(raw.get("requirement", "")),
    )
    jd = description
    if requirement and requirement != description:
        jd += "\n任职要求\n" + requirement
    jd = jd.strip()[:16000]
    if len(jd) < 40:
        return None
    nature = raw.get("nature")
    if nature not in ("campus", "intern", "social"):
        return None
    evidence = str(raw.get("natureEvidence", ""))
    if re.search(r"实习|\bintern(?:ship)?\b|trainee", title + " " + evidence, re.I):
        nature = "intern"
    elif re.search(r"社招|社会招聘|\bsocial\b", evidence, re.I):
        nature = "social"
    if query.category != "不限" and nature != NATURES[query.category]:
        return None
    years = set(re.findall(r"(20\d{2})\s*届", title))
    if not years:
        years = {"20" + y for y in re.findall(r"(?<!\d)(\d{2})\s*届", title)}
    if not years:
        years = set(re.findall(r"(20\d{2})\s*(?:届|年(?:应届|毕业))", jd))
    # A graduation date window may span two calendar years. Do not reject a cohort inside it.
    windows = re.findall(
        r"(20\d{2})[年./-]\d{1,2}.*?(?:至|到|~|—|–|-).*?(20\d{2})[年./-]\d{1,2}", jd
    )
    if not years and re.search(r"毕业|graduat", jd, re.I):
        for start, end in windows:
            if 0 <= int(end) - int(start) <= 2:
                years.update(str(y) for y in range(int(start), int(end) + 1))
    if query.year and nature != "social" and years and query.year not in years:
        return None
    city = text(raw.get("city", ""))[:160]
    cities = [
        c.strip().removesuffix("市")
        for c in re.split(r"[,，;；、\s]+", query.cities)
        if c.strip()
    ]
    if cities and not any(c.casefold() in city.casefold() for c in cities):
        return None
    terms = search_terms(query.keywords)
    haystack = (title + "\n" + jd).casefold()
    matched = [term for term in terms if keyword_matches(term, haystack)]
    if not matched:
        return None
    fingerprint = hashlib.sha256(
        f"{source}\0{nature}\0{raw['id']}".encode()
    ).hexdigest()[:24]
    return SearchJob(
        id="search_" + fingerprint,
        title=title,
        company=company[:160],
        city=city,
        url=url,
        jd=jd,
        source=source,
        nature=nature,
        natureLabel={"campus": "校招", "intern": "实习", "social": "社招"}[nature],
        yearLabel=" / ".join(sorted(years)) + " 届" if years else "届别未注明",
        checkedAt=now,
        updatedAt=str(raw.get("updatedAt", ""))[:80],
        score=sum(5 if keyword_matches(term, title) else 1 for term in matched)
        + (2 if jd else 0),
    )


class JobSearchService:
    def __init__(self, root: Path):
        self.root = root / "job-search"
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_store = PrivateStore(self.root / "search.dpapi")
        self.catalog = JobCatalog(self.root)
        self.refresh_sources = None
        self.current: SearchState | None = None
        self.task: asyncio.Task | None = None
        self.process = None
        self.harvest_tasks = set()
        self.harvest_gate = asyncio.Semaphore(2)
        try:
            saved = json.loads((self.root / "latest.json").read_text(encoding="utf-8"))
            if "mode" not in saved.get("query", {}):
                saved["query"]["mode"] = "direct"
                saved["engineVersion"] = 1
            self.current = SearchState.model_validate(saved)
            if self.current.status == "running":
                self.current.status = "cancelled"
                self.current.error = "服务已重启，可重新寻找岗位。"
        except (OSError, ValueError):
            pass

    def save(self):
        if self.current:
            tmp = self.root / "latest.tmp"
            tmp.write_text(self.current.model_dump_json(), encoding="utf-8")
            tmp.replace(self.root / "latest.json")

    def routes(self):
        return [
            web.get("/api/jobs/search", self.get),
            web.post("/api/jobs/search", self.start),
            web.post("/api/jobs/search/cancel", self.cancel),
            web.put("/api/jobs/search/config", self.configure),
            web.delete("/api/jobs/search/config", self.remove_config),
            web.post("/api/jobs/collect/refresh", self.refresh),
            web.post("/api/jobs/collect/bulk", self.bulk),
        ]

    def key(self):
        return os.environ.get("OFFERLY_TAVILY_API_KEY") or (
            self.config_store.read() or {}
        ).get("apiKey", "")

    def public_config(self):
        return {
            "provider": "Tavily",
            "configured": bool(self.key()),
            "environment": bool(os.environ.get("OFFERLY_TAVILY_API_KEY")),
        }

    async def configure(self, request):
        try:
            update = SearchConfigUpdate.model_validate_json(await request.read())
            self.config_store.write({"apiKey": update.apiKey})
        except (ValueError, ValidationError, OSError):
            raise web.HTTPBadRequest(
                text="搜索密钥格式不正确或无法保存到本机。"
            ) from None
        return web.json_response(self.public_config())

    async def remove_config(self, request):
        self.config_store.clear()
        return web.json_response(self.public_config())

    async def get(self, request):
        return web.json_response(
            {
                "search": self.current.model_dump() if self.current else None,
                "config": self.public_config(),
                "catalog": self.catalog.summary().model_dump(),
            }
        )

    async def start(self, request):
        try:
            query = SearchInput.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise web.HTTPBadRequest(
                text="请检查岗位关键词、城市和四位毕业年份。"
            ) from None
        if query.mode == "catalog":
            return await self.search_catalog(query)
        if self.task and not self.task.done():
            if self.current.query == query:
                return await self.get(request)
            raise web.HTTPConflict(text="正在寻找岗位，请先停止或等待本次搜索完成。")
        if query.mode == "web" and not self.key():
            raise web.HTTPConflict(
                text="运营者全网采集需要先在管理页配置 Tavily 搜索密钥；普通用户可直接查询共享岗位库。"
            )
        if (
            self.current
            and self.current.engineVersion == 2
            and self.current.status in ("complete", "partial")
            and self.current.query == query
            and time.time() - self.current.finishedAt < CACHE_SECONDS
        ):
            self.current.cacheHit = True
            return await self.get(request)
        if not (
            RUNNER.parent / "node_modules" / "jobhunt-cli" / "package.json"
        ).is_file():
            raise web.HTTPServiceUnavailable(
                text="岗位搜索依赖未安装，请按根目录 README.md 安装岗位采集依赖。"
            )
        self.current = SearchState(
            id=uuid.uuid4().hex,
            query=query,
            startedAt=time.time(),
            phase="discovering" if query.mode == "web" else "reading",
        )
        self.refresh_sources = None
        self.save()
        self.task = asyncio.create_task(self.run())
        return await self.get(request)

    async def query_catalog(self, query):
        """Return catalog data for HTTP callers and Agent tools alike."""
        address = os.environ.get("OFFERLY_CATALOG_URL", "").rstrip("/")
        if address:
            import httpx

            try:
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    response = await client.get(
                        address + "/api/jobs",
                        params=query.model_dump(exclude={"mode", "notes"}),
                    )
                    response.raise_for_status()
                    from .job_catalog import CatalogResult

                    result = CatalogResult.model_validate(response.json())
            except (httpx.HTTPError, ValueError):
                raise web.HTTPServiceUnavailable(
                    text="共享岗位库暂不可用，请稍后重试。"
                ) from None
        else:
            result = await asyncio.to_thread(self.catalog.search, query)
        return result

    async def remember_search_hits(self, hits):
        """Save every lead, then verify/collect in the background without more credits."""
        self.catalog.remember_leads(hits)
        if hits:
            task = asyncio.create_task(self.harvest_hits(hits))
            self.harvest_tasks.add(task)
            task.add_done_callback(self.harvest_done)

    def harvest_done(self, task):
        self.harvest_tasks.discard(task)
        if not task.cancelled() and task.exception():
            logging.getLogger(__name__).warning(
                "岗位线索核实未完成，线索已保留：%s", type(task.exception()).__name__
            )

    async def harvest_hits(self, hits):
        async with self.harvest_gate:
            try:
                found = await WebDiscovery("").verify_hits(hits)
                self.catalog.remember(found.sources)
                for job in found.postings:
                    self.catalog.store_verified(
                        job.company,
                        job.title,
                        job.url,
                        job.description + "\n" + job.requirement,
                        job.city,
                        job.nature,
                        job.updatedAt,
                    )
            except (
                DiscoveryError,
                aiohttp.ClientError,
                TimeoutError,
                ValueError,
                OSError,
            ):
                # Leads remain persisted for a later attempt; they never become fake jobs.
                return

    async def search_catalog(self, query):
        result = await self.query_catalog(query)
        state = SearchState(
            id=uuid.uuid4().hex,
            query=query,
            status="complete",
            phase="done",
            startedAt=time.time(),
            finishedAt=time.time(),
            jobs=[SearchJob.model_validate(j) for j in result.jobs],
            catalog=result.catalog.model_dump(),
            closedJobIds=result.closedJobIds,
            cacheHit=True,
        )
        return web.json_response(
            {"search": state.model_dump(), "config": self.public_config()}
        )

    async def refresh(self, request):
        if self.task and not self.task.done():
            raise web.HTTPConflict(text="管理端已有采集正在运行。")
        sources = self.catalog.sources()
        if not sources:
            raise web.HTTPConflict(
                text="还没有已发现的企业入口，请先在管理端采集新企业。"
            )
        try:
            query = SearchInput.model_validate(await request.json())
        except ValueError:
            raise web.HTTPBadRequest(text="岗位偏好不正确。") from None
        query.mode = "direct"
        sources = [
            s
            for s in sources
            if query.category == "不限" or s.nature == NATURES[query.category]
        ]
        self.current = SearchState(
            id=uuid.uuid4().hex,
            query=query,
            startedAt=time.time(),
            batchCollect=True,
            total=len(sources),
        )
        self.refresh_sources = sources
        self.save()
        self.task = asyncio.create_task(self.run())
        return await self.get(request)

    def node(self):
        return (
            os.environ.get("OFFERLY_NODE")
            or shutil.which("node")
            or (
                "D:/nodejs/node.exe" if Path("D:/nodejs/node.exe").is_file() else "node"
            )
        )

    async def builtin_sources(self) -> list[BuiltinSource]:
        process = await asyncio.create_subprocess_exec(
            self.node(),
            str(RUNNER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            async with asyncio.timeout(15):
                output, _ = await process.communicate(b'{"action":"sources"}')
            if process.returncode:
                raise ValueError("source inventory unavailable")
            return [
                BuiltinSource.model_validate(s) for s in json.loads(output)["sources"]
            ]
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def bulk(self, request):
        """Owner-only broad collection; directory and official requests use no search credits."""
        if self.task and not self.task.done():
            raise web.HTTPConflict(text="管理端已有采集正在运行。")
        try:
            options = CollectionInput.model_validate(await request.json())
        except ValueError:
            raise web.HTTPBadRequest(text="批量采集范围不正确。") from None
        if options.searchCredits and not self.key():
            raise web.HTTPConflict(
                text="分行业发现新企业需要先配置管理端 Tavily 密钥。"
            )
        if not (RUNNER.parent / "node_modules/jobhunt-cli/package.json").is_file():
            raise web.HTTPServiceUnavailable(text="请先安装官网采集依赖。")
        self.current = SearchState(
            id=uuid.uuid4().hex,
            query=SearchInput(keywords="工程师,产品,营销,财务", mode="direct"),
            startedAt=time.time(),
            phase="discovering",
            batchCollect=True,
        )
        self.save()
        self.task = asyncio.create_task(self.run_bulk(options))
        return await self.get(request)

    async def scan_sources(self, payload: dict):
        """Persist each completed source immediately, including partial pages."""
        state = self.current
        self.process = await asyncio.create_subprocess_exec(
            self.node(),
            str(RUNNER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False).encode())
            await self.process.stdin.drain()
            self.process.stdin.close()
            async with asyncio.timeout(
                90 if state.query.mode == "web" or state.batchCollect else 150
            ):
                while line := await self.process.stdout.readline():
                    event = json.loads(line)
                    if event.get("type") == "start" and not state.batchCollect:
                        state.total += event["total"]
                    elif event.get("type") == "source":
                        state.closedJobIds.extend(
                            self.catalog.ingest(
                                event["source"],
                                event["company"],
                                event["jobs"],
                                event.get("closedIds", []),
                            )
                        )
                        self.catalog.record_scan(event["source"])
                        report = SourceReport(
                            source=event["source"],
                            company=event["company"],
                            status=event["status"],
                            warning=event.get("warning", ""),
                            found=len(event["jobs"]),
                        )
                        existing = {j.url for j in state.jobs}
                        for raw in event["jobs"]:
                            job = normalize_job(
                                raw,
                                report.source,
                                report.company,
                                state.query,
                                time.time(),
                            )
                            if job and job.url not in existing:
                                job.searchMode = state.query.mode
                                report.matched += 1
                                existing.add(job.url)
                                state.jobs.append(job)
                        state.jobs.sort(key=lambda j: (-j.score, j.company, j.title))
                        state.jobs = state.jobs[:MAX_RESULTS]
                        state.sources.append(report)
                        state.done += 1
                        state.catalog = self.catalog.summary().model_dump()
                    elif event.get("type") == "done":
                        state.requests += event["requests"]
                    self.save()
                if await self.process.wait():
                    state.error = "部分官网查询中断，已保留取得的岗位。"
        finally:
            if self.process and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            self.process = None

    async def run_bulk(self, options: CollectionInput):
        from .job_discovery import portal_from_url
        from .job_sources import discover_portals, portal_key, read_directories

        state = self.current
        try:
            native = [s for s in await self.builtin_sources() if "campus" in s.natures]
            directory = await read_directories(cache_root=self.root)
            if options.searchCredits:

                async def progress(calls, credits, pages):
                    state.searchCalls = calls
                    state.searchCredits = credits
                    state.discoveredPages = pages
                    self.save()

                found = await discover_portals(
                    self.key(), options.searchCredits, progress, cache_root=self.root
                )
                state.searchCalls = found.searchCalls
                state.searchCredits = found.credits
                state.discoveredPages = len(found.hits)
                directory.sources.extend(found.sources)
                directory.warnings.extend(found.warnings)
            # Preserve saved identities when a directory uses another spelling of the same URL.
            dynamic = {
                portal_key(s): s for s in self.catalog.sources() if s.nature == "campus"
            }
            for source in directory.sources:
                dynamic.setdefault(portal_key(source), source)
            native_keys = {
                portal_key(s)
                for item in native
                if (s := portal_from_url(item.url, item.name))
            }
            extra = [
                s
                for key, s in dynamic.items()
                if key not in native_keys
                and not any(
                    n.name in s.name or s.name in n.name
                    for n in native
                    if len(n.name) > 1 and not s.name.startswith("企业（")
                )
            ]
            extra = extra[: max(0, options.limit - len(native))]
            native = native[: options.limit]
            self.catalog.remember(extra)
            planned = [s.id for s in native] + [s.id for s in extra]
            recent = self.catalog.recent_sources()
            ids = [source for source in planned if source not in recent]
            state.skippedSources = len(planned) - len(ids)
            state.total = len(ids)
            state.phase = "reading"
            state.error = " ".join(directory.warnings)
            self.save()
            for offset in range(0, len(ids), 6):
                selected = ids[offset : offset + 6]
                await self.scan_sources(
                    {
                        "action": "search",
                        "nature": "campus",
                        "terms": [""],
                        "collectAll": True,
                        "pages": options.pages,
                        "sources": selected,
                        "extraSources": [
                            s.model_dump() for s in extra if s.id in selected
                        ],
                    }
                )
            healthy = bool(state.skippedSources) or any(
                s.status in ("ok", "partial") for s in state.sources
            )
            state.status = (
                (
                    "partial"
                    if state.error
                    or state.done < state.total
                    or any(s.status != "ok" for s in state.sources)
                    else "complete"
                )
                if healthy
                else "failed"
            )
        except asyncio.CancelledError:
            state.status = "cancelled"
        except (OSError, ValueError, TimeoutError):
            state.status = "partial" if state.done else "failed"
            state.error = "批量采集中断，已入库的岗位已保留，可重新刷新。"
        finally:
            state.phase = "done"
            state.finishedAt = time.time()
            state.catalog = self.catalog.summary().model_dump()
            self.save()

    async def run(self):
        state = self.current
        discovery = None
        try:
            payload = {
                "action": "search",
                "nature": NATURES[state.query.category],
                "terms": search_terms(state.query.keywords),
            }
            if self.refresh_sources is not None:
                payload["extraSources"] = [s.model_dump() for s in self.refresh_sources]
                payload["sources"] = [s.id for s in self.refresh_sources]
            if state.query.mode == "web":
                discovery = WebDiscovery(self.key())

                async def progress(phase, calls, found):
                    state.phase, state.searchCalls, state.discoveredPages = (
                        phase,
                        calls,
                        found,
                    )
                    self.save()

                async with asyncio.timeout(90):
                    found = await discovery.run(state.query, progress)
                state.searchCalls, state.searchCredits = (
                    found.searchCalls,
                    found.credits,
                )
                state.requests = found.searchCalls + found.pageRequests
                state.leads = found.leads
                state.error = " ".join(found.warnings)
                self.catalog.remember(found.sources)
                for posting in found.postings:
                    self.catalog.ingest(
                        "web_structured", posting.company, [posting.model_dump()]
                    )
                    job = normalize_job(
                        posting.model_dump(),
                        "web_structured",
                        posting.company,
                        state.query,
                        time.time(),
                    )
                    if job:
                        job.searchMode = "web"
                        state.jobs.append(job)
                if found.postings:
                    state.sources.append(
                        SourceReport(
                            source="web_structured",
                            company="官网招聘页面",
                            status="ok",
                            found=len(found.postings),
                            matched=len(state.jobs),
                        )
                    )
                    state.done = state.total = 1
                # The web path visits only companies discovered in this search. It never silently scans the fixed list.
                payload["extraSources"] = [s.model_dump() for s in found.sources]
                payload["sources"] = [s.id for s in found.sources]
                state.phase = "reading"
                self.save()
            if self.refresh_sources is not None:
                payload.update(collectAll=True, pages=5)
                for offset in range(0, len(self.refresh_sources), 6):
                    group = self.refresh_sources[offset : offset + 6]
                    await self.scan_sources(
                        {
                            **payload,
                            "sources": [s.id for s in group],
                            "extraSources": [s.model_dump() for s in group],
                        }
                    )
            else:
                await self.scan_sources(payload)
            healthy = sum(s.status in ("ok", "partial") for s in state.sources)
            state.status = (
                (
                    "partial"
                    if state.error
                    or state.done < state.total
                    or any(s.status != "ok" for s in state.sources)
                    else "complete"
                )
                if healthy
                else "partial"
                if state.leads
                else "failed"
            )
            if not healthy:
                state.error = state.error or (
                    "已发现招聘线索，尚未取得符合条件的完整岗位 JD。"
                    if state.leads
                    else "本次未能取得官网岗位，请展开来源状态查看；稍后可重试。"
                )
        except DiscoveryError as error:
            state.status = "partial" if state.jobs else "failed"
            state.error = str(error)
        except asyncio.CancelledError:
            state.status = "cancelled"
        except (OSError, ValueError, TimeoutError):
            state.status = "partial" if state.jobs else "failed"
            state.error = "查询超时或接口发生变化，已保留取得的岗位。"
        finally:
            if self.process and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
            self.process = None
            if discovery:
                state.searchCalls, state.searchCredits = (
                    discovery.result.searchCalls,
                    discovery.result.credits,
                )
            state.phase = "done"
            state.finishedAt = time.time()
            state.catalog = self.catalog.summary().model_dump()
            self.save()

    async def cancel(self, request):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                self.current.status = "cancelled"
                self.current.finishedAt = time.time()
                self.save()
        return await self.get(request)

    async def close(self, app=None):
        await self.cancel(None)
        for task in tuple(self.harvest_tasks):
            task.cancel()
        await asyncio.gather(*self.harvest_tasks, return_exceptions=True)
