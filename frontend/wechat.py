"""One local WeChat binding, independent of expiring phone-control sessions."""

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .private_store import PrivateStore


class _HideToken(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(
            r"(https://wx\.xtuis\.cn/)[^/\s]+\.send",
            r"\1[redacted].send",
            record.getMessage(),
        )
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_HideToken())


class WeChatSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    token: str = Field(default="", max_length=200)


class WeChatNotifications:
    endpoint = "https://wx.xtuis.cn/"

    def __init__(self, path=None, *, store_factory=PrivateStore):
        self.token = self.scope = self.sending = None
        self.confirmed = self.test_accepted = False
        self.error = ""
        self.blocked_until = 0
        self.requests = deque()
        self.lock = asyncio.Lock()
        self.generation = 0
        self.msg_id = ""
        self.store = store_factory(path) if path else None
        self.notified = set()
        self.pending_events = deque()
        if self.store:
            try:
                data = self.store.read()
                if data:
                    self.token = self.validated_token(data)
                    self.scope = secrets.token_urlsafe(24)
                    self.test_accepted = data.get("test_accepted") is True
                    self.confirmed = data.get("confirmed") is True
            except (OSError, ValueError, TypeError):
                self.error = "本机微信凭据读取失败，请重新绑定。"

    def persist(self):
        if self.store:
            self.store.write(
                {
                    "token": self.token,
                    "test_accepted": self.test_accepted,
                    "confirmed": self.confirmed,
                }
            )

    def status(self):
        return {
            "bound": bool(self.token),
            "enabled": self.confirmed,
            "test_accepted": self.test_accepted,
            "error": self.error,
            "msg_id": self.msg_id,
        }

    def clear(self):
        self.generation += 1
        self.token = self.scope = None
        self.confirmed = self.test_accepted = False
        self.error = ""
        self.msg_id = ""
        self.pending_events.clear()
        self.notified.clear()
        if self.store:
            self.store.clear()
        if self.sending and not self.sending.done():
            self.sending.cancel()

    async def close(self):
        # Shutdown and pairing changes do not revoke the saved notification identity.
        if self.sending:
            try:
                await asyncio.wait_for(asyncio.shield(self.sending), timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self.sending.cancel()
                await asyncio.gather(self.sending, return_exceptions=True)

    @staticmethod
    def validated_token(data):
        token = data.get("token", "") if isinstance(data, dict) else ""
        if not isinstance(token, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{16,200}", token.strip()
        ):
            raise ValueError("请输入“虾推啥”公众号发给你的 token。")
        return token.strip()

    def bind(self, scope, data):
        token = self.validated_token(data)
        self.clear()
        self.token, self.scope = token, scope
        self.confirmed = True
        self.persist()

    def confirm(self, scope):
        if self.scope != scope or not self.test_accepted:
            raise ValueError("请先发送测试消息，收到微信消息后再确认。")
        self.confirmed = True
        self.error = ""
        self.persist()

    async def send(self, scope, origin, title, body):
        async with self.lock:
            if not self.token or self.scope != scope:
                return False
            url = urlsplit(origin)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.port not in (None, 443)
                or url.path not in ("", "/")
                or url.query
                or url.fragment
            ):
                raise ValueError("微信提醒需要可信 HTTPS 手机入口。")
            now = time.monotonic()
            digest = hashlib.sha256((title + body).encode()).hexdigest()
            while self.requests and self.requests[0][0] < now - 3600:
                self.requests.popleft()
            if (
                now < self.blocked_until
                or sum(t > now - 60 for t, _ in self.requests) >= 5
                or sum(d == digest for _, d in self.requests) >= 3
            ):
                self.error = "发送次数受限，请稍后再试；不会自动重复发送。"
                return False
            self.requests.append((now, digest))
            token = self.token
            generation = self.generation
            content = (
                body + "\n\n返回手机协作页：" + origin.rstrip("/") + "/phone"
                "\n如微信内页面未配对，请回到原来已配对的手机浏览器页面。"
            )
            try:
                async with httpx.AsyncClient(
                    timeout=8, trust_env=False, follow_redirects=False
                ) as client:
                    response = await client.post(
                        self.endpoint + token + ".send",
                        data={"text": title, "desp": content},
                    )
                    try:
                        data = response.json()
                    except ValueError:
                        data = {}
                if (
                    self.scope != scope
                    or self.token != token
                    or self.generation != generation
                ):
                    return False
                code = data.get("code") if isinstance(data, dict) else None
                msg_id = data.get("msg_id") if isinstance(data, dict) else None
                if (
                    response.status_code == 200
                    and code == 200
                    and isinstance(msg_id, str)
                    and re.fullmatch(r"wx_\d{14}_[a-f0-9]{16}", msg_id)
                ):
                    self.error = ""
                    self.msg_id = msg_id
                    return True  # Queue acceptance, not phone delivery.
                if response.status_code == 429 or code == 429:
                    delay = response.headers.get("Retry-After") or (
                        data.get("retry_after_seconds")
                        if isinstance(data, dict)
                        else None
                    )
                    try:
                        delay = float(delay)
                        if not math.isfinite(delay) or delay <= 0:
                            raise ValueError()
                    except (TypeError, ValueError):
                        delay = 60
                    self.blocked_until = now + delay
                    self.error = "虾推啥发送次数受限，已按服务要求暂停；不会自动重发。"
                    return False
                self.error = (
                    "虾推啥未接收消息，请核对 token、公众号关注状态和账号额度。"
                )
                self.confirmed = self.test_accepted = False
                self.persist()
            except (httpx.HTTPError, ValueError):
                if (
                    self.scope == scope
                    and self.token == token
                    and self.generation == generation
                ):
                    self.error = "发送结果尚未确认，请先检查微信；不会自动重发。"
            return False

    async def test(self, scope, origin):
        if not self.token or self.scope != scope:
            raise ValueError("请先绑定“虾推啥” token。")
        generation = self.generation
        accepted = await self.send(
            scope,
            origin,
            "Offerly 微信测试",
            "这是一条测试消息。Agent 需要协助时将通过这里提醒您。",
        )
        if self.scope == scope and self.generation == generation:
            self.test_accepted = accepted
            self.persist()
        return accepted

    async def setup_test(self, data, origin):
        options = WeChatSetup.model_validate(data)
        if not origin.startswith("https://"):
            raise ValueError("手机 HTTPS 入口尚未就绪，请稍后测试。")
        if options.token:
            self.bind(secrets.token_urlsafe(24), {"token": options.token})
        accepted = await self.test(self.scope, origin)
        if accepted:
            self.confirm(self.scope)
        return {**self.status(), "accepted": accepted}

    def watch(self, job, origin):
        if not job or not self.confirmed or not origin.startswith("https://"):
            return
        events = []
        pending = job.get("pending")
        if job.get("active") and pending:
            key = json.dumps(
                [
                    job["id"],
                    job.get("current"),
                    pending.get("kind"),
                    pending.get("revision"),
                    pending.get("reason"),
                ]
            )
            body = (
                "需要你补充资料。"
                if pending.get("kind") == "information"
                else "需要你完成登录或网页操作。"
            )
            events.append((key, "Agent 需要你配合", body + " 请返回手机协作页处理。"))
        if job.get("active") and job.get("state") == "PAUSED":
            key = json.dumps([job["id"], "pause", job.get("progress", {}).get("since")])
            events.append(
                (key, "Agent 任务已暂停", "任务已暂停，请回到任务页面继续或停止。")
            )
        for index, item in enumerate(job.get("companies", [])):
            state = item.get("state")
            if state in (
                "BLOCKED",
                "FAILED",
                "INCOMPLETE",
                "EXPIRED",
                "NEEDS_INPUT",
                "SUBMISSION_UNCONFIRMED",
            ):
                key = json.dumps([job["id"], index, state])
                # Do not send candidate facts or page text to the notification provider.
                budget = "token" in item.get(
                    "reason", ""
                ).lower() or "模型请求上限" in item.get("reason", "")
                body = (
                    "模型用量达到保护上限，本次已停止。"
                    if budget
                    else "企业申请遇到阻塞或未通过核验，请查看任务详情。"
                )
                events.append((key, "Agent 任务需要检查", body))
        if not job.get("active"):
            state = job.get("state")
            if state in ("FINISHED", "STOPPED", "FAILED"):
                key = json.dumps([job["id"], "task", state])
                completed = sum(
                    c.get("state") in ("SUBMITTED", "USER_CONFIRMED")
                    for c in job.get("companies", [])
                )
                body = (
                    f"任务已结束，确认投递成功 {completed} 家。"
                    if state == "FINISHED"
                    else (
                        "任务已停止。"
                        if state == "STOPPED"
                        else "任务失败，已停止后续操作。"
                    )
                )
                events.append(
                    (
                        key,
                        {
                            "FINISHED": "Agent 任务已结束",
                            "STOPPED": "Agent 任务已停止",
                            "FAILED": "Agent 任务失败",
                        }[state],
                        body,
                    )
                )
        for key, title, body in events:
            if key in self.notified:
                continue
            self.notified.add(key)
            self.pending_events.append(
                (
                    title,
                    body + " 提醒编号：" + hashlib.sha256(key.encode()).hexdigest()[:8],
                )
            )
        if self.pending_events and (not self.sending or self.sending.done()):
            self.sending = asyncio.create_task(self.drain(origin))

    async def drain(self, origin):
        while self.pending_events and self.confirmed:
            # Queue unsent events behind the known local/provider cooldown;
            # never retry a message whose network result was uncertain.
            now = time.monotonic()
            recent = [t for t, _ in self.requests if t > now - 60]
            until = max(
                self.blocked_until, recent[-5] + 60 if len(recent) >= 5 else now
            )
            if until > now:
                await asyncio.sleep(until - now + 0.01)
                continue
            title, body = self.pending_events.popleft()
            await self.send(self.scope, origin, title, body)
