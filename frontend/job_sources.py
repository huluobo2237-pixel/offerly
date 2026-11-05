"""Public campus directories supply portal hints; live official APIs supply jobs."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import aiohttp
import httpx
from pydantic import BaseModel, Field

from .job_discovery import (
    DiscoveredSource,
    DiscoveryError,
    DiscoveryResult,
    WebDiscovery,
    portal_from_url,
)

# Import link facts only. An old directory entry never proves a job is still open.
DIRECTORIES = (
    ("xixicc186/xixicc2027", "jobs.json"),
    ("blackboxo/2023-campus-recruitment-for-IT-industry", "README.md"),
    ("ygbingo/Campus-Recruitment-Website", "README.md"),
    ("leijobs/CampusCareers", "README.md"),
)

DISCOVERY_INDUSTRIES = (
    "半导体 芯片",
    "汽车 机械",
    "新能源 电气",
    "金融 银行",
    "生物 医药",
    "消费 零售",
    "通信 国企",
    "游戏 设计",
    "物流 制造",
    "互联网 软件",
    "化工 材料",
    "建筑 工程",
)


class DirectoryResult(BaseModel):
    sources: list[DiscoveredSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PortalDiscoveryCache(BaseModel):
    checkedAt: float
    budget: int
    sources: list[DiscoveredSource]


class DirectoryCache(BaseModel):
    checkedAt: float
    result: DirectoryResult


def portal_key(source: DiscoveredSource) -> str:
    """Old/new Moka URL spellings describe the same tenant and site."""
    if source.kind == "moka":
        return "/".join(source.url.rstrip("/").split("/")[-2:])
    return source.url.rstrip("/")


def parse_directory(content: str, filename: str) -> list[DiscoveredSource]:
    candidates = []
    if filename.endswith(".json"):
        rows = json.loads(content)
        if not isinstance(rows, list):
            raise ValueError("invalid directory")
        candidates = [
            (str(row.get("apply_url") or ""), str(row.get("company") or ""))
            for row in rows[:10000]
            if isinstance(row, dict)
        ]
    else:
        heading = ""
        for line in content.splitlines():
            if line.startswith("#"):
                heading = line.lstrip("# ").strip("* ")
            urls = re.findall(
                r'https://(?:app\.mokahr\.com|[a-z0-9-]+\.jobs\.(?:feishu\.cn|f\.mioffice\.cn|larkoffice\.com))/[^\s<>\]）)"\u201d]+',
                line,
            )
            if not urls:
                continue
            cells = [cell.strip() for cell in line.strip("| ").split("|")]
            label = cells[0] if line.lstrip().startswith("|") and cells else heading
            label = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", label)
            label = re.sub(r"✅.*|<[^>]*>|[*`]", "", label).strip()[:100]
            candidates.extend((url, label) for url in urls)
    sources = {}
    for url, label in candidates:
        source = portal_from_url(url, label)
        # A social portal is never relabelled as a campus portal to inflate coverage.
        if source and source.nature == "campus":
            sources.setdefault(portal_key(source), source)
    return list(sources.values())


async def read_directories(cache_root: Path | None = None) -> DirectoryResult:
    cache = cache_root / "directories.json" if cache_root else None
    if cache:
        try:
            saved = DirectoryCache.model_validate_json(
                cache.read_text(encoding="utf-8")
            )
            if time.time() - saved.checkedAt < 3600:
                return saved.result
        except (OSError, ValueError):
            pass
    result = DirectoryResult()
    seen = set()
    async with httpx.AsyncClient(
        timeout=18, trust_env=False, follow_redirects=False
    ) as client:
        for repository, filename in DIRECTORIES:
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{repository}/contents/{filename}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Offerly-catalog",
                    },
                )
                response.raise_for_status()
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError("directory too large")
                payload = response.json()
                content = base64.b64decode(payload["content"], validate=False).decode(
                    "utf-8"
                )
                for source in parse_directory(content, filename):
                    key = portal_key(source)
                    if key not in seen:
                        seen.add(key)
                        result.sources.append(source)
            except (httpx.HTTPError, ValueError, KeyError, UnicodeError):
                result.warnings.append(f"{repository} 目录暂不可读取，已继续其他来源。")
    if cache and result.sources:
        snapshot = DirectoryCache(checkedAt=time.time(), result=result)
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
        temporary.replace(cache)
    return result


async def discover_portals(
    key: str, budget: int, progress=None, cache_root: Path | None = None
) -> DiscoveryResult:
    """Spend at most the owner's explicit basic-search budget across industries."""
    cache = cache_root / "industry-discovery.json" if cache_root else None
    if cache:
        try:
            saved = PortalDiscoveryCache.model_validate_json(
                cache.read_text(encoding="utf-8")
            )
            if time.time() - saved.checkedAt < 86400 and saved.budget >= budget:
                return DiscoveryResult(sources=saved.sources)
        except (OSError, ValueError):
            pass
    discovery = WebDiscovery(key)
    sources = {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12), trust_env=False
        ) as session:
            for industry in DISCOVERY_INDUSTRIES[:budget]:
                try:
                    hits = await discovery.search(
                        session,
                        f"校园招聘 {industry} site:app.mokahr.com/campus-recruitment",
                    )
                    discovery.result.hits.extend(hits)
                    for hit in hits:
                        source = portal_from_url(hit.url, hit.title)
                        if source and source.nature == "campus":
                            sources.setdefault(portal_key(source), source)
                except DiscoveryError as error:
                    discovery.result.warnings.append(str(error))
                    break
                if progress:
                    await progress(
                        discovery.result.searchCalls,
                        discovery.result.credits,
                        len(discovery.result.hits),
                    )
    finally:
        discovery.result.sources = list(sources.values())
    if cache and discovery.result.sources and not discovery.result.warnings:
        snapshot = PortalDiscoveryCache(
            checkedAt=time.time(), budget=budget, sources=discovery.result.sources
        )
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
        temporary.replace(cache)
    return discovery.result
