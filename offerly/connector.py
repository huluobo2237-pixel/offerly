"""Opt-in outbound helper transport; the original CDP bridge remains unchanged."""

import asyncio
import base64
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from aiohttp import WSMsgType, web

from .attachments import MAX_FILE, MAX_TOTAL
from .browser_connection import UserBrowserConnection, UserBrowsers


class HelperSocket:
    def __init__(self, socket):
        self.socket = socket

    async def send(self, message):
        await self.socket.send_str(message)

    async def close(self):
        await self.socket.close()

    async def __aiter__(self):
        async for message in self.socket:
            if message.type == WSMsgType.TEXT:
                yield message.data
            elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break


class HelperConnection(UserBrowserConnection):
    def __init__(self, browser, root):
        super().__init__(browser)
        self.root = root.resolve()
        self.staged = {}
        self.stage_lock = asyncio.Lock()

    def status(self):
        return {**super().status(), "installed": True, "source": "helper"}

    async def open_socket(self, endpoint):
        return HelperSocket(endpoint)

    async def call(self, method, params=None, session_id=None, timeout=30):
        if method == "DOM.setFileInputFiles" and (params or {}).get("files"):
            async with self.stage_lock:
                paths = [Path(p).resolve(strict=True) for p in params["files"]]
                if sum(p.stat().st_size for p in paths) > MAX_TOTAL:
                    raise ValueError("附件总大小超出限制。")
                local_paths = []
                for path in paths:
                    if not path.is_relative_to(self.root) or not path.is_file():
                        raise ValueError("仅允许传输当前工作区的投递附件。")
                    stat = path.stat()
                    if stat.st_size > MAX_FILE:
                        raise ValueError("单个附件不能超过 20 MB。")
                    key = (str(path), stat.st_mtime_ns, stat.st_size)
                    if key not in self.staged:
                        content = await asyncio.to_thread(path.read_bytes)
                        result = await super().call(
                            "Offerly.stageFile",
                            {
                                "name": path.name,
                                "content": base64.b64encode(content).decode(),
                            },
                            timeout=120,
                        )
                        local = result.get("path")
                        if not isinstance(local, str) or not local:
                            raise RuntimeError("连接助手未能接收附件，请重新连接。")
                        self.staged[key] = local
                    local_paths.append(self.staged[key])
                params = {**params, "files": local_paths}
        return await super().call(method, params, session_id, timeout)

    async def close(self):
        await super().close()
        self.staged.clear()


class BrowserAccess(UserBrowsers):
    """Local mode stays direct. Hosted mode pairs a per-visitor native helper."""

    def __init__(self, manager, origin=None, local=None):
        super().__init__()
        if local is not None:
            self.connections = dict(local)
        self.manager, self.origin = manager, origin
        self.local = dict(self.connections)
        self.helpers = {
            name: HelperConnection(name, manager.root) for name in self.local
        }
        if origin:
            self.connections = dict(self.helpers)
        self.ticket = None
        self.lock = asyncio.Lock()

    def ensure_idle(self):
        if (self.manager.public() or {}).get("active"):
            raise web.HTTPConflict(text="请先停止任务，再切换浏览器连接。")

    def status(self):
        if self.ticket and time.monotonic() >= self.ticket[2]:
            connection = self.connections[self.ticket[1]]
            if connection.state == "awaiting_helper":
                connection.state = "connection_error"
                connection.error = "连接链接已过期，请重新点击连接。"
            self.ticket = None
        return super().status()

    async def pair(self, browser, origin):
        origin = self.origin or origin
        parsed = urlsplit(origin)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
        ):
            raise web.HTTPBadRequest(text="连接助手需要 HTTPS 网站。")
        async with self.lock:
            self.ensure_idle()
            await self.connections[browser].close()
            connection = self.connections[browser] = self.helpers[browser]
            await connection.close()
            connection.state = "awaiting_helper"
            code = secrets.token_urlsafe(32)
            self.ticket = (code, browser, time.monotonic() + 600)
            uri = "offerly-connect://connect?" + urlencode(
                {"site": origin, "browser": browser, "code": code}
            )
            return {"launchUrl": uri, "expiresIn": 600, **connection.status()}

    async def begin_local(self, browser):
        async with self.lock:
            self.ensure_idle()
            previous = self.connections[browser]
            connection = self.local[browser]
            if previous is not connection:
                await previous.close()
                self.connections[browser] = connection
            self.ticket = None
            return await connection.begin()

    async def relay(self, request):
        code = request.headers.get("X-Offerly-Pair-Code", "")
        browser = request.headers.get("X-Offerly-Browser", "")
        async with self.lock:
            self.ensure_idle()
            ticket = self.ticket
            if (
                not ticket
                or not secrets.compare_digest(code.encode(), ticket[0].encode())
                or browser != ticket[1]
                or time.monotonic() >= ticket[2]
            ):
                raise web.HTTPForbidden(text="连接链接已失效，请回网站重新点击连接。")
            socket = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024, heartbeat=25)
            await socket.prepare(request)
            self.ticket = None
            connection = self.connections[browser]
            connection.state = "awaiting_permission"
            task = connection.connecting = asyncio.create_task(
                connection._connect(socket)
            )
        try:
            await task
            if connection.reader:
                await connection.reader
        finally:
            await socket.close()
        return socket

    async def disconnect(self, browser):
        async with self.lock:
            self.ensure_idle()
            if self.ticket and self.ticket[1] == browser:
                self.ticket = None
            await self.connections[browser].close()
            return self.connections[browser].status()

    async def close(self):
        self.ticket = None
        await asyncio.gather(
            *(
                connection.close()
                for connection in {*self.local.values(), *self.helpers.values()}
            )
        )
