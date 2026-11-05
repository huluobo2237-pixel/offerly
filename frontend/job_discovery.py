"""Open-web discovery followed by bounded, public recruitment-page verification."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .job_search import SearchInput

MAX_QUERIES = 5
MAX_PAGES = 24
MAX_PORTALS = 20
SEARCH_RESULTS_PER_REQUEST = 20


class WebHit(BaseModel):
    title: str = Field(max_length=500)
    url: str = Field(max_length=4096)
    snippet: str = Field(default="", max_length=3000)
    rawContent: str = Field(default="", max_length=30000)


class DiscoveryLead(BaseModel):
    title: str
    url: str
    reason: str = "尚未取得完整岗位 JD"


class DiscoveredSource(BaseModel):
    id: str
    name: str
    url: str
    kind: Literal["moka", "feishu"]
    nature: Literal["campus", "intern", "social"]


class WebPosting(BaseModel):
    id: str
    title: str
    company: str
    url: str
    city: str = ""
    description: str
    requirement: str = ""
    nature: Literal["campus", "intern", "social"]
    natureEvidence: str
    updatedAt: str = ""


class DiscoveryResult(BaseModel):
    hits: list[WebHit] = Field(default_factory=list)
    leads: list[DiscoveryLead] = Field(default_factory=list)
    sources: list[DiscoveredSource] = Field(default_factory=list)
    postings: list[WebPosting] = Field(default_factory=list)
    searchCalls: int = 0
    pageRequests: int = 0
    credits: int = 0
    warnings: list[str] = Field(default_factory=list)


class DiscoveryError(Exception):
    """A safe, user-facing error; never include provider response bodies or keys."""


def public_url(value: str) -> str:
    try:
        u = urlsplit(value)
        if (
            u.scheme not in ("https", "http")
            or not u.hostname
            or u.username
            or u.password
            or u.port not in (None, 80, 443)
        ):
            return ""
        if u.hostname.lower() in ("localhost",) or "." not in u.hostname:
            return ""
        try:
            if not ipaddress.ip_address(u.hostname).is_global:
                return ""
        except ValueError:
            pass
        return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, u.fragment))
    except (ValueError, TypeError):
        return ""


class PublicResolver(AbstractResolver):
    """Validate the actual resolved addresses used by the HTTP connector."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM
        )
        if not rows or any(
            not ipaddress.ip_address(row[4][0]).is_global for row in rows
        ):
            raise OSError("非公网地址不可读取")
        return [
            {
                "hostname": host,
                "host": row[4][0],
                "port": port,
                "family": row[0],
                "proto": row[2],
                "flags": socket.AI_NUMERICHOST,
            }
            for row in rows
        ]

    async def close(self):
        pass


def make_queries(query: SearchInput) -> list[str]:
    from .job_search import search_terms

    terms = search_terms(query.keywords)
    category = query.category if query.category != "不限" else "招聘"
    city = query.cities.replace("，", " ")[:80]
    year = query.year if query.category != "社会招聘" else ""
    queries = [
        f"{year} {category} {query.keywords} {city} 岗位 官网",
        f"{category} {terms[0]} {city} 职位 招聘",
    ]
    for term in terms[1:3]:
        queries.append(f"{year} {category} {term} {city} 岗位")
    queries.append(f"{year} {category} {terms[0]} 招聘 site:app.mokahr.com")
    return list(dict.fromkeys(" ".join(q.split())[:500] for q in queries))[:MAX_QUERIES]


def company_label(title: str, fallback: str) -> str:
    parts = re.split(r"[|｜_—–]|\s-\s", title)
    for part in parts:
        part = re.sub(
            r"20\d{2}(?:届|年)?|校园招聘|社会招聘|招聘官网|官方招聘|招聘网站|校招|招聘|职位详情|岗位详情|职位列表",
            "",
            part,
        ).strip(" -·：:【】[]")
        if 1 < len(part) <= 30 and not re.search(
            r"工程师|经理|实习|职位|岗位|Moka|飞书", part, re.I
        ):
            return part
    return fallback


