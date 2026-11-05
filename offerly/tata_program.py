"""Trigger Tata on the current owned tab through CDP, without model or OS clicks."""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import re
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .browser_connection import BrowserName, PLUGIN_IDS

logger = logging.getLogger(__name__)

Stage = Literal["preflight", "tata", "panel", "fill"]


class TataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser: BrowserName
    targetId: str = Field(min_length=1, max_length=120)
    dryRun: bool = True


class TataResult(BaseModel):
    ok: bool
    stage: Stage
    message: str
    steps: list[str] = Field(default_factory=list)
    fill_attempted: bool = False
    filled_before: int = 0


class TataError(RuntimeError):
    def __init__(self, stage: Stage, message: str):
        super().__init__(message)
        self.stage = stage


class CDPTataLauncher:
    """Trigger Tata's real action on the active tab, using the approved connection."""

    async def open(self, connection, target, url):
        tabs = (
            await connection.call(
                "Target.getTargets",
                {
                    "filter": [{"type": "tab", "exclude": False}],
                },
            )
        ).get("targetInfos", [])
        candidates = [
            tab
            for tab in tabs
            if tab.get("type") == "tab"
            and tab.get("url") == url
            and tab.get("embedderData", {}).get("tabActive")
        ]
        if len(candidates) != 1:
            raise TataError(
                "tata",
                "无法唯一确认当前申请标签页，未触发插件。请保持投递页面处于当前标签页。",
            )
        # Recheck the owned page immediately before invoking the extension action.
        info = (await connection.call("Target.getTargetInfo", {"targetId": target}))[
            "targetInfo"
        ]
        if target not in connection.owned_targets or info.get("url") != url:
            raise TataError("tata", "当前申请页面已变化，未触发插件。")
        await connection.call(
            "Extensions.triggerAction",
            {
                "id": PLUGIN_IDS[connection.browser],
                "targetId": candidates[0]["targetId"],
            },
        )


PANEL = r"""() => {
 const b=document.querySelector('#floating-div #filling');
 if(!b)return JSON.stringify({found:false,ready:false});
 const r=b.getBoundingClientRect(),s=getComputedStyle(b);
 return JSON.stringify({found:true,ready:r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&!b.disabled&&!b.classList.contains('disabled')});
}"""
FILLED = '() => JSON.stringify([...document.querySelectorAll("input:not([type=hidden]):not([type=password]):not([type=file]),textarea,select")].filter(e=>["checkbox","radio"].includes(e.type)?e.checked:Boolean(e.value?.trim())).length)'


