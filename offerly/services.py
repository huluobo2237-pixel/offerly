"""Application storage and the Agent service boundary."""

from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Protocol

from aiohttp import web

from frontend.private_store import PrivateStore
from .paths import DATA
from .browser_connection import UserBrowsers


class AgentBackend(Protocol):
    bridge: object | None

    def public(self) -> dict | None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def create_task(self, request: web.Request) -> web.Response: ...
    async def action(self, request: web.Request) -> web.Response: ...
    async def proxy(self, path: str, data: dict | None = None) -> dict: ...


class ApplicationServices:
    def __init__(self, root: Path | None = None, agent: AgentBackend | None = None):
        self.root = Path(root) if root is not None else DATA
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.agent = agent
        if agent is None:
            from .agent import RecruitmentAgent

            self.agent = RecruitmentAgent(self)
        self.browsers = UserBrowsers()
        self.phone = None
        self.notification_file = self.root / "wechat.dpapi"
        self.history_path = self.root / "applications.sqlite"
        self.model_store = PrivateStore(self.root / "model.dpapi")

    @property
    def agent_available(self) -> bool:
        return self.agent is not None

    @property
    def bridge(self):
        return self.agent.bridge if self.agent else None

    @property
    def job(self):
        return self.public()

    def public(self):
        return copy.deepcopy(self.agent.public()) if self.agent else None

    async def start(self):
        if self.agent:
            await self.agent.start()

    async def close(self):
        if self.agent:
            await self.agent.close()
        await self.browsers.close()

    def require_agent(self) -> AgentBackend:
        if self.agent is None:
            raise web.HTTPServiceUnavailable(
                text="Agent 正在重构，当前可使用资料、岗位、简历和手机配对功能。"
            )
        return self.agent

    async def create_task(self, request):
        return await self.require_agent().create_task(request)

    async def action(self, request):
        return await self.require_agent().action(request)

    async def proxy(self, path, data=None):
        return await self.require_agent().proxy(path, data)
