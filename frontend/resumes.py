"""Authenticated, revision-aware resume artifacts and content-addressed generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets

from aiohttp import web
from pydantic import ValidationError
from urllib.parse import quote

from offerly.resume import (
    RANKING_VERSION,
    VERSION,
    GenerateResumes,
    ResumeOptions,
    ai_plan,
    build_document,
    digest,
    local_plan,
    render_resume,
    validate_plan,
)


class ResumeService:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root / "resumes"
        self.lock = asyncio.Lock()

    def read(self):
        path = self.root / "index.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def write(self, records):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "index.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def path(self, record):
        if not re.fullmatch(r"[0-9a-f]{32}", record["id"]):
            raise ValueError("简历编号无效。")
        if record.get("fileName"):
            name = record["fileName"]
            if (
                not isinstance(name, str)
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or not name.endswith(".pdf")
            ):
                raise ValueError("简历文件名无效。")
            return self.root / record["id"] / name
        return self.root / (record["id"] + ".pdf")

    def valid_file(self, record):
        path = self.path(record)
        return (
            path.is_file()
            and path.stat().st_size == record["size"]
            and hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        )

    async def options(self, request, kind):
        try:
            raw = await request.read()
            if len(raw) > 200000:
                raise ValueError("生成配置过长。")
            return kind.model_validate_json(raw)
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(
                text="请检查岗位、JD 和模块选择；一次最多生成 10 份。"
            ) from None

    async def snapshot(self, options):
        async with self.workspace.lock:
            record = self.workspace.read()
            if record.revision != options.revision:
                raise web.HTTPConflict(text="个人资料已更新，请刷新后再生成简历。")
            try:
                document = build_document(record.profile)
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc)) from None
            if not any(document.modules[key] for key in options.sections):
                raise web.HTTPBadRequest(
                    text="所选模块没有资料，请填写资料或选择其他模块。"
                )
            return document, digest(record.profile)

    async def list(self, request):
        profile_hash = digest(self.workspace.read().profile)
        return web.json_response(
            {
                "resumes": [
                    {
                        **r,
                        "stale": r.get("renderer_version") != VERSION
                        or r["profile_hash"] != profile_hash
                        or not self.valid_file(r),
                    }
                    for r in self.read()
                ]
            }
        )

    async def preview(self, request):
        options = await self.options(request, ResumeOptions)
        document, _ = await self.snapshot(options)
        try:
            result = await asyncio.to_thread(
                render_resume,
                document,
                options.template,
                options.sections,
                one_page=options.onePage,
            )
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.Response(
            body=result.data,
            content_type="application/pdf",
            headers={"X-Resume-Pages": str(result.pages)},
        )

    async def generate(self, request):
        options = await self.options(request, GenerateResumes)
        return web.json_response(await self.generate_options(options))

    async def generate_options(self, options, plans=None):
        """Shared generation service; Agent can supply validated entry ordering."""
        if self.lock.locked():
            raise web.HTTPConflict(text="正在生成简历，请等待当前任务完成。")
        async with self.lock:
            document, profile_hash = await self.snapshot(options)
            document = document.model_copy(deep=True)
            document.modules = {
                k: v if k in options.sections else []
                for k, v in document.modules.items()
            }
            saved = self.workspace.model_store.read() or {}
            if options.mode == "ai" and not saved.get("apiKey"):
                raise web.HTTPBadRequest(
                    text="请先保存 DeepSeek API 配置，再生成岗位专属简历。"
                )
            records = self.read()
            results = []
            calls = 0
            provider_failed = False
            for job in options.jobs:
                fingerprint = digest(
                    {
                        "version": VERSION,
                        "agent_plan": plans.get(job.id).model_dump()
                        if plans and job.id in plans
                        else None,
                        "profile": profile_hash,
                        "job": job.model_dump(),
                        "template": options.template,
                        "sections": sorted(options.sections),
                        "onePage": options.onePage,
                        "mode": options.mode,
                        "model": {k: saved.get(k) for k in ("model", "baseUrl")}
                        if options.mode == "ai"
                        else {},
                    }
                )
                cached = next(
                    (
                        r
                        for r in records
                        if r["fingerprint"] == fingerprint
                        and r.get("fileName")
                        and self.valid_file(r)
                    ),
                    None,
                )
                if cached:
                    results.append({**cached, "cache_hit": True, "model_calls": 0})
                    continue
                if provider_failed:
                    results.append(
                        {
                            "job": job.model_dump(),
                            "error": "模型服务暂不可用，本岗位尚未请求；检查配置或余额后重试。",
                        }
                    )
                    continue
                try:
                    plan = (
                        plans[job.id]
                        if plans and job.id in plans
                        else local_plan(document, job)
                    )
                    usage = {}
                    plan_cached = False
                    if options.mode == "ai":
                        plan_key = digest(
                            {
                                "version": RANKING_VERSION,
                                "document": document.modules,
                                "job": {
                                    "title": job.title,
                                    "company": job.company,
                                    "jd": job.jd,
                                },
                                "model": {
                                    k: saved.get(k) for k in ("model", "baseUrl")
                                },
                            }
                        )
                        plan_path = self.root / "plans" / (plan_key + ".json")
                        if plan_path.is_file():
                            plan = validate_plan(
                                json.loads(plan_path.read_text(encoding="utf-8")),
                                document,
                            )
                            plan_cached = True
                        else:
                            calls += 1
                            plan, usage = await ai_plan(document, job, saved)
                            plan_path.parent.mkdir(parents=True, exist_ok=True)
                            temporary = plan_path.with_suffix(".tmp")
                            temporary.write_text(
                                plan.model_dump_json(), encoding="utf-8"
                            )
                            temporary.replace(plan_path)
                    rendered = await asyncio.to_thread(
                        render_resume,
                        document,
                        options.template,
                        options.sections,
                        job.title,
                        plan,
                        options.onePage,
                    )
                    data = rendered.data
                    pages = rendered.pages
                    if pages > 20:
                        raise ValueError("简历超过 20 页，请精简经历内容后重试。")
                    record = {
                        "id": secrets.token_hex(16),
                        "job": job.model_dump(),
                        "template": options.template,
                        "sections": options.sections,
                        "onePage": options.onePage,
                        "renderer_version": VERSION,
                        "body_font_size": rendered.body_font_size,
                        "warning": rendered.warning,
                        "profile_hash": profile_hash,
                        "fingerprint": fingerprint,
                        "name": re.sub(
                            r'[<>:"/\\|?*\x00-\x1f]',
                            "_",
                            re.sub(
                                r"[()（）\[\]【】]", "", document.name + "-" + job.title
                            ),
                        )[:160]
                        + ".pdf",
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "pages": pages,
                        "mode": options.mode,
                        "focus": plan.focus,
                        "tailoring": plan.model_dump(),
                        "usage": usage,
                    }
                    self.root.mkdir(parents=True, exist_ok=True)
                    record["fileName"] = record["name"]
                    self.path(record).parent.mkdir(parents=True, exist_ok=True)
                    self.path(record).write_bytes(data)
                    records.append(record)
                    self.write(records)
                    results.append(
                        {
                            **record,
                            "cache_hit": False,
                            "model_calls": int(
                                options.mode == "ai" and not plan_cached
                            ),
                        }
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    from openai import APIConnectionError, APIStatusError

                    if (
                        isinstance(exc, APIConnectionError)
                        or isinstance(exc, APIStatusError)
                        and exc.status_code in (401, 402, 403, 429)
                    ):
                        provider_failed = True
                    # Provider exceptions can contain request internals; return no raw SDK text.
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        and not isinstance(exc, json.JSONDecodeError)
                        else "生成失败，请检查模型配置或余额后重试；已成功的岗位不会重复调用。"
                    )
                    results.append({"job": job.model_dump(), "error": message})
            stale = profile_hash != digest(self.workspace.read().profile)
            return {"resumes": results, "model_calls": calls, "stale": stale}

    async def download(self, request):
        record = next(
            (r for r in self.read() if r["id"] == request.match_info["id"]), None
        )
        if record is None:
            raise web.HTTPNotFound(text="简历不存在，请重新生成。")
        if not self.valid_file(record):
            raise web.HTTPConflict(text="简历文件已变化，请重新生成。")
        return web.FileResponse(
            self.path(record),
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": "inline; filename*=UTF-8''"
                + quote(record["name"], safe=""),
                "X-Resume-Pages": str(record["pages"]),
            },
        )

    def routes(self):
        return [
            web.get("/api/resumes", self.list),
            web.post("/api/resumes/preview", self.preview),
            web.post("/api/resumes/generate", self.generate),
            web.get("/api/resumes/{id}/file", self.download),
        ]
