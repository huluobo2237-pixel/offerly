"""Consent-based CDP attachment to the user's real Edge/Chrome browser.

No temporary browser, profile cloning, extension copying or browser shutdown.
A single approved upstream connection is shared through a loopback CDP bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Literal

from aiohttp import WSMsgType, web
from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

BrowserName = Literal["edge", "chrome"]
PLUGIN_IDS = {
    "edge": "jekomlapnmbblkcfcjkfnagdnnhcnbdo",
    "chrome": "ldohbgcnonoffldimgdngkojkejibina",
}


INSTALL_URLS = {
    "edge": "https://microsoftedge.microsoft.com/addons/search/%E5%A1%94%E5%A1%94%E7%BD%91%E7%94%B3%E7%A5%9E%E5%99%A8",
    "chrome": "https://chromewebstore.google.com/search/%E5%A1%94%E5%A1%94%E7%BD%91%E7%94%B3%E7%A5%9E%E5%99%A8%20-%20AI%E4%B8%80%E9%94%AE%E6%B1%82%E8%81%8C%E5%A1%AB%E7%AE%80%E5%8E%86?hl=zh-CN&utm_source=ext_sidebar",
}


class BrowserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser: BrowserName


class OpenBrowserRequest(BrowserRequest):
    page: Literal["setup", "plugin"] = "setup"


def user_data_dir(browser: BrowserName) -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return local / (
        "Microsoft/Edge/User Data" if browser == "edge" else "Google/Chrome/User Data"
    )


def browser_executable(browser: BrowserName) -> Path | None:
    suffix = (
        "Microsoft/Edge/Application/msedge.exe"
        if browser == "edge"
        else "Google/Chrome/Application/chrome.exe"
    )
    for base in [
        os.environ.get("PROGRAMFILES", "C:/Program Files"),
        os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")),
    ]:
        candidate = Path(base) / suffix
        if candidate.is_file():
            return candidate
    return None


def native_endpoint(profile_root: Path) -> str:
    """Only the user's browser-generated port file can authorize discovery."""
    lines = (
        (profile_root / "DevToolsActivePort")
        .read_text(encoding="utf-8")[:2048]
        .splitlines()
    )
    if (
        len(lines) < 2
        or not lines[0].isdigit()
        or not 0 < int(lines[0]) < 65536
        or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", lines[1])
    ):
        raise ValueError("浏览器连接信息无效，请重新开启远程调试。")
    return f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"


