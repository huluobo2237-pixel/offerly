import { PointerInput } from "./browser-input.js";

export class BrowserRemote {
  constructor(container, api, scope, onStatus, onStats, onEnded, onError) {
    this.container = container;
    this.api = api;
    this.scope = scope;
    this.onStatus = onStatus;
    this.onStats = onStats;
    this.onEnded = onEnded;
    this.onError = onError;
    this.pc = new RTCPeerConnection({ iceServers: [] });
    this.channel = this.pc.createDataChannel("input", { ordered: true });
    this.video = document.createElement("video");
    this.video.autoplay = true;
    this.video.muted = true;
    this.video.playsInline = true;
    Object.assign(this.video.style, {
      position: "absolute",
      maxWidth: "none",
      pointerEvents: "none",
    });
    container.replaceChildren(this.video);
    this.layout = null;
    this.closed = false;
    this.seq = 0;
    this.pending = new Map();
    this.scale = 1;
    this.panX = this.panY = 0;
    this.stats = {};
    this.lastPaint = performance.now();
    this.hasVideo = false;
    this.lastMedia = this.visibleSince = this.lastPaint;
    this.lastPlaybackAttempt = 0;
    this.wasVisible = false;
    this.playing = false;
    this.measuring = false;
    this.input = new PointerInput(
      container,
      () => this.geometry(),
      (event) => this.send(event),
      {
        pan: (x, y) => {
          this.panX += x;
          this.panY += y;
          this.render();
        },
        zoom: (ratio, x, y) => this.zoom(ratio, x, y),
      },
    );
    this.resize = new ResizeObserver(() => this.render());
    this.resize.observe(container);
    this.intersection = new IntersectionObserver(() => this.checkVisibility());
    this.intersection.observe(container);
    this.wakePlayback = () => this.resumePlayback(true);
    container.addEventListener("pointerdown", this.wakePlayback, {
      passive: true,
    });
    this.video.addEventListener("loadedmetadata", () => {
      this.hasVideo = true;
      this.lastPaint = performance.now();
      this.render();
    });
    this.video.addEventListener("resize", () => this.render());
    this.channel.onmessage = (event) => this.receive(JSON.parse(event.data));
    this.channel.onopen = () => {
      this.ping();
      this.onStatus("浏览器输入已连接，等待视频");
    };
    this.channel.onclose = () =>
      this.fail(
        "远控画面未连通或已断开。请确认手机与电脑连接同一 Wi-Fi，并检查 VPN 或防火墙；5G 网络目前不支持跨网远控。",
      );
    this.pc.onconnectionstatechange = () => {
      if (["failed", "closed"].includes(this.pc.connectionState))
        this.fail(
          "远控画面未连通或已断开。请确认手机与电脑连接同一 Wi-Fi，并检查 VPN 或防火墙；5G 网络目前不支持跨网远控。",
        );
    };
    this.pc.ontrack = (event) => {
      this.video.srcObject = new MediaStream([event.track]);
      event.track.addEventListener("ended", () =>
        this.fail("视频连接已中断，正在重新连接…"),
      );
      if ("jitterBufferTarget" in event.receiver)
        event.receiver.jitterBufferTarget = 0;
      this.resumePlayback(true);
      if (this.video.requestVideoFrameCallback) {
        const painted = () => {
          if (this.closed) return;
          this.lastPaint = this.lastMedia = performance.now();
          this.onStatus("浏览器画面已连接");
          this.frameCallback = this.video.requestVideoFrameCallback(painted);
        };
        this.frameCallback = this.video.requestVideoFrameCallback(painted);
      }
    };
    this.timer = setInterval(() => {
      this.ping();
      this.measure().catch(() => {});
    }, 1000);
  }
  visible() {
    if (document.hidden || !this.container.isConnected) return false;
    const rect = this.container.getBoundingClientRect(),
      viewport = window.visualViewport;
    const top = viewport?.offsetTop || 0,
      left = viewport?.offsetLeft || 0;
    return (
      rect.width > 0 &&
      rect.height > 0 &&
      rect.bottom > top &&
      rect.right > left &&
      rect.top < top + (viewport?.height || window.innerHeight) &&
      rect.left < left + (viewport?.width || window.innerWidth)
    );
  }
  checkVisibility() {
    const visible = this.visible();
    if (visible && !this.wasVisible) {
      this.visibleSince = this.lastPaint = this.lastMedia = performance.now();
      this.render();
      this.resumePlayback(true);
    }
    if (!visible && this.wasVisible) this.input.cancel();
    this.wasVisible = visible;
    return visible;
  }
  async resumePlayback(force = false) {
    if (this.closed || this.playing || !this.video.srcObject || !this.visible())
      return;
    const now = performance.now();
    if (!force && now - this.lastPlaybackAttempt < 2000) return;
    this.playing = true;
    this.lastPlaybackAttempt = now;
    try {
      await this.video.play();
    } catch (error) {
      if (!this.closed && error.name !== "AbortError")
        this.onStatus("点击画面恢复播放");
    } finally {
      this.playing = false;
    }
  }
  async open() {
    const config = await this.api("status");
    if (this.closed) return;
    if (config.iceServers?.length)
      this.pc.setConfiguration({ iceServers: config.iceServers });
    const transceiver = this.pc.addTransceiver("video", {
      direction: "recvonly",
    });
    const codecs = RTCRtpReceiver.getCapabilities?.("video")?.codecs;
    if (codecs && transceiver.setCodecPreferences) {
      transceiver.setCodecPreferences([
        ...codecs.filter((c) => c.mimeType === "video/H264"),
        ...codecs.filter((c) => c.mimeType !== "video/H264"),
      ]);
    }
    await this.pc.setLocalDescription(await this.pc.createOffer());
    await new Promise((resolve, reject) => {
      if (this.pc.iceGatheringState === "complete") {
        resolve();
        return;
      }
      const timeout = setTimeout(() => {
        this.pc.removeEventListener("icegatheringstatechange", changed);
        reject(Error("连接协商超时"));
      }, config.iceServers?.length ? 12000 : 5000);
      const changed = () => {
        if (this.pc.iceGatheringState === "complete") {
          clearTimeout(timeout);
          this.pc.removeEventListener("icegatheringstatechange", changed);
          resolve();
        }
      };
      this.pc.addEventListener("icegatheringstatechange", changed);
    });
    const answer = await this.api("remote/offer", {
      ...this.scope,
      type: "offer",
      sdp: this.pc.localDescription.sdp,
    });
    if (!this.closed) await this.pc.setRemoteDescription(answer);
  }
  geometry() {
    if (
      this.closed ||
      !this.layout ||
      this.channel.readyState !== "open" ||
      this.video.videoWidth !== this.layout.encoded_width ||
      this.video.videoHeight !== this.layout.encoded_height
    )
      return null;
    return { rect: this.video.getBoundingClientRect(), layout: this.layout };
  }
  send(event) {
    if (this.closed || this.channel.readyState !== "open") return false;
    if (this.channel.bufferedAmount > 16384) {
      this.fail("输入通道积压，已释放拖动，请重连");
      return false;
    }
    const seq = ++this.seq;
    this.pending.set(seq, performance.now());
    this.channel.send(JSON.stringify({ ...event, seq }));
    return true;
  }
  ping() {
    if (!this.closed && this.channel.readyState === "open")
      this.channel.send(
        JSON.stringify({ kind: "ping", at: performance.now() }),
      );
  }
  receive(message) {
    if (this.closed) return;
    if (message.kind === "layout") {
      if (this.layout?.epoch !== message.epoch) this.input.cancel();
      this.layout = message;
      this.render();
    } else if (message.kind === "pong") {
      this.stats.transport_ms = Math.round(performance.now() - message.at);
      this.stats.decode_ms = message.decode_ms;
    } else if (message.kind === "applied") {
      const started = this.pending.get(message.seq);
      if (started !== undefined)
        this.stats.input_ms = Math.round(performance.now() - started);
      for (const seq of this.pending.keys())
        if (seq <= message.seq) this.pending.delete(seq);
    } else if (message.kind === "error") this.onError(message.message);
  }
  async measure() {
    if (this.closed || this.measuring) return;
    this.measuring = true;
    try {
      const visible = this.checkVisibility(),
        now = performance.now();
      const report = await this.pc.getStats();
      if (this.closed) return;
      for (const data of report.values()) {
        if (
          data.type === "inbound-rtp" &&
          (data.kind || data.mediaType) === "video"
        ) {
          const counter = Number.isFinite(data.framesDecoded)
            ? "framesDecoded"
            : "bytesReceived";
          if (!this.previous || data[counter] > this.previous[counter])
            this.lastMedia = now;
          if (this.previous) {
            const elapsed = (data.timestamp - this.previous.timestamp) / 1000;
            this.stats.fps =
              elapsed > 0
                ? Math.round(
                    (data.framesDecoded - this.previous.framesDecoded) /
                      elapsed,
                  )
                : 0;
            const count =
              data.jitterBufferEmittedCount -
              this.previous.jitterBufferEmittedCount;
            const delay =
              count > 0
                ? Math.round(
                    ((data.jitterBufferDelay -
                      this.previous.jitterBufferDelay) *
                      1000) /
                      count,
                  )
                : 0;
            if (Number.isFinite(delay)) this.stats.buffer_ms = delay;
          }
          this.previous = data;
        }
      }
      this.onStats({ ...this.stats });
      if (!visible) return; // Offscreen/background playback suspension is not a broken transport.
      if (
        this.pending.size &&
        performance.now() - this.pending.values().next().value > 2000
      )
        this.fail("浏览器输入超时，已停止接管，请重连");
      if (this.video.paused || (this.hasVideo && now - this.lastPaint > 3000))
        this.resumePlayback();
      if (now - Math.max(this.visibleSince, this.lastMedia) > 10000)
        this.fail("视频未恢复，正在重新连接…");
    } finally {
      this.measuring = false;
    }
  }
  render() {
    if (!this.video.videoWidth || !this.video.videoHeight) return;
    const width = this.container.clientWidth,
      height = this.container.clientHeight;
    const fit = Math.min(
      width / this.video.videoWidth,
      height / this.video.videoHeight,
    );
    const w = this.video.videoWidth * fit * this.scale,
      h = this.video.videoHeight * fit * this.scale;
    this.panX = Math.max(
      -Math.max(0, (w - width) / 2),
      Math.min(Math.max(0, (w - width) / 2), this.panX),
    );
    this.panY = Math.max(
      -Math.max(0, (h - height) / 2),
      Math.min(Math.max(0, (h - height) / 2), this.panY),
    );
    Object.assign(this.video.style, {
      width: w + "px",
      height: h + "px",
      left: (width - w) / 2 + this.panX + "px",
      top: (height - h) / 2 + this.panY + "px",
    });
  }
  zoom(ratio, clientX, clientY) {
    const r = this.video.getBoundingClientRect(),
      box = this.container.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const next = Math.max(1, Math.min(5, this.scale * ratio)),
      factor = next / this.scale;
    this.panX =
      clientX -
      box.left -
      (clientX - r.left) * factor -
      (box.width - r.width * factor) / 2;
    this.panY =
      clientY -
      box.top -
      (clientY - r.top) * factor -
      (box.height - r.height * factor) / 2;
    this.scale = next;
    this.render();
  }
  fit() {
    this.input.cancel();
    this.scale = 1;
    this.panX = this.panY = 0;
    this.render();
  }
  actualSize() {
    const box = this.container.getBoundingClientRect();
    this.zoom(2, box.left + box.width / 2, box.top + box.height / 2);
  }
  setInputMode(mode) {
    this.input.setMode(mode);
  }
  key(key) {
    this.input.flush();
    this.send({ kind: "key", key });
  }
  text(text) {
    this.input.flush();
    this.send({ kind: "text", text });
  }
  fail(message) {
    if (!this.closed) {
      this.onError(message);
      this.close();
      this.onEnded();
    }
  }
  close() {
    if (this.closed) return;
    this.input.close();
    this.closed = true;
    clearInterval(this.timer);
    this.resize.disconnect();
    this.intersection.disconnect();
    this.container.removeEventListener("pointerdown", this.wakePlayback);
    if (this.frameCallback !== undefined)
      this.video.cancelVideoFrameCallback?.(this.frameCallback);
    this.pc.close();
    this.video.srcObject = null;
    this.video.remove();
    this.pending.clear();
  }
}