class TataProgram:
    def __init__(self, launcher=None):
        self.launcher = launcher or CDPTataLauncher()
        self.lock = asyncio.Lock()
        self.attempted = set()
        self.last_result = {}

    async def run(self, connection, page, *, dry_run=False, progress=None):
        if self.lock.locked():
            return TataResult(
                ok=False,
                stage="preflight",
                message="塔塔程序已在运行，请等待当前操作结束。",
            )
        async with self.lock:
            target = page._target_id
            steps = []
            stage: Stage = "preflight"
            attempted = False
            url = ""
            before = 0
            diagnostic_message = ""
            try:
                if (
                    not connection.status()["connected"]
                    or target not in connection.owned_targets
                ):
                    raise TataError(stage, "只能在已连接浏览器的本轮投递标签页运行。")
                url = await page.evaluate("() => location.href")
                if not re.match(r"^https?://", url):
                    raise TataError(stage, "请先进入招聘申请表单，再启动塔塔程序。")
                if (target, url) in self.attempted and not dry_run:
                    raise TataError(
                        stage, "本页已尝试过一键填入，请先人工核对，程序不会重复覆盖。"
                    )
                await connection.call("Target.activateTarget", {"targetId": target})
                stage = "tata"
                if progress:
                    progress("在当前申请标签页唤起塔塔网申")
                await self.launcher.open(connection, target, url)
                steps.append("tata")
                if await page.evaluate("() => document.visibilityState") != "visible":
                    raise TataError("panel", "投递标签页已切换，已停止，不会点击填入。")
                stage = "panel"
                if progress:
                    progress("固定程序：等待“一键填入”按钮")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if await page.evaluate("() => location.href") != url:
                        raise TataError(
                            stage, "网页已跳转，请先完成插件登录或资料填写。"
                        )
                    panel = json.loads(await page.evaluate(PANEL))
                    if panel.get("ready"):
                        break
                    await asyncio.sleep(0.3)
                else:
                    raise TataError(
                        stage,
                        "已打开塔塔，但未出现可用的“一键填入”按钮；请检查插件登录、资料及站点权限。",
                    )
                steps.append("panel")
                before = json.loads(await page.evaluate(FILLED))
                if dry_run:
                    return TataResult(
                        ok=True,
                        stage=stage,
                        steps=steps,
                        filled_before=before,
                        message="已在当前标签页打开塔塔并找到“一键填入”，本次仅检查入口，尚未点击填入。",
                    )
                stage = "fill"
                self.attempted.add((target, url))
                attempted = True
                if progress:
                    progress("固定程序：点击“一键填入”")
                result = json.loads(
                    await page.evaluate(
                        """(expected) => {
 if(location.href!==expected)return JSON.stringify({clicked:false});
 const b=document.querySelector('#floating-div #filling');
 if(document.visibilityState!=='visible'||!b||b.disabled||b.classList.contains('disabled')||!b.getBoundingClientRect().width)return JSON.stringify({clicked:false});
 b.click();return JSON.stringify({clicked:true});
}""",
                        url,
                    )
                )
                if not result.get("clicked"):
                    raise TataError(
                        stage,
                        "点击前页面或按钮状态发生变化，请人工核对；不会自动重试。",
                    )
                steps.append("fill")
                return TataResult(
                    ok=True,
                    stage=stage,
                    steps=steps,
                    fill_attempted=True,
                    filled_before=before,
                    message="固定程序已点击“一键填入”，接下来等待插件响应；不代表表单已全部填完。",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stage = exc.stage if isinstance(exc, TataError) else stage
                diagnostic_message = str(exc)[:800]
                logger.warning(
                    "Tata program failed at %s: %s", stage, diagnostic_message
                )
                return TataResult(
                    ok=False,
                    stage=stage,
                    steps=steps,
                    fill_attempted=attempted,
                    filled_before=before,
                    message=str(exc)[:800],
                )
            finally:
                self.last_result[connection.browser] = {
                    "stage": stage,
                    "steps": list(steps),
                    "fillAttempted": attempted,
                    "message": diagnostic_message,
                }
                logger.info(
                    "Tata program stopped: browser=%s stage=%s completed=%s fillAttempted=%s",
                    connection.browser,
                    stage,
                    steps,
                    attempted,
                )


def main():
    """Standalone command using the same program inside the local Offerly service."""
    import urllib.request

    parser = argparse.ArgumentParser(description="当前标签页：唤起塔塔网申 → 一键填入")
    parser.add_argument("--browser", choices=["edge", "chrome"], required=True)
    parser.add_argument("--target", required=True, help="本轮投递标签页的 target ID")
    parser.add_argument("--port", type=int, default=9189)
    parser.add_argument(
        "--dry-run", action="store_true", help="只打开插件并检查按钮，不填资料"
    )
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    bootstrap = json.load(urllib.request.urlopen(base + "/api/bootstrap", timeout=5))
    payload = {"browser": args.browser, "targetId": args.target, "dryRun": args.dry_run}
    request = urllib.request.Request(
        base + "/api/plugin/tata",
        data=json.dumps(payload).encode(),
        headers={
            "X-Offerly-Token": bootstrap["token"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response = json.load(urllib.request.urlopen(request, timeout=60))
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