class UserBrowserConnection:
    def __init__(self, browser: BrowserName, profile_root: Path | None = None):
        self.browser = browser
        self.profile_root = profile_root or user_data_dir(browser)
        self.socket = self.reader = self.connecting = None
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.state = "not_connected"
        self.error = ""
        self.browser_version = ""
        self.agent_ready = False
        self.runner = None
        self.clients: set = set()
        self.client_sessions: dict[object, set[str]] = {}
        self.page_clients = {}
        self.agent_mode = False
        self.owned_targets: set[str] = set()
        self.sessions: set[str] = set()
        self.session_targets: dict[str, str] = {}
        self.popup_tasks: dict[str, asyncio.Task] = {}
        self.token = secrets.token_urlsafe(32)
        self.http_url = self.ws_url = ""

    def status(self):
        return {
            "browser": self.browser,
            "installed": browser_executable(self.browser) is not None,
            "connected": self.state == "connected"
            and self.reader is not None
            and not self.reader.done(),
            "state": self.state,
            "error": self.error,
            "debuggingEnabled": (self.profile_root / "DevToolsActivePort").is_file(),
            "version": self.browser_version,
            "sameUserProfile": True,
            "agentBrowserReady": self.agent_ready,
        }

    async def begin(self):
        if self.status()["connected"] or self.connecting and not self.connecting.done():
            return self.status()
        self.error = ""
        try:
            endpoint = native_endpoint(self.profile_root)
        except (OSError, ValueError):
            self.state = "setup_required"
            self.error = (
                "请在当前浏览器的远程调试页面勾选允许连接，然后点击“连接我的浏览器”。"
            )
            return self.status()
        self.state = "awaiting_permission"
        self.connecting = asyncio.create_task(self._connect(endpoint))
        return self.status()

    async def _connect(self, endpoint):
        try:
            self.socket = await self.open_socket(endpoint)
            self.reader = asyncio.create_task(self._read())
            info = await self.call("Browser.getVersion", timeout=1800)
            product = info.get("product", "")
            # Do not silently connect the Chrome choice to Edge (or the reverse).
            is_edge = "Edg/" in info.get("userAgent", "") or "Edg/" in product
            if (
                self.browser == "edge"
                and not is_edge
                or self.browser == "chrome"
                and is_edge
            ):
                raise ValueError("连接的浏览器与所选类型不一致。")
            self.browser_version = product
            await self._start_bridge()
            self.state = "connected"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = (
                "permission_required"
                if isinstance(exc, TimeoutError)
                else "connection_error"
            )
            self.error = (
                "本次连接请求已等待超时，旧授权提示已失效。请重新发起连接并允许新提示。"
                if isinstance(exc, TimeoutError)
                else "未能连接当前浏览器。请确认远程调试已开启，并在浏览器中允许连接。"
            )
            if isinstance(exc, ValueError):
                self.error = str(exc)
            if self.socket:
                await self.socket.close()

    async def open_socket(self, endpoint):
        return await connect(endpoint, open_timeout=1800, max_size=32 * 1024 * 1024)

    async def call(self, method, params=None, session_id=None, timeout=30):
        if self.socket is None:
            raise ConnectionError("浏览器尚未连接。")
        if self.reader is not None and self.reader.done():
            raise ConnectionError("浏览器连接已断开，请重新连接。")
        self.sequence += 1
        ident = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        message = {"id": ident, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            await self.socket.send(json.dumps(message))
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(ident, None)
            if future.done() and not future.cancelled():
                future.exception()
            elif not future.done():
                future.cancel()

    async def _read(self):
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                RuntimeError(
                                    message["error"].get("message", "CDP error")
                                )
                            )
                        else:
                            future.set_result(message.get("result", {}))
                    continue
                # The Agent sees only its own tabs; personal tabs remain outside its session.
                target = message.get("params", {}).get("targetInfo", {}).get(
                    "targetId"
                ) or message.get("params", {}).get("targetId")
                session = message.get("sessionId")
                info = message.get("params", {}).get("targetInfo", {})
                # Extension popups are not application tabs. Their creation/closure
                # must not replace Browser Use's focus or enter the phone tab list.
                if info.get("url", "").startswith("chrome-extension://"):
                    continue
                if (
                    info.get("openerId") in self.owned_targets
                    or target
                    and session in self.sessions
                ):
                    self.owned_targets.add(target)
                attached = message.get("method") == "Target.attachedToTarget"
                if target and target not in self.owned_targets and not session:
                    continue
                if session and session not in self.sessions:
                    continue
                if attached:
                    attached_session = message["params"]["sessionId"]
                    self.sessions.add(attached_session)
                    self.session_targets[attached_session] = target
                    if not session:
                        # Root attachments include phone viewers. Replay only
                        # to the Agent client that requested its own attachment.
                        continue
                if (
                    message.get("method")
                    in ("Target.targetCreated", "Target.targetInfoChanged")
                    and info.get("type") == "page"
                    and info.get("openerId") in self.owned_targets
                    and target not in self.popup_tasks
                    and target not in self.session_targets.values()
                ):
                    # Discovery alone does not register a page in Browser Use.
                    # Attach only children of owned pages, outside the reader so
                    # it can keep receiving the CDP responses needed by attach.
                    task = asyncio.create_task(self._attach_popup(target))
                    self.popup_tasks[target] = task
                    task.add_done_callback(
                        lambda _, t=target: self.popup_tasks.pop(t, None)
                    )
                for client, page_session in tuple(self.page_clients.items()):
                    if session == page_session and not client.closed:
                        await self._send_client(
                            client,
                            {k: v for k, v in message.items() if k != "sessionId"},
                        )
                for client in tuple(self.clients):
                    owned = self.client_sessions.get(client, set())
                    detached_session = (
                        message.get("params", {}).get("sessionId")
                        if message.get("method") == "Target.detachedFromTarget"
                        else None
                    )
                    if session and session not in owned:
                        continue
                    if detached_session and detached_session not in owned:
                        continue
                    if not client.closed:
                        await self._send_client(client, message)
                if message.get("method") == "Target.targetDestroyed":
                    self.owned_targets.discard(target)
                    self.agent_ready = False
                    for ident, owner in tuple(self.session_targets.items()):
                        if owner == target:
                            self.session_targets.pop(ident, None)
                            self.sessions.discard(ident)
                elif message.get("method") == "Target.detachedFromTarget":
                    ident = message.get("params", {}).get("sessionId")
                    self.sessions.discard(ident)
                    self.session_targets.pop(ident, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Browser upstream reader stopped: %s", type(exc).__name__
            )
        finally:
            if self.state == "connected":
                self.state, self.error = (
                    "disconnected",
                    "浏览器连接已断开，请重新连接。",
                )
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError("浏览器连接已断开。"))

    async def _send_client(self, client, message):
        """A departing viewer must never tear down the shared browser reader."""
        try:
            await asyncio.wait_for(client.send_json(message), timeout=2)
        except (ConnectionError, RuntimeError, TimeoutError):
            self.clients.discard(client)
            self.page_clients.pop(client, None)
            logging.getLogger(__name__).info("Detached an unavailable browser viewer")

    async def _start_bridge(self):
        if self.runner is not None:
            return
        app = web.Application()
        app.router.add_get(
            "/" + self.token + "/devtools/browser/session", self._websocket
        )
        app.router.add_get("/" + self.token + "/json/list", self._target_list)
        app.router.add_get(
            "/" + self.token + "/devtools/page/{target}", self._page_websocket
        )
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.http_url = f"http://127.0.0.1:{port}/{self.token}"
        self.ws_url = f"ws://127.0.0.1:{port}/{self.token}/devtools/browser/session"

    async def _attach_popup(self, target):
        for client in tuple(self.clients):
            if client.closed or target not in self.owned_targets:
                continue
            try:
                await self._forward(
                    {
                        "method": "Target.attachToTarget",
                        "params": {"targetId": target, "flatten": True},
                    },
                    client=client,
                )
            except (ConnectionError, ConnectionClosed, RuntimeError, TimeoutError):
                logging.getLogger(__name__).warning(
                    "Could not attach new application tab %s", target[:8]
                )

    async def create_tab(self, url="about:blank", client=None) -> str:
        if not self.status()["connected"]:
            raise ConnectionError("请先连接你正在使用的浏览器。")
        result = await self.call("Target.createTarget", {"url": url})
        target = result["targetId"]
        self.owned_targets.add(target)
        info = (await self.call("Target.getTargetInfo", {"targetId": target}))[
            "targetInfo"
        ]
        for recipient in tuple(self.clients):
            if not recipient.closed:
                await self._send_client(
                    recipient,
                    {"method": "Target.targetCreated", "params": {"targetInfo": info}},
                )
        active_clients = (
            [client]
            if client is not None
            else [c for c in self.clients if not c.closed]
        )
        if active_clients:
            # Root auto-attach is deliberately isolated from personal tabs. New
            # Agent tabs must still generate an attached event for SessionManager.
            for owner in active_clients:
                await self._forward(
                    {
                        "method": "Target.attachToTarget",
                        "params": {"targetId": target, "flatten": True},
                    },
                    client=owner,
                )
        return target

    async def reconcile_targets(self):
        infos = (await self.call("Target.getTargets")).get("targetInfos", [])
        live = {t["targetId"] for t in infos}
        self.owned_targets.intersection_update(live)
        for ident, target in tuple(self.session_targets.items()):
            if target not in live:
                self.session_targets.pop(ident, None)
                self.sessions.discard(ident)
        pages = [
            t
            for t in infos
            if t["targetId"] in self.owned_targets
            and t.get("type") == "page"
            and not t.get("url", "").startswith("chrome-extension://")
        ]
        if not pages:
            self.agent_ready = False
        return pages

    async def _forward(self, request, client=None):
        method, params = request.get("method", ""), request.get("params", {})
        if method in {
            "Browser.close",
            "Browser.crash",
            "Browser.resetPermissions",
            "Target.disposeBrowserContext",
            "Target.createBrowserContext",
        }:
            raise ValueError("不能关闭或重置用户浏览器。")
        target, session = params.get("targetId"), request.get("sessionId")
        if target and target not in self.owned_targets:
            raise ValueError("只能操作本轮创建的投递标签页。")
        # Only Target.* uses params.sessionId for a CDP target session.
        # Page.screencastFrameAck uses the same name for an integer frame token;
        # rejecting that token stalls screencast after the first frames.
        if (
            method.startswith("Target.")
            and params.get("sessionId")
            and params["sessionId"] not in self.sessions
        ):
            raise ValueError("标签页会话不属于本轮投递。")
        if session and session not in self.sessions:
            raise ValueError("标签页会话不属于本轮投递。")
        if (
            self.agent_mode
            and session
            and method in ("Input.dispatchMouseEvent", "Input.dispatchKeyEvent")
        ):
            await self._guard_submission(method, params, session)
        if method == "Target.setAutoAttach" and not session:
            # Browser Use attaches explicitly; never auto-attach personal tabs.
            return {}
        if method == "Target.createTarget":
            return {
                "targetId": await self.create_tab(
                    params.get("url", "about:blank"), client=client
                )
            }
        result = await self.call(method, params, session)
        if method == "Target.getTargets":
            result["targetInfos"] = [
                t
                for t in result.get("targetInfos", [])
                if t["targetId"] in self.owned_targets
                and t.get("type") == "page"
                and not t.get("url", "").startswith("chrome-extension://")
            ]
        if method == "Target.attachToTarget":
            self.sessions.add(result["sessionId"])
            self.session_targets[result["sessionId"]] = params["targetId"]
            if client is not None:
                self.client_sessions.setdefault(client, set()).add(result["sessionId"])
                info = (
                    await self.call(
                        "Target.getTargetInfo", {"targetId": params["targetId"]}
                    )
                )["targetInfo"]
                await self._send_client(
                    client,
                    {
                        "method": "Target.attachedToTarget",
                        "params": {
                            "sessionId": result["sessionId"],
                            "targetInfo": info,
                            "waitingForDebugger": False,
                        },
                        **({"sessionId": session} if session else {}),
                    },
                )
        if method == "Target.detachFromTarget":
            self.sessions.discard(params.get("sessionId", ""))
            self.session_targets.pop(params.get("sessionId", ""), None)
        if method == "Page.enable" and client is not None and not client.closed:
            # Browser Use registers the target before enabling page monitoring.
            # Replay current metadata here: navigation may have finished while
            # its async attach handler was still registering the new target.
            owner = self.session_targets.get(session)
            if owner in self.owned_targets:
                info = (await self.call("Target.getTargetInfo", {"targetId": owner}))[
                    "targetInfo"
                ]
                await self._send_client(
                    client,
                    {
                        "method": "Target.targetInfoChanged",
                        "params": {"targetInfo": info},
                    },
                )
        return result

    async def _guard_submission(self, method, params, session):
        mouse = (
            method == "Input.dispatchMouseEvent"
            and params.get("type") == "mousePressed"
        )
        enter = method == "Input.dispatchKeyEvent" and params.get("key") == "Enter"
        if not mouse and not enter:
            return
        point = (
            (
                "document.elementFromPoint("
                + json.dumps(params.get("x", 0))
                + ","
                + json.dumps(params.get("y", 0))
                + ")"
            )
            if mouse
            else "document.activeElement"
        )
        expression = (
            "(()=>{const e="
            + point
            + ';const b=e?.closest("button,input[type=submit],a,[role=button]");const text=(b?.innerText||b?.value||b?.getAttribute("aria-label")||"").trim();const form=document.querySelectorAll("input:not([type=hidden]):not([type=password]),textarea,select").length>1;return form&&(/提交|确认投递|立即投递|发送申请|\\bsubmit\\b|send\\s+application/i.test(text)'
            + (
                '||Boolean(e?.form)&&e?.type!=="password"&&e?.getAttribute("role")!=="combobox"&&e?.tagName!=="SELECT"'
                if enter
                else ""
            )
            + ");})()"
        )
        value = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session,
        )
        if value.get("result", {}).get("value"):
            raise ValueError(
                "最终提交必须由用户操作。请调用 request_review，等待用户检查并自行提交。"
            )

    async def _target_list(self, request):
        targets = (await self.call("Target.getTargets"))["targetInfos"]
        return web.json_response(
            [
                {
                    "id": t["targetId"],
                    "type": t["type"],
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "webSocketDebuggerUrl": self.ws_url.replace(
                        "/browser/session", "/page/" + t["targetId"]
                    ),
                }
                for t in targets
                if t["targetId"] in self.owned_targets and t["type"] == "page"
            ]
        )

    async def _page_websocket(self, request):
        target = request.match_info["target"]
        if target not in self.owned_targets or request.headers.get("Origin"):
            raise web.HTTPForbidden()
        session = (
            await self.call(
                "Target.attachToTarget", {"targetId": target, "flatten": True}
            )
        )["sessionId"]
        self.sessions.add(session)
        ws = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        await ws.prepare(request)
        self.page_clients[ws] = session
        try:
            async for frame in ws:
                if frame.type == WSMsgType.TEXT:
                    data = json.loads(frame.data)
                    try:
                        result = await self._forward({**data, "sessionId": session})
                        await self._send_client(
                            ws, {"id": data["id"], "result": result}
                        )
                    except Exception as exc:
                        await self._send_client(
                            ws,
                            {
                                "id": data.get("id"),
                                "error": {"code": -32000, "message": str(exc)},
                            },
                        )
        finally:
            self.page_clients.pop(ws, None)
            self.sessions.discard(session)
            try:
                await self.call("Target.detachFromTarget", {"sessionId": session})
            except Exception:
                pass
        return ws

    async def _websocket(self, request):
        if request.headers.get("Origin"):
            raise web.HTTPForbidden(text="只接受本机 Agent 连接。")
        ws = web.WebSocketResponse(max_msg_size=32 * 1024 * 1024)
        await ws.prepare(request)
        self.clients.add(ws)
        tasks = set()

        async def dispatch(data):
            try:
                result = await self._forward(data, client=ws)
                response = {"id": data["id"], "result": result}
            except Exception as exc:
                response = {
                    "id": data.get("id"),
                    "error": {"code": -32000, "message": str(exc)},
                }
            if data.get("sessionId"):
                response["sessionId"] = data["sessionId"]
            if not ws.closed:
                await self._send_client(ws, response)

        try:
            async for frame in ws:
                if frame.type == WSMsgType.TEXT:
                    data = json.loads(frame.data)
                    task = asyncio.create_task(dispatch(data))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        finally:
            self.clients.discard(ws)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for ident in self.client_sessions.pop(ws, set()):
                try:
                    await self.call(
                        "Target.detachFromTarget", {"sessionId": ident}, timeout=2
                    )
                except (ConnectionError, ConnectionClosed, RuntimeError, TimeoutError):
                    pass
                self.sessions.discard(ident)
                self.session_targets.pop(ident, None)
        return ws

    async def browser_session(self):
        """The only Browser Use factory: attach, never launch or clone profiles."""
        if not self.status()["connected"]:
            raise ConnectionError("请先连接你正在使用的浏览器。")
        self.agent_ready = False
        if not await self.reconcile_targets():
            await self.create_tab()
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
        os.environ.setdefault("BROWSER_USE_VERSION_CHECK", "false")
        from .browser_session import ConnectedBrowserSession

        browser = ConnectedBrowserSession(
            cdp_url=self.ws_url,
            is_local=True,
            headless=False,
            no_viewport=True,
            keep_alive=True,
            enable_default_extensions=False,
        )
        try:
            await browser.start()
            # Clear overrides left on owned tabs by an earlier server session.
            # Personal tabs remain outside the bridge and are never changed.
            for target in tuple(self.owned_targets):
                session = await browser.get_or_create_cdp_session(
                    target_id=target, focus=False
                )
                await session.cdp_client.send.Emulation.clearDeviceMetricsOverride(
                    session_id=session.session_id
                )
        except Exception:
            await browser.stop()
            raise
        self.agent_ready = True
        return browser

    async def close(self):
        if self.connecting and not self.connecting.done():
            self.connecting.cancel()
            await asyncio.gather(self.connecting, return_exceptions=True)
        popup_tasks = tuple(self.popup_tasks.values())
        for task in popup_tasks:
            task.cancel()
        await asyncio.gather(*popup_tasks, return_exceptions=True)
        self.popup_tasks.clear()
        for client in tuple(self.clients) + tuple(self.page_clients):
            await client.close()
        if self.socket:
            await self.socket.close()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
        self.runner = self.socket = self.reader = self.connecting = None
        self.state = "not_connected"
        self.agent_ready = False
        self.owned_targets.clear()
        self.sessions.clear()
        self.session_targets.clear()
        self.client_sessions.clear()


