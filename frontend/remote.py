"""Browser handoff: latest-frame WebRTC video and ordered CDP input."""

import asyncio
import base64
import io
import json
import logging
import math
import time
from collections import deque
from fractions import Fraction
from urllib.parse import urlsplit

import httpx
from aiohttp import web
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from PIL import Image
from websockets.asyncio.client import connect


def decode_frame(jpeg):
    with Image.open(io.BytesIO(base64.b64decode(jpeg))) as image:
        image.load()
        width, height = image.size
        if width % 2 or height % 2:
            padded = Image.new("RGB", (width + width % 2, height + height % 2))
            padded.paste(image)
            image = padded
        return VideoFrame.from_image(image), width, height


class BrowserVideo(VideoStreamTrack):
    def __init__(self, on_layout=None):
        super().__init__()
        self.latest = None
        self.ready = asyncio.Event()
        self.frames = self.decoded = self.overwritten = 0
        self.cached = None
        self.cached_seq = 0
        self.layout = None
        self.epoch = 0
        self.generation = 0
        self.on_layout = on_layout or (lambda layout: None)
        self.start = time.monotonic()
        self.last_sent = self.decode_ms = 0

    def update(self, jpeg, metadata):
        if self.latest and self.latest[0] != self.cached_seq:
            self.overwritten += 1
        self.frames += 1
        self.latest = (self.frames, jpeg, metadata)
        self.ready.set()

    def stop(self):
        super().stop()
        self.ready.set()

    def reset(self):
        self.generation += 1
        self.layout = self.latest = self.cached = None
        self.ready.set()

    async def recv(self):
        # Cap at 20fps. Decode each source image once, outside the input loop.
        await asyncio.sleep(max(0, self.last_sent + 0.05 - time.monotonic()))
        if self.latest is None or self.latest[0] == self.cached_seq:
            self.ready.clear()
            try:
                await asyncio.wait_for(self.ready.wait(), 1 if self.cached else 10)
            except TimeoutError:
                if self.cached is None:
                    raise MediaStreamError()
        if self.readyState != "live":
            raise MediaStreamError()
        while self.cached is None or (
            self.latest and self.latest[0] != self.cached_seq
        ):
            if not self.latest:
                self.ready.clear()
                await asyncio.wait_for(self.ready.wait(), 10)
                if self.readyState != "live":
                    raise MediaStreamError()
                continue
            seq, jpeg, meta = self.latest
            generation = self.generation
            started = time.monotonic()
            frame, width, height = await asyncio.to_thread(decode_frame, jpeg)
            if self.readyState != "live":
                raise MediaStreamError()
            if generation != self.generation:
                continue
            self.decode_ms = (time.monotonic() - started) * 1000
            self.cached, self.cached_seq = frame, seq
            self.decoded += 1
            geometry = {
                "width": meta["deviceWidth"],
                "height": meta["deviceHeight"],
                "scale": meta.get("pageScaleFactor", 1),
                "top": meta.get("offsetTop", 0),
                "image_width": width,
                "image_height": height,
                "encoded_width": frame.width,
                "encoded_height": frame.height,
            }
            previous = {k: self.layout[k] for k in geometry} if self.layout else None
            if geometry != previous:
                self.epoch += 1
                self.layout = {**geometry, "epoch": self.epoch}
                self.on_layout(self.layout)
            break
        self.last_sent = time.monotonic()
        self.cached.pts = int((self.last_sent - self.start) * 90000)
        self.cached.time_base = Fraction(1, 90000)
        return self.cached


class ChromeWire:
    def __init__(self, socket, video):
        self.socket, self.video = socket, video
        self.sequence, self.pending = 0, {}
        self.reader = asyncio.create_task(self.read())

    async def call(self, method, params=None):
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.socket.send(
                json.dumps({"id": key, "method": method, "params": params or {}})
            )
            return await asyncio.wait_for(future, 2)
        finally:
            self.pending.pop(key, None)

    async def read(self):
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                RuntimeError("Chrome input or capture rejected")
                            )
                        else:
                            future.set_result(message.get("result", {}))
                elif message.get("method") == "Page.screencastFrame":
                    params = message["params"]
                    self.video.update(params["data"], params["metadata"])
                    self.sequence += 1
                    await self.socket.send(
                        json.dumps(
                            {
                                "id": self.sequence,
                                "method": "Page.screencastFrameAck",
                                "params": {"sessionId": params["sessionId"]},
                            }
                        )
                    )
        finally:
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError("Browser connection closed"))

    async def close(self):
        await self.socket.close()
        await asyncio.gather(self.reader, return_exceptions=True)


