"""Agent-led job discovery and per-job preparation, sharing existing application services."""

from __future__ import annotations
import asyncio
import hashlib
import copy
import json
import logging
import re
import secrets
import tempfile
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit
from aiohttp import web
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)
from .browser_connection import BrowserName
from .company_policy import blacklist_match, normalize_company
from .resume import (
    build_document,
    GenerateResumes,
    ResumeJob,
    TailoredBullet,
    validate_plan,
    TailoringPlan,
)


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    keywords: str = Field(default="", max_length=160)
    cities: str = Field(default="", max_length=160)
    companyBlacklist: str = Field(default="", max_length=6000)
    category: Literal["校园招聘", "实习招聘", "社会招聘", "不限"] = "校园招聘"
    year: str = Field(default="", pattern=r"^(?:20\d{2})?$")
    notes: str = Field(default="", max_length=1000)
    salary: str = Field(default="不限", max_length=100)
    experience: str = Field(default="不限", max_length=100)
    education: str = Field(default="不限", max_length=100)
    industry: str = Field(default="", max_length=160)
    companySize: str = Field(default="不限", max_length=100)
    published: str = Field(default="不限", max_length=100)

    @field_validator("category", mode="before")
    @classmethod
    def blank_category(cls, value):
        return value or "不限"


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(default="", max_length=160)
    url: str = Field(default="", max_length=4096)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("请提供有效企业官网。")
        return value


class TaskLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser: BrowserName
    pluginConfirmed: Literal[True]
    workspaceRevision: int = Field(ge=0)
    resumePlan: Literal["upload", "template"]
    source: Literal["search", "companies", "websites"]
    count: int = Field(ge=1, le=200)
    requirements: Requirements
    targets: list[Target] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_source(self):
        if self.source == "search":
            if not self.requirements.keywords or self.targets:
                raise ValueError("自动找岗需要岗位方向，不应包含企业清单。")
        else:
            if not self.targets or self.count != len(self.targets):
                raise ValueError("目标数量应与企业清单一致。")
            values = [
                t.url or t.company if self.source == "companies" else t.url
                for t in self.targets
            ]
            if any(not value for value in values) or len(set(values)) != len(values):
                raise ValueError("企业清单为空或有重复项。")
            if self.source == "companies" and any(
                bool(t.url) == bool(t.company) for t in self.targets
            ):
                raise ValueError("每行填写一个企业名称或投递链接。")
            if self.source == "websites" and any(t.company for t in self.targets):
                raise ValueError("请提供企业链接。")
        return self


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchJobs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keywords: str = Field(
        min_length=1, max_length=160, description="结合用户方向与简历提炼的检索词"
    )
    offset: int = Field(default=0, ge=0, le=199)
    limit: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="候选池数量，不是投递数量；程序至少准备本批目标数的三倍候选并优先不同企业。",
    )


class ChooseJob(Target):
    title: str = Field(min_length=1, max_length=160)
    company: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=4096)
    city: str = Field(default="", max_length=160)
    jd: str = Field(
        default="",
        max_length=16000,
        description="岗位职责和要求。已从 search_jobs 获得并核对一致的 JD 可留空，程序复用已保存全文，避免重复抄写；直接官网选岗时必须提供。",
    )
    reason: str = Field(
        min_length=1, max_length=1200, description="结合简历选择本岗的理由及偏好差异"
    )


class ConfirmJob(ChooseJob):
    forBatch: bool = Field(
        default=True,
        description="是否加入本批投递；不符合当前偏好但有效的岗位设为false，只存入公共岗位库。",
    )


class GenerateResume(TailoringPlan):
    """The Agent supplies JD-specific wording in its current decision."""


def resume_action_schema(record):
    document = build_document(record.profile)
    entry_ids = tuple(
        item.id
        for section in record.resumeSections
        for item in document.modules.get(section, ())
    )
    if not entry_ids:
        return GenerateResume
    entry_id = Literal[entry_ids]
    bullets = Annotated[list[TailoredBullet], Field(min_length=1, max_length=8)]
    return create_model(
        "GenerateResume",
        __base__=GenerateResume,
        rewrites=(
            dict[entry_id, bullets],
            Field(
                min_length=1,
                description=(
                    "键必须逐字使用以下条目 ID，不能使用模块名或条目标题："
                    + "、".join(entry_ids)
                    + "。只传需要改写的条目。"
                ),
            ),
        ),
    )


def extract_resume_text(path):
    from pypdf import PdfReader

    from pathlib import Path

    if Path(path).suffix.lower() in (".jpg", ".jpeg", ".png"):
        import subprocess

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path(__file__).with_name("image_resume_ocr.ps1")),
                "-ImagePath",
                str(path),
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise ValueError(
                "图片简历文字识别失败，请确认图片清晰，或换用可复制文字的PDF。"
            )
        text = result.stdout.strip()
        page_count = 1
    else:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("简历 PDF 已加密，请上传可读取的 PDF。")
        text = "\n".join(
            page.extract_text() or "" for page in reader.pages[:30]
        ).strip()
        page_count = len(reader.pages)
    # Some PDF font mappings yield an unpaired UTF-16 surrogate for an emoji.
    # Repair extracted text only; the uploaded PDF itself remains untouched.
    text = text.encode("utf-16", errors="surrogatepass").decode(
        "utf-16", errors="replace"
    )
    if len(re.sub(r"\s", "", text)) < 20:
        raise ValueError("简历没有足够可读取文本，请上传可复制文字的 PDF 简历。")
    # Contact and identity numbers are unnecessary for matching positions.
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱省略]", text)
    text = re.sub(r"(?<!\d)(?:1[3-9]\d{9}|\d{17}[\dXx])(?!\d)", "[号码省略]", text)
    return {
        "text": text[:16000],
        "truncated": len(text) > 16000 or page_count > 30,
        "pages": page_count,
    }


