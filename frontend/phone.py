"""Separate LAN surface: expiring pairing tickets and scoped, revocable phone sessions."""

import asyncio
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit, quote

from aiohttp import web

from .wechat import WeChatNotifications

PUBLIC = Path(__file__).resolve().parent / "public"
PHONE_SESSION = web.RequestKey("phone_session", str)


class PhoneHub:
    def __init__(self, manager, host, port, scheme="http", *, credential_store=None):
        self.manager, self.host, self.port = manager, host, port
        self.ticket = self.session = None
        self.ticket_until = self.session_until = self.seen = 0
        self.remote_scope = None
        self.remote_lock = asyncio.Lock()
        self.pair_revision = 0
        self.scheme = scheme
        self.public_origin = None
        self.connection_error = ""
        self.wechat = WeChatNotifications(
            getattr(manager, "notification_file", manager.root / "wechat.dpapi"),
            **({"store_factory": credential_store} if credential_store else {}),
        )

    @property
    def origin(self):
        return self.public_origin or f"{self.scheme}://{self.host}:{self.port}"

    def configure_public_origin(self, origin):
        value = urlsplit(origin)
        if (
            value.scheme != "https"
            or not value.hostname
            or value.username
            or value.password
            or value.port not in (None, 443)
            or value.path not in ("", "/")
            or value.query
            or value.fragment
        ):
            raise ValueError("手机公网入口必须为 HTTPS 域名根地址。")
        self.public_origin = "https://" + value.netloc
        self.connection_error = ""

    def status(self):
        wechat = self.wechat.status()
        secure = self.origin.startswith("https://")
        if wechat["enabled"] and not secure:
            wechat = {
                **wechat,
                "enabled": False,
                "error": "微信提醒暂不可用，请恢复 HTTPS 手机入口；已保存的绑定仍保留。",
            }
        return {
            "enabled": not bool(self.connection_error),
            "error": self.connection_error,
            "secure": secure,
            "public": bool(self.public_origin),
            "connected": bool(
                self.session
                and time.monotonic() < self.session_until
                and time.monotonic() - self.seen < 15
            ),
            "paired": bool(self.session and time.monotonic() < self.session_until),
            "address": self.origin + "/phone",
            "foreground_only": not wechat["enabled"],
            "wechat": wechat,
        }

    async def pair(self, request):
        if self.connection_error:
            raise web.HTTPServiceUnavailable(text=self.connection_error)
        # Only the authenticated loopback desktop API calls this method. It may
        # carry a token whose test the user already confirmed on their phone.
        data = {}
        if request is not None and request.can_read_body:
            try:
                data = await request.json()
            except ValueError:
                raise web.HTTPBadRequest(text="配对配置必须为 JSON 对象。") from None
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="配对配置格式不正确。")
        self.pair_revision += 1
        self.ticket = secrets.token_urlsafe(32)
        self.ticket_until = time.monotonic() + 600
        return web.json_response(
            {
                **self.status(),
                "url": self.origin + f"/phone#{self.ticket}",
                "expires_in": 600,
            }
        )

    async def revoke(self, request):
        self.pair_revision += 1
        self.ticket = self.session = None
        await self.close_remote()
        return web.json_response(self.status())

    async def close_remote(self):
        self.remote_scope = None
        if self.manager.bridge:
            try:
                await self.manager.proxy("remote/close", {})
            except web.HTTPException:
                pass

    async def exchange(self, request):
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest()
        ticket = data.get("ticket", "")
        if (
            not isinstance(ticket, str)
            or not self.ticket
            or time.monotonic() >= self.ticket_until
            or not secrets.compare_digest(ticket, self.ticket)
        ):
            raise web.HTTPForbidden(text="配对链接无效或已使用，请在电脑重新生成。")
        # Consume before awaiting the old connection close: concurrent requests
        # must never redeem the same link twice.
        self.ticket = None
        self.session = None
        revision = self.pair_revision
        await self.close_remote()
        if revision != self.pair_revision or self.session is not None:
            raise web.HTTPConflict(text="配对状态已变化，请重试。")
        self.session = secrets.token_urlsafe(32)
        self.session_until = time.monotonic() + 8 * 3600
        self.seen = time.monotonic()
        return web.json_response({"token": self.session, "expires_in": 8 * 3600})

    def authenticate(self, request):
        token = request.headers.get("X-Phone-Token", "")
        if (
            not self.session
            or time.monotonic() >= self.session_until
            or not secrets.compare_digest(token, self.session)
        ):
            raise web.HTTPForbidden(text="手机连接已失效，请重新配对。")
        self.seen = time.monotonic()
        request[PHONE_SESSION] = self.session

    async def status_request(self, request):
        # No model config, API keys, attachment paths, or candidate profile on this surface.
        job = self.manager.public()
        if job:
            job = {
                k: job.get(k)
                for k in (
                    "id",
                    "state",
                    "active",
                    "current",
                    "phase",
                    "decisions",
                    "api_calls",
                    "pending",
                    "progress",
                    "started_at",
                )
            }
            job["company"] = self.manager.job["companies"][job["current"]]["name"]
            item = self.manager.job["companies"][job["current"]]
            job["canConfirmSubmitted"] = bool(item.get("job", {}).get("url"))
            records = {r["id"]: r for r in self.manager.resumes.read()}
            r = records.get(item.get("resumeId"))
            job["application"] = {
                "company": item["name"],
                "job": item.get("job", {}),
                "reason": item.get("reason", ""),
                "resume": {"id": r["id"], "name": r["name"], "pages": r["pages"]}
                if r
                else None,
            }

        return web.json_response(
            {
                "connection": self.status(),
                "task": job,
                "iceServers": getattr(self, "client_rtc_servers", []),
            }
        )

    async def resume_file(self, request):
        ident = request.match_info["id"]
        job = self.manager.public()
        if not job or not any(
            c.get("resumeId") == ident for c in self.manager.job["companies"]
        ):
            raise web.HTTPNotFound(text="本轮没有这份简历。")
        service = self.manager.resumes
        record = next((r for r in service.read() if r["id"] == ident), None)
        if record is None or not service.valid_file(record):
            raise web.HTTPNotFound(text="简历文件不可用。")
        return web.FileResponse(
            service.path(record),
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": "inline; filename*=UTF-8''"
                + quote(record["name"], safe=""),
            },
        )

    async def resume_page(self, request):
        from offerly.resume_layout import _pdfium_lock
        import pypdfium2 as pdfium
        import io

        ident = request.match_info["id"]
        job = self.manager.public()
        if (
            not job
            or self.manager.job["companies"][job["current"]].get("resumeId") != ident
        ):
            raise web.HTTPNotFound(text="当前岗位没有这份简历。")
        service = self.manager.resumes
        record = next((r for r in service.read() if r["id"] == ident), None)
        if not record or not service.valid_file(record):
            raise web.HTTPNotFound(text="简历尚未生成。")
        number = int(request.match_info["page"])
        if not 1 <= number <= record["pages"]:
            raise web.HTTPNotFound()

        def render():
            with _pdfium_lock, pdfium.PdfDocument(service.path(record)) as doc:
                page = doc[number - 1]
                bitmap = page.render(scale=1.5)
                try:
                    output = io.BytesIO()
                    bitmap.to_pil().save(output, format="PNG")
                    return output.getvalue()
                finally:
                    bitmap.close()
                    page.close()

        data = await asyncio.to_thread(render)
        return web.Response(body=data, content_type="image/png")

    async def action(self, request):
        action = request.match_info["action"]
        if action not in ("resume", "complete", "skip", "pause", "continue", "stop"):
            raise web.HTTPNotFound()
        # Reading the body can yield while the phone is revoked or re-paired.
        await request.json()
        self.authenticate(request)
        return await self.manager.action(request)

    async def remote_offer(self, request):
        data = await request.json()
        async with self.remote_lock:
            if request[PHONE_SESSION] != self.session:
                raise web.HTTPForbidden(text="手机连接已失效，请重新配对。")
            job = self.manager.public()
            scope = (job["id"], job["current"], self.session) if job else None
            if (
                not isinstance(data, dict)
                or not job
                or not job["active"]
                or job["state"] != "WAITING_HUMAN"
                or data.get("id") != job["id"]
                or data.get("current") != job["current"]
            ):
                raise web.HTTPConflict(text="当前不在等待手机接管。")
            result = await self.manager.proxy("remote/offer", data)
            current = self.manager.public()
            if (
                not current
                or not current["active"]
                or current["state"] != "WAITING_HUMAN"
                or scope != (current["id"], current["current"], self.session)
                or time.monotonic() >= self.session_until
            ):
                await self.close_remote()
                raise web.HTTPConflict(text="接管状态已变化。")
            self.remote_scope = scope
            return web.json_response(result)

    async def remote_close(self, request):
        async with self.remote_lock:
            self.authenticate(request)
            await self.close_remote()
        return web.json_response({"ok": True})

    async def remote_tab_action(self, request):
        data = dict(request.query) if request.method == "GET" else await request.json()
        job = self.manager.public()
        if (
            request[PHONE_SESSION] != self.session
            or not isinstance(data, dict)
            or not job
            or not job["active"]
            or job["state"] != "WAITING_HUMAN"
            or data.get("id") != job["id"]
            or str(data.get("current")) != str(job["current"])
        ):
            raise web.HTTPConflict(text="当前不在等待手机接管。")
        if request.method == "GET":
            result = await self.manager.proxy("remote/tabs")
        else:
            result = await self.manager.proxy(
                "remote/switch", {"target_id": data.get("target_id")}
            )
        return web.json_response(result)

    def app(self):
        @web.middleware
        async def access(request, handler):
            try:
                allowed_hosts = {urlsplit(self.origin).netloc}
                if request.host not in allowed_hosts:
                    raise web.HTTPForbidden(text="访问地址不匹配。")
                origin = request.headers.get("Origin")
                if origin and origin != self.origin:
                    raise web.HTTPForbidden(text="不接受其他网页的请求。")
                if request.path.startswith("/phone-api/") and request.path not in (
                    "/phone-api/pair",
                ):
                    self.authenticate(request)
                response = await handler(request)
            except web.HTTPException as exc:
                response = web.json_response({"error": exc.text}, status=exc.status)
            except ConnectionResetError:
                # The phone cancelled the offer/body while navigating or reconnecting.
                return web.Response(status=499)
            response.headers.update(
                {
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                }
            )
            return response

        app = web.Application(middlewares=[access], client_max_size=65536)

        async def page(request):
            return web.FileResponse(PUBLIC / "phone.html")

        async def script(request):
            return web.FileResponse(PUBLIC / "phone.js")

        app.add_routes(
            [
                web.get("/phone", page),
                web.get("/phone.js", script),
                web.post("/phone-api/pair", self.exchange),
                web.get("/phone-api/status", self.status_request),
                web.get("/phone-api/resumes/{id}", self.resume_file),
                web.get(r"/phone-api/resumes/{id}/pages/{page:\d+}", self.resume_page),
                web.post("/phone-api/remote/offer", self.remote_offer),
                web.post("/phone-api/remote/close", self.remote_close),
                web.get("/phone-api/remote/tabs", self.remote_tab_action),
                web.post("/phone-api/remote/switch", self.remote_tab_action),
                web.post("/phone-api/tasks/{id}/{action}", self.action),
            ]
        )
        for filename in ("browser-remote.js", "browser-input.js"):

            async def module(request, filename=filename):
                return web.FileResponse(PUBLIC / filename)

            app.router.add_get("/" + filename, module)

        async def monitor():
            while True:
                self.wechat.watch(self.manager.public(), self.origin)
                async with self.remote_lock:
                    if self.remote_scope:
                        job = self.manager.public()
                        if (
                            not job
                            or not job["active"]
                            or job["state"] != "WAITING_HUMAN"
                            or self.remote_scope
                            != (job["id"], job["current"], self.session)
                            or time.monotonic() >= self.session_until
                            or time.monotonic() - self.seen > 15
                        ):
                            await self.close_remote()
                await asyncio.sleep(0.25)

        async def lifecycle(app):
            watcher = asyncio.create_task(monitor())
            try:
                yield
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                await self.wechat.close()
                await self.close_remote()

        app.cleanup_ctx.append(lifecycle)
        return app
