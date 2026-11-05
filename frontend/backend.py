"""Offerly workspace, attachments, resumes, catalog and phone HTTP API."""

import asyncio
import secrets
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError
from offerly.browser_connection import BrowserRequest, OpenBrowserRequest
from offerly.tata_program import TataRequest

from offerly.attachments import MAX_TOTAL
from offerly.services import ApplicationServices


async def plugin_check_page(request):
    return web.Response(
        text='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Offerly · 塔塔试填</title><body style="font:16px/1.8 system-ui;max-width:720px;margin:60px auto;padding:24px"><h1>简历基本信息</h1><p>本地塔塔试填：填写结果仅显示在当前页面，不保存资料，不提交申请。</p><form id="tata-test" onsubmit="event.preventDefault()"><p><label for="name">姓名</label><input id="name" name="name" autocomplete="name" required></p><p><label for="mobile">手机号码</label><input id="mobile" name="mobile" type="tel" minlength="7" maxlength="20" autocomplete="tel" required></p><p><label for="email">邮箱</label><input id="email" name="email" type="email" autocomplete="email" required></p></form><p id="test-result" role="status">正在测试，请稍候…</p></body></html>',
        content_type="text/html",
    )


def make_app(manager=None, *, public_origin=None):
    manager = manager or ApplicationServices()
    public_root = Path(__file__).resolve().parent / "public"
    from offerly.connector import BrowserAccess

    if not isinstance(manager.browsers, BrowserAccess):
        manager.browsers = BrowserAccess(
            manager, public_origin, manager.browsers.connections
        )

    @web.middleware
    async def local_only(request, handler):
        port = request.url.port
        from urllib.parse import urlsplit

        allowed_hosts = (
            (urlsplit(public_origin).netloc,)
            if public_origin
            else (f"127.0.0.1:{port}", f"localhost:{port}")
        )
        if request.host not in allowed_hosts:
            raise web.HTTPForbidden(text="只允许本机访问。")
        origin = request.headers.get("Origin")
        allowed_origins = (
            (public_origin,)
            if public_origin
            else (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            )
        )
        if origin and origin not in allowed_origins:
            raise web.HTTPForbidden(text="不接受外部网页请求。")
        if request.path.startswith("/api/") and request.path != "/api/bootstrap":
            if not secrets.compare_digest(
                request.headers.get("X-Offerly-Token", ""), manager.token
            ):
                raise web.HTTPForbidden(text="页面会话已变化，请刷新页面。")
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({"error": exc.text}, status=exc.status)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    app = web.Application(
        middlewares=[local_only], client_max_size=MAX_TOTAL + 2 * 1024 * 1024
    )

    async def bootstrap(request):
        saved = manager.model_store.read() or {}
        return web.json_response(
            {
                "token": manager.token,
                "serviceReady": True,
                "execution": manager.agent_available,
                "agentAvailable": manager.agent_available,
                "autonomousTasks": hasattr(manager.agent, "create_task"),
                "browsers": manager.browsers.status(),
                "browserHelperRequired": bool(public_origin),
                "submission_allowed": False,
                "phone_connected": bool(
                    manager.phone and manager.phone.status()["connected"]
                ),
                "modelConfigured": bool(saved.get("apiKey")),
                "savedModelUrl": saved.get("baseUrl", ""),
            }
        )

    async def active(request):
        return web.json_response(
            {
                "task": manager.public(),
                "phone": manager.phone.status()
                if manager.phone
                else {"enabled": False},
                "browsers": manager.browsers.status(),
            }
        )

    async def browsers(request):
        return web.json_response(manager.browsers.status())

    async def browser_action(request):
        try:
            kind = request.match_info["action"]
            if kind == "open":
                data = OpenBrowserRequest.model_validate_json(await request.read())
                result = await manager.browsers.open(data)
            elif kind == "connect":
                data = BrowserRequest.model_validate_json(await request.read())
                if public_origin:
                    result = await manager.browsers.pair(data.browser, public_origin)
                else:
                    result = await manager.browsers.begin_local(data.browser)
            elif kind == "check":
                data = BrowserRequest.model_validate_json(await request.read())
                if manager.public() and manager.public().get("active"):
                    raise web.HTTPConflict(text="任务运行中，请勿重复检查连接。")
                connection = manager.browsers.connections[data.browser]
                if not connection.status()["connected"]:
                    raise web.HTTPConflict(text="请先连接浏览器。")
                browser = await connection.browser_session()
                try:
                    page = await browser.get_current_page()
                    if not page or page._target_id not in connection.owned_targets:
                        raise web.HTTPConflict(text="未能建立独立投递标签页。")
                    result = connection.status()
                finally:
                    await browser.stop()
            elif kind == "disconnect":
                data = BrowserRequest.model_validate_json(await request.read())
                if manager.public() and manager.public().get("active"):
                    raise web.HTTPConflict(text="请先停止本轮任务，再断开浏览器。")
                result = await manager.browsers.disconnect(data.browser)
            else:
                raise web.HTTPNotFound()
            return web.json_response(result)
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(
                text="请检查浏览器选项；未安装时请选择另一种浏览器。"
            ) from None

    async def tata_status(request):
        return web.json_response(manager.agent.tata_program.last_result)

    async def tata_program(request):
        if manager.public() and manager.public().get("active"):
            raise web.HTTPConflict(text="Agent 正在执行，不能同时手动启动塔塔程序。")
        try:
            probe = request.path.endswith("/probe")
            data = (
                BrowserRequest.model_validate_json(await request.read())
                if probe
                else TataRequest.model_validate_json(await request.read())
            )
        except ValidationError:
            raise web.HTTPBadRequest(text="请检查浏览器和目标标签页。") from None
        connection = manager.browsers.connections[data.browser]
        if not connection.status()["connected"]:
            raise web.HTTPConflict(text="请先连接浏览器。")
        if probe:
            target = await connection.create_tab(
                (public_origin or f"http://127.0.0.1:{request.url.port}")
                + "/plugin-check"
            )
        else:
            target = data.targetId
        if target not in connection.owned_targets:
            raise web.HTTPForbidden(text="只能操作本轮投递标签页。")
        browser = await connection.browser_session()
        try:
            from browser_use.browser.events import SwitchTabEvent

            event = browser.event_bus.dispatch(SwitchTabEvent(target_id=target))
            await event
            await event.event_result(raise_if_any=True, raise_if_none=False)
            page = await browser.get_current_page()
            if not page or page._target_id != target:
                raise web.HTTPConflict(text="投递标签页发生变化，请重试。")
            result = await manager.agent.tata_program.run(
                connection, page, dry_run=False if probe else data.dryRun
            )
            response = {**result.model_dump(), "targetId": target}
            if probe:
                filled = 0
                if result.ok or result.fill_attempted:
                    for _ in range(90):
                        filled = await page.evaluate(
                            "() => Array.from(document.querySelectorAll('#tata-test input')).filter(e => e.value.trim() && e.checkValidity()).length"
                        )
                        if int(filled) == 3:
                            break
                        await asyncio.sleep(1)
                response.update(
                    ok=int(filled) == 3,
                    stage="verify",
                    filledFields=int(filled),
                    message="姓名、手机、邮箱均已填入。"
                    if int(filled) == 3
                    else "试填未通过：仅填写了 %s/3 项。请检查插件资料、登录状态或本地站点权限。"
                    % filled,
                )
            if probe:
                await page.evaluate(
                    "(ok) => {const e=document.getElementById('test-result');if(e){e.textContent=ok?'测试成功，请返回 Offerly':'测试失败，请联系管理员';e.style.color=ok?'green':'red';}}",
                    bool(response["ok"]),
                )
            return web.json_response(response)
        finally:
            await browser.stop()

    async def history(request):
        from offerly.history import ApplicationHistory

        return web.json_response(
            {"applications": ApplicationHistory(manager.history_path).records()}
        )

    async def pair(request):
        if not manager.phone:
            raise web.HTTPConflict(text="服务未配置局域网手机连接。")
        return await manager.phone.pair(request)

    async def revoke(request):
        if not manager.phone:
            raise web.HTTPConflict(text="手机连接未开启。")
        return await manager.phone.revoke(request)

    async def wechat_bind(request):
        if not manager.phone:
            raise web.HTTPConflict(text="手机连接服务尚未开启。")
        try:
            manager.phone.wechat.bind(secrets.token_urlsafe(24), await request.json())
        except ValueError:
            raise web.HTTPBadRequest(text="请输入公众号提供的有效 token。") from None
        return web.json_response(manager.phone.wechat.status())

    async def wechat_setup(request):
        if not manager.phone:
            raise web.HTTPConflict(text="手机连接服务尚未开启。")
        try:
            return web.json_response(
                await manager.phone.wechat.setup_test(
                    await request.json(), manager.phone.origin
                )
            )
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(
                text="请检查 token 码和手机 HTTPS 入口；已有绑定可留空直接测试。"
            ) from None

    async def static(request):
        path = (
            public_root / (request.match_info.get("path", "") or "index.html")
        ).resolve()
        try:
            path.relative_to(public_root)
        except ValueError:
            raise web.HTTPNotFound()
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    from .workspace import Workspace

    workspace = Workspace(manager.root / "workspace", manager.model_store)
    app.add_routes(workspace.routes())
    from .resumes import ResumeService

    resumes = ResumeService(workspace)
    manager.workspace, manager.resumes = workspace, resumes
    app.add_routes(resumes.routes())
    from .job_search import JobSearchService

    job_search = JobSearchService(manager.root)
    manager.job_search = job_search
    app.add_routes(job_search.routes())
    app.on_cleanup.append(job_search.close)

    async def helper_pair(request):
        try:
            data = BrowserRequest.model_validate_json(await request.read())
        except ValidationError:
            raise web.HTTPBadRequest(text="请选择 Edge 或 Chrome。") from None
        return web.json_response(
            await manager.browsers.pair(
                data.browser, public_origin or f"http://{request.host}"
            )
        )

    async def helper_download(request):
        path = public_root / "downloads" / "OfferlyConnect.exe"
        if not path.is_file():
            raise web.HTTPServiceUnavailable(text="连接助手暂不可下载，请联系管理员。")
        return web.FileResponse(
            path,
            headers={
                "Content-Disposition": 'attachment; filename="OfferlyConnect.exe"'
            },
        )

    app.add_routes(
        [
            web.post("/api/browser-helper/pair", helper_pair),
            web.get("/browser-helper/relay", manager.browsers.relay),
            web.get("/downloads/OfferlyConnect.exe", helper_download),
            web.get("/api/bootstrap", bootstrap),
            web.get("/api/browsers", browsers),
            web.get("/api/plugin/tata/status", tata_status),
            web.post("/api/plugin/tata", tata_program),
            web.post("/api/plugin/tata/probe", tata_program),
            web.get("/plugin-check", plugin_check_page),
            web.post("/api/browser/{action}", browser_action),
            web.get("/api/task", active),
            web.get("/api/history", history),
            web.post("/api/agent/tasks", manager.create_task),
            web.post("/api/tasks/{id}/{action}", manager.action),
            web.post("/api/phone/pair", pair),
            web.post("/api/phone/revoke", revoke),
            web.post("/api/phone/wechat/setup-test", wechat_setup),
            web.post("/api/phone/wechat/bind", wechat_bind),
            web.get("/{path:.*}", static),
        ]
    )

    async def start(app):
        await manager.start()

    async def close(app):
        await manager.close()

    app.on_startup.append(start)
    app.on_cleanup.append(close)
    return app