def portal_from_url(url: str, title: str = "") -> DiscoveredSource | None:
    url = public_url(url)
    if not url:
        return None
    u = urlsplit(url)
    if u.hostname == "app.mokahr.com":
        match = re.match(
            r"^/(?:m/)?(campus-recruitment|campus_apply|social-recruitment|social_apply)/([A-Za-z0-9_-]{1,80})/(\d{1,12})(?:/|$)",
            u.path,
        )
        if not match:
            return None
        channel, org, site = match.groups()
        nature = "campus" if channel.startswith("campus") else "social"
        root = f"https://app.mokahr.com/{channel}/{org}/{site}"
        return DiscoveredSource(
            id="web_moka_" + hashlib.sha256(root.encode()).hexdigest()[:16],
            name=company_label(title, f"企业（{org}）"),
            url=root,
            kind="moka",
            nature=nature,
        )
    if u.hostname.endswith(
        (".jobs.feishu.cn", ".jobs.f.mioffice.cn", ".jobs.larkoffice.com")
    ):
        path = u.path.strip("/").split("/")[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", path) or path in (
            "api",
            "position",
            "jobs",
        ):
            path = "index"
        nature = (
            "campus"
            if re.search(r"campus", path, re.I) or re.search(r"校招|校园", title)
            else "intern"
            if path in ("intern", "internship")
            else "social"
        )
        root = f"https://{u.hostname}/{path}"
        return DiscoveredSource(
            id="web_feishu_" + hashlib.sha256(root.encode()).hexdigest()[:16],
            name=company_label(title, f"企业（{u.hostname.split('.')[0]}）"),
            url=root,
            kind="feishu",
            nature=nature,
        )
    return None