class BrowserInput:
    def __init__(self, call, layout):
        self.call, self.layout = call, layout
        self.pressed = False
        self.position = (0, 0)

    async def release(self):
        if self.pressed:
            self.pressed = False
            await self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": self.position[0],
                    "y": self.position[1],
                    "button": "left",
                    "buttons": 0,
                    "clickCount": 1,
                },
            )

    async def dispatch(self, event):
        kind = event.get("kind")
        if kind in ("pointer", "scroll"):
            phase = event.get("phase")
            if kind == "pointer" and phase == "up":
                await self.release()
                return
            layout = self.layout()
            if not layout or event.get("epoch") != layout["epoch"]:
                await self.release()
                raise ValueError("网页尺寸已变化，请等待画面更新")
            nx, ny = float(event["nx"]), float(event["ny"])
            if not all(math.isfinite(n) and 0 <= n <= 1 for n in (nx, ny)):
                raise ValueError("无效坐标")
            x = nx * layout["width"] / layout["scale"]
            y = max(0, ny * layout["height"] - layout["top"]) / layout["scale"]
            if kind == "scroll":
                dx, dy = float(event.get("dx", 0)), float(event.get("dy", 0))
                if not math.isfinite(dx) or not math.isfinite(dy):
                    raise ValueError("无效滚动")
                await self.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "x": x,
                        "y": y,
                        "deltaX": max(-800, min(800, dx)),
                        "deltaY": max(-800, min(800, dy)),
                    },
                )
                return
            if phase == "down":
                await self.release()
                self.pressed = True
            elif phase != "move" or not self.pressed:
                raise ValueError("无效拖动状态")
            self.position = (x, y)
            await self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed" if phase == "down" else "mouseMoved",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 1,
                    "clickCount": 1 if phase == "down" else 0,
                },
            )
        elif kind == "text":
            if not isinstance(event.get("text"), str) or len(event["text"]) > 4096:
                raise ValueError("无效文本")
            await self.call("Input.insertText", {"text": event["text"]})
        elif kind == "key":
            keys = {
                "Backspace": 8,
                "Tab": 9,
                "Enter": 13,
                "Escape": 27,
                "Control+A": 65,
            }
            key = event["key"]
            if key not in keys:
                raise ValueError("不支持的按键")
            for phase in ("keyDown", "keyUp"):
                await self.call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": phase,
                        "key": "a" if key == "Control+A" else key,
                        "modifiers": 2 if key == "Control+A" else 0,
                        "windowsVirtualKeyCode": keys[key],
                    },
                )
        else:
            raise ValueError("不支持的输入")


