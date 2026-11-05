import { BrowserRemote } from "./browser-remote.js";
const $ = (s) => document.querySelector(s),
  esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
let token =
    localStorage.getItem("offerly-phone") ||
    sessionStorage.getItem("offerly-phone") ||
    "",
  task = null,
  lastPrompt = "",
  returning = false;
const message = (s) => ($("#message").textContent = s);
const feedback = $("#agent-feedback");
feedback.value = sessionStorage.getItem("offerly-agent-feedback") || "";
feedback.addEventListener("input", () =>
  sessionStorage.setItem("offerly-agent-feedback", feedback.value),
);
async function api(path, data, { signal } = {}) {
  const controller = new AbortController(),
    abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, path === "remote/offer" ? 25000 : 10000);
  try {
    let r;
    try {
      r = await fetch("/phone-api/" + path, {
        method: data === undefined ? "GET" : "POST",
        headers: { "X-Phone-Token": token, "Content-Type": "application/json" },
        signal: controller.signal,
        cache: "no-store",
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw Error("手机与电脑控制通道暂时中断，请检查网络；页面会自动重连。");
    }
    const d = await r.json();
    if (!r.ok) {
      if (r.status === 403 && path !== "pair") {
        localStorage.removeItem("offerly-phone");
        sessionStorage.removeItem("offerly-phone");
        token = "";
      }
      throw Error(d.error || "连接失败");
    }
    return d;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
async function act(action, data = {}) {
  if (!task) throw Error("暂无任务");
  return api(`tasks/${task.id}/${action}`, {
    ...data,
    id: task.id,
    current: task.current,
  });
}
function owns() {
  return task?.active && task.state === "WAITING_HUMAN";
}
let resumeKey = "",
  resumeBlobs = [],
  resumeGeneration = 0;
function drawDetails() {
  const item = task?.application;
  $("#application-details").hidden = !item;
  $("#job-details").hidden = !$("#show-job").checked;
  $("#job-details").textContent = item
    ? `${item.company}\n${item.job?.title || "尚未选岗"}\n${item.job?.city || ""}\n${item.job?.url || ""}\n\n${item.job?.jd || "尚未读取岗位详情"}\n\n选岗理由：${item.reason || ""}`
    : "";
  const show = $("#show-resume").checked;
  $("#resume-details").hidden = !show;
  const key =
    show && item?.resume ? `${task.id}:${task.current}:${item.resume.id}` : "";
  if (!key)
    $("#resume-message").textContent =
      "本次尚未生成专属简历，或使用的是自带简历。";
  if (key === resumeKey) return;
  resumeKey = key;
  const generation = ++resumeGeneration;
  for (const blob of resumeBlobs) URL.revokeObjectURL(blob);
  resumeBlobs = [];
  $("#resume-preview").replaceChildren();
  $("#resume-message").textContent = item?.resume
    ? `${item.resume.name} · ${item.resume.pages} 页 · 正在加载…`
    : "本次尚未生成专属简历，或使用的是自带简历。";
  if (!key) return;
  (async () => {
    for (let page = 1; page <= item.resume.pages; page++) {
      const response = await fetch(
        `/phone-api/resumes/${encodeURIComponent(item.resume.id)}/pages/${page}`,
        { headers: { "X-Phone-Token": token }, cache: "no-store" },
      );
      if (!response.ok) throw Error("简历预览加载失败，请取消勾选后重试。");
      const blob = await response.blob();
      if (generation !== resumeGeneration) return;
      const url = URL.createObjectURL(blob);
      resumeBlobs.push(url);
      const image = document.createElement("img");
      image.src = url;
      image.alt = `简历第 ${page} 页`;
      image.style.cssText =
        "width:100%;height:auto;display:block;margin:12px 0";
      $("#resume-preview").append(image);
    }
    if (generation === resumeGeneration)
      $("#resume-message").textContent =
        `${item.resume.name} · ${item.resume.pages} 页`;
  })().catch((e) => {
    if (generation === resumeGeneration)
      $("#resume-message").textContent = e.message;
  });
}
for (const id of ["show-job", "show-resume"])
  $("#" + id).addEventListener("change", drawDetails);
function draw() {
  drawDetails();
  $("#company").textContent = task?.company || "你的投递助手";
  $("#phase").textContent = task?.phase || "已连接，等待电脑启动任务";
  const elapsed = task?.progress?.since
    ? Math.max(
        0,
        Math.floor((Date.now() - Date.parse(task.progress.since)) / 1000),
      )
    : 0;
  $("#counts").textContent = task
    ? `已决策 ${task.decisions} 步 · 模型调用 ${task.api_calls} 次 · 当前动作 ${elapsed} 秒`
    : "";
  $("#timeline").innerHTML = (task?.progress?.timeline || [])
    .slice(-6)
    .map(
      (e) =>
        `<li>${esc(new Date(e.at).toLocaleTimeString())} ${esc(e.text)}</li>`,
    )
    .join("");
  const pending = task?.pending;
  $("#notice").style.display = pending ? "block" : "none";
  $("#notice").textContent =
    pending?.reason || (pending ? "请补充下方资料，Agent 正在等待。" : "");
  const prompt = pending
    ? `${task.id}:${task.current}:${pending.kind}:${pending.revision || pending.reason}`
    : "";
  if (!prompt) lastPrompt = "";
  if (prompt && lastPrompt !== prompt) {
    lastPrompt = prompt;
    $("#alert-text").textContent = $("#notice").textContent;
    if (!$("#alert-dialog").open) $("#alert-dialog").showModal();
    navigator.vibrate?.([150, 80, 150]);
  }
  $("#pause").hidden =
    !task?.active ||
    !["RUNNING", "RESUMING", "PAUSED", "PAUSING"].includes(task.state);
  $("#pause").textContent = ["PAUSED", "PAUSING"].includes(task?.state)
    ? "继续"
    : "暂停";
  $("#stop").hidden = !task?.active;
  $("#picture-section").hidden = !owns();
  drawInputMode();
  for (const id of ["resume", "complete", "skip"])
    $("#" + id).disabled = !owns() || returning;
  $("#open-keyboard").disabled =
    !owns() || returning || remoteView?.channel.readyState !== "open";
  $("#complete").hidden = !owns() || !task.canConfirmSubmitted;
  $("#resume").textContent = returning ? "正在交还…" : "继续，交还 Agent";
}
// Retire only this project's previous notification worker and subscription.
async function retireNotificationWorker() {
  if (!navigator.serviceWorker?.getRegistrations) return;
  for (const registration of await navigator.serviceWorker.getRegistrations()) {
    const worker =
      registration.active || registration.waiting || registration.installing;
    if (!worker || new URL(worker.scriptURL).pathname !== "/phone-sw.js")
      continue;
    try {
      const subscription = await registration.pushManager?.getSubscription();
      await subscription?.unsubscribe();
    } catch {}
    await registration.unregister();
  }
}
retireNotificationWorker().catch(() => {});
let phoneReady = false,
  pollTimer,
  pollController = null,
  pollGeneration = 0,
  lastStatusAt = 0,
  phoneStarted = false,
  suspended = false;
async function poll() {
  if (!phoneStarted || suspended || document.hidden || !token || pollController)
    return;
  clearTimeout(pollTimer);
  const controller = (pollController = new AbortController()),
    generation = pollGeneration;
  try {
    const started = performance.now(),
      d = await api("status", undefined, { signal: controller.signal });
    if (generation !== pollGeneration) return;
    lastStatusAt = performance.now();
    $("#network-stats").textContent =
      `控制接口往返 ${Math.round(lastStatusAt - started)} ms（不是画面延迟）`;
    task = d.task;
    phoneReady = true;
    $("#connection").textContent = "已连接";
    draw();
    if (owns() && !returning) {
      connectRemote();
      refreshTabs();
    } else closeRemote();
  } catch (e) {
    if (generation !== pollGeneration) return;
    phoneReady = false;
    $("#connection").textContent = "正在恢复连接";
    message(e.message);
    if (!token || performance.now() - lastStatusAt >= 15000) closeRemote();
  } finally {
    if (generation === pollGeneration) {
      pollController = null;
      if (!suspended && !document.hidden && token)
        pollTimer = setTimeout(poll, 1000);
    }
  }
}
function stopPolling() {
  clearTimeout(pollTimer);
  pollGeneration++;
  pollController?.abort();
  pollController = null;
}
function suspendPhone() {
  suspended = true;
  stopPolling();
  closeRemote();
  $("#frame-status").textContent = "回到本页后自动恢复画面";
}
function wakePhone() {
  if (document.hidden) return;
  suspended = false;
  retryAt = 0;
  if (!phoneStarted || !token) return;
  stopPolling();
  poll();
}
let remoteView = null,
  connectAttempt = null,
  remoteTask = "",
  panMode = false,
  scrollMode = true,
  retryAt = 0,
  remoteGeneration = 0;
let tabsBusy = false,
  tabsAt = 0,
  tabsScope = "",
  switching = false;
const screen = $("#screen");
function drawInputMode() {
  $("#scroll-mode").textContent = scrollMode ? "切回拖动模式" : "切换滚动模式";
  $("#scroll-mode").setAttribute("aria-pressed", String(scrollMode));
  $("#pan-mode").textContent = panMode ? "返回网页操作" : "移动视野";
  $("#input-state").textContent = panMode
    ? "移动视野模式"
    : scrollMode
      ? "滚动模式"
      : "拖动模式";
  $("#screen-help").textContent = panMode
    ? "滑动移动放大后的画面，捏合缩放。"
    : scrollMode
      ? "单指滑动滚动网页；轻触点击。拖动验证码滑块前，切回拖动模式。"
      : "轻触点击；按住并滑动拖动鼠标，可用于滑块验证。滚动网页请切换滚动模式；捏合缩放画面。";
}
function updateInputMode() {
  remoteView?.setInputMode(panMode ? "pan" : scrollMode ? "scroll" : "control");
  drawInputMode();
}
async function refreshTabs(force = false) {
  if (
    !owns() ||
    tabsBusy ||
    switching ||
    (!force && Date.now() - tabsAt < 3000)
  )
    return;
  const scope = `${task.id}:${task.current}`;
  tabsBusy = true;
  tabsAt = Date.now();
  try {
    const d = await api(
      `remote/tabs?id=${encodeURIComponent(task.id)}&current=${task.current}`,
    );
    if (!owns() || scope !== `${task.id}:${task.current}`) return;
    const list = $("#browser-tabs"),
      selected = tabsScope === scope ? list.value : d.current;
    const html = d.tabs
      .map(
        (t) =>
          `<option value="${esc(t.id)}">${esc(t.title)} · ${esc(t.id.slice(-4))}${t.id === d.current ? " · 当前" : ""}</option>`,
      )
      .join("");
    if (list.innerHTML !== html) {
      list.innerHTML = html;
      list.value = d.tabs.some((t) => t.id === selected) ? selected : d.current;
    }
    tabsScope = scope;
  } catch (e) {
    message(e.message);
  } finally {
    tabsBusy = false;
  }
}
function closeRemote() {
  remoteGeneration++;
  connectAttempt?.abort();
  connectAttempt = null;
  remoteTask = "";
  const previous = remoteView;
  remoteView = null;
  keyboard.blur();
  composing = false;
  compositionCommitted = false;
  resetKeyboard();
  previous?.close();
}
async function connectRemote(force = false) {
  const id = `${task?.id}:${task?.current}`;
  if (
    !owns() ||
    suspended ||
    document.hidden ||
    returning ||
    (!force &&
      (connectAttempt ||
        (remoteView && remoteTask === id) ||
        Date.now() < retryAt))
  )
    return;
  closeRemote();
  const attempt = (connectAttempt = new AbortController());
  remoteTask = id;
  const generation = remoteGeneration;
  const current = () =>
    generation === remoteGeneration &&
    !suspended &&
    !document.hidden &&
    owns() &&
    id === `${task?.id}:${task?.current}`;
  $("#frame-status").textContent = "正在恢复浏览器画面…";
  let remote;
  try {
    remote = new BrowserRemote(
      screen,
      (path, data) => api(path, data, { signal: attempt.signal }),
      { id: task.id, current: task.current },
      (status) => {
        if (current()) $("#frame-status").textContent = status;
      },
      (stats) => {
        if (current())
          $("#remote-stats").textContent =
            `输入往返 ${stats.input_ms ?? "—"} ms · 视频 ${stats.fps ?? "—"} fps · 播放缓冲 ${stats.buffer_ms ?? "—"} ms（非端到端延迟）`;
      },
      () => {
        if (current()) {
          closeRemote();
          retryAt = Date.now() + 1000;
          $("#frame-status").textContent = "连接中断，正在自动恢复画面…";
        }
      },
      (error) => {
        if (current()) message(error);
      },
    );
    remoteView = remote;
    updateInputMode();
    await remote.open();
    if (!current()) {
      remote.close();
      return;
    }
  } catch (e) {
    if (current()) {
      message(e.message);
      closeRemote();
      retryAt = Date.now() + 2000;
    }
  } finally {
    if (connectAttempt === attempt) connectAttempt = null;
  }
}
$("#zoom-in").onclick = () => remoteView?.actualSize();
$("#zoom-out").onclick = () => remoteView?.fit();
$("#pan-mode").onclick = () => {
  panMode = !panMode;
  updateInputMode();
};
$("#scroll-mode").onclick = () => {
  scrollMode = !scrollMode;
  panMode = false;
  updateInputMode();
};
$("#switch-tab").onclick = async () => {
  if (!owns() || switching) return;
  switching = true;
  $("#switch-tab").disabled = true;
  try {
    await api("remote/switch", {
      id: task.id,
      current: task.current,
      target_id: $("#browser-tabs").value,
    });
    closeRemote();
    await connectRemote(true);
    message("已切换标签页。");
  } catch (e) {
    message(e.message);
  } finally {
    switching = false;
    $("#switch-tab").disabled = false;
    refreshTabs(true);
  }
};
$("#fullscreen").onclick = () =>
  screen.requestFullscreen?.().catch((e) => message(e.message));
$("#retry-frame").onclick = () => connectRemote(true);
async function handBack(action) {
  if (returning || !owns()) return;
  const id = task.id,
    current = task.current,
    note = feedback.value;
  returning = true;
  draw();
  message("正在向 Agent 交还浏览器，请稍候…");
  try {
    await act(action, {
      feedback: note,
      ...(action === "complete" ? { confirmSubmitted: true } : {}),
    });
    closeRemote();
    if (task?.id === id && task.current === current) {
      task.state = "RESUMING";
      task.pending = null;
      task.phase =
        action === "skip"
          ? "已放弃本岗，Agent 正在继续"
          : "已交还，Agent 正在继续";
    }
    if (feedback.value === note) {
      feedback.value = "";
      sessionStorage.removeItem("offerly-agent-feedback");
    }
    message(
      action === "skip"
        ? "已放弃该岗位，继续下一目标。"
        : action === "complete"
          ? "已记录该岗位投递完成。"
          : note
            ? "已交还，你的回复已传给 Agent。"
            : "已交还，Agent 正在继续。",
    );
  } catch (e) {
    message(`交还未成功：${e.message}。请重试对应按钮。`);
  } finally {
    returning = false;
    draw();
  }
}
$("#resume").onclick = () => handBack("resume");
$("#complete").onclick = () => handBack("complete");
$("#skip").onclick = () => handBack("skip");
$("#pause").onclick = () =>
  act(["PAUSED", "PAUSING"].includes(task.state) ? "continue" : "pause").catch(
    (e) => message(e.message),
  );
$("#stop").onclick = () => act("stop").catch((e) => message(e.message));
$("#dismiss").onclick = () => $("#alert-dialog").close();
const keyboard = $("#remote-keyboard");
let composing = false,
  compositionCommitted = false;
const keyboardMarker = "\u200b";
function resetKeyboard() {
  keyboard.value = keyboardMarker;
  keyboard.setSelectionRange(1, 1);
}
$("#open-keyboard").onclick = () => {
  if (!owns() || returning || remoteView?.channel.readyState !== "open") return;
  keyboard.blur();
  resetKeyboard();
  keyboard.focus({ preventScroll: true });
};
resetKeyboard();
keyboard.addEventListener("compositionstart", () => {
  composing = true;
  compositionCommitted = false;
});
keyboard.addEventListener("beforeinput", (e) => {
  if (composing || e.isComposing) return;
  if (compositionCommitted) {
    e.preventDefault();
    return;
  }
  if (e.inputType === "deleteContentBackward" && e.cancelable) {
    e.preventDefault();
    remoteView?.key("Backspace");
    resetKeyboard();
  } else if (
    ["insertLineBreak", "insertParagraph"].includes(e.inputType) &&
    e.cancelable
  ) {
    e.preventDefault();
    remoteView?.key("Enter");
    resetKeyboard();
  } else if (e.inputType === "insertText" && e.data && e.cancelable) {
    e.preventDefault();
    remoteView?.text(e.data);
    resetKeyboard();
  }
});
keyboard.addEventListener("compositionend", (e) => {
  composing = false;
  if (e.data) remoteView?.text(e.data);
  resetKeyboard();
  compositionCommitted = true;
  setTimeout(() => (compositionCommitted = false), 0);
});
keyboard.addEventListener("input", (e) => {
  if (composing || e.isComposing || compositionCommitted) return;
  const text = keyboard.value.replaceAll(keyboardMarker, "");
  if (text) remoteView?.text(text);
  else if (e.inputType === "deleteContentBackward")
    remoteView?.key("Backspace");
  resetKeyboard();
});
keyboard.addEventListener("keydown", (e) => {
  if (
    !composing &&
    !e.isComposing &&
    ["Tab", "Enter", "Escape", "Backspace"].includes(e.key)
  ) {
    e.preventDefault();
    remoteView?.key(e.key);
    resetKeyboard();
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) suspendPhone();
  else wakePhone();
});
window.addEventListener("pagehide", suspendPhone);
window.addEventListener("pageshow", wakePhone);
window.addEventListener("online", wakePhone);
window.addEventListener("focus", () => {
  if (!document.hidden && (!phoneReady || !remoteView)) wakePhone();
});
async function init() {
  try {
    const ticket = location.hash.slice(1);
    if (ticket) {
      try {
        const result = await api("pair", { ticket });
        token = result.token;
        localStorage.setItem("offerly-phone", token);
        sessionStorage.setItem("offerly-phone", token);
      } catch (e) {
        if (!token) throw e;
        await api("status");
      }
      history.replaceState(null, "", location.pathname);
    }
    if (!token) throw Error("请打开电脑生成的配对链接。");
    phoneStarted = true;
    wakePhone();
  } catch (e) {
    message(e.message);
    $("#connection").textContent = "未连接";
  }
}
init();
