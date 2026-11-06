"""Sequential application workflow built on Browser Use with a Chinese recruitment system prompt."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from datetime import datetime, timezone

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tata_program import TataProgram
from .orchestration import AutonomousWorkflow


def now():
    return datetime.now(timezone.utc).isoformat()


class UserAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    current: int = Field(ge=0)
    feedback: str = Field(default="", max_length=4000)
    confirmSubmitted: bool = False


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=1200)


class NoParams(BaseModel):
    pass


class CheckboxInput(BaseModel):
    index: int = Field(ge=1)
    checked: bool = True


class RecruitmentAgent(AutonomousWorkflow):
    def __init__(self, services):
        self.services = services
        self.task = self.job = self.browser = self.agent = self.remote = None
        self.connection = None
        self.lock = asyncio.Lock()
        self.action_lock = asyncio.Lock()
        self.signal = asyncio.Event()
        self.signal_value = None
        self.deadline = 0
        self.request_count = self.prompt_tokens = self.completion_tokens = 0
        self.files = []
        self.model_factory = None
        self.last_model_error = ""
        self.tata_program = TataProgram()

    @property
    def bridge(self):
        return self if self.browser else None

    @property
    def state(self):
        return self.job["state"] if self.job else "IDLE"

    @property
    def browser_http_url(self):
        return self.connection.http_url if self.connection else ""

    async def start(self):
        # Recover explicit outcomes from the last pre-ledger checkpoint only.
        path = self.services.root / "agent" / "latest.json"
        try:
            saved = (
                json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            )
            for item in saved.get("companies", []):
                job = item.get("job") or {}
                if (
                    item.get("state") in ("USER_CONFIRMED", "SKIPPED")
                    and job.get("url")
                    and job.get("title")
                ):
                    self.application_history.record_outcome(
                        item["name"],
                        job["title"],
                        job["url"],
                        "user_confirmed"
                        if item["state"] == "USER_CONFIRMED"
                        else "abandoned",
                        item.get("reason", ""),
                    )
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def public(self):
        return copy.deepcopy(self.job)

    def progress(self, text):
        if not self.job:
            return
        p = self.job["progress"]
        p.update(activity=text, since=now(), updated_at=now())
        p["timeline"] = (p["timeline"] + [{"at": now(), "text": text}])[-30:]
        self.job["phase"] = text
        self.job["api_calls"] = self.request_count
        self.job["usage"] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
        self.checkpoint()

    def checkpoint(self):
        if not self.job:
            return
        folder = self.services.root / "agent"
        folder.mkdir(exist_ok=True)
        path = folder / "latest.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    async def wait_user(self, kind, reason):
        self.signal.clear()
        self.signal_value = None
        self.deadline = time.monotonic() + 1800
        self.connection.agent_mode = False
        self.job.update(state="WAITING_HUMAN", pending={"kind": kind, "reason": reason})
        self.job["companies"][self.job["current"]]["state"] = "WAITING_HUMAN"
        self.progress(reason)
        try:
            await asyncio.wait_for(self.signal.wait(), 1800)
        except TimeoutError:
            raise RuntimeError(
                "等待协助超时；网页与已填写内容保留，请重新开始本轮。"
            ) from None
        result = self.signal_value
        if result["action"] == "stop":
            raise asyncio.CancelledError()
        self.connection.agent_mode = True
        self.job.update(state="RUNNING", pending=None)
        self.job["companies"][self.job["current"]]["state"] = "RUNNING"
        return result

    async def application_page(self):
        """Recover the owned application tab if a native extension popup lost focus."""
        page = await self.browser.get_current_page()
        infos = (await self.connection.call("Target.getTargets"))["targetInfos"]
        valid = {
            t["targetId"]: t
            for t in infos
            if t["type"] == "page"
            and t["targetId"] in self.connection.owned_targets
            and t.get("url", "").startswith(("http://", "https://"))
        }
        if page and page._target_id in valid:
            self.application_target = page._target_id
            return page
        target = getattr(self, "application_target", None)
        if target not in valid:
            target = next(iter(valid)) if len(valid) == 1 else None
        if not target:
            raise web.HTTPConflict(
                text="申请标签页已关闭或无法确认，请在电脑端打开本轮申请页面后重试。"
            )
        from browser_use.browser.events import SwitchTabEvent

        event = self.browser.event_bus.dispatch(SwitchTabEvent(target_id=target))
        await event
        await event.event_result(raise_if_any=True, raise_if_none=False)
        page = await self.browser.get_current_page()
        if not page or page._target_id != target:
            raise web.HTTPConflict(text="申请标签页正在恢复，请稍后重连画面。")
        self.application_target = target
        return page

    async def plugin(self):
        page = await self.application_page()
        url = await self.browser.get_current_page_url()
        if getattr(self, "plugin_url", None) == url:
            return (
                "本页已使用过塔塔。请核对结果，不要重复覆盖；如需重填，请请求用户协助。"
            )
        result = await self.tata_program.run(
            self.connection, page, progress=self.progress
        )
        if result.fill_attempted:
            self.plugin_url = url
        if not result.ok:
            raise RuntimeError(
                "塔塔固定程序在“" + result.stage + "”步骤停止：" + result.message
            )
        self.progress("塔塔正在填入简历，等待插件完成")
        before = result.filled_before
        last = None
        stable = 0
        for i in range(90):
            await asyncio.sleep(1)
            if (await self.application_page())._target_id != page._target_id:
                raise RuntimeError("申请标签页已变化，请人工确认当前页面。")
            snapshot = json.loads(
                await page.evaluate(
                    '() => {const e=[...document.querySelectorAll("input:not([type=hidden]):not([type=password]):not([type=file]),textarea,select")];const b=document.querySelector("#filling");return {filled:e.filter(x=>["checkbox","radio"].includes(x.type)?x.checked:Boolean(x.value?.trim())).length,busy:Boolean(b?.disabled||b?.classList.contains("disabled"))};}'
                )
            )
            stable = stable + 1 if snapshot == last else 0
            last = snapshot
            if not snapshot["busy"] and (
                i >= 8
                and snapshot["filled"] != before
                and stable >= 3
                or i >= 20
                and stable >= 3
            ):
                return f"已触发塔塔，当前 {snapshot['filled']} 个输入项有值。请交给用户检查；不要再次上传附件覆盖填写结果。不要逐字段重填或重复点击塔塔；这不代表申请已提交。"
        raise RuntimeError("塔塔执行超时，请人工检查插件状态；不会反复点击覆盖。")

    def tools(self):
        from browser_use import ActionResult, Tools

        owner = self

        class ApplicationTools(Tools):
            async def act(self, action, browser_session, **kwargs):
                command = action.model_dump(exclude_unset=True)
                if set(command) & {"ask_user", "request_review", "use_tata"}:
                    # Human assistance waits 30 minutes; the generic 180s cap
                    # must not cancel it and resume the model behind the user.
                    kwargs["action_timeout"] = 1950
                upload = None
                if "navigate" in command:
                    url = command["navigate"].get("url", "")
                    saved = owner.discovered.get(url, {})
                    if owner.blacklisted(url, saved.get("company", "")):
                        return ActionResult(
                            error="该链接属于用户黑名单企业，已在打开前拦截。请使用其他候选岗位。"
                        )
                if "upload_file" in command:
                    try:
                        await owner.check_job_page()
                    except ValueError as exc:
                        return ActionResult(error=str(exc))
                    params = command["upload_file"]
                    material = next(
                        (
                            f
                            for f in owner.files[owner.job["current"]]
                            if f["path"] == params.get("path")
                        ),
                        {},
                    )
                    upload = {
                        "current": owner.job["current"],
                        "company": owner.job["companies"][owner.job["current"]]["name"],
                        "file": material.get("name", "未授权附件"),
                        "type": material.get("type", ""),
                        "index": params.get("index"),
                        "target": f"网页元素 #{params.get('index')}",
                        "state": "pending",
                    }
                    owner.job.setdefault("uploads", []).append(upload)
                    owner.job["uploads"] = owner.job["uploads"][-40:]
                    batch = owner.current_batch()
                    if batch and (
                        len(batch["jobs"]) < batch["size"]
                        or not owner.job["companies"][owner.job["current"]].get("job")
                    ):
                        error = (
                            "本批岗位尚未确认齐，请先继续 confirm_job。"
                            if len(batch["jobs"]) < batch["size"]
                            else "本批已齐，但当前岗位尚未登记。请先打开本岗详情并 choose_job，不要重复上传。"
                        )
                        owner.upload_result(upload, error, blocked=True)
                        return ActionResult(error=error)
                    if command["upload_file"].get("path") not in [
                        f["path"] for f in owner.files[owner.job["current"]]
                    ]:
                        error = "该文件不是当前岗位对应附件，请使用本岗 generate_resume 或 choose_job 返回的路径。"
                        owner.upload_result(upload, error, blocked=True)
                        return ActionResult(error=error)
                    if browser_session:
                        try:
                            node = (await browser_session.get_selector_map()).get(
                                params.get("index")
                            )
                            if node:
                                upload["target"] = (
                                    node.get_meaningful_text_for_llm()[:160]
                                    or upload["target"]
                                )
                                upload["accept"] = node.attributes.get("accept", "")
                        except Exception:
                            pass  # The upstream upload tool still validates the current selector.
                if "click" in command:
                    params = command["click"]
                    label = ""
                    if params.get("index"):
                        node = (await browser_session.get_selector_map()).get(
                            params["index"]
                        )
                        if node:
                            label = (
                                node.get_meaningful_text_for_llm()
                                + " "
                                + node.attributes.get("aria-label", "")
                                + " "
                                + node.attributes.get("value", "")
                            )
                            if re.search(
                                r"隐私|privacy", label, re.I
                            ) and node.tag_name.lower() in ("input", "label", "span"):
                                from .checkbox import set_checkbox

                                result = await set_checkbox(
                                    browser_session, params["index"]
                                )
                                if result.get("recognized"):
                                    return ActionResult(
                                        extracted_content=json.dumps(
                                            result, ensure_ascii=False
                                        )
                                    )
                    elif params.get("coordinate_x") is not None:
                        page = await browser_session.get_current_page()
                        label = await page.evaluate(
                            '(x,y) => {const e=document.elementFromPoint(x,y)?.closest("button,a,[role=button],input[type=submit]");return e?.innerText||e?.value||"";}',
                            params["coordinate_x"],
                            params.get("coordinate_y", 0),
                        )
                    if re.search(
                        r"提交|确认投递|发送申请|\bsubmit\b|send\s+application",
                        label,
                        re.I,
                    ):
                        return ActionResult(
                            error="此按钮可能最终提交申请。请调用 request_review，让用户亲自检查并提交。"
                        )
                try:
                    result = await super().act(
                        action, browser_session=browser_session, **kwargs
                    )
                except Exception as exc:
                    if upload:
                        owner.upload_result(
                            upload, f"{type(exc).__name__}: {str(exc)[:500]}"
                        )
                    raise
                if upload:
                    owner.upload_result(upload, result.error)
                return result

        tools = ApplicationTools(
            exclude_actions=[
                "extract",
                "close",
                "find_text",
                "screenshot",
                "evaluate",
                "write_file",
                "read_file",
                "replace_file",
                "save_as_pdf",
            ]
        )

        @tools.action(
            "将已识别的复选框设为指定勾选状态并回读 checked；已满足时不重复点击。用于申请入口的隐私复选框；最终签署、承诺或提交交给用户。",
            param_model=CheckboxInput,
        )
        async def set_checkbox(params: CheckboxInput):
            from .checkbox import set_checkbox as apply_checkbox

            result = await apply_checkbox(self.browser, params.index, params.checked)
            return ActionResult(
                extracted_content=json.dumps(result, ensure_ascii=False)
            )

        @tools.action(
            "进入简历表单后，先上传全部所需附件并等待解析结束，再调用此工具。在当前标签页唤起塔塔并一键填入，每页一次；之后交用户检查。没有上传项时可直接调用。",
            param_model=NoParams,
            terminates_sequence=True,
        )
        async def use_tata(params: NoParams):
            try:
                await self.check_job_page()
            except ValueError as exc:
                return ActionResult(error=str(exc))
            batch = self.current_batch()
            if batch and (
                len(batch["jobs"]) < batch["size"]
                or not self.job["companies"][self.job["current"]].get("job")
            ):
                return ActionResult(
                    error="本批岗位尚未全部确认或当前岗位未激活，请先完成本批选岗。"
                )
            try:
                return ActionResult(extracted_content=await self.plugin())
            except (RuntimeError, ConnectionError, web.HTTPException) as exc:
                result = await self.wait_user(
                    "login",
                    exc.text if isinstance(exc, web.HTTPException) else str(exc),
                )
                if result["action"] in ("complete", "skip"):
                    return self.target_response(result)
                return ActionResult(
                    extracted_content="用户已协助插件，请重新观察页面。补充说明："
                    + result["feedback"]
                )

        @tools.action(
            "遇到登录、验证码、缺少资料或插件需要操作时，暂停并请求用户通过手机协助。",
            param_model=Reason,
            terminates_sequence=True,
        )
        async def ask_user(params: Reason):
            result = await self.wait_user("login", params.reason)
            if result["action"] in ("complete", "skip"):
                return self.target_response(result)
            skipped = await self.skip_existing_application()
            if skipped is not None:
                return skipped
            return ActionResult(
                extracted_content="用户已交还浏览器。请重新观察并核实结果。补充说明："
                + result["feedback"]
            )

        @tools.action(
            "当前岗位官网显示已投递、已申请，或出现已有职位在流程中不能再次投递等明确限制时调用。务必在提示仍显示时调用，再关闭提示或切换岗位；工具更新当前岗位并继续下一岗，不计入新增投递数。",
            param_model=NoParams,
            terminates_sequence=True,
        )
        async def skip_applied_job(params: NoParams):
            result = await self.skip_existing_application()
            return (
                result
                if result is not None
                else ActionResult(
                    error="当前登记岗位与网页不一致，或没有可核实的已投递/限制投递提示。不要把其他网页当作当前岗位；返回 task 上下文中的当前岗位处理，需跳过时先在该岗位提示仍显示时调用本工具，再进入下一岗。"
                )
            )

        @tools.action(
            "插件和附件处理后，请用户检查并亲自最终提交；等待用户确认，不自行提交。",
            param_model=Reason,
            terminates_sequence=True,
        )
        async def request_review(params: Reason):
            item = self.job["companies"][self.job["current"]]
            if not item["job"]:
                return ActionResult(error="请先阅读岗位并调用 choose_job 选择本岗。")
            if self.task_options.resumePlan == "template" and not item.get("resumeId"):
                return ActionResult(
                    error="请先调用 generate_resume 生成本岗 PDF，再处理附件和交接。"
                )
            result = await self.wait_user("review", params.reason)
            if result["action"] in ("complete", "skip"):
                return self.target_response(result)
            return ActionResult(
                extracted_content="用户要求继续核对或修改：" + result["feedback"]
            )

        self.register_task_tools(tools)
        return tools

    def upload_result(self, attempt, error=None, blocked=False):
        attempt.update(
            state="blocked" if blocked else "failed" if error else "sent",
            error=error or "",
        )
        if error:
            self.progress(
                f"附件未上传：{attempt['file']} → {attempt['target']}；{error}"
            )
        else:
            self.progress(
                f"附件已送入网页控件：{attempt['file']} → {attempt['target']}（不代表企业已接收）"
            )

    def make_model(self, model):
        if self.model_factory:
            return self.model_factory(self)
        from .model import AgentDeepSeek

        owner = self
        balance_error = None

        class MeteredDeepSeek(AgentDeepSeek):
            def usage_received(self, usage):
                owner.prompt_tokens += usage.prompt_tokens
                owner.completion_tokens += usage.completion_tokens

            async def ainvoke(self, *args, **kwargs):
                nonlocal balance_error
                if balance_error is not None:
                    raise balance_error
                if (
                    owner.connection is not None
                    and hasattr(owner.connection, "status")
                    and not owner.connection.status()["connected"]
                ):
                    from browser_use.llm.exceptions import ModelProviderError

                    balance_error = ModelProviderError(
                        "浏览器连接已断开，已停止模型请求。请重新连接浏览器后继续。",
                        status_code=503,
                    )
                    owner.last_model_error = str(balance_error)
                    if owner.agent:
                        owner.agent.stop()
                    raise balance_error
                output_format = kwargs.get(
                    "output_format", args[1] if len(args) > 1 else None
                )
                if owner.request_count >= 100:
                    raise RuntimeError(
                        "本轮已达到 100 次模型请求，停止执行以避免继续消耗。"
                    )
                owner.request_count += 1
                owner.progress(
                    "AI 正在判断下一步" if output_format else "AI 正在整理历史记录"
                )
                from browser_use.llm.exceptions import ModelProviderError

                try:
                    response = await super().ainvoke(*args, **kwargs)
                except ModelProviderError as exc:
                    owner.last_model_error = str(exc)[:1200]
                    if exc.status_code in (400, 401, 402, 403, 404, 422):
                        balance_error = exc
                        if owner.agent:
                            owner.agent.stop()
                    raise
                if output_format is not None and any(
                    "done" not in a.model_dump(exclude_unset=True)
                    for a in response.completion.action
                ):
                    owner.last_model_error = ""
                return response

        return MeteredDeepSeek(
            model=model["model"],
            api_key=model["apiKey"],
            base_url=model.get("baseUrl", "https://api.deepseek.com"),
            thinking=False,
            temperature=0,
            timeout=90,
            client_params={"max_retries": 0},
        )

    async def close_remote(self):
        if self.remote:
            await self.remote.close()
            self.remote = None

    async def action(self, request):
        async with self.action_lock:
            if (
                not self.job
                or request.match_info["id"] != self.job["id"]
                or not self.job["active"]
            ):
                raise web.HTTPConflict(text="任务已变化，请刷新页面。")
            try:
                data = UserAction.model_validate_json(await request.read())
            except ValidationError:
                raise web.HTTPBadRequest(text="操作参数不完整。") from None
            if data.id is not None and data.id != self.job["id"]:
                raise web.HTTPConflict(text="任务已变化，请刷新页面。")
            if data.current != self.job["current"]:
                raise web.HTTPConflict(text="当前岗位已变化，请刷新后再操作。")
            action = request.match_info["action"]
            if action == "resume" and re.fullmatch(
                r"(?:没事[，, ]*)?(?:继续投下一个|继续下一个|跳过本岗|放弃该岗位)[。！! ]*",
                data.feedback.strip(),
            ):
                action = "skip"
            if action in ("resume", "complete", "skip") and self.signal.is_set():
                raise web.HTTPConflict(text="本次回复已收到，请等待 Agent 继续。")
            if action == "stop":
                self.signal_value = {"action": "stop"}
                self.task.cancel()
            elif action == "skip":
                if self.job["state"] != "WAITING_HUMAN":
                    raise web.HTTPConflict(text="请在 Agent 请求协助时放弃当前岗位。")
                await self.close_remote()
                self.signal_value = {"action": "skip", "feedback": data.feedback}
                self.signal.set()
            elif action in ("resume", "complete"):
                if self.job["state"] != "WAITING_HUMAN":
                    raise web.HTTPConflict(text="当前不在人工协作阶段。")
                if (
                    action == "complete"
                    and not self.job["companies"][self.job["current"]]["job"]
                ):
                    raise web.HTTPBadRequest(
                        text="尚未选择真实岗位，不能确认投递完成。"
                    )
                if action == "complete" and not data.confirmSubmitted:
                    raise web.HTTPBadRequest(text="请在亲自提交后明确确认投递完成。")
                await self.close_remote()
                self.signal_value = {"action": action, "feedback": data.feedback}
                self.signal.set()
            elif action in ("pause", "continue") and self.agent:
                if self.job.get("pending"):
                    raise web.HTTPConflict(text="当前已经在等待人工协作，无需暂停。")
                if action == "pause":
                    self.agent.pause()
                    self.job["state"] = "PAUSED"
                else:
                    self.agent.resume()
                    self.job["state"] = "RUNNING"
            elif action == "focus" and self.browser:
                await self.connection.call(
                    "Target.activateTarget",
                    {"targetId": (await self.browser.get_current_page())._target_id},
                )
            else:
                raise web.HTTPBadRequest(text="当前不支持此操作。")
            return web.json_response(self.public())

    async def proxy(self, path, data=None):
        if not self.browser or self.state != "WAITING_HUMAN":
            raise web.HTTPConflict(text="当前不在人工接管阶段。")
        if path == "remote/close":
            await self.close_remote()
            return {"ok": True}
        if path == "remote/tabs":
            page = await self.application_page()
            return {
                "current": page._target_id,
                "tabs": [
                    {"id": t.target_id, "title": t.title or "招聘网页"}
                    for t in await self.browser.get_tabs()
                ],
            }
        if path == "remote/switch":
            from browser_use.browser.events import SwitchTabEvent

            target = (data or {}).get("target_id")
            if target not in self.connection.owned_targets:
                raise web.HTTPForbidden(text="只能接管本轮投递标签页。")
            await self.close_remote()
            event = self.browser.event_bus.dispatch(SwitchTabEvent(target_id=target))
            await event
            await event.event_result(raise_if_any=True, raise_if_none=False)
            return {"ok": True}
        if path == "remote/offer":
            await self.application_page()
            from frontend.remote import offer

            class Payload:
                async def json(self):
                    return data

            response = await offer(self, Payload())
            return json.loads(response.body)
        raise web.HTTPNotFound()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.close_remote()