class RemoteSession:
    def __init__(self, control):
        self.control = control
        phone = getattr(getattr(control, "services", None), "phone", None)
        ice = getattr(phone, "rtc_servers", [])
        self.pc = RTCPeerConnection(
            RTCConfiguration(iceServers=[RTCIceServer(**item) for item in ice])
        )
        self.video = BrowserVideo(
            lambda layout: self.send({"kind": "layout", **layout})
        )
        self.wire = self.channel = None
        self.input = BrowserInput(
            lambda method, params: self.wire.call(method, params),
            lambda: self.video.layout,
        )
        self.tasks, self.events = [], deque()
        self.wake, self.lock = asyncio.Event(), asyncio.Lock()
        self.closed, self.last_seq = False, -1
        self.target = None

    def send(self, message):
        if self.channel and self.channel.readyState == "open":
            if self.channel.bufferedAmount > 16384:
                asyncio.create_task(self.close())
            else:
                self.channel.send(json.dumps(message))

    def owns(self):
        return (
            not self.closed
            and self.control.state == "WAITING_HUMAN"
            and time.monotonic() < self.control.deadline
        )

    def enqueue(self, event):
        """Collapse continuous gestures, preserving key/press/release ordering and acknowledgements."""
        previous = self.events[-1] if self.events else None
        if (
            event.get("phase") == "move"
            and previous
            and previous.get("phase") == "move"
        ):
            self.events[-1] = event
        elif (
            event.get("kind") == "scroll"
            and previous
            and previous.get("kind") == "scroll"
            and previous.get("epoch") == event.get("epoch")
        ):
            merged = {**event}
            for axis in ("dx", "dy"):
                before, after = previous.get(axis, 0), event.get(axis, 0)
                if (
                    not isinstance(before, (float, int))
                    or not isinstance(after, (float, int))
                    or not math.isfinite(before)
                    or not math.isfinite(after)
                ):
                    return False
                merged[axis] = max(-800, min(800, before + after))
            self.events[-1] = merged
        elif len(self.events) < 32:
            self.events.append(event)
        else:
            return False
        self.wake.set()
        return True

    async def attach(self, page):
        await self.input.release()
        self.events.clear()
        if self.wire:
            await self.wire.close()
        self.video.reset()
        address = urlsplit(self.control.browser.cdp_url)
        endpoint = (
            getattr(self.control, "browser_http_url", "")
            or ("https" if address.scheme in ("wss", "https") else "http")
            + "://"
            + address.netloc
        )
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            targets = (await client.get(endpoint + "/json/list")).json()
        target = next(t for t in targets if t["id"] == page._target_id)
        self.target = page._target_id
        self.wire = ChromeWire(
            await connect(
                target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024, max_queue=1
            ),
            self.video,
        )
        await self.wire.call("Page.enable")
        await self.wire.call("Page.bringToFront")
        await self.wire.call(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 65,
                "maxWidth": 1280,
                "maxHeight": 720,
                "everyNthFrame": 1,
            },
        )

    async def open(self, offer):
        await self.attach(await self.control.browser.get_current_page())
        if not self.owns():
            raise RuntimeError("Handoff ended during browser attachment")
        self.pc.addTrack(self.video)

        @self.pc.on("datachannel")
        def channel_open(channel):
            if channel.label != "input" or self.channel:
                channel.close()
                return
            self.channel = channel

            @channel.on("open")
            def ready():
                if self.video.layout:
                    self.send({"kind": "layout", **self.video.layout})

            @channel.on("message")
            def receive(raw):
                if not self.owns() or not isinstance(raw, str) or len(raw) > 8192:
                    return
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        return
                    if event.get("kind") == "ping":
                        self.send(
                            {
                                "kind": "pong",
                                "at": event.get("at"),
                                "capture_frames": self.video.frames,
                                "decode_ms": round(self.video.decode_ms, 1),
                            }
                        )
                        if self.video.layout:
                            self.send({"kind": "layout", **self.video.layout})
                        return
                    seq = event.get("seq")
                    if not isinstance(seq, int) or seq <= self.last_seq:
                        return
                    self.last_seq = seq
                    if not self.enqueue(event):
                        asyncio.create_task(self.close())
                except (ValueError, TypeError):
                    return

            @channel.on("close")
            def ended():
                asyncio.create_task(self.close())

        @self.pc.on("connectionstatechange")
        async def connection_changed():
            if self.pc.connectionState in ("failed", "closed"):
                await self.close()

        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=offer["sdp"], type=offer["type"])
        )
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        if not self.owns():
            raise RuntimeError("Handoff ended during peer negotiation")
        self.tasks = [
            asyncio.create_task(self.inputs()),
            asyncio.create_task(self.watch()),
        ]
        return {
            "sdp": self.pc.localDescription.sdp,
            "type": self.pc.localDescription.type,
        }

    async def inputs(self):
        while self.owns():
            await self.wake.wait()
            self.wake.clear()
            while self.events and self.owns():
                event = self.events.popleft()
                try:
                    async with self.lock:
                        if not self.owns():
                            break
                        await self.input.dispatch(event)
                    self.send({"kind": "applied", "seq": event["seq"]})
                except (ValueError, KeyError, TypeError) as error:
                    self.send({"kind": "error", "message": str(error) or "输入无效"})
                except (RuntimeError, TimeoutError, ConnectionError):
                    await self.close()
                    return

    async def watch(self):
        try:
            while self.owns():
                if self.wire.reader.done():
                    break
                page = await self.control.browser.get_current_page()
                if page is None and hasattr(self.control, "application_page"):
                    page = await self.control.application_page()
                if page and page._target_id != self.target:
                    async with self.lock:
                        await self.attach(page)
                await asyncio.sleep(0.25)
        finally:
            await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            if task is not asyncio.current_task():
                task.cancel()
        async with self.lock:
            if self.wire:
                try:
                    await self.input.release()
                    await self.wire.call("Page.stopScreencast")
                except Exception:
                    pass
                await self.wire.close()
        self.video.stop()
        await self.pc.close()


async def offer(control, request):
    async with control.lock:
        if control.state != "WAITING_HUMAN":
            raise web.HTTPConflict(text="当前不在人工接管阶段。")
        payload = await request.json()
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "offer"
            or not isinstance(payload.get("sdp"), str)
            or len(payload["sdp"]) > 60000
        ):
            raise web.HTTPBadRequest()
        if getattr(control, "remote", None):
            await control.remote.close()
        remote = control.remote = RemoteSession(control)
        try:
            return web.json_response(
                await asyncio.wait_for(remote.open(payload), timeout=15)
            )
        except asyncio.CancelledError:
            await remote.close()
            raise
        except Exception:
            logging.getLogger(__name__).exception("Browser handoff setup failed")
            await remote.close()
            raise web.HTTPServiceUnavailable(text="浏览器连接失败，请重试。") from None
