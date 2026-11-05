"""Public HTTPS gateway with private visitor workspaces and native helper pairing."""

import asyncio
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from offerly.attachments import MAX_TOTAL
from offerly.services import ApplicationServices
from .backend import make_app, plugin_check_page
from .job_catalog import JobCatalog
from .phone import PhoneHub
from .server_store import ServerStore

PUBLIC = Path(__file__).resolve().parent / "public"
COOKIE = "__Host-offerly"
IDENT = re.compile(r"[A-Za-z0-9_-]{43}")


@dataclass
class Visitor:
    manager: ApplicationServices
    desktop: web.AppRunner
    phone: web.AppRunner
    seen: float
    requests: int = 0


class HostedSite:
    def __init__(self, root, origin, ice=None):
        self.root = Path(root)
        self.origin = origin.rstrip("/")
        address = urlsplit(self.origin)
        if (
            address.scheme != "https"
            or not address.hostname
            or address.username
            or address.password
            or address.path
            or address.query
            or address.fragment
        ):
            raise ValueError("正式网站必须配置 HTTPS 根地址。")
        self.host = address.netloc
        self.ice = ice or {}
        self.visitors = {}
        self.lock = asyncio.Lock()
        self.task_lock = asyncio.Lock()
        self.catalog = JobCatalog(self.root / "job-search")

    async def create(self, ident):
        manager = ApplicationServices(self.root / "visitors" / ident)
        manager.model_store = ServerStore(manager.root / "model.json")
        if manager.model_store.read() is None:
            configured = ServerStore(self.root / "model.json").read()
            if configured:
                manager.model_store.write(configured)
        manager.notification_file = manager.root / "wechat.json"
        manager.phone = PhoneHub(manager, "127.0.0.1", 0, credential_store=ServerStore)
        manager.phone.configure_public_origin(self.origin)
        manager.phone.rtc_servers = self.ice.get("server", [])
        manager.phone.client_rtc_servers = self.ice.get("client", [])
        desktop = web.AppRunner(
            make_app(manager, public_origin=self.origin), access_log=None
        )
        # Search results and task state stay private; verified public jobs are shared.
        manager.job_search.catalog = self.catalog
        config = ServerStore(manager.root / "search.json")
        if config.read() is None:
            configured = ServerStore(self.root / "job-search/search.json").read()
            if configured:
                config.write(configured)
        manager.job_search.config_store = config
        phone = web.AppRunner(manager.phone.app(), access_log=None)
        await desktop.setup()
        try:
            await phone.setup()
        except BaseException:
            await desktop.cleanup()
            raise
        return Visitor(manager, desktop, phone, time.monotonic())

    async def get_visitor(self, request):
        ident = request.cookies.get(COOKIE, "")
        if not IDENT.fullmatch(ident):
            ident = ""
        visitor = self.visitors.get(ident)
        if visitor is None and request.path == "/api/bootstrap":
            # Connected helpers and active tasks are never evicted.
            for key, old in list(self.visitors.items()):
                if (
                    not old.requests
                    and not (old.manager.public() or {}).get("active")
                    and time.monotonic() - old.seen > 1800
                ):
                    self.visitors.pop(key)
                    await old.desktop.cleanup()
                    await old.phone.cleanup()
            if len(self.visitors) >= 8:
                raise web.HTTPServiceUnavailable(text="当前体验人数已满，请稍后重试。")
            if not ident or not (self.root / "visitors" / ident).is_dir():
                ident = secrets.token_urlsafe(32)
            visitor = self.visitors[ident] = await self.create(ident)
        if visitor is None:
            raise web.HTTPForbidden(text="页面连接已更新，请刷新网站。")
        return ident, visitor

    async def dispatch(self, request):
        if request.host != self.host or request.headers.get("Origin") not in (
            None,
            self.origin,
        ):
            raise web.HTTPForbidden(text="访问地址不匹配。")
        path = request.path
        if path == "/healthz":
            return web.json_response({"ok": True, "browserConnection": "helper"})
        if path == "/plugin-check" and request.method in ("GET", "HEAD"):
            return await plugin_check_page(request)
        if (
            request.method in ("GET", "HEAD")
            and not path.startswith(("/api/", "/phone-api/", "/browser-helper/"))
            and path != "/plugin-check"
        ):
            filename = (
                "phone.html" if path == "/phone" else path.lstrip("/") or "index.html"
            )
            target = (PUBLIC / filename).resolve()
            if not target.is_relative_to(PUBLIC) or not target.is_file():
                raise web.HTTPNotFound()
            headers = (
                {"Content-Disposition": 'attachment; filename="OfferlyConnect.exe"'}
                if path == "/downloads/OfferlyConnect.exe"
                else {}
            )
            return web.FileResponse(target, headers=headers)
        routed = request.clone()
        ident = None
        async with self.lock:
            if path == "/browser-helper/relay":
                code = request.headers.get("X-Offerly-Pair-Code", "")
                visitor = next(
                    (
                        v
                        for v in self.visitors.values()
                        if v.manager.browsers.ticket
                        and secrets.compare_digest(
                            code.encode(), v.manager.browsers.ticket[0].encode()
                        )
                    ),
                    None,
                )
                if visitor is None:
                    raise web.HTTPForbidden(
                        text="连接链接已失效，请回网站重新点击连接。"
                    )
                runner = visitor.desktop
            elif path.startswith("/phone-api/"):
                pairing = path == "/phone-api/pair"
                token = request.headers.get("X-Phone-Token", "")
                if pairing:
                    data = await routed.json()
                    token = data.get("ticket", "") if isinstance(data, dict) else ""
                if not isinstance(token, str) or len(token) > 64:
                    raise web.HTTPForbidden(text="手机配对信息无效。")
                visitor = next(
                    (
                        v
                        for v in self.visitors.values()
                        if token
                        and secrets.compare_digest(
                            token.encode(),
                            (
                                (
                                    v.manager.phone.ticket
                                    if pairing
                                    else v.manager.phone.session
                                )
                                or ""
                            ).encode(),
                        )
                    ),
                    None,
                )
                if visitor is None:
                    raise web.HTTPForbidden(
                        text="手机连接已失效，请在电脑重新生成链接。"
                    )
                runner = visitor.phone
            else:
                ident, visitor = await self.get_visitor(request)
                runner = visitor.desktop
            visitor.requests += 1
            visitor.seen = time.monotonic()
        try:
            if path == "/api/agent/tasks":
                async with self.task_lock:
                    running = sum(
                        bool((v.manager.public() or {}).get("active"))
                        for v in self.visitors.values()
                    )
                    if running >= 2:
                        raise web.HTTPServiceUnavailable(
                            text="当前任务正在执行，请稍后再启动。你的配置已保留。"
                        )
                    response = await runner.server.request_handler(routed)
            else:
                response = await runner.server.request_handler(routed)
            if path == "/api/bootstrap" and response.status == 200:
                response.set_cookie(
                    COOKIE,
                    ident,
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="Strict",
                    max_age=7 * 86400,
                )
            return response
        finally:
            visitor.seen = time.monotonic()
            visitor.requests -= 1

    def app(self):
        @web.middleware
        async def errors(request, handler):
            try:
                response = await handler(request)
            except web.HTTPException as error:
                response = web.json_response({"error": error.text}, status=error.status)
            except (ValueError, UnicodeError):
                response = web.json_response({"error": "请求格式不正确。"}, status=400)
            response.headers.update(
                {
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                }
            )
            return response

        app = web.Application(
            middlewares=[errors], client_max_size=MAX_TOTAL + 2 * 1024 * 1024
        )
        app.router.add_route("*", "/{path:.*}", self.dispatch)

        async def lifecycle(_):
            try:
                yield
            finally:
                for visitor in list(self.visitors.values()):
                    await visitor.desktop.cleanup()
                    await visitor.phone.cleanup()

        app.cleanup_ctx.append(lifecycle)
        return app