class AutonomousWorkflow:
    @property
    def application_history(self):
        from .history import ApplicationHistory

        return ApplicationHistory(self.services.history_path)

    def record_target(self, state, reason=""):
        item = self.job["companies"][self.job["current"]]
        job = item.get("job") or {}
        if job.get("url"):
            self.application_history.record_outcome(
                item["name"], job["title"], job["url"], state, reason
            )

    def prepare_materials(self, revision, plan):
        record = self.services.workspace.read()
        if record.revision != revision or record.resumePlan != plan:
            raise ValueError("资料已变更，请刷新后重新确认。")
        files = []
        for attachment in record.attachments:
            path = self.services.workspace.path(attachment)
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != attachment.sha256
            ):
                raise ValueError("附件已失效，请重新上传。")
            files.append(
                {
                    "type": attachment.type,
                    "name": attachment.name,
                    "label": attachment.label,
                    "path": str(self.services.workspace.upload_path(attachment)),
                }
            )
        if plan == "upload" and sum(f["type"] == "resume" for f in files) != 1:
            raise ValueError("请上传一份 PDF 简历。")
        return record, files

    async def create_task(self, request):
        async with self.action_lock:
            if self.task and not self.task.done():
                raise web.HTTPConflict(text="已有任务正在执行。")
            try:
                options = TaskLaunch.model_validate_json(await request.read())
                connection = self.services.browsers.connections[options.browser]
                if not connection.status()["connected"]:
                    raise ValueError("请先连接浏览器。")
                if (
                    not self.services.phone
                    or not self.services.phone.status()["connected"]
                ):
                    raise ValueError("请先连接手机。")
                model = self.services.model_store.read() or {}
                if not model.get("apiKey") or not model.get("model"):
                    raise ValueError("请保存 AI 服务配置。")
                record, materials = self.prepare_materials(
                    options.workspaceRevision, options.resumePlan
                )
                if options.resumePlan == "template":
                    document = build_document(record.profile)
                    if not any(document.modules[key] for key in record.resumeSections):
                        raise ValueError("请补充所选简历模块的资料。")
            except (ValidationError, ValueError) as exc:
                raise web.HTTPBadRequest(
                    text=str(exc)
                    if not isinstance(exc, ValidationError)
                    else "请检查任务来源、数量、企业清单和岗位条件。"
                ) from None
            self.task_options = options
            self.workspace_record = record
            self.connection = connection
            self.candidate = None
            self.discovered = {}
            self.selected_urls = set()
            self.plugin_url = None
            self.last_model_error = ""
            self.request_count = self.prompt_tokens = self.completion_tokens = 0
            base = [
                f
                for f in materials
                if options.resumePlan == "upload" or f["type"] != "resume"
            ]
            self.files = [copy.deepcopy(base) for _ in range(options.count)]
            companies = []
            for i in range(options.count):
                target = (
                    options.targets[i].model_dump(exclude_defaults=True)
                    if options.targets
                    else {}
                )
                companies.append(
                    {
                        "id": secrets.token_hex(8),
                        "name": target.get("company")
                        or (
                            urlsplit(target["url"]).hostname
                            if target.get("url")
                            else "待寻找岗位 " + str(i + 1)
                        ),
                        "target": target,
                        "job": {},
                        "state": "QUEUED",
                        "reason": "",
                    }
                )
            self.job = {
                "id": secrets.token_hex(12),
                "active": True,
                "workflow": "autonomous",
                "source": options.source,
                "requirements": options.requirements.model_dump(),
                "current": 0,
                "phase": "准备任务",
                "state": "STARTING",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "api_calls": 0,
                "decisions": 0,
                "pending": None,
                "companies": companies,
                "progress": {"activity": "准备任务", "timeline": []},
            }
            self.task = asyncio.create_task(self.run(model))
            return web.json_response(self.public(), status=201)

    def task_context(self, include_candidate=False):
        return {
            "source": self.task_options.source,
            "requirements": self.task_options.requirements.model_dump(),
            "targetCount": len(self.job["companies"]),
            "current": self.job["current"],
            "target": self.job["companies"][self.job["current"]]["target"],
            "selectedJob": self.job["companies"][self.job["current"]]["job"],
            "savedSearch": self.job.get("discovery", {}),
            "completed": sum(
                item["state"] == "USER_CONFIRMED" for item in self.job["companies"]
            ),
            "resumePlan": self.task_options.resumePlan,
            "candidateSource": "resume_attachment"
            if self.task_options.resumePlan == "upload"
            else "profile",
            "candidate": self.candidate
            if include_candidate
            else {"alreadyRead": self.candidate is not None},
            "attachments": self.files[self.job["current"]],
            "batch": self.batch_context(),
        }

    def current_batch(self):
        if self.task_options.source != "search":
            return None
        batch = self.job.get("batch")
        current = self.job["current"]
        if not batch or not batch["start"] <= current < batch["start"] + batch["size"]:
            start = current
            batch = {
                "start": start,
                "size": min(5, len(self.job["companies"]) - start),
                "jobs": [],
            }
            self.job["batch"] = batch
        return batch

    def batch_context(self):
        from .history import canonical_job_url

        batch = self.current_batch()
        if not batch:
            return None
        states = {
            canonical_job_url(c["job"]["url"]): c["state"]
            for c in self.job["companies"]
            if c.get("job")
        }
        return {
            "start": batch["start"],
            "required": batch["size"],
            "confirmed": len(batch["jobs"]),
            "ready": len(batch["jobs"]) >= batch["size"],
            "jobs": [
                {
                    **{k: v for k, v in job.items() if k != "jd"},
                    "state": states.get(canonical_job_url(job["url"]), "CONFIRMED"),
                }
                for job in batch["jobs"]
            ],
        }

    def catalog_job(self, company, title, url, jd, city=""):
        catalog = getattr(getattr(self.services, "job_search", None), "catalog", None)
        return bool(catalog and catalog.store_verified(company, title, url, jd, city))

    async def confirm_position(self, params):
        """Record a real current detail page without selecting an application yet."""
        from .history import canonical_job_url

        page = await self.application_page()
        if await page.evaluate("() => location.href") != params.url:
            raise ValueError(
                "请在真实岗位详情页核实后，用当前网址调用 confirm_job；招聘首页或摘要不能算已确认岗位。"
            )
        jd = params.jd
        if not jd:
            cached = next(
                (
                    j
                    for url, j in self.discovered.items()
                    if canonical_job_url(url) == canonical_job_url(params.url)
                    and j["company"] == params.company
                    and j["title"] == params.title
                ),
                {},
            )
            jd = cached.get("jd", "")
        if len(jd.strip()) < 40:
            raise ValueError(
                "请读取岗位职责和任职要求后提供完整 JD，不能仅凭搜索摘要确认岗位。"
            )
        stored = self.catalog_job(
            params.company, params.title, params.url, jd, params.city
        )
        if not params.forBatch or self.task_options.source != "search":
            return {
                "catalogStored": stored,
                "addedToBatch": False,
                "note": "岗位已按公共信息保存；本轮是否投递另行筛选。",
            }
        if self.candidate is None:
            raise ValueError("先读取个人资料，再判断此岗位是否适合加入本批。")
        if self.blacklisted(params.company, params.url):
            raise ValueError("此有效岗位可以入库，但属于本轮黑名单，不得加入投递批次。")
        key = canonical_job_url(params.url)
        if self.application_history.find_duplicate(params.company, params.url) or any(
            c.get("job")
            and (
                canonical_job_url(c["job"]["url"]) == key
                or normalize_company(c["name"]) == normalize_company(params.company)
            )
            for c in self.job["companies"]
        ):
            raise ValueError("企业或岗位已经处理过，请为本批寻找另一家企业。")
        batch = self.current_batch()
        existing = next(
            (j for j in batch["jobs"] if canonical_job_url(j["url"]) == key), None
        )
        if not existing:
            if len(batch["jobs"]) >= batch["size"]:
                raise ValueError("本批岗位已全部确认，请先完成本批投递。")
            if any(
                normalize_company(j["company"]) == normalize_company(params.company)
                for j in batch["jobs"]
            ):
                raise ValueError("本批已有这家企业，请选择其他企业。")
            batch["jobs"].append({**params.model_dump(exclude={"forBatch"}), "jd": jd})
        self.progress(f"本批已确认 {len(batch['jobs'])} / {batch['size']} 个真实岗位")
        return {
            "catalogStored": stored,
            "addedToBatch": True,
            "batch": self.batch_context(),
            "next": "本批已齐，依次打开已确认链接并 choose_job 开始投递。"
            if len(batch["jobs"]) >= batch["size"]
            else "继续核实其他企业岗位并 confirm_job，本批凑齐前不开始申请。",
        }

    async def candidate_profile(self):
        if self.candidate is not None:
            return {
                "alreadyRead": True,
                "message": "资料已经读取，请使用前面的工具结果，不要重复读取。",
            }
        if self.task_options.resumePlan == "upload":
            attachment = next(f for f in self.files[0] if f["type"] == "resume")
            data = await asyncio.to_thread(extract_resume_text, attachment["path"])
            self.candidate = {"source": "resume_attachment", **data}
        else:
            document = build_document(self.workspace_record.profile)
            data = {
                "source": "profile",
                "modules": {
                    k: [item.model_dump() for item in rows]
                    for k, rows in document.modules.items()
                    if rows and k in self.workspace_record.resumeSections
                },
            }
            if len(json.dumps(data, ensure_ascii=False)) > 24000:
                raise ValueError("简历资料过长，请精简后重新启动。")
            self.candidate = data
        self.progress("已读取选岗资料")
        return self.candidate

    async def search_positions(self, params):
        from frontend.job_search import SearchInput

        batch = self.current_batch()
        if batch and len(batch["jobs"]) >= batch["size"]:
            return {
                "batch": self.batch_context(),
                "note": "本批已确认足够岗位，先完成本批投递；这一批结束后再搜索。",
            }
        query = SearchInput(
            keywords=params.keywords,
            cities=self.task_options.requirements.cities,
            category=self.task_options.requirements.category,
            year=self.task_options.requirements.year,
            mode="catalog",
        )
        # One Agent call collects a batch; the model need not request each job.
        from frontend.job_discovery import (
            WebDiscovery,
            DiscoveryError,
            SEARCH_RESULTS_PER_REQUEST,
        )
        import aiohttp

        leads = []
        warning = ""
        desired = min(
            200,
            max(
                15,
                (batch["size"] if batch else min(5, self.task_options.count)) * 3,
                params.limit or 0,
            ),
        )
        preferences = self.task_options.requirements.model_dump()
        phrase = " ".join(
            str(v)
            for k, v in preferences.items()
            if v and v != "不限" and k not in ("companyBlacklist", "keywords")
        )
        try:
            key = self.services.job_search.key()
            if not key:
                raise DiscoveryError("尚未配置全网搜索服务，请使用浏览器搜索。")
            searcher = WebDiscovery(key)
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), trust_env=False
            ) as session:
                seen_links = set()
                gate = asyncio.Semaphore(4)

                async def search_batch(batch):
                    async with gate:
                        suffixes = [
                            "招聘官网",
                            "职位详情",
                            "人才招聘",
                            "校园招聘岗位",
                            "企业招聘入口",
                        ]
                        scopes = [
                            "",
                            "site:zhiye.com",
                            "site:feishu.cn",
                            "site:mokahr.com",
                        ]
                        return await searcher.search(
                            session,
                            f"{params.keywords} {phrase} {suffixes[batch % 5]} {scopes[batch // 5]}",
                        )

                batches = await asyncio.gather(
                    *(
                        search_batch(i)
                        for i in range(
                            max(
                                1,
                                (desired + SEARCH_RESULTS_PER_REQUEST - 1)
                                // SEARCH_RESULTS_PER_REQUEST,
                            )
                        )
                    ),
                    return_exceptions=True,
                )
                all_hits = {
                    h.url: h
                    for hits in batches
                    if not isinstance(hits, Exception)
                    for h in hits
                }
                remember = getattr(
                    self.services.job_search, "remember_search_hits", None
                )
                if remember:
                    await remember(list(all_hits.values()))
                for hits in batches:
                    if isinstance(hits, Exception):
                        warning = "部分搜索未完成，可以继续浏览器搜索或补充检索。"
                        continue
                    for hit in hits:
                        if hit.url in seen_links or self.blacklisted(
                            hit.title, hit.snippet, hit.url
                        ):
                            continue
                        seen_links.add(hit.url)
                        leads.append(
                            {
                                "title": hit.title,
                                "url": hit.url,
                                "summary": hit.snippet[:700],
                                "verifiedJob": False,
                            }
                        )
        except (DiscoveryError, aiohttp.ClientError, TimeoutError, ValueError) as exc:
            warning = (
                str(exc)
                if isinstance(exc, DiscoveryError)
                else "搜索服务暂不可用，可使用浏览器搜索。"
            )
        result = await self.services.job_search.query_catalog(query)
        from .history import canonical_job_url

        seen = {
            canonical_job_url(c["job"]["url"])
            for c in self.job["companies"]
            if c.get("job")
        }
        seen.update(canonical_job_url(j["url"]) for j in (batch or {}).get("jobs", []))
        seen_companies = {
            "".join(c["name"].split()).casefold()
            for c in self.job["companies"]
            if c.get("job")
        }
        seen_companies.update(
            normalize_company(j["company"]) for j in (batch or {}).get("jobs", [])
        )
        eligible = [
            j
            for j in result.jobs
            if not self.blacklisted(j["company"], j["url"], j.get("source", ""))
            and canonical_job_url(j["url"]) not in seen
            and "".join(j["company"].split()).casefold() not in seen_companies
            and not self.application_history.find_duplicate(j["company"], j["url"])
        ]
        # A batch of five jobs at one employer is not five application targets.
        distinct = {}
        for job in eligible:
            distinct.setdefault(normalize_company(job["company"]), job)
        eligible = list(distinct.values())
        jobs = eligible[params.offset : params.offset + desired]
        for job in jobs:
            self.discovered[job["url"]] = job
        self.job["discovery"] = {
            "jobs": jobs,
            "leads": leads,
            "requested": desired,
            "warning": warning,
        }
        self.checkpoint()
        self.progress(f"岗位搜索返回 {len(jobs)} 家企业候选及 {len(leads)} 条招聘线索")
        return {
            "jobs": [
                {
                    k: (v[:2400] if k == "jd" and isinstance(v, str) else v)
                    for k, v in j.items()
                    if k
                    in (
                        "id",
                        "company",
                        "title",
                        "city",
                        "url",
                        "jd",
                        "updatedAt",
                        "natureLabel",
                        "yearLabel",
                    )
                }
                for j in jobs
            ],
            "leads": leads,
            "warning": warning,
            "requested": desired,
            "total": len(eligible),
            "offset": params.offset,
            "hasMore": params.offset + len(jobs) < len(eligible),
            "requirements": self.task_options.requirements.model_dump(),
            "targetCount": self.task_options.count,
            "batch": self.batch_context(),
            "note": "jobs 已在搜索阶段过滤黑名单、重复投递并按企业去重；候选池大于实际投递数。leads 为全网线索，不等于已核实岗位。打开链接前先用已给出的企业名称、标题、摘要和网址排除黑名单，企业身份不明先检索确认。核对岗位要求后依次申请，不足补充检索，不编造。链接和信息已保存。",
        }

    def blacklisted(self, *evidence):
        return bool(
            blacklist_match(self.task_options.requirements.companyBlacklist, *evidence)
        )

    async def choose_position(self, params):
        item = self.job["companies"][self.job["current"]]
        if (
            item.get("job")
            and self.browser is not None
            and self.task_options.source == "search"
        ):
            skipped = await self.skip_existing_application()
            if skipped is not None:
                return {"status": "already_applied", "next": skipped.extracted_content}
        if self.blacklisted(params.company, params.url):
            raise ValueError("该企业在用户黑名单中，请选择其他企业。")
        batch = self.current_batch()
        confirmed = None
        if batch:
            from .history import canonical_job_url

            if len(batch["jobs"]) < batch["size"]:
                raise ValueError(
                    f"本批需先确认 {batch['size']} 个真实岗位，当前只有 {len(batch['jobs'])} 个。先用 confirm_job 核实凑齐，再开始投递。"
                )
            confirmed = next(
                (
                    j
                    for j in batch["jobs"]
                    if canonical_job_url(j["url"]) == canonical_job_url(params.url)
                    and j["company"] == params.company
                    and j["title"] == params.title
                ),
                None,
            )
            if not confirmed:
                raise ValueError("请选择本批已确认列表中的岗位；本批结束后再找下一批。")
        item = self.job["companies"][self.job["current"]]
        if item["job"]:
            if item["job"]["url"] == params.url:
                return {
                    "job": {k: v for k, v in item["job"].items() if k != "jd"},
                    "attachments": self.files[self.job["current"]],
                    "alreadySelected": True,
                    "next": "本岗已登记，继续准备附件或进入申请表，不重复选岗。",
                }
            raise ValueError("本岗已选定，完成或由用户跳过后才能选择下一岗位。")
        if self.candidate is None:
            raise ValueError("请先调用 read_candidate_profile 读取选岗依据。")
        page = await self.application_page()
        if await page.evaluate("() => location.href") != params.url:
            raise ValueError("请先打开真实岗位详情，再用当前网址和 JD 选择岗位。")
        if params.url in self.selected_urls:
            raise ValueError(
                "本轮之前的目标已处理过此岗位；当前目标尚未选择岗位。请调用 search_jobs 选择另一家企业的新岗位，不能对此岗位调用 generate_resume。"
            )
        duplicate = self.application_history.find_duplicate(params.company, params.url)
        if duplicate:
            raise ValueError(
                "该企业或岗位已有投递或待核实记录，不重复申请："
                + json.dumps(duplicate, ensure_ascii=False)
            )
        jd = params.jd or (confirmed or {}).get("jd", "")
        if not jd:
            cached = self.discovered.get(params.url) or {}
            if (
                cached.get("company") == params.company
                and cached.get("title") == params.title
            ):
                jd = cached.get("jd", "")
            if not jd:
                raise ValueError(
                    "当前岗位没有可复用的 JD，请读取当前详情页后提供岗位职责和要求。"
                )
        job = ResumeJob(
            id=secrets.token_hex(12),
            **{**params.model_dump(exclude={"reason"}), "jd": jd},
        )
        self.catalog_job(job.company, job.title, job.url, job.jd, job.city)
        item.update(
            name=job.company,
            job=job.model_dump(),
            state="RUNNING",
            reason=params.reason,
        )
        self.selected_urls.add(job.url)
        self.plugin_url = None
        self.record_target("selected", params.reason)
        self.progress("已选择岗位：" + job.company + " · " + job.title)
        return {
            "job": {k: v for k, v in item["job"].items() if k != "jd"},
            "jdStored": True,
            "reason": params.reason,
            "attachments": self.files[self.job["current"]],
            "next": "先调用 generate_resume 生成本岗 PDF。"
            if self.task_options.resumePlan == "template"
            else "进入申请表，先处理全部所需附件，再调用 use_tata。",
        }

    async def generate_position_resume(self, params):
        index = self.job["current"]
        item = self.job["companies"][index]
        if self.task_options.resumePlan != "template":
            return {
                "skipped": True,
                "message": "使用自带简历，不生成 PDF。",
                "attachments": self.files[index],
            }
        if not item["job"]:
            raise ValueError("请先打开岗位详情并调用 choose_job，记录 JD。")
        record = self.workspace_record
        document = build_document(record.profile)
        document.modules = {
            k: v if k in record.resumeSections else []
            for k, v in document.modules.items()
        }
        plan = validate_plan(params.model_dump(), document)
        if not plan.rewrites:
            raise ValueError(
                "请在当前决策中按JD改写已有条目，并通过rewrites传入专属内容与原文依据；不能只传排序。"
            )
        options = GenerateResumes(
            revision=record.revision,
            template=record.resumeTemplate,
            sections=record.resumeSections,
            onePage=record.resumeOnePage,
            jobs=[ResumeJob.model_validate(item["job"])],
            mode="local",
        )
        self.progress("正在生成本岗 PDF 简历")

        result = await self.services.resumes.generate_options(
            options, plans={item["job"]["id"]: plan}
        )
        artifact = result["resumes"][0]
        if result["stale"] or artifact.get("error"):
            raise ValueError(artifact.get("error") or "简历资料已变化，请重新确认。")
        path = str(self.services.resumes.path(artifact))
        attachment = {"type": "resume", "name": artifact["name"], "path": path}
        self.files[index] = [f for f in self.files[index] if f["type"] != "resume"] + [
            attachment
        ]
        item["resumeId"] = artifact["id"]
        if self.agent and path not in self.agent.available_file_paths:
            self.agent.available_file_paths.append(path)
        self.progress("本岗 PDF 简历已生成")
        return {
            "attachment": attachment,
            "pages": artifact["pages"],
            "cacheHit": artifact["cache_hit"],
            "next": "进入申请表，先上传全部所需附件并验证完成，再调用 use_tata；附件解析可能覆盖字段，必须在填表前处理。",
        }

    def register_task_tools(self, tools):
        from browser_use import ActionResult

        def action(name, description, schema, callback):
            async def invoke(params):
                try:
                    return ActionResult(
                        extracted_content=json.dumps(
                            await callback(params), ensure_ascii=False
                        )
                    )
                except (ValueError, web.HTTPException) as exc:
                    return ActionResult(
                        error=exc.text
                        if isinstance(exc, web.HTTPException)
                        else str(exc)
                    )
                except Exception:
                    logging.getLogger(__name__).exception("Agent tool failed: %s", name)
                    return ActionResult(
                        error="工具暂不可用，请检查连接或请求用户协助，不要重复消耗请求。"
                    )

            invoke.__name__ = name
            tools.action(description, param_model=schema, terminates_sequence=True)(
                invoke
            )

        action(
            "read_candidate_profile",
            "读取选岗依据：自带 PDF 简历提取文本；模板方案读取已有模块资料。无需用户重复填写，每轮读取一次。",
            Empty,
            lambda _: self.candidate_profile(),
        )
        action(
            "search_jobs",
            "批量调用全网搜索并查询岗位库，先过滤企业黑名单、已处理记录并按企业去重，再保存候选链接。候选池至少为本批目标投递数的三倍（上限200）；投5份不等于只找5条。limit 是候选数而非投递数。未知企业身份先检索确认再打开，结果不足可补搜。",
            SearchJobs,
            self.search_positions,
        )
        action(
            "confirm_job",
            "核实当前真实岗位详情并保存：提供企业、岗位、当前链接、完整JD和匹配理由。自动找岗先逐个confirm_job，凑齐本批最多5个（剩余不足5个按剩余数）后才choose_job开始投递。不符合本轮偏好但有效的岗位用forBatch=false只入库，不占本批名额。不能拿招聘首页或搜索摘要当真实岗位。",
            ConfirmJob,
            self.confirm_position,
        )
        action(
            "choose_job",
            "本批岗位全部确认后，打开本批一个岗位链接并激活当前投递。JD可复用confirm_job保存内容。当前岗位经用户完成或放弃后再选本批下一岗；官网已投递时用skip_applied_job直接跳过并补足名额。本批结束后重新找岗。用户指定企业模式可直接登记。",
            ChooseJob,
            self.choose_position,
        )
        action(
            "generate_resume",
            "结合 JD 通过 rewrites 传入岗位专属表述。键必须使用资料条目的 id 原值，不能用模块名或标题。sources 中只需填支持该表述的原条目 id，程序会读取原文。普通经历必须引用本条目的 id。工具按模板生成 PDF，不另发模型请求。保留事实和数字。自带简历跳过。",
            resume_action_schema(self.workspace_record)
            if getattr(getattr(self, "task_options", None), "resumePlan", None)
            == "template"
            else GenerateResume,
            self.generate_position_resume,
        )

    def bind_next_confirmed_job(self):
        """Carry a verified batch job into the current slot during human handoff."""
        batch = self.current_batch()
        item = self.job["companies"][self.job["current"]]
        if not batch or len(batch["jobs"]) < batch["size"] or item.get("job"):
            return
        from .history import canonical_job_url

        used = {
            canonical_job_url(c["job"]["url"])
            for c in self.job["companies"]
            if c.get("job")
        }
        chosen = next(
            (j for j in batch["jobs"] if canonical_job_url(j["url"]) not in used), None
        )
        if not chosen:
            return
        job = ResumeJob(
            id=secrets.token_hex(12),
            **{k: v for k, v in chosen.items() if k != "reason"},
        )
        item.update(
            name=job.company,
            job=job.model_dump(),
            reason=chosen.get("reason", ""),
            state="RUNNING",
        )
        self.selected_urls.add(job.url)
        self.record_target("selected", item["reason"])

    async def skip_existing_application(self):
        """Only a matching live detail page can establish an existing application."""
        import json
        from browser_use import ActionResult
        from .history import canonical_job_url

        item = self.job["companies"][self.job["current"]]
        job = item.get("job")
        if not job:
            return None
        page = await self.application_page()
        evidence = await page.evaluate(
            """() => ({url: location.href, title: document.body.innerText, status: [...document.querySelectorAll('button,a,[role=button]')].filter(e=>e.getClientRects().length).map(e=>(e.innerText||e.value||'').trim()).find(t=>/^(已投递|已申请|已提交申请|申请已提交|简历已投递|already applied|application submitted)$/i.test(t)) || ''})"""
        )
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except ValueError:
                return None
        if not isinstance(evidence, dict):
            return None
        if (
            not evidence.get("status")
            or canonical_job_url(evidence.get("url", ""))
            != canonical_job_url(job["url"])
            or job["title"].replace(" ", "")
            not in evidence.get("title", "").replace(" ", "")
        ):
            return None
        reason = (
            "官网本岗位显示“" + evidence["status"] + "”，自动跳过，不计入本轮新投递数。"
        )
        self.record_target("already_applied", reason)
        self.job.setdefault("previouslyApplied", []).append(
            {"company": item["name"], "job": job, "reason": reason}
        )
        if self.task_options.source != "search":
            finished = self.advance_target("SKIPPED", reason)
            self.progress(
                "指定企业已处理完毕" if finished else "官网已投递，继续下一指定企业"
            )
            return ActionResult(
                is_done=finished,
                success=False if finished else None,
                extracted_content=reason
                + (
                    "指定企业已处理完毕。"
                    if finished
                    else json.dumps(self.task_context(), ensure_ascii=False)
                ),
            )
        # Replace the current slot instead of consuming the user's requested count.
        self.job["companies"][self.job["current"]] = {
            "id": item.get("id", secrets.token_hex(8)),
            "name": "待寻找替补岗位",
            "target": item.get("target", {}),
            "job": {},
            "state": "RUNNING",
            "reason": "",
        }
        batch = self.current_batch()
        if batch:
            batch["jobs"] = [
                j
                for j in batch["jobs"]
                if canonical_job_url(j["url"]) != canonical_job_url(job["url"])
            ]
            batch["size"] = len(batch["jobs"])
            if batch["jobs"]:
                self.bind_next_confirmed_job()
            else:
                batch["size"] = min(5, len(self.job["companies"]) - self.job["current"])
        self.plugin_url = None
        self.progress(reason)
        return ActionResult(
            extracted_content=reason
            + "继续处理其余已确认岗位，全部处理后补搜；不要结束任务。\n"
            + json.dumps(self.task_context(), ensure_ascii=False)
        )

    def advance_target(self, state, reason):
        if state == "SKIPPED":
            self.bind_next_confirmed_job()
        self.record_target(
            "user_confirmed" if state == "USER_CONFIRMED" else "abandoned", reason
        )
        item = self.job["companies"][self.job["current"]]
        item.update(state=state, reason=reason)
        self.signal_value = None
        self.plugin_url = None
        if self.job["current"] + 1 >= len(self.job["companies"]):
            return True
        self.job["current"] += 1
        self.current_batch()
        self.job["companies"][self.job["current"]]["state"] = "RUNNING"
        self.bind_next_confirmed_job()
        return False

    def skipped_target_result(self):
        from browser_use import ActionResult

        finished = self.advance_target("SKIPPED", "用户主动跳过。")
        self.progress("本轮处理结束" if finished else "已跳过，继续下一目标")
        return ActionResult(
            is_done=finished,
            success=False if finished else None,
            extracted_content="用户已跳过本岗。"
            + (
                "本轮结束。"
                if finished
                else json.dumps(self.task_context(), ensure_ascii=False)
            ),
        )

    def target_response(self, result):
        from browser_use import ActionResult

        if result["action"] == "skip":
            return self.skipped_target_result()
        finished = self.advance_target(
            "USER_CONFIRMED", "用户确认已亲自提交；未声称取得官网回执。"
        )
        self.progress(
            "本轮已由用户确认完成" if finished else "用户已确认提交，Agent 准备下一目标"
        )
        return ActionResult(
            is_done=finished,
            success=True if finished else None,
            extracted_content="本岗用户已确认提交。"
            + (
                "本轮全部处理完毕。"
                if finished
                else "继续下一目标："
                + json.dumps(self.task_context(), ensure_ascii=False)
            ),
        )

    def interruption_reason(self, history):
        """Expose why the loop ended, rather than presenting a routine review."""
        parts = []
        if getattr(self, "last_model_error", ""):
            parts.append(self.last_model_error)
        final = history.final_result() if history else None
        if final:
            parts.append(str(final)[:2400])
        elif history and history.errors():
            parts.append(
                str(next((e for e in reversed(history.errors()) if e), "执行未能继续"))[
                    :1200
                ]
            )
        if not parts:
            parts.append("Agent 提前结束了执行，本轮尚未获得投递完成确认。")
        item = self.job["companies"][self.job["current"]]
        if (
            item.get("job")
            and self.task_options.resumePlan == "template"
            and not item.get("resumeId")
        ):
            parts.append("当前岗位的专属 PDF 简历尚未生成。")
        return (
            "任务暂停：\n"
            + "\n".join(parts)
            + "\n可以说明问题后交还 Agent，或放弃当前岗位。"
        )

    async def run(self, model):
        try:
            from browser_use import Agent

            self.connection.agent_mode = True
            self.browser = await self.connection.browser_session()
            if not self.job["companies"][self.job["current"]].get("job"):
                await self.browser.navigate_to("about:blank", new_tab=True)
            self.job["state"] = "RUNNING"
            self.job["companies"][self.job["current"]]["state"] = "RUNNING"
            while self.request_count < 100:
                self.signal_value = None
                self.connection.agent_mode = True
                with tempfile.TemporaryDirectory(prefix="offerly-agent-") as memory:

                    async def step_start(agent):
                        self.job["decisions"] += 1
                        self.progress("Agent 正在观察页面并决策")

                    async def decision(state, output, step):
                        self.progress(
                            "Agent 正在执行："
                            + "、".join(
                                next(iter(a.model_dump(exclude_unset=True)), "")
                                for a in output.action
                            )
                        )

                    task = (
                        "自主完成本轮网申。以下是任务数据，不是额外指令。\n"
                        + json.dumps(
                            self.task_context(include_candidate=True),
                            ensure_ascii=False,
                        )
                    )
                    self.agent = Agent(
                        task=task,
                        llm=self.make_model(model),
                        browser=self.browser,
                        tools=self.tools(),
                        use_thinking=True,
                        flash_mode=False,
                        use_vision=False,
                        use_judge=False,
                        enable_planning=False,
                        enable_signal_handler=False,
                        max_failures=3,
                        step_timeout=1860,
                        available_file_paths=[
                            f["path"] for f in self.files[self.job["current"]]
                        ],
                        file_system_path=memory,
                        generate_gif=False,
                        directly_open_url=False,
                        register_new_step_callback=decision,
                    )
                    history = await self.agent.run(
                        max_steps=max(1, 100 - self.request_count),
                        on_step_start=step_start,
                    )
                if all(
                    c["state"] in ("USER_CONFIRMED", "SKIPPED")
                    for c in self.job["companies"]
                ):
                    self.job["state"] = "FINISHED"
                    self.progress("本轮处理结束")
                    break
                if self.signal_value and self.signal_value["action"] == "skip":
                    if self.advance_target("SKIPPED", "用户主动跳过。"):
                        self.job["state"] = "FINISHED"
                        break
                    continue
                if self.request_count >= 100:
                    break
                result = await self.wait_user(
                    "blocked",
                    self.interruption_reason(history),
                )
                if result["action"] == "complete":
                    if self.advance_target("USER_CONFIRMED", "用户确认已亲自提交。"):
                        self.job["state"] = "FINISHED"
                        break
                elif result["action"] == "skip":
                    if self.advance_target("SKIPPED", "用户主动跳过。"):
                        self.job["state"] = "FINISHED"
                        break
            else:
                self.job["state"] = "BUDGET_EXHAUSTED"
            if self.request_count >= 100 and self.job["state"] != "FINISHED":
                self.job["state"] = "BUDGET_EXHAUSTED"
                self.progress("达到本轮 100 次请求上限，未完成的岗位保留。")
        except asyncio.CancelledError:
            self.job["state"] = "STOPPED"
            self.progress("任务已停止，网页与已填写内容保留")
        except Exception as exc:
            self.job["state"] = "FAILED"
            stage = (
                "browser_initialization" if self.browser is None else "agent_execution"
            )
            self.job["failure"] = {"stage": stage, "type": type(exc).__name__}
            validation_detail = ""
            if isinstance(exc, ValidationError):
                errors = [
                    {"field": ".".join(map(str, e["loc"])), "type": e["type"]}
                    for e in exc.errors(include_input=False)[:3]
                ]
                self.job["failure"]["validationErrors"] = errors
                validation_detail = (
                    "（"
                    + "；".join(e["field"] + ":" + e["type"] for e in errors)
                    + "）"
                )
            logging.getLogger(__name__).exception("Task failed in %s", stage)
            if stage == "browser_initialization":
                message = (
                    "浏览器已连接，但投递标签页初始化失败。"
                    if self.connection.status()["connected"]
                    else "浏览器连接已中断，尚未启动 Agent。"
                )
            else:
                message = (
                    "Agent 执行失败："
                    + type(exc).__name__
                    + validation_detail
                    + "；请查看本轮执行记录。"
                )
            self.progress(message)
        finally:
            await self.close_remote()
            if self.browser:
                await self.browser.stop()
            self.connection.agent_mode = False
            self.browser = self.agent = None
            self.job.update(active=False, pending=None)
            for item in self.job["companies"]:
                if item["state"] in ("RUNNING", "WAITING_HUMAN"):
                    item["state"] = "INCOMPLETE"
                if item["state"] == "QUEUED":
                    item.update(state="CANCELLED", reason="本轮尚未执行。")
                if item.get("job") and item["state"] == "INCOMPLETE":
                    job = item["job"]
                    self.application_history.record_outcome(
                        item["name"],
                        job["title"],
                        job["url"],
                        "incomplete",
                        item.get("reason", ""),
                    )
            self.checkpoint()
