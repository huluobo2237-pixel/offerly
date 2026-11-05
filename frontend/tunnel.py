"""Managed temporary HTTPS entry for the phone-only app, using cloudflared."""

import asyncio
import os
import re
import subprocess
from pathlib import Path


class PhoneTunnel:
    def __init__(self, hub, executable, workdir):
        self.hub = hub
        self.executable = Path(executable)
        self.workdir = Path(workdir)
        self.process = self.reader = None
        self.ready = asyncio.Event()
        self.stopping = False

    async def start(self, local_url):
        if not self.executable.is_file():
            raise RuntimeError("找不到 cloudflared，请提供已安装的程序路径。")
        self.workdir.mkdir(parents=True, exist_ok=True)
        config = self.workdir / "quick-tunnel.yml"
        config.write_text("no-autoupdate: true\n", encoding="utf-8")
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "--no-autoupdate",
            "--config",
            str(config),
            "tunnel",
            "--protocol",
            "http2",
            "--edge-ip-version",
            "4",
            "--metrics",
            "127.0.0.1:0",
            "--url",
            local_url,
            cwd=self.workdir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = asyncio.create_task(self.read())
        try:
            await asyncio.wait_for(self.ready.wait(), 45)
            if not self.hub.public_origin or self.hub.connection_error:
                raise RuntimeError("HTTPS 通道启动失败，请检查网络后重试。")
        except BaseException:
            await self.close()
            raise

    async def read(self):
        connected = False
        try:
            async for raw in self.process.stderr:
                line = raw.decode("utf-8", errors="replace")
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                if match:
                    self.hub.configure_public_origin(match.group())
                    if not connected:
                        self.hub.connection_error = "手机 HTTPS 入口正在连接，请稍候。"
                if "Registered tunnel connection" in line:
                    connected = True
                if connected and self.hub.public_origin:
                    self.hub.connection_error = ""
                    self.ready.set()
            await self.process.wait()
        finally:
            if not self.stopping:
                self.hub.connection_error = (
                    "手机 HTTPS 通道已断开，请重新启动服务并配对。"
                )
                await self.hub.revoke(None)
            self.ready.set()

    async def close(self):
        self.stopping = True
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