class RecruitmentPage(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.title = ""
        self.links = []
        self.link_titles = {}
        self.structured = []
        self._title = False
        self._script = None
        self._anchor = None
        self._anchor_text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "title":
            self._title = True
        if tag == "a" and attr.get("href"):
            self.links.append(attr["href"])
            self._anchor = attr["href"]
            self._anchor_text = []
        if tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self._script = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag == "a" and self._anchor:
            self.link_titles[self._anchor] = "".join(self._anchor_text).strip()
            self._anchor = None
        if tag == "script" and self._script is not None:
            try:
                self.structured.append(json.loads("".join(self._script)))
            except ValueError:
                pass
            self._script = None

    def handle_data(self, value):
        if self._title:
            self.title += value
        if self._anchor:
            self._anchor_text.append(value)
        if self._script is not None:
            self._script.append(value)


def jsonld_postings(page: RecruitmentPage, url: str) -> list[WebPosting]:
    from .job_search import text

    def walk(value):
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            types = value.get("@type") or []
            if "JobPosting" in (
                [types]
                if isinstance(types, str)
                else types
                if isinstance(types, list)
                else []
            ):
                yield value
            for key in ("@graph", "mainEntity", "itemListElement", "item"):
                if key in value:
                    yield from walk(value[key])

    jobs = []
    for data in walk(page.structured):
        org = data.get("hiringOrganization") or {}
        if not isinstance(org, dict):
            continue
        title, description, company = (
            text(data.get("title", "")),
            text(data.get("description", "")),
            text(org.get("name", "")),
        )
        if not title or not company or len(description) < 40:
            continue
        valid = data.get("validThrough")
        if valid:
            try:
                expiry = datetime.fromisoformat(str(valid).replace("Z", "+00:00"))
                if expiry.replace(tzinfo=expiry.tzinfo or timezone.utc) < datetime.now(
                    timezone.utc
                ):
                    continue
            except ValueError:
                pass
        host = urlsplit(url).hostname
        org_url = public_url(org.get("url") or org.get("sameAs") or "")
        org_host = urlsplit(org_url).hostname if org_url else ""
        org_host = (org_host or "").removeprefix("www.")
        # A third-party job board's JSON-LD is a lead, not proof of an official application URL.
        official = bool(
            org_host and (host == org_host or host.endswith("." + org_host))
        )
        official |= bool(
            host
            and host.endswith(
                (".myworkdayjobs.com", ".successfactors.com", ".eightfold.ai")
            )
        )
        if not official:
            continue
        evidence = str(data.get("employmentType", "")) + " " + title
        nature = (
            "intern"
            if re.search(r"实习|intern", evidence, re.I)
            else "campus"
            if re.search(
                r"校招|校园|应届|毕业生|\d{2,4}\s*届|new.?grad|graduate",
                evidence + " " + description,
                re.I,
            )
            else "social"
        )
        locations = data.get("jobLocation") or []
        if isinstance(locations, dict):
            locations = [locations]
        cities = []
        for location in locations:
            address = location.get("address", {}) if isinstance(location, dict) else {}
            if isinstance(address, dict):
                cities.append(
                    str(
                        address.get("addressLocality")
                        or address.get("addressRegion")
                        or ""
                    )
                )
        jobs.append(
            WebPosting(
                id=hashlib.sha256((url + title).encode()).hexdigest()[:24],
                title=title[:160],
                company=company[:160],
                url=url,
                city="、".join(filter(None, cities))[:160],
                description=description[:16000],
                nature=nature,
                natureEvidence=evidence[:160],
                updatedAt=str(data.get("datePosted", ""))[:80],
            )
        )
    return jobs


def extracted_posting(hit: WebHit) -> WebPosting | None:
    """Read explicit sections from a recognized official detail page, never a search snippet."""
    source = portal_from_url(hit.url, hit.title)
    if (
        not source
        or source.kind != "feishu"
        or not re.search(r"/position/\d+/detail", urlsplit(hit.url).path)
    ):
        return None
    body = hit.rawContent.strip()
    if not body or re.search(r"职位已下线|岗位已关闭|停止招聘", body):
        return None
    match = re.search(
        r"(?:职位描述|岗位职责|工作职责)\s*[：:]?\s*(.+?)(?:职位要求|任职要求)\s*[：:]?\s*(.+)",
        body,
        re.S,
    )
    if not match:
        return None
    title = re.split(r"\s[-|｜]\s", hit.title)[0].strip()
    if len(match[1]) + len(match[2]) < 80 or source.name.startswith("企业（"):
        return None
    city_pattern = r"^(?:北京|上海|广州|深圳|杭州|南京|苏州|成都|武汉|西安|合肥|天津|重庆|长沙|厦门|福州|珠海|东莞|佛山|宁波|无锡|济南|青岛|郑州|南昌|长春|沈阳|大连|哈尔滨|香港|澳门)(?:市)?$"
    cities = [
        line.strip()
        for line in body[: body.find(match[1])].splitlines()
        if re.fullmatch(city_pattern, line.strip())
    ]
    cohort = " ".join(dict.fromkeys(re.findall(r"20\d{2}\s*届[^\n]{0,30}", body)))
    return WebPosting(
        id=hashlib.sha256(hit.url.encode()).hexdigest()[:24],
        title=title[:160],
        company=source.name,
        url=hit.url,
        city="、".join(dict.fromkeys(cities)),
        description=(cohort + "\n" + match[1]).strip()[:12000],
        requirement=match[2][:4000],
        nature=source.nature,
        natureEvidence="官方招聘详情页",
    )


class WebDiscovery:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.result = DiscoveryResult()
        self.semaphore = asyncio.Semaphore(4)

    async def search(self, session, query: str) -> list[WebHit]:
        self.result.searchCalls += 1
        try:
            async with session.post(
                "https://api.tavily.com/search",
                headers={"Authorization": "Bearer " + self.api_key},
                allow_redirects=False,
                json={
                    "query": query,
                    "search_depth": "basic",
                    "max_results": SEARCH_RESULTS_PER_REQUEST,
                    "include_answer": False,
                    "include_raw_content": "text",
                    "auto_parameters": False,
                    "include_usage": True,
                },
            ) as response:
                if response.status in (401, 403):
                    raise DiscoveryError("全网搜索密钥不可用，请检查 Tavily 配置。")
                if response.status in (429, 432, 433):
                    raise DiscoveryError("搜索服务达到限额，本次已停止新增检索。")
                if response.status != 200:
                    raise DiscoveryError("搜索服务暂不可用，请稍后重试。")
                payload = await response.json()
            self.result.credits += int(payload.get("usage", {}).get("credits", 1))
            return [
                WebHit(
                    title=str(h.get("title", ""))[:500],
                    url=h["url"],
                    snippet=str(h.get("content", ""))[:3000],
                    rawContent=str(h.get("raw_content") or "")[:30000],
                )
                for h in payload.get("results", [])
                if public_url(h.get("url", ""))
            ]
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise DiscoveryError("无法连接搜索服务，请检查网络后重试。") from None

    async def fetch_page(self, session, url: str) -> tuple[str, str]:
        async with self.semaphore:
            for _ in range(4):
                url = public_url(url)
                if not url:
                    raise DiscoveryError("招聘链接不是可读取的公网地址。")
                if self.result.pageRequests >= MAX_PAGES:
                    raise DiscoveryError("达到本次招聘页读取上限。")
                self.result.pageRequests += 1
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("Location", ""))
                        continue
                    if response.status != 200:
                        raise DiscoveryError("招聘页暂不可读取。")
                    if "html" not in response.headers.get(
                        "Content-Type", ""
                    ) and "text/plain" not in response.headers.get("Content-Type", ""):
                        raise DiscoveryError("招聘页面不是网页格式。")
                    chunks = []
                    size = 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise DiscoveryError("招聘页面超过读取大小限制。")
                        chunks.append(chunk)
                    return url, b"".join(chunks).decode(
                        response.charset or "utf-8", errors="replace"
                    )
            raise DiscoveryError("招聘页重定向过多。")

    async def run(self, query: SearchInput, progress=None) -> DiscoveryResult:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(
            timeout=timeout, trust_env=False
        ) as search_session:
            seen = set()
            for index, phrase in enumerate(make_queries(query)):
                try:
                    for hit in await self.search(search_session, phrase):
                        if hit.url not in seen:
                            seen.add(hit.url)
                            self.result.hits.append(hit)
                except DiscoveryError as error:
                    self.result.warnings.append(str(error))
                    if not self.result.hits:
                        raise
                    break
                if progress:
                    await progress("discovering", index + 1, len(self.result.hits))
        return await self.verify_hits(self.result.hits, progress)

    async def verify_hits(self, hits: list[WebHit], progress=None) -> DiscoveryResult:
        """Verify already-paid search results without issuing another search request."""
        self.result.hits = list({hit.url: hit for hit in hits}.values())
        timeout = aiohttp.ClientTimeout(total=12)
        sources = {}
        postings = {}
        leads = {
            hit.url: DiscoveryLead(title=hit.title, url=hit.url)
            for hit in self.result.hits
        }
        for hit in self.result.hits:
            source = portal_from_url(hit.url, hit.title)
            if source:
                sources[source.id] = source
                leads.pop(hit.url, None)
        connector = aiohttp.TCPConnector(
            resolver=PublicResolver(), use_dns_cache=False, limit=4
        )
        if progress:
            await progress("verifying", self.result.searchCalls, len(self.result.hits))
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": "Offerly/1.0 (public recruitment reader)"},
        ) as pages:

            async def read(hit):
                try:
                    final, html = await self.fetch_page(pages, hit.url)
                    page = RecruitmentPage(html)
                    from .job_search import text

                    live_text = text(html)
                    if re.search(r"职位已下线|岗位已关闭|停止招聘", live_text):
                        return
                    posting = extracted_posting(
                        WebHit(
                            title=page.title or hit.title,
                            url=final,
                            rawContent=live_text,
                        )
                    )
                    if posting:
                        postings[posting.id] = posting
                        leads.pop(hit.url, None)
                    own = portal_from_url(final, page.title or hit.title)
                    if own:
                        sources[own.id] = own
                        leads.pop(hit.url, None)
                    for link in page.links[:300]:
                        found = portal_from_url(
                            urljoin(final, unescape(link)),
                            page.link_titles.get(link, ""),
                        )
                        if found and len(sources) < MAX_PORTALS:
                            sources.setdefault(found.id, found)
                    for job in jsonld_postings(page, final):
                        postings[job.id] = job
                        leads.pop(hit.url, None)
                except (
                    DiscoveryError,
                    aiohttp.ClientError,
                    TimeoutError,
                    ValueError,
                    LookupError,
                ):
                    pass

            await asyncio.gather(*(read(h) for h in self.result.hits[:MAX_PAGES]))
        self.result.sources = list(sources.values())[:MAX_PORTALS]
        self.result.postings = list(postings.values())
        self.result.leads = list(leads.values())[:50]
        if progress:
            await progress("verifying", self.result.searchCalls, len(self.result.hits))
        return self.result