class UserBrowsers:
    def __init__(self):
        self.connections = {
            name: UserBrowserConnection(name) for name in ("edge", "chrome")
        }

    def status(self):
        return {
            name: connection.status() for name, connection in self.connections.items()
        }

    async def open(self, data: OpenBrowserRequest):
        executable = browser_executable(data.browser)
        if executable is None:
            raise ValueError("未找到所选浏览器，请安装或选择另一种浏览器。")
        url = (
            INSTALL_URLS[data.browser]
            if data.page == "plugin"
            else f"{data.browser if data.browser == 'edge' else 'chrome'}://inspect/#remote-debugging"
        )
        connection = self.connections[data.browser]
        if connection.status()["connected"]:
            # Open in the actual consented instance, never an executable-selected profile.
            infos = (await connection.call("Target.getTargets"))["targetInfos"]
            target = next((t["targetId"] for t in infos if t.get("url") == url), None)
            if not target:
                target = (await connection.call("Target.createTarget", {"url": url}))[
                    "targetId"
                ]
            await connection.call("Target.activateTarget", {"targetId": target})
            actual = (
                await connection.call("Target.getTargetInfo", {"targetId": target})
            )["targetInfo"].get("url", "")
            return {
                **connection.status(),
                "openedUrl": actual,
                "navigationVerified": actual == url,
            }
        # Reuse the last personal profile, including non-Default profiles. Do not
        # launch a temporary user-data directory or a separate automation browser.
        args = [str(executable), "--new-tab"]
        try:
            profile = (
                json.loads(
                    (connection.profile_root / "Local State").read_text(
                        encoding="utf-8"
                    )
                )
                .get("profile", {})
                .get("last_used", "")
            )
            if isinstance(profile, str) and re.fullmatch(
                r"Default|Profile \d+", profile
            ):
                args.append("--profile-directory=" + profile)
        except (OSError, ValueError, TypeError):
            pass
        subprocess.Popen(
            [*args, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {**connection.status(), "openedUrl": url, "navigationVerified": False}

    async def close(self):
        await asyncio.gather(
            *(connection.close() for connection in self.connections.values())
        )
