export function mapPoint(rect, layout, x, y, clamp = false) {
  if (!layout || !(rect.width > 0 && rect.height > 0)) return null;
  let nx =
    (((x - rect.left) / rect.width) * layout.encoded_width) /
    layout.image_width;
  let ny =
    (((y - rect.top) / rect.height) * layout.encoded_height) /
    layout.image_height;
  if (!Number.isFinite(nx) || !Number.isFinite(ny)) return null;
  if (!clamp && (nx < 0 || ny < 0 || nx >= 1 || ny >= 1)) return null;
  return {
    nx: Math.max(0, Math.min(0.999999, nx)),
    ny: Math.max(0, Math.min(0.999999, ny)),
    epoch: layout.epoch,
  };
}

export class PointerInput {
  constructor(element, geometry, send, camera) {
    this.element = element;
    this.geometry = geometry;
    this.send = send;
    this.camera = camera;
    this.points = new Map();
    this.owner = null;
    this.last = null;
    this.move = null;
    this.raf = 0;
    this.scroll = null;
    this.mode = "control";
    this.suppressed = false;
    this.gesture = null;
    this.tap = null;
    this.listeners = [];
    for (const type of [
      "pointerdown",
      "pointermove",
      "pointerup",
      "pointercancel",
      "lostpointercapture",
    ]) {
      const fn = (event) => this.handle(event);
      element.addEventListener(type, fn);
      this.listeners.push([type, fn]);
    }
    this.wheel = (event) => {
      const point = this.point(event.clientX, event.clientY);
      if (point && this.mode !== "pan") {
        event.preventDefault();
        this.queueScroll({
          kind: "scroll",
          ...point,
          dx: event.deltaX,
          dy: event.deltaY,
        });
      }
    };
    element.addEventListener("wheel", this.wheel, { passive: false });
    this.blur = () => this.cancel();
    window.addEventListener("blur", this.blur);
  }
  point(x, y, clamp = false) {
    const data = this.geometry();
    return data ? mapPoint(data.rect, data.layout, x, y, clamp) : null;
  }
  flush() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.move) {
      const point = this.move;
      this.move = null;
      this.send({ kind: "pointer", phase: "move", ...point });
    }
    if (this.scroll) {
      const event = this.scroll;
      this.scroll = null;
      this.send(event);
    }
  }
  queueScroll(event) {
    if (this.scroll && this.scroll.epoch !== event.epoch) this.flush();
    this.scroll = {
      ...event,
      dx: (this.scroll?.dx || 0) + event.dx,
      dy: (this.scroll?.dy || 0) + event.dy,
    };
    if (!this.raf) this.raf = requestAnimationFrame(() => this.flush());
  }
  release() {
    this.flush();
    if (this.owner !== null) {
      this.owner = null;
      this.send({ kind: "pointer", phase: "up", ...this.last });
    }
  }
  cancel() {
    this.release();
    const ids = [...this.points.keys()];
    this.points.clear();
    this.gesture = null;
    this.tap = null;
    this.suppressed = false;
    for (const id of ids)
      if (this.element.hasPointerCapture(id))
        this.element.releasePointerCapture(id);
  }
  setMode(mode) {
    if (!["control", "scroll", "pan"].includes(mode))
      throw Error("未知操作模式");
    this.cancel();
    this.mode = mode;
  }
  handle(event) {
    if (
      event.pointerType === "mouse" &&
      event.type === "pointerdown" &&
      event.button !== 0
    )
      return;
    const id = event.pointerId,
      position = { x: event.clientX, y: event.clientY };
    if (event.type === "pointerdown") {
      if (!this.point(position.x, position.y)) return;
      event.preventDefault();
      this.points.set(id, position);
      this.element.setPointerCapture(id);
      if (this.points.size === 1 && !this.suppressed) {
        if (this.mode === "control") {
          this.owner = id;
          this.last = this.point(position.x, position.y);
          this.send({ kind: "pointer", phase: "down", ...this.last });
        } else if (this.mode === "scroll")
          this.tap = { id, ...position, scrolling: false };
      } else {
        this.release();
        this.tap = null;
        this.suppressed = true;
        if (this.points.size === 2)
          this.gesture = {
            ...this.multi(),
            initialDistance: this.multi().distance,
            type: null,
          };
      }
      return;
    }
    if (!this.points.has(id)) return;
    if (event.type === "pointermove") {
      event.preventDefault();
      const previous = this.points.get(id);
      this.points.set(id, position);
      if (this.points.size === 2 && this.gesture) {
        const now = this.multi(),
          old = this.gesture;
        if (
          !old.type &&
          Math.abs(Math.log(now.distance / old.initialDistance)) > 0.05
        )
          old.type = "zoom";
        if (old.type === "zoom")
          this.camera.zoom(now.distance / old.distance, now.x, now.y);
        else if (this.mode === "pan")
          this.camera.pan(now.x - old.x, now.y - old.y);
        else {
          const point = this.point(now.x, now.y, true);
          const g = this.geometry();
          if (point && g)
            this.queueScroll({
              kind: "scroll",
              ...point,
              dx: 0,
              dy: ((old.y - now.y) * g.layout.height) / g.rect.height,
            });
        }
        this.gesture = {
          ...now,
          initialDistance: old.initialDistance,
          type: old.type,
        };
      } else if (this.mode === "pan" && !this.suppressed)
        this.camera.pan(position.x - previous.x, position.y - previous.y);
      else if (
        this.mode === "scroll" &&
        !this.suppressed &&
        this.tap?.id === id
      ) {
        const tap = this.tap,
          g = this.geometry(),
          point = this.point(position.x, position.y, true);
        if (
          point &&
          g &&
          (tap.scrolling ||
            Math.hypot(position.x - tap.x, position.y - tap.y) >= 8)
        ) {
          const from = tap.scrolling ? previous : tap;
          this.queueScroll({
            kind: "scroll",
            ...point,
            dx: ((from.x - position.x) * g.layout.width) / g.rect.width,
            dy: ((from.y - position.y) * g.layout.height) / g.rect.height,
          });
          tap.scrolling = true;
        }
      } else if (this.owner === id) {
        const point = this.point(position.x, position.y, true);
        if (point) {
          this.last = this.move = point;
          if (!this.raf) this.raf = requestAnimationFrame(() => this.flush());
        }
      }
      return;
    }
    if (this.tap?.id === id) {
      if (
        event.type === "pointerup" &&
        !this.tap.scrolling &&
        !this.suppressed &&
        Math.hypot(position.x - this.tap.x, position.y - this.tap.y) < 8
      ) {
        const point = this.point(position.x, position.y);
        if (point) {
          this.send({ kind: "pointer", phase: "down", ...point });
          this.send({ kind: "pointer", phase: "up", ...point });
        }
      }
      this.tap = null;
    }
    if (event.type === "pointerup" && this.owner === id) {
      const point = this.point(position.x, position.y, true);
      if (point) this.last = this.move = point;
    }
    if (this.owner === id) this.release();
    this.points.delete(id);
    if (!this.points.size) {
      this.flush();
      this.suppressed = false;
      this.gesture = null;
    }
  }
  multi() {
    const [a, b] = this.points.values();
    return {
      x: (a.x + b.x) / 2,
      y: (a.y + b.y) / 2,
      distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
    };
  }
  close() {
    this.cancel();
    for (const [type, fn] of this.listeners)
      this.element.removeEventListener(type, fn);
    this.element.removeEventListener("wheel", this.wheel);
    window.removeEventListener("blur", this.blur);
  }
}
