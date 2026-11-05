import {
  sections,
  attachmentTypes,
  blankItem,
  emptyProfile,
  normalizeProfile,
} from "./profile-schema.js";

import { syncDerivedProfile } from "./profile-derived.js";
import {
  bookSteps,
  robotArt,
  coverView,
  storyLeft,
  introRight,
} from "./book-scene.js";

const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  layers: "m12 3 9 5-9 5-9-5 9-5z M3 12l9 5 9-5 M3 16l9 5 9-5",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6",
  briefcase: "M3 7h18v14H3z M8 7V3h8v4 M3 12l9 3 9-3",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
  phone:
    "M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2 M10 18h4",
  plus: "M12 5v14 M5 12h14",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  upload: "M12 16V3 M7 8l5-5 5 5 M4 16v5h16v-5",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12 M6 18 18 6",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6",
  link: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4",
  chevron: "m6 9 6 6 6-6",
};
const icon = (n) =>
  `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="${paths[n] || paths.file}"/></svg>`;
const button = (label, action, cls = "", i = "") =>
  `<button type="button" class="btn ${cls}" data-action="${action}">${i ? icon(i) : ""}${label}</button>`;
const routes = {
  browser: ["选择浏览器", "layers"],
  plugin: ["准备插件", "layers"],
  materials: ["准备资料", "file"],
  phone: ["连接手机", "phone"],
};
const routeAlias = {
  plan: "materials",
  companies: "preferences",
  jobs: "preferences",
  profile: "materials",
  model: "launch",
};
const STORAGE = "autumn.source.v1";
const resumeModules = {
  summary: "个人优势",
  education: "教育经历",
  internships: "实习经历",
  projects: "项目经历",
  campus: "校园经历",
  custom: "自定义模块",
};
const fresh = () => ({
  version: 2,
  selectedBrowser: "edge",
  pluginReadyFor: "",
  applicationCount: 5,
  jobSource: "search",
  targetLines: "",
  resumeOnePage: true,
  resumeSections: ["summary", "education"],
  resumePlan: "upload",
  resumeTemplate: "classic",
  preferences: {
    keywords: "AI",
    cities: "",
    companyBlacklist: "",
    category: "校园招聘",
    year: "",
    notes: "",
    salary: "不限",
    experience: "不限",
    education: "不限",
    industry: "",
    companySize: "不限",
    published: "不限",
  },
  profile: emptyProfile(),
  attachments: [],
  model: {
    provider: "DeepSeek",
    model: "deepseek-flash",
    baseUrl: "https://api.deepseek.com",
  },
});

function loadState() {
  const base = fresh();
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE));
    if (saved?.version === 2) {
      base.profile = { ...base.profile, ...saved.profile };
      for (const s of sections)
        base.profile[s.id] = s.repeat
          ? Array.isArray(saved.profile?.[s.id])
            ? saved.profile[s.id]
            : []
          : { ...base.profile[s.id], ...saved.profile?.[s.id] };
      return {
        ...base,
        jobSource: ["search", "companies", "websites"].includes(saved.jobSource)
          ? saved.jobSource
          : "search",
        targetLines:
          typeof saved.targetLines === "string" ? saved.targetLines : "",
        selectedBrowser: saved.selectedBrowser === "chrome" ? "chrome" : "edge",
        pluginTestVersion: saved.pluginTestVersion,
        pluginReadyFor:
          saved.pluginTestVersion === 1 &&
          ["edge", "chrome"].includes(saved.pluginReadyFor)
            ? saved.pluginReadyFor
            : "",
        applicationCount:
          Number.isInteger(saved.applicationCount) &&
          saved.applicationCount > 0 &&
          saved.applicationCount <= 200
            ? saved.applicationCount
            : 5,
        resumeOnePage: true,
        resumeSections: Array.isArray(saved.resumeSections)
          ? saved.resumeSections.filter((k) => k in resumeModules)
          : base.resumeSections,
        resumePlan: saved.resumePlan === "template" ? "template" : "upload",
        resumeTemplate: ["classic", "technical", "graduate"].includes(
          saved.resumeTemplate,
        )
          ? saved.resumeTemplate
          : "classic",
        attachments: Array.isArray(saved.attachments) ? saved.attachments : [],
        preferences: { ...base.preferences, ...saved.preferences },
        model: { ...base.model, ...saved.model },
      };
    }
    const old = JSON.parse(localStorage.getItem("offerly.frontend.v1"));
    if (old?.version === 1) {
      base.companies = (old.companies || []).map((c) => ({ ...c }));
      for (const k of Object.keys(base.preferences))
        if (old.config?.[k]) base.preferences[k] = old.config[k];
      base.model = { ...base.model, ...old.settings };
      // Preserve edited values, even if other fields still contain the fictional defaults.
      if (old.profile) {
        const demo = {
          name: "林知夏",
          email: "demo@example.invalid",
          phone: "13800000000",
          degree: "本科",
          school: "示例大学",
          major: "计算机科学与技术",
          start: "2023-09",
          graduation: "2027-06",
          city: "成都",
          salary: "10000",
          experience:
            "示例项目：开发基于大模型的知识库问答系统，负责数据处理、检索与接口开发。",
          attachment: "",
        };
        const p = Object.fromEntries(
          Object.entries(old.profile).filter(([k, v]) => v !== demo[k]),
        );
        base.profile.basic.name = p.name || "";
        base.profile.contact.email = p.email || "";
        base.profile.contact.phone = p.phone || "";
        base.profile.intent.highestEducation = p.degree || "";
        base.profile.intent.desiredCities = p.city || "";
        base.profile.intent.salaryMonthly = p.salary || "";
        if ([p.school, p.major, p.degree, p.start, p.graduation].some(Boolean))
          base.profile.education = [
            {
              ...blankItem(sections.find((s) => s.id === "education")),
              school: p.school || "",
              major: p.major || "",
              level: p.degree || "",
              start: p.start || "",
              end: p.graduation || "",
            },
          ];
        base.profile.extra.notes = p.experience || "";
      }
    }
  } catch {
    /* A damaged or unavailable local draft must not prevent the page opening. */
  }
  return base;
}
let state = loadState();
state.profile = normalizeProfile(state.profile);
syncDerivedProfile(state.profile);
delete state.preferences.exclude;
let bookPage = routes[
  routeAlias[location.hash.slice(1)] || location.hash.slice(1)
]
  ? routeAlias[location.hash.slice(1)] || location.hash.slice(1)
  : location.hash === "#intro"
    ? "intro"
    : "cover";
let assistantOpen = false,
  assistantStage = "preferences",
  assistantError = "";
const pageScroll = new Map();
let storedModel = false,
  savedModelUrl = "",
  apiKey = "",
  toastTimer,
  saveTimer;
let serviceToken = "",
  serviceOnline = false,
  activeTask = null,
  startingTask = false,
  lastPrompt = "",
  pollTimer;
let phoneStatus = { enabled: false },
  pairLink = "",
  wechatToken = "",
  wechatBusy = false,
  wechatMessage = "";
let browserConnections = {},
  browserHelperRequired = /(?:^|[?&])connection=helper(?:&|$)/.test(location.search),
  helperLaunchUrl = "",
  agentAvailable = false,
  autonomousTaskAvailable = false;
const browserTests = {},
  browserTesting = new Set(),
  browserRequests = new Set();
let tataChecking = false;
const tataResults = {};
let workspaceLoaded = false,
  workspaceRevision = 0,
  workspaceSignature = "",
  remoteSaveTimer,
  remoteSaveChain = Promise.resolve(),
  savingChapter = false,
  profileEdited = false,
  modelEdited = false;
const profileSnapshot = () => ({
  profile: state.profile,
  resumePlan: state.resumePlan,
  resumeTemplate: state.resumeTemplate,
  resumeSections: state.resumeSections,
  resumeOnePage: state.resumeOnePage,
});
const files = new Map();

// These are PDF content fields, independent of the detailed recruitment form schema.
const pdfModuleFields = {
  summary: {
    path: "extra.evaluation",
    label: "描述",
    hint: "填写与你求职方向相关的个人优势。",
  },
  education: {
    group: "education",
    fields: [
      ["school", "学校"],
      ["level", "学历"],
      ["major", "专业"],
      ["dateRange", "就读时间"],
      ["description", "课程、成绩或其他亮点", "textarea"],
    ],
  },
  internships: {
    group: "internships",
    fields: [
      ["company", "公司"],
      ["role", "实习岗位"],
      ["dateRange", "实习时间"],
      ["duties", "实习内容与成果", "textarea"],
    ],
  },
  projects: {
    group: "projects",
    fields: [
      ["name", "项目名称"],
      ["role", "担任角色"],
      ["dateRange", "项目时间"],
      ["duties", "负责内容", "textarea"],
      ["results", "项目成果", "textarea"],
    ],
  },
  campus: {
    group: "campus",
    fields: [
      ["organization", "学校 / 组织"],
      ["role", "职务 / 角色"],
      ["dateRange", "经历时间"],
      ["duties", "参与内容与成果", "textarea"],
    ],
  },
};
function pdfRows(group, fields) {
  const rows = state.profile[group] || [];
  const basics = fields.filter(([, , type]) => type !== "textarea");
  return `${(rows.length ? rows : [{}])
    .map((row, index) => {
      const previous = fields
        .filter(([, , type]) => type === "textarea")
        .map(([key]) => row[key])
        .filter(Boolean);
      const description =
        row.resumeDescription ?? [...new Set(previous)].join("\n\n");
      return `<div class="pdf-entry"><div class="pdf-entry-header"><small>第 ${index + 1} 条</small>${rows.length > 1 ? `<button type="button" class="text-btn danger" data-action="remove-pdf-entry" data-group="${group}" data-index="${index}">删除</button>` : ""}</div><div class="form-grid">${basics.map(([key, label, type = "text"]) => field(label, `profile.${group}.${index}.${key}`, row[key] || "", type, null, key === "dateRange" ? "例如：2023.09 — 2027.06 / 至今" : "")).join("")}</div>${field("描述", `profile.${group}.${index}.resumeDescription`, description, "textarea", null, "自由描述这段经历。")}</div>`;
    })
    .join(
      "",
    )}${button("＋ 添加一条", "add-pdf-entry", "text-btn pdf-add").replace('data-action="add-pdf-entry"', `data-action="add-pdf-entry" data-group="${group}"`)}`;
}
function pdfModuleEditor(id) {
  const spec = pdfModuleFields[id];
  if (spec.path) {
    const [group, key] = spec.path.split(".");
    return field(
      spec.label,
      "profile." + spec.path,
      state.profile[group]?.[key] || "",
      "textarea",
      null,
      spec.hint,
    );
  }
  return pdfRows(spec.group, spec.fields);
}
function resumeModuleView() {
  const fixed = Object.entries(resumeModules).filter(([id]) => id !== "custom");
  const choices = fixed
    .map(([id, label]) => {
      const selected = state.resumeSections.includes(id);
      return `<div class="module-tile ${selected ? "is-selected" : ""}"><input type="checkbox" data-resume-section="${id}" aria-label="将${label}加入简历" ${selected ? "checked" : ""}><button type="button" data-action="edit-module" data-module="${id}"><strong>${label}</strong><small>点击填写</small></button></div>`;
    })
    .join("");
  const custom = (state.profile.customModules || [])
    .map(
      (row, i) =>
        `<button type="button" class="module-tile custom-module-tile is-selected" data-action="edit-custom-module" data-index="${i}"><strong>${esc(row.name || "自定义模块 " + (i + 1))}</strong><small>点击填写</small></button>`,
    )
    .join("");
  const count = fixed.length + (state.profile.customModules || []).length + 1;
  return `<div class="pdf-contact"><h3>简历抬头</h3><div class="form-grid">${field("姓名", "profile.basic.name", state.profile.basic.name)}${field("手机号", "profile.contact.phone", state.profile.contact.phone, "tel")}${field("邮箱", "profile.contact.email", state.profile.contact.email, "email")}</div></div><details class="resume-modules pdf-modules" open><summary>调整简历模块</summary><p class="muted compact-note">点击模块填写资料，勾选需要放入简历的模块。</p><div class="pdf-module-list module-grid">${choices}${custom}<button type="button" class="module-tile module-add" data-action="add-custom-module"><strong>＋ 添加模块</strong><small>自定义名称与内容</small></button>${Array(
    Math.max(0, 9 - count),
  )
    .fill('<span class="pdf-module-space" aria-hidden="true"></span>')
    .join("")}</div><small>取消勾选会保留已填写资料。</small></details>`;
}
function openModule(id) {
  modal(
    resumeModules[id],
    `<div class="module-modal-editor">${pdfModuleEditor(id)}</div><div class="form-actions">${button("完成", "finish-module", "primary")}</div>`,
  );
  $("#modal-root").dataset.module = id;
}
function openCustomModule(index) {
  const row = state.profile.customModules[index];
  modal(
    "自定义模块",
    `${field("模块名称", `profile.customModules.${index}.name`, row.name || "")}${field("描述", `profile.customModules.${index}.content`, row.content || "", "textarea")}<div class="form-actions">${button("完成", "finish-module", "primary")}<button type="button" class="text-btn danger" data-action="remove-custom-module" data-index="${index}">移除此模块</button></div>`,
  );
  $("#modal-root").dataset.customIndex = String(index);
}
function finishModule() {
  save();
  $("#modal-root").innerHTML = "";
  render();
}
function templateCards() {
  return `<div class="template-grid">${[
    ["classic", "经典单栏", "黑白单栏，清晰列出每段经历", "通用网申"],
    ["technical", "深蓝侧栏", "技能在左，项目与经历在右", "技术研发"],
    ["graduate", "清爽双栏", "教育优先，其他模块均衡双列", "校招实习"],
  ]
    .map(
      ([id, title, note, tag]) =>
        `<article class="template-card ${state.resumeTemplate === id ? "selected" : ""}"><label><div class="template-image"><img src="assets/resumes/${id}.png" alt="${title}真实 PDF 版式示例" loading="lazy"><span>${state.resumeTemplate === id ? "已选择" : tag}</span></div><strong><input type="radio" name="resume-template" value="${id}" ${state.resumeTemplate === id ? "checked" : ""}>${title}</strong><p>${note}</p></label><button type="button" class="text-btn" data-action="preview-template" data-template="${id}">用我的资料预览 ↗</button></article>`,
    )
    .join("")}</div>`;
}

async function pdfBlob(path, options) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-Offerly-Token": serviceToken, ...options?.headers },
  });
  if (!response.ok) {
    const data = await response.json();
    throw Error(data.error || "PDF 加载失败");
  }
  const blob = await response.blob();
  blob.resumePages = Number(response.headers.get("X-Resume-Pages")) || 0;
  return blob;
}
function showPDF(blob, name) {
  const url = URL.createObjectURL(blob);
  modal(
    `简历预览${blob.resumePages ? " · " + blob.resumePages + " 页" : ""}`,
    `${blob.resumePages > 1 ? '<p class="resume-page-warning">内容较多，已保留全部内容。可取消不重要的模块或精简描述。</p>' : ""}<iframe class="resume-pdf" src="${url}" title="PDF 简历预览"></iframe><a class="btn primary" href="${url}" download="${esc(name)}">下载 PDF</a>`,
  );
  setTimeout(() => URL.revokeObjectURL(url), 600000);
}
async function previewTemplate(template = state.resumeTemplate) {
  if (!state.resumeSections.length) return toast("请至少勾选一个简历模块。");
  try {
    await saveRemoteProfile();
    const blob = await pdfBlob("/api/resumes/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        revision: workspaceRevision,
        template,
        sections: state.resumeSections,
        onePage: state.resumeOnePage,
      }),
    });
    showPDF(blob, "个人简历.pdf");
  } catch (error) {
    toast(error.message);
  }
}

function toast(message) {
  const e = $("#toast");
  e.textContent = message;
  e.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => e.classList.remove("show"), 3800);
}
function save() {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(state));
    if ($("#save-state"))
      $("#save-state").textContent = workspaceLoaded
        ? JSON.stringify(profileSnapshot()) === workspaceSignature
          ? "已保存到本机"
          : "保存中…"
        : "本地草稿";
  } catch {
    if ($("#save-state")) $("#save-state").textContent = "未能保存";
    toast("本地存储空间不足或不可用，请保持页面打开。");
  }
  if (workspaceLoaded) {
    clearTimeout(remoteSaveTimer);
    remoteSaveTimer = setTimeout(
      () =>
        saveRemoteProfile().catch((e) => {
          if ($("#save-state")) $("#save-state").textContent = "待保存到本机";
          toast(e.message);
        }),
      700,
    );
  }
}
function queueSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 200);
}
window.addEventListener("pagehide", save);
function go(next) {
  next = routeAlias[next] || next;
  if (next === "launch" || next === "preferences") {
    assistantOpen = true;
    assistantStage =
      next === "launch" && activeTask ? "progress" : "preferences";
  } else {
    if (!routes[next] && !["intro", "cover"].includes(next)) return;
    bookPage = next;
    assistantOpen = false;
    history.replaceState(
      null,
      "",
      next === "cover" ? location.pathname + location.search : "#" + next,
    );
  }
  $("#modal-root").innerHTML = "";
  save();
  render();
  window.scrollTo({ top: 0, behavior: "instant" });
}
window.addEventListener("hashchange", () => go(location.hash.slice(1)));
const heading = (title, description) =>
  `<div class="heading"><h2>${title}</h2><p>${description}</p></div>`;
function render() {
  const oldPage = $(".paper-scroll");
  if (oldPage) pageScroll.set(oldPage.dataset.page, oldPage.scrollTop);
  const chatScroll = $(".assistant-body")?.scrollTop || 0;
  const order = ["intro", ...bookSteps.map((s) => s.id)];
  const index = order.indexOf(bookPage);
  const views = {
    browser: browserView,
    plugin: pluginView,
    materials: materialsView,
    phone: phoneView,
  };
  const contents = bookSteps
    .map(
      (s) =>
        `<button data-route="${s.id}"><span>${s.mark}</span>${s.title}</button>`,
    )
    .join("");
  $("#app").innerHTML =
    `<div class="reading-room"><header class="room-header" ${assistantOpen ? "inert" : ""}><button class="room-brand" data-route="cover" aria-label="返回封面"><span class="room-brand-mark">o.</span>offerly<span class="room-brand-note">求职，翻开新一页</span></button><div class="room-tools"><button class="text-btn" data-action="application-history">投递记录</button><button class="text-btn" data-action="model-settings">服务设置</button><span id="save-state" class="save-state">${workspaceLoaded ? "已保存到本机" : "本地草稿"}</span></div></header><main ${assistantOpen ? "inert" : ""} class="main reading-desk ${bookPage === "cover" ? "is-closed" : ""}">${bookPage === "cover" ? coverView() : `<article class="manual-spread" aria-label="Offerly 使用说明"><div class="book-tabs"><button data-route="intro" class="${bookPage === "intro" ? "active" : ""}">序言</button>${bookSteps.map((s) => `<button data-route="${s.id}" class="${bookPage === s.id ? "active" : ""}">${s.title}</button>`).join("")}</div><section class="paper-left">${storyLeft(bookPage)}<span class="paper-page">OFFERLY · 使用说明</span></section><section class="paper-right"><div class="paper-running-head"><span>${bookPage === "intro" ? "一份关于求职的使用说明" : routes[bookPage][0]}</span><span class="paper-save">填写后自动保存</span></div><div class="paper-scroll" data-page="${bookPage}">${bookPage === "intro" ? introRight() : views[bookPage]()}</div><footer class="paper-footer"><button class="page-turn previous" data-route="${index > 0 ? order[index - 1] : "cover"}"><span aria-hidden="true">←</span> ${index > 0 ? "上一页" : "合上书"}</button><span class="page-number">${String(index + 1).padStart(2, "0")} / 05</span>${index < order.length - 1 ? `<button class="page-turn next" data-route="${order[index + 1]}">下一页 <span aria-hidden="true">→</span></button>` : `<button class="page-turn next" data-action="assistant-launch">准备好了 <span aria-hidden="true">↗</span></button>`}</footer></section></article>`}<nav class="screen-reader-only" aria-label="使用说明目录">${contents}</nav></main><aside class="robot-companion" ${assistantOpen ? "inert" : ""}><button class="robot-start" data-action="assistant-launch"><span class="robot-start-dot"></span>${taskRunning() ? "查看工作进度" : "开始投递"}<span aria-hidden="true">↗</span></button>${robotArt()}<span class="robot-caption">你的求职搭子 · 随时待命</span></aside>${assistantView()}</div>`;
  renderTaskUI();
  if ($(".paper-scroll"))
    $(".paper-scroll").scrollTop = pageScroll.get(bookPage) || 0;
  if ($(".assistant-body")) $(".assistant-body").scrollTop = chatScroll;
}

const launchAttachments = () =>
  state.attachments.filter(
    (a) => state.resumePlan === "upload" || a.type !== "resume",
  );
const targetCount = () =>
  state.jobSource === "search"
    ? Number(state.applicationCount)
    : targetEntries().length;
const targetLabel = (c) =>
  [c?.name, c?.job?.title || c?.targetJob].filter(Boolean).join(" · ");
const modelReady = () =>
  validModel() &&
  (!!apiKey.trim() ||
    (storedModel &&
      state.model.baseUrl.replace(/\/$/, "") ===
        savedModelUrl.replace(/\/$/, "")));
const pluginUrls = {
  edge: "https://microsoftedge.microsoft.com/addons/search/%E5%A1%94%E5%A1%94%E7%BD%91%E7%94%B3%E7%A5%9E%E5%99%A8",
  chrome:
    "https://chromewebstore.google.com/search/%E5%A1%94%E5%A1%94%E7%BD%91%E7%94%B3%E7%A5%9E%E5%99%A8%20-%20AI%E4%B8%80%E9%94%AE%E6%B1%82%E8%81%8C%E5%A1%AB%E7%AE%80%E5%8E%86?hl=zh-CN&utm_source=ext_sidebar",
};
const browserName = () =>
  state.selectedBrowser === "edge" ? "Microsoft Edge" : "Google Chrome";
function targetEntries() {
  return [
    ...new Set(
      state.targetLines
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}
function targetEntry(value) {
  const looksLikeURL =
    /^[a-z][a-z\d+.-]*:/i.test(value) ||
    /^(?:[a-z\d-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)/i.test(value);
  return looksLikeURL
    ? {
        url: cleanURL(/^https?:\/\//i.test(value) ? value : "https://" + value),
      }
    : { company: value };
}
function invalidTargetURLs() {
  return state.jobSource === "search"
    ? []
    : targetEntries().filter((value) => {
        const t = targetEntry(value);
        return "url" in t && !t.url;
      });
}
function taskDraft() {
  return {
    version: 1,
    source: state.jobSource === "search" ? "search" : "companies",
    count: targetCount(),
    targets:
      state.jobSource === "search" ? [] : targetEntries().map(targetEntry),
    requirements: { ...state.preferences },
    candidateSource:
      state.resumePlan === "upload" ? "resume_attachment" : "profile",
    resumeAttachmentId:
      state.resumePlan === "upload"
        ? state.attachments.find((a) => a.type === "resume")?.id || null
        : null,
    resume: {
      plan: state.resumePlan,
      template: state.resumePlan === "template" ? state.resumeTemplate : null,
      sections:
        state.resumePlan === "template" ? [...state.resumeSections] : [],
      onePage: state.resumeOnePage,
    },
    browser: state.selectedBrowser,
  };
}
function taskTargetReady() {
  return state.jobSource === "search"
    ? !!state.preferences.keywords.trim() &&
        Number.isInteger(Number(state.applicationCount)) &&
        Number(state.applicationCount) > 0 &&
        Number(state.applicationCount) <= 200
    : targetEntries().length > 0 &&
        targetEntries().length <= 200 &&
        !invalidTargetURLs().length;
}
function browserConnectionStatus() {
  const browser = state.selectedBrowser,
    c = browserConnections[browser],
    test = browserTests[browser];
  if (!serviceOnline)
    return {
      state: "unknown",
      label: "状态未知",
      detail: "正在连接本地服务，暂时无法确认浏览器状态。",
    };
  if (browserTesting.has(browser))
    return {
      state: "testing",
      label: "正在测试",
      detail: "正在验证 Agent 能否控制投递标签页，请稍候。不调用 AI 模型。",
    };
  if (c?.connected) {
    if (test?.ok === false)
      return { state: "failed", label: "测试未通过", detail: test.message };
    return {
      state: "connected",
      label: "已连接",
      detail:
        c.agentBrowserReady || test?.ok
          ? "浏览器已连接，Agent 控制验证已通过。可以继续下一步。"
          : "浏览器已连接，可以继续下一步。",
    };
  }
  if (c?.state === "awaiting_helper")
    return {
      state: "waiting",
      label: "等待连接助手",
      detail: "在浏览器提示中打开 Offerly 连接助手。首次使用请先下载运行助手，再重新点击连接。",
    };
  if (
    c?.state === "awaiting_permission" ||
    browserRequests.has(browser + ":connect")
  )
    return {
      state: "waiting",
      label: "等待授权",
      detail: "请切换到浏览器，在连接弹窗中点击允许。请求最多等待 5 分钟。",
    };
  if (c?.state === "disconnected")
    return {
      state: "disconnected",
      label: "连接已断开",
      detail: "浏览器连接已断开。请保持浏览器打开，再点击“连接我的浏览器”。",
    };
  if (["permission_required", "connection_error"].includes(c?.state))
    return {
      state: "failed",
      label: "连接失败",
      detail: c.error || "请重新连接浏览器，并在浏览器弹窗中点击允许。",
    };
  return {
    state: "offline",
    label: "未连接",
    detail:
      c?.installed === false
        ? "未找到所选浏览器，请安装或选择另一种浏览器。"
        : c?.error || "先打开连接设置并允许远程调试，再点击“连接我的浏览器”。",
  };
}
function browserConnectionText() {
  return browserConnectionStatus().detail;
}
function browserConnectionView() {
  const address = `${state.selectedBrowser}://inspect/#remote-debugging`;
  return `<section class="card padded browser-connect"><h2>连接你的 ${browserName()}</h2>${browserHelperRequired ? `<div class="connector-install"><span class="connector-step">01</span><div><b>首次使用，下载并运行连接助手</b><p>Windows 10 / 11，无需管理员权限。以后直接点击下方连接即可。</p><a class="btn" href="/downloads/OfferlyConnect.exe" download>下载 Offerly 连接助手 <span aria-hidden="true">↓</span></a></div></div><div class="connector-install"><span class="connector-step">02</span><div><b>允许浏览器连接</b>` : ""}<p>复制 <code>${address}</code> 到该浏览器地址栏打开，勾选 <b>Allow remote debugging for this browser instance</b>（允许对此浏览器实例进行远程调试）。</p>${button("复制授权地址", "copy-browser-address", "text-btn")}${browserHelperRequired ? "</div></div>" : ""}<div class="form-actions">${button("连接我的浏览器", "connect-browser", "primary")}${button("断开连接", "disconnect-browser")}</div><div class="connection-title" id="browser-connection-result" role="status" aria-live="polite"><span class="connection-dot"></span><strong id="browser-connection-label"></strong></div><p id="browser-connection-state" class="connector-status-detail" role="status"></p>${!browserHelperRequired ? `<div class="connector-alternative"><span>想使用连接助手？</span>${button("下载并连接助手", "use-browser-helper", "text-btn")}</div>` : /(?:^|[?&])connection=helper(?:&|$)/.test(location.search) ? `<a class="text-btn" href="/#browser">返回本机直接连接</a>` : ""}</section>`;
}
function showHelperConnection(result) {
  helperLaunchUrl = result.launchUrl;
  modal("连接你的浏览器", `<div class="connector-modal"><div class="connector-symbol" aria-hidden="true">O.</div><h3 id="helper-connection-title">正在唤起连接助手</h3><p id="helper-connection-status">请在浏览器提示中选择“打开 Offerly 连接助手”，并在浏览器连接授权中点击允许。</p><a id="helper-launch" class="btn primary" href="${esc(helperLaunchUrl)}">打开连接助手</a><details><summary>没有打开？首次使用看这里</summary><p>先下载并运行助手，安装完成后点击上方“打开连接助手”。</p><a class="btn" href="/downloads/OfferlyConnect.exe" download>下载连接助手 ↓</a><p>也可以把连接链接粘贴到助手窗口。</p>${button("复制连接链接", "copy-helper-link")}</details><small>连接链接 10 分钟内有效，仅可使用一次。连接后可以最小化助手。</small></div>`);
  window.location.href = helperLaunchUrl;
}
function updateBrowserConnectionUI() {
  const browser = state.selectedBrowser,
    c = browserConnections[browser],
    status = browserConnectionStatus(),
    test = browserTests[browser];
  const badge = $("#global-browser-status");
  if (badge) {
    badge.dataset.state = status.state;
    badge.querySelector("[data-browser-status-label]").textContent =
      (browser === "edge" ? "Edge" : "Chrome") + " · " + status.label;
    badge.title = status.detail + " 点击查看连接状态。";
    badge.setAttribute(
      "aria-label",
      (browser === "edge" ? "Edge" : "Chrome") +
        " 浏览器" +
        status.label +
        "，点击查看连接状态",
    );
  }
  const panel = $("#browser-connection-result");
  if (panel) panel.dataset.state = status.state;
  const label = $("#browser-connection-label");
  if (
    label &&
    label.textContent !==
      (browser === "edge" ? "Edge" : "Chrome") + " · " + status.label
  )
    label.textContent =
      (browser === "edge" ? "Edge" : "Chrome") + " · " + status.label;
  const detail = $("#browser-connection-state");
  if (detail && detail.textContent !== status.detail)
    detail.textContent = status.detail;
  const helperTitle = $("#helper-connection-title");
  if (helperTitle) {
    helperTitle.textContent = c?.connected ? "已连接，准备好继续了" : status.label;
    $("#helper-connection-status").textContent = status.detail;
    const launch = $("#helper-launch");
    if (launch) launch.hidden = !!c?.connected;
  }
  const time = $("#browser-test-time");
  if (time)
    time.textContent = test
      ? "上次测试：" +
        new Date(test.at).toLocaleTimeString("zh-CN", {
          hour12: false,
          timeZone: "Asia/Shanghai",
        }) +
        " · " +
        (test.ok ? "通过" : "未通过")
      : c?.connected && c.agentBrowserReady
        ? "Agent 控制已验证，连接状态持续更新。"
        : "尚未手动测试";
  const running = taskRunning(),
    testing = browserTesting.has(browser),
    waiting = c?.state === "awaiting_permission";
  const check = $("[data-action=check-browser]");
  if (check) {
    check.disabled = !serviceOnline || !c?.connected || testing || running;
    check.textContent = testing ? "测试中…" : "测试连接";
    check.title = running
      ? "任务执行期间无需重复测试连接"
      : !c?.connected
        ? "先连接浏览器，再测试 Agent 控制是否可用"
        : "验证实际浏览器控制，不调用模型";
  }
  const connect = $("[data-action=connect-browser]");
  if (connect) {
    connect.disabled =
      !serviceOnline ||
      !!c?.connected ||
      waiting ||
      testing ||
      running ||
      browserRequests.has(browser + ":connect");
    connect.textContent = waiting ? "等待浏览器授权…" : c?.state === "awaiting_helper" ? "重新唤起助手" : "连接我的浏览器";
  }
  const disconnect = $("[data-action=disconnect-browser]");
  if (disconnect)
    disconnect.disabled =
      !serviceOnline || (!c?.connected && !waiting && c?.state !== "awaiting_helper") || testing || running;
  const setup = $("[data-action=browser-setup]");
  if (setup) setup.disabled = !serviceOnline || testing || running;
  const tata = $("[data-action=test-tata-program]");
  if (tata) {
    tata.disabled = !serviceOnline || !c?.connected || running || tataChecking;
    tata.textContent = tataChecking ? "正在试填…" : "测试塔塔";
    const reason = !serviceOnline
      ? "本地服务连接中，请稍候。"
      : running
        ? "Agent 正在执行，任务结束后可测试。"
        : tataChecking
          ? "正在试填，请保持浏览器打开。"
          : !c?.connected
            ? `${browserName()} 尚未连接，请先在第一步点击“连接”并允许授权。`
            : "浏览器已连接，可以测试塔塔。";
    tata.title = reason;
    const readiness = $("#tata-test-readiness");
    if (readiness) readiness.textContent = reason;
    const reconnect = $("[data-tata-reconnect]");
    if (reconnect) reconnect.hidden = !!c?.connected || running || tataChecking;
  }
}
async function browserAction(action, page) {
  const browser = state.selectedBrowser,
    key = browser + ":" + action;
  if (browserRequests.has(key)) return;
  if (action === "check") {
    if (taskRunning()) return toast("Agent 正在执行，无需重复测试连接。");
    if (!serviceOnline || !browserConnections[browser]?.connected)
      return toast("请先连接浏览器，再测试连接。");
    browserTesting.add(browser);
  }
  browserRequests.add(key);
  updateBrowserConnectionUI();
  try {
    const endpoint = action === "connect" && browserHelperRequired
      ? "/api/browser-helper/pair"
      : "/api/browser/" + action;
    const result = await api(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser, ...(page ? { page } : {}) }),
    });
    browserConnections[browser] = result;
    if (action === "connect" && result.launchUrl) showHelperConnection(result);
    if (action === "check") {
      const ok = !!(result.connected && result.agentBrowserReady);
      browserTests[browser] = {
        ok,
        at: Date.now(),
        message: ok
          ? "连接测试通过，Agent 可以正常控制投递标签页。"
          : "浏览器控制验证未通过，请重新连接后重试。",
      };
      if (state.selectedBrowser === browser)
        toast(ok ? "连接测试通过，可以继续。" : "连接测试未通过，请重新连接。");
    } else if (action === "disconnect" || action === "connect")
      delete browserTests[browser];
    if (action === "open") {
      if (page === "setup" && !result.navigationVerified)
        modal(
          "首次连接授权",
          `<p>已请求浏览器打开设置。若仍停在新标签页，请将下面的网址粘贴到所选浏览器地址栏：</p><input readonly value="${state.selectedBrowser === "edge" ? "edge" : "chrome"}://inspect/#remote-debugging"><p>勾选 Allow remote debugging for this browser instance 后，返回连接浏览器。</p>`,
        );
      else
        toast(
          page === "plugin"
            ? "已打开插件安装页。"
            : "已在当前浏览器打开连接设置。",
        );
    } else if (
      action === "connect" &&
      state.selectedBrowser === browser &&
      !result.connected
    )
      toast(browserConnectionText());
  } catch (error) {
    if (action === "check" && !error.message.includes("任务运行中"))
      browserTests[browser] = {
        ok: false,
        at: Date.now(),
        message: "连接测试未通过：" + error.message,
      };
    toast(error.message);
  } finally {
    browserTesting.delete(browser);
    browserRequests.delete(key);
    updateBrowserConnectionUI();
  }
}
async function testTataProgram() {
  if (tataChecking || taskRunning()) return;
  const browser = state.selectedBrowser;
  if (!browserConnections[browser]?.connected) return toast("请先连接浏览器。");
  tataChecking = true;
  state.pluginReadyFor = "";
  save();
  updateBrowserConnectionUI();
  const message = "正在本地表单试填，请保持浏览器打开，等待结果。";
  if ($("#tata-program-result"))
    $("#tata-program-result").textContent = message;
  try {
    const result = await api("/api/plugin/tata/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser }),
    });
    state.pluginReadyFor = result.ok ? browser : "";
    state.pluginTestVersion = 1;
    save();
    tataResults[browser] = result.ok ? "测试成功" : "测试失败，请联系管理员。";
  } catch (error) {
    tataResults[browser] = "检查未通过：" + error.message;
  } finally {
    tataChecking = false;
    updateBrowserConnectionUI();
    if ($("#tata-program-result"))
      $("#tata-program-result").textContent =
        tataResults[state.selectedBrowser] || "尚未检查所选浏览器。";
  }
}
function browserView() {
  return `${heading("选择<em>投递浏览器。</em>", "连接你正在使用的浏览器，保留插件、账号和个人资料。")}<section class="card padded"><div class="plan-grid">${[
    ["edge", "Microsoft Edge", "推荐使用"],
    ["chrome", "Google Chrome", "也可使用 Chrome"],
  ]
    .map(
      ([id, name, note]) =>
        `<label class="plan-card browser-card ${state.selectedBrowser === id ? "selected" : ""}"><input type="radio" name="selected-browser" value="${id}" ${state.selectedBrowser === id ? "checked" : ""}><strong>${name}</strong><p>${note}</p></label>`,
    )
    .join("")}</div></section>${browserConnectionView()}`;
}
function pluginView() {
  return `${heading("准备<em>塔塔网申。</em>", `在刚连接的 ${browserName()} 中完成以下步骤。`)}<section class="card padded narrow"><ol class="install-steps"><li><a id="plugin-install" class="text-btn" href="${esc(pluginUrls[state.selectedBrowser])}" target="_blank" rel="noopener noreferrer">点击这里安装插件</a><p class="muted">已安装可跳过，不需要重新安装。</p></li><li>打开已有的塔塔插件并登录账号。</li><li>填写、核对插件里的个人资料；已有资料无需重填。</li></ol><div class="form-actions">${button("测试塔塔", "test-tata-program")}<button type="button" class="text-btn" data-route="browser" data-tata-reconnect>连接浏览器</button></div><p class="muted compact-note" id="tata-test-readiness" role="status"></p><p class="muted compact-note" id="tata-program-result" role="status">${esc(tataResults[state.selectedBrowser] || "将在本地简易网申表单试填，检查姓名和联系方式是否填写成功。")}</p></section>`;
}
function materialsView() {
  return `${heading("准备<em>资料。</em>", "附件会按企业要求上传，普通网申表单由塔塔填写。")}<section class="card padded"><div class="attachment-grid materials-grid">${["resume", "photo", "transcript", "other"].map((id) => attachmentCard({ ...attachmentTypes.find((t) => t.id === id), name: { resume: "附件简历", photo: "个人照片", transcript: "成绩单", other: "其他附件" }[id], required: id === "resume" && state.resumePlan === "upload" })).join("")}</div><p class="muted compact-note">每份附件最多20 MB。添加其他附件前，请先填写材料名称或用途。</p></section><section class="card padded"><label class="target-company-option"><input type="checkbox" id="tailored-resume" ${state.resumePlan === "template" ? "checked" : ""}>需要 Agent 根据岗位 JD 生成专属简历</label><p class="muted compact-note">勾选后，使用下方资料生成岗位专属 PDF；不勾选则使用上传的附件简历。</p>${state.resumePlan === "template" ? `${templateCards()}${resumeModuleView()}` : ""}</section>`;
}

function preparationChecks() {
  const contentReady = state.resumeSections.some((id) => {
    if (id === "summary") return !!state.profile.extra?.evaluation?.trim();
    if (id === "custom")
      return state.profile.customModules?.some(
        (x) => x.name?.trim() && x.content?.trim(),
      );
    return state.profile[id]?.some((x) =>
      Object.values(x).some((v) => typeof v === "string" && v.trim()),
    );
  });
  const materialsReady =
    state.resumePlan === "upload"
      ? state.attachments.some((a) => a.type === "resume" && files.has(a.id))
      : !!state.profile.basic?.name?.trim() &&
        !!state.profile.contact?.phone?.trim() &&
        !!state.profile.contact?.email?.trim() &&
        contentReady;
  return [
    {
      id: "browser",
      label: "第一步：连接浏览器",
      ok: !!browserConnections[state.selectedBrowser]?.connected,
      reason: "请先连接你的浏览器。",
    },
    {
      id: "plugin",
      label: "第二步：准备塔塔",
      ok: state.pluginReadyFor === state.selectedBrowser,
      reason: "请先完成一次塔塔试填测试。",
    },
    {
      id: "materials",
      label: "第三步：准备资料",
      ok: materialsReady && launchAttachments().every((a) => files.has(a.id)),
      reason:
        state.resumePlan === "upload"
          ? "请上传一份可用的附件简历。"
          : "请补充简历抬头和所选模块的资料，并检查附件。",
    },
    {
      id: "phone",
      label: "第四步：连接手机",
      ok: !!phoneStatus.connected,
      reason: "请用手机打开连接链接，保持在线。",
    },
  ];
}
function launchAssistant() {
  assistantOpen = true;
  assistantError = "";
  assistantStage = taskRunning()
    ? "progress"
    : preparationChecks().some((x) => !x.ok) ||
        !modelReady() ||
        !serviceOnline ||
        !agentAvailable ||
        !autonomousTaskAvailable
      ? "gate"
      : "preferences";
  render();
  $(".assistant-panel .icon-btn")?.focus();
}
function assistantMessage(text) {
  return `<div class="assistant-message"><span class="assistant-avatar" aria-hidden="true">o.</span><p>${text}</p></div>`;
}
function assistantView() {
  let body = "";
  if (assistantStage === "gate") {
    const missing = preparationChecks().filter((x) => !x.ok);
    body =
      assistantMessage(
        "出发之前，还有一点准备工作。完成下面这些，我们就可以开始聊岗位了。",
      ) +
      `<div class="assistant-checks">${missing.map((x) => `<button data-route="${x.id}"><span>${x.label}</span><small>${x.reason}</small><b aria-hidden="true">↗</b></button>`).join("")}${!modelReady() ? `<button data-action="model-settings"><span>AI 服务尚未配置</span><small>请在服务设置中保存 API 配置。</small><b aria-hidden="true">↗</b></button>` : ""}${!serviceOnline || !agentAvailable || !autonomousTaskAvailable ? '<p class="assistant-alert" role="status">投递服务暂未就绪，正在等待连接。</p>' : ""}</div><div class="assistant-bottom-actions">${button("我准备好了，再检查一次", "assistant-launch", "primary")}</div>`;
  } else if (assistantStage === "preferences") {
    body =
      assistantMessage(
        "你好，我是你的求职搭子。你想找什么样的岗位？这次希望投递多少份？",
      ) +
      preferencesView() +
      `<div class="assistant-bottom-actions">${button("填好了，确认投递细节", "assistant-confirm", "primary", "arrow")}</div>`;
  } else if (assistantStage === "confirm") {
    const p = state.preferences;
    body =
      assistantMessage("收到。出发前，我们再一起确认一下。") +
      `<div class="conversation-recap"><div><span>岗位条件</span><strong>${esc(p.keywords)} · ${esc(p.cities || "城市不限")}</strong><p>${esc([p.category, p.salary && "薪资 " + p.salary, p.experience && "经验 " + p.experience, p.education && "学历 " + p.education, p.industry && "行业 " + p.industry, p.companySize && "规模 " + p.companySize, p.published && "发布 " + p.published, p.year && "毕业年份 " + p.year].filter(Boolean).join(" · "))}</p>${p.notes ? `<p>${esc(p.notes)}</p>` : ""}<small>本轮希望投递 ${esc(state.applicationCount)} 份</small></div><div><span>企业黑名单</span><p>${esc(p.companyBlacklist || "未设置")}</p></div><div><span>简历方案</span><p>${esc(state.resumePlan === "template" ? "结合每个岗位的 JD 和你的资料，生成专属 PDF 简历。" : "使用附件简历：" + (state.attachments.find((a) => a.type === "resume")?.name || "尚未上传"))}</p><button class="text-btn" data-route="materials">调整资料与简历方案 ↗</button></div></div>` +
      assistantMessage("这次由我帮你找岗位，还是你已经有想投的企业了？") +
      `<div class="conversation-choices"><button data-action="assistant-search" ${startingTask ? "disabled" : ""}>OK，你可以帮我找满足条件的岗位并投递了<span aria-hidden="true">↗</span></button><button data-action="assistant-companies">不用你帮我找，我告诉你我要投哪些企业你去投<span aria-hidden="true">↗</span></button></div><button class="text-btn" data-action="assistant-edit">← 修改岗位条件或黑名单</button>`;
  } else if (assistantStage === "companies") {
    body =
      assistantMessage(
        "告诉我企业名称或网址，我会在企业发布的职位中，找到最匹配你岗位条件或个人资料的岗位。你也可以直接告诉我岗位。",
      ) +
      `<div class="assistant-form">${field("想投的企业或岗位（一行一个）", "targetLines", state.targetLines, "textarea", null, "例如：汇川技术，AI 应用开发方向；或直接粘贴岗位链接。")}<p class="muted">每行作为一个投递目标，最多 200 个。</p></div><div class="assistant-bottom-actions">${button("告诉你了，开始投递", "assistant-submit-companies", "primary", "arrow")}<button class="text-btn" data-action="assistant-confirm">← 返回确认</button></div>`;
  }
  return `<div class="assistant-scrim" data-action="assistant-close" ${assistantOpen ? "" : "hidden"}></div><section class="assistant-panel" role="dialog" aria-modal="true" aria-labelledby="assistant-title" ${assistantOpen ? "" : "hidden"}><header class="assistant-header"><div><span class="assistant-online-dot"></span><h2 id="assistant-title">和你的求职搭子聊聊</h2><small>${assistantStage === "progress" ? "本轮投递 · 工作进度" : "准备妥当，再一起出发"}</small></div><button class="icon-btn" data-action="assistant-close" aria-label="收起对话">${icon("close")}</button></header><div class="assistant-body"><div id="assistant-conversation" ${assistantStage === "progress" ? "hidden" : ""}>${body}</div><p id="assistant-error" class="assistant-alert" role="alert" ${assistantError ? "" : "hidden"}>${esc(assistantError)}</p><section id="agent-execution" ${assistantStage === "progress" ? "" : "hidden"}>${assistantMessage("我会在这里更新进度。需要你登录或检查申请时，会暂停等你。")}<section id="task-status" hidden></section><div id="queue-results" class="queue-results"></div><button class="text-btn new-round" data-action="assistant-new-round" ${taskRunning() ? "hidden" : ""}>开始新一轮投递 ↗</button></section></div><footer class="assistant-footer"><span class="assistant-online-dot"></span>最终申请由你检查并确认提交</footer></section>`;
}
function confirmPreferences() {
  if (!state.preferences.keywords.trim())
    return assistantProblem("请先告诉我你想找的岗位方向。");
  const n = Number(state.applicationCount);
  if (!Number.isInteger(n) || n < 1 || n > 200)
    return assistantProblem("投递数量请填写 1 至 200 的整数。");
  assistantError = "";
  assistantStage = "confirm";
  save();
  render();
  $(".assistant-body").scrollTop = 0;
}
function assistantProblem(message) {
  assistantError = message;
  const box = $("#assistant-error");
  if (box) {
    box.hidden = false;
    box.textContent = message;
    box.scrollIntoView({ block: "nearest" });
  }
}
async function submitAssistant(source) {
  if (startingTask || taskRunning()) return;
  state.jobSource = source;
  if (!taskTargetReady())
    return assistantProblem(
      source === "search"
        ? "请检查岗位方向和投递数量。"
        : "请填写有效的企业名称或链接，一行一个，最多 200 条。",
    );
  if (
    preparationChecks().some((x) => !x.ok) ||
    !modelReady() ||
    !serviceOnline ||
    !agentAvailable ||
    !autonomousTaskAvailable
  )
    return launchAssistant();
  assistantError = "";
  save();
  await startTask();
}

function field(label, path, value, type = "text", options = null, hint = "") {
  const attr = `id="field-${esc(path)}" data-bind="${esc(path)}"`;
  let control;
  if (options) {
    const choices =
      options.includes(value) || !value ? options : [value, ...options];
    control = `<select ${attr}><option value="">请选择 / 暂不填写</option>${choices.map((v) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  } else if (type === "textarea")
    control = `<textarea ${attr} rows="3">${esc(value)}</textarea>`;
  else
    control = `<input ${attr} type="${type}" value="${esc(value)}" autocomplete="off">`;
  return `<div class="field ${type === "textarea" ? "wide" : ""}"><label for="field-${esc(path)}">${esc(label)}</label>${control}${hint ? `<small>${esc(hint)}</small>` : ""}</div>`;
}

function preferencesView() {
  const p = state.preferences;
  return `<section class="card padded"><div class="form-grid">${field("企业黑名单（一行一家）", "preferences.companyBlacklist", p.companyBlacklist || "", "textarea", null, "不希望投递的企业名称")}${field("岗位方向", "preferences.keywords", p.keywords, "text", null, "例如：AI 应用开发、产品经理")}${field("工作城市", "preferences.cities", p.cities, "text", null, "多个城市用逗号分隔")}${field("求职类型", "preferences.category", p.category, "select", ["校园招聘", "实习招聘", "社会招聘", "不限"])}${field("本轮希望投递多少份", "applicationCount", state.applicationCount, "number", null, "1–200")}</div><details class="task-more"><summary>更多岗位偏好（选填）</summary><div class="form-grid">${field("薪资待遇", "preferences.salary", p.salary, "select", ["不限", "5K 以下", "5–10K", "10–15K", "15–20K", "20–30K", "30K 以上", "面议"])}${field("工作经验", "preferences.experience", p.experience, "select", ["不限", "应届毕业生", "在校生 / 实习生", "1 年以内", "1–3 年", "3–5 年", "5 年以上"])}${field("学历要求", "preferences.education", p.education, "select", ["不限", "高中 / 中专", "大专", "本科", "硕士", "博士"])}${field("公司行业", "preferences.industry", p.industry, "text", null, "例如：互联网、制造业")}${field("公司规模", "preferences.companySize", p.companySize, "select", ["不限", "20 人以下", "20–99 人", "100–499 人", "500–999 人", "1000–9999 人", "10000 人以上"])}${field("岗位发布时间", "preferences.published", p.published, "select", ["不限", "近 24 小时", "近 3 天", "近 7 天", "近 30 天"])}${field("毕业年份（选填）", "preferences.year", p.year, "text", null, "例如：2027")}${field("其他要求（选填）", "preferences.notes", p.notes, "textarea", null, "补充你希望 Agent 参考的要求")}</div></details></section>`;
}

function attachmentCard(t) {
  const items = state.attachments.filter((a) => a.type === t.id);
  return `<div class="attachment-card ${t.required ? "resume-card" : ""}"><div class="attachment-title"><h3>${t.name}${t.required ? '<span class="required-dot">*</span>' : ""}</h3><label class="text-btn" for="attachment-${t.id}">${items.length && !t.multiple ? "重新选择" : "+ 添加"}</label><input class="file-input" type="file" id="attachment-${t.id}" data-attachment="${t.id}" accept="${t.accept}" ${t.id === "other" ? "disabled" : ""} ${t.multiple ? "multiple" : ""}></div><small title="${esc(t.description)}">${t.accept.replaceAll(".", "").toUpperCase().replaceAll(",", " / ")}</small>${t.id === "other" ? '<label for="other-material-name">附件是什么</label><input id="other-material-name" maxlength="120" placeholder="例如：获奖证书、作品集、资格证明">' : ""}${items.length ? items.map((a) => `<div class="attachment-item"><div><b title="${esc(a.name)}">${esc(a.label ? a.label + " · " + a.name : a.name)}</b><small class="${files.has(a.id) ? "success" : "warning"}">${files.has(a.id) ? (a.stored ? "已保存 · " : "已选择 · ") + sizeText(a.size) : "需重新选择文件"}</small></div><button class="icon-btn" data-action="remove-attachment" data-id="${esc(a.id)}" aria-label="移除${esc(a.name)}">${icon("close")}</button></div>`).join("") : `<p class="attachment-empty">${t.required ? "请添加一份简历（PDF 或图片）" : "尚未添加"}</p>`}</div>`;
}
const sizeText = (n) =>
  n < 1024 * 1024
    ? `${Math.ceil(n / 1024)} KB`
    : `${(n / 1024 / 1024).toFixed(1)} MB`;

function modelView() {
  const m = state.model;
  return `<section class="card padded"><div class="field"><label for="api-key">DeepSeek API Key</label><input type="password" id="api-key" value="${esc(apiKey)}" placeholder="输入 API Key" autocomplete="off"><small>${storedModel ? "已保存密钥，可留空使用。" : "密钥不会保存在浏览器草稿中。"}</small></div><details class="model-advanced"><summary>高级设置</summary><div class="form-grid">${field("模型名称", "model.model", m.model)}${field("API 地址", "model.baseUrl", m.baseUrl, "url")}</div>${button("检查格式", "check-model")}</details></section>`;
}

function wechatView() {
  return `<section class="card padded narrow"><h2>设置弹窗提醒</h2><p>完成以下操作，以便 Agent 在需要协助时给您弹窗：</p><ol><li>微信搜索关注“虾推啥”公众号。</li><li>关注成功后公众号会给您发一个 token 码（一串英文和数字的组合码）。</li><li>将 token 码粘贴到此处。</li></ol><input id="wechat-token" aria-label="公众号提醒凭据" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="200" value="${esc(wechatToken)}" placeholder="${phoneStatus.wechat?.bound ? "已绑定，留空继续使用；输入新码可更换" : "粘贴 token 码"}"><div class="form-actions"><button type="button" class="btn primary" data-action="test-wechat" ${wechatBusy ? "disabled" : ""}>${wechatBusy ? "正在测试…" : "弹窗测试"}</button></div><p id="wechat-status" class="muted" role="status">${esc(wechatMessage || phoneStatus.wechat?.error || (phoneStatus.wechat?.enabled ? "提醒已开启。" : ""))}</p></section>`;
}
async function bindWechat() {
  const value = wechatToken.trim();
  if (!value || wechatBusy) return;
  try {
    const result = await api("/api/phone/wechat/bind", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: value }),
    });
    phoneStatus.wechat = result;
    if (wechatToken.trim() === value) wechatToken = "";
    wechatMessage = "已绑定并保存，下次无需重复填写。";
    const status = $("#wechat-status");
    if (status) status.textContent = wechatMessage;
  } catch (e) {
    toast(e.message);
  }
}
async function testWechat() {
  if (wechatBusy) return;
  if (!wechatToken.trim() && !phoneStatus.wechat?.bound)
    return toast("请粘贴公众号提供的 token 码。");
  wechatBusy = true;
  wechatMessage = "正在发送测试弹窗…";
  render();
  try {
    const result = await api("/api/phone/wechat/setup-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: wechatToken.trim() }),
    });
    phoneStatus.wechat = result;
    wechatToken = "";
    wechatMessage = result.accepted
      ? "请稍等几分钟检查手机是否收到微信虾推啥公众号的弹窗提醒"
      : result.error || "测试未发送，请稍后重试。";
  } catch (e) {
    wechatMessage = e.message;
  } finally {
    wechatBusy = false;
    render();
  }
}
function phoneView() {
  return `${heading("连接<em>你的手机。</em>", "用手机打开链接，遇到登录时协助 Agent。")}<section class="card padded narrow"><span class="pill" id="phone-state">${esc(phoneStatus.error || (phoneStatus.connected ? "手机已连接" : phoneStatus.paired ? "已配对，手机暂离线" : "尚未连接"))}</span><div class="row">${button("生成连接链接", "pair-phone", "primary", "phone")}${button("断开手机", "revoke-phone")}</div><div id="phone-link">${pairLink ? `<p><a href="${esc(pairLink)}" target="_blank" rel="noopener">${esc(pairLink)}</a></p>${button("复制链接", "copy-phone")}` : ""}</div><p class="muted phone-hint">手机和电脑需连接同一 Wi-Fi。链接 10 分钟内有效。</p></section>${wechatView()}`;
}
async function pairPhone() {
  try {
    const data = await api("/api/phone/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    pairLink = data.url;
    phoneStatus = data;
    render();
    toast("请用手机打开配对链接。");
  } catch (e) {
    toast(e.message);
  }
}

async function showApplicationHistory() {
  try {
    const data = await api("/api/history");
    const names = {
      user_confirmed: "用户确认已投递",
      submitted: "已提交（有回执）",
      abandoned: "已放弃",
      incomplete: "未完成",
      selected: "处理中",
      pending_review: "待核实",
      submission_unknown: "待核实",
    };
    const groups = Object.groupBy
      ? Object.groupBy(data.applications, (r) => r.company)
      : data.applications.reduce(
          (g, r) => ((g[r.company] ??= []).push(r), g),
          {},
        );
    modal(
      "投递记录",
      Object.entries(groups)
        .map(
          ([company, rows]) =>
            `<section><h3>${esc(company)}</h3>${rows.map((r) => `<p><a href="${esc(r.job_url)}" target="_blank" rel="noopener noreferrer">${esc(r.job_title)}</a><br><small>${esc(names[r.state] || r.state)} · ${esc(new Date(r.updated_at).toLocaleString())}</small></p>`).join("")}</section>`,
        )
        .join("") || "<p>暂无投递记录。</p>",
    );
  } catch (e) {
    toast(e.message);
  }
}
function modal(title, body) {
  delete $("#modal-root").dataset.module;
  delete $("#modal-root").dataset.customIndex;
  $("#modal-root").innerHTML =
    `<div class="modal-shade"><section class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="section-header"><h2>${esc(title)}</h2><button class="icon-btn" data-action="close-modal" aria-label="关闭">${icon("close")}</button></div><div class="padded">${body}</div></section></div>`;
  $(".modal button")?.focus();
}

function validModel() {
  try {
    return (
      ["https:", "http:"].includes(new URL(state.model.baseUrl).protocol) &&
      !!state.model.model.trim()
    );
  } catch {
    return false;
  }
}

const taskLabels = {
  BUDGET_EXHAUSTED: "达到本轮模型请求上限",
  USER_CONFIRMED: "用户确认已提交",
  QUEUED: "排队中",
  STARTING: "启动中",
  RUNNING: "执行中",
  WAITING_HUMAN: "等待你协助",
  PAUSED: "已暂停",
  SKIPPED: "已放弃",
  CANCELLED: "未执行",
  STOPPED: "已停止",
  FAILED: "执行失败",
  INCOMPLETE: "未完成",
  FINISHED: "本次处理结束",
};
function taskRunning() {
  return activeTask?.active === true;
}
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "X-Offerly-Token": serviceToken, ...options.headers },
  });
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error("本地执行服务未启动，请重新运行 start-offerly.cmd。");
  }
  if (!res.ok) throw new Error(data.error || "操作失败，请稍后重试。");
  return data;
}
async function syncTask() {
  clearTimeout(pollTimer);
  try {
    if (!serviceToken) {
      const res = await fetch("/api/bootstrap");
      const info = await res.json();
      if (!info.serviceReady && !info.execution) throw new Error("服务未就绪");
      browserConnections = info.browsers || {};
      const helperRequired = !!info.browserHelperRequired || /(?:^|[?&])connection=helper(?:&|$)/.test(location.search);
      const helperChanged = browserHelperRequired !== helperRequired;
      browserHelperRequired = helperRequired;
      agentAvailable = !!info.agentAvailable;
      autonomousTaskAvailable = !!info.autonomousTasks;
      serviceToken = info.token;
      storedModel = !!info.modelConfigured;
      savedModelUrl = info.savedModelUrl || "";
      if (helperChanged) render();
    }
    if (!workspaceLoaded) await loadWorkspace();
    const data = await api("/api/task");
    activeTask = data.task;
    phoneStatus = data.phone || { enabled: false };
    browserConnections = data.browsers || browserConnections;
    serviceOnline = true;
    updateBrowserConnectionUI();
    const prompt = activeTask?.pending
      ? JSON.stringify([
          activeTask.id,
          activeTask.current,
          activeTask.pending.kind,
          activeTask.pending.revision || activeTask.pending.reason,
        ])
      : "";
    if (prompt && prompt !== lastPrompt) {
      toast(
        activeTask.pending.kind === "review"
          ? "请检查网页并亲自提交，完成后点击“我已提交”。"
          : activeTask.pending.kind === "login"
            ? "Agent 需要你在招聘浏览器完成协助。"
            : "任务需要你协助，请查看当前说明。",
      );
      lastPrompt = prompt;
    }
    if (!prompt) lastPrompt = "";
  } catch {
    serviceOnline = false;
    serviceToken = "";
  }
  renderTaskUI();
  pollTimer = setTimeout(syncTask, taskRunning() ? 1200 : 4000);
}

async function loadWorkspace() {
  const data = await api("/api/workspace");
  workspaceRevision = data.revision;
  if (Object.keys(data.profile || {}).length && !profileEdited) {
    state.profile = normalizeProfile(data.profile);
    syncDerivedProfile(state.profile);
    state.resumePlan = data.resumePlan;
    state.resumeTemplate = data.resumeTemplate;
    state.resumeSections = (
      data.resumeSections || Object.keys(resumeModules)
    ).filter((k) => k in resumeModules);
    state.resumeOnePage = true;
  }
  if (data.model?.configured) {
    storedModel = true;
    savedModelUrl = data.model.baseUrl;
    if (!modelEdited)
      state.model = {
        ...state.model,
        provider: "DeepSeek",
        model: data.model.model,
        baseUrl: data.model.baseUrl,
      };
  }
  const old = state.attachments.filter(
    (a) => !a.stored && !data.attachments.some((b) => b.id === a.id),
  );
  state.attachments = [
    ...data.attachments.map((a) => ({ ...a, stored: true })),
    ...old.filter(
      (a) =>
        !data.attachments.some(
          (b) =>
            ["resume", "englishResume", "photo"].includes(a.type) &&
            a.type === b.type,
        ),
    ),
  ];
  for (const a of data.attachments) {
    if (files.has(a.id)) continue;
    const response = await fetch(
      "/api/attachments/" + encodeURIComponent(a.id),
      { headers: { "X-Offerly-Token": serviceToken } },
    );
    if (!response.ok) {
      toast("附件 " + a.name + " 需重新上传。");
      continue;
    }
    const blob = await response.blob();
    files.set(a.id, new File([blob], a.name, { type: blob.type }));
  }
  workspaceSignature = Object.keys(data.profile || {}).length
    ? JSON.stringify({
        profile: data.profile,
        resumePlan: data.resumePlan,
        resumeTemplate: data.resumeTemplate,
        resumeSections: (
          data.resumeSections || Object.keys(resumeModules)
        ).filter((k) => k in resumeModules),
        resumeOnePage: data.resumeOnePage !== false,
      })
    : "";
  workspaceLoaded = true;
  save();
  render();
}
function saveRemoteProfile() {
  const signature = JSON.stringify(profileSnapshot()),
    snapshot = JSON.parse(signature);
  remoteSaveChain = remoteSaveChain
    .catch(() => {})
    .then(async () => {
      if (!workspaceLoaded) return;
      if (signature === workspaceSignature) {
        if ($("#save-state")) $("#save-state").textContent = "已保存到本机";
        return;
      }
      const data = await api("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...snapshot, revision: workspaceRevision }),
      });
      workspaceRevision = data.revision;
      workspaceSignature = signature;
      if ($("#save-state")) $("#save-state").textContent = "已保存到本机";
    });
  return remoteSaveChain;
}
function uploadStoredAttachment(type, file, label = "") {
  remoteSaveChain = remoteSaveChain
    .catch(() => {})
    .then(async () => {
      const data = new FormData();
      data.append("type", type);
      if (label) data.append("label", label);
      data.append("file", file, file.name);
      const result = await api("/api/attachments", {
        method: "POST",
        headers: { "X-Workspace-Revision": String(workspaceRevision) },
        body: data,
      });
      workspaceRevision = result.revision;
      return { ...result.attachment, stored: true };
    });
  return remoteSaveChain;
}
async function saveChapter() {
  if (savingChapter) return;
  savingChapter = true;
  try {
    if (!workspaceLoaded) throw Error("请先连接本地服务。");
    if (apiKey.trim() || storedModel) {
      const model = await api("/api/model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...state.model, provider: "DeepSeek", apiKey }),
      });
      storedModel = model.configured;
      savedModelUrl = model.baseUrl;
      apiKey = "";
    }
    for (const a of [...state.attachments].filter((a) => !a.stored)) {
      const file = files.get(a.id);
      if (!file) throw Error("请重新选择 " + a.name);
      const stored = await uploadStoredAttachment(a.type, file, a.label);
      state.attachments = state.attachments.filter(
        (x) =>
          x.id !== a.id &&
          (!["resume", "englishResume", "photo"].includes(a.type) ||
            x.type !== a.type),
      );
      state.attachments.push(stored);
      files.delete(a.id);
      files.set(stored.id, file);
    }
    await saveRemoteProfile();
    save();
    toast("资料、配置和附件已保存到本机。");
    $("#modal-root").innerHTML = "";
  } catch (e) {
    toast(e.message);
  } finally {
    savingChapter = false;
    render();
  }
}
async function removeStoredAttachment(id) {
  const a = state.attachments.find((a) => a.id === id);
  if (!a) return;
  try {
    if (a.stored) {
      remoteSaveChain = remoteSaveChain
        .catch(() => {})
        .then(async () => {
          const data = await api("/api/attachments/" + encodeURIComponent(id), {
            method: "DELETE",
            headers: { "X-Workspace-Revision": String(workspaceRevision) },
          });
          workspaceRevision = data.revision;
        });
      await remoteSaveChain;
    }
    files.delete(id);
    state.attachments = state.attachments.filter((a) => a.id !== id);
    save();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function renderTaskUI() {
  const notice = $("#wechat-status");
  if (notice && !wechatBusy && !wechatMessage)
    notice.textContent =
      phoneStatus.wechat?.error ||
      (phoneStatus.wechat?.enabled ? "提醒已开启。" : "");
  const main = $(".main");
  if (!main) return;
  const strip = $("#task-status");
  const t = activeTask,
    c = t?.companies[t.current],
    running = taskRunning();
  if (strip) {
    strip.className = "task-strip" + (t?.pending ? " needs-you" : "");
    strip.hidden = !t;
  }
  if (t && strip) {
    const done = t.companies.filter((c) =>
      ["USER_CONFIRMED", "SKIPPED", "CANCELLED", "INCOMPLETE"].includes(
        c.state,
      ),
    ).length;
    strip.innerHTML = `<div class="task-top"><div><span class="pill">${esc(taskLabels[t.state] || t.state)}</span><h2>${running ? esc(targetLabel(c) || "准备执行") : esc(t.phase)}</h2><p>${running ? esc(t.phase) : `已结束 ${done} / ${t.companies.length} 项；用户确认已提交 ${t.companies.filter((c) => c.state === "USER_CONFIRMED").length} 项。`}</p></div><div class="task-controls">${running ? `${t.state === "RUNNING" ? button("暂停", "pause-task") : t.state === "PAUSED" ? button("继续", "continue-task", "primary") : ""}${button("停止", "stop-task", "danger")}` : button("查看企业结果", "task-companies")}</div></div><div class="task-meta"><span>第 ${t.current + 1} / ${t.companies.length} 项</span><span>模型判断 ${t.api_calls || 0} / 100 次${t.usage ? ` · 输入 ${t.usage.prompt_tokens} / 输出 ${t.usage.completion_tokens} tokens` : ""}</span></div>${t.pending && ["review", "blocked"].includes(t.pending.kind) ? `<div class="task-prompt"><b>${t.pending.kind === "blocked" ? "Agent 遇到执行问题" : "请检查并亲自完成投递"}</b><p>${esc(t.pending.reason)}</p><div class="row">${button("显示招聘浏览器", "focus-task")}${c?.job?.url ? button("我已提交，继续下一岗位", "complete-task", "primary") : ""}${button("需要修改，交还 Agent", "resume-task")}</div></div>` : t.pending?.kind === "login" ? `<div class="task-prompt"><b>Agent 需要你协助</b><p>${esc(t.pending.reason)}</p><small>直接操作已打开的招聘浏览器，完成后交还 Agent；无需在此输入账号或验证码。</small><div class="row">${button("显示招聘浏览器", "focus-task")}${button("完成协助，继续", "resume-task", "primary")}</div></div>` : ""}${running && t.state === "WAITING_HUMAN" ? '<div class="task-skip"><button class="text-btn" data-action="skip-task">跳过这家，继续下一家 →</button></div>' : ""}${!serviceOnline ? '<div class="inline-note">连接暂时中断，以上是最后一次收到的状态；重连后会自动更新。</div>' : ""}`;
  }
  let results = $("#queue-results");
  if (t) {
    if (!results) {
      results = document.createElement("div");
      results.id = "queue-results";
      results.className = "queue-results";
      $("#agent-execution").append(results);
    }
    const html = t.companies
      .map(
        (c) =>
          `<details><summary>${esc(targetLabel(c))} · ${esc(taskLabels[c.state] || c.state)}</summary><p>${esc(c.reason || { USER_CONFIRMED: "用户已确认亲自提交。", SKIPPED: "用户已放弃该岗位。", CANCELLED: "本轮尚未执行。", QUEUED: "等待 Agent 处理。", RUNNING: "正在处理。", WAITING_HUMAN: "等待用户协助。" }[c.state] || "本岗尚未确认提交，请查看执行记录。")}</p></details>`,
      )
      .join("");
    if (results.dataset.content !== html) {
      results.innerHTML = html;
      results.dataset.content = html;
    }
  }
  if ($(".prototype"))
    $(".prototype").textContent = serviceOnline ? "服务已连接" : "服务连接中";
  updateBrowserConnectionUI();
  const robotButton = $(".robot-start");
  if (robotButton) {
    robotButton.innerHTML = `<span class="robot-start-dot"></span>${running ? "查看工作进度" : "开始投递"}<span aria-hidden="true">↗</span>`;
  }
  document
    .querySelectorAll(
      "[data-action=assistant-search], [data-action=assistant-submit-companies]",
    )
    .forEach((b) => {
      b.disabled = startingTask || running;
      b.title = startingTask ? "正在启动，请稍候…" : "";
    });
  const newRound = $(".new-round");
  if (newRound) newRound.hidden = running;
  if ($("#launch-phone-state"))
    $("#launch-phone-state").textContent = phoneStatus.connected
      ? "在线"
      : "未连接";
  if ($("#phone-state"))
    $("#phone-state").textContent =
      phoneStatus.error ||
      (phoneStatus.connected
        ? "手机已连接"
        : phoneStatus.paired
          ? "已配对，手机暂离线"
          : "尚未连接");
  if (t && strip) {
    const p = t.progress,
      elapsed = p?.since
        ? Math.max(0, Math.floor((Date.now() - Date.parse(p.since)) / 1000))
        : 0;
    strip.insertAdjacentHTML(
      "beforeend",
      `<div class="live-progress"><div><small>${running ? `当前动作 ${elapsed} 秒` : "执行记录"} · ${serviceOnline ? "实时更新" : "连接中断"}</small></div><ol>${(
        p?.timeline || []
      )
        .slice(-5)
        .reverse()
        .map(
          (e) =>
            `<li><time>${esc(new Date(e.at).toLocaleTimeString())}</time>${esc(e.text)}</li>`,
        )
        .join(
          "",
        )}</ol><small>${phoneStatus.connected ? "手机已在线，可接收协作请求" : "手机未在线，可在「手机连接」建立连接"}</small></div>`,
    );
  }
}
async function startTask() {
  if (startingTask || taskRunning()) return;
  if (!autonomousTaskAvailable || !taskTargetReady())
    return toast("请检查任务配置与服务连接。");
  startingTask = true;
  renderTaskUI();
  try {
    if (apiKey.trim()) {
      const configured = await api("/api/model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...state.model, provider: "DeepSeek", apiKey }),
      });
      storedModel = configured.configured;
      savedModelUrl = configured.baseUrl;
      apiKey = "";
    }
    await saveRemoteProfile();
    const draft = taskDraft();
    localStorage.setItem("offerly.agent-task.v1", JSON.stringify(draft));
    activeTask = await api("/api/agent/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        browser: state.selectedBrowser,
        pluginConfirmed: state.pluginReadyFor === state.selectedBrowser,
        workspaceRevision,
        resumePlan: state.resumePlan,
        source: draft.source,
        count: draft.count,
        requirements: draft.requirements,
        targets: draft.targets,
      }),
    });
    $("#modal-root").innerHTML = "";
    assistantStage = "progress";
    assistantOpen = true;
    render();
    $(".assistant-body").scrollTop = 0;
    toast("Agent 已启动，正在自主准备本轮投递。");
  } catch (error) {
    assistantProblem(error.message);
  } finally {
    startingTask = false;
    await syncTask();
  }
}

async function controlTask(action) {
  if (!activeTask) return;
  try {
    await api(`/api/tasks/${activeTask.id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current: activeTask.current,
        ...(action === "complete" ? { confirmSubmitted: true } : {}),
      }),
    });
    await syncTask();
  } catch (error) {
    toast(error.message);
  }
}

function cleanURL(raw) {
  try {
    const u = new URL(String(raw).trim());
    if (
      !["http:", "https:"].includes(u.protocol) ||
      !u.hostname.includes(".") ||
      u.username ||
      u.password
    )
      return null;
    return u.href.replace(/\/$/, "");
  } catch {
    return null;
  }
}

async function addAttachments(type, incoming) {
  const t = attachmentTypes.find((t) => t.id === type);
  const materialLabel =
    type === "other" ? $("#other-material-name")?.value.trim() : "";
  if (type === "other" && !materialLabel)
    return toast("请先填写附件是什么，再上传。");
  let accepted = 0;
  const errors = [];
  for (const file of Array.from(incoming)) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!t.accept.split(",").includes(ext)) {
      errors.push(`${file.name} 格式不支持`);
      continue;
    }
    if (!file.size || file.size > 20 * 1024 * 1024) {
      errors.push(`${file.name} 为空或超过 20 MB`);
      continue;
    }
    if (
      ext === ".pdf" &&
      !new TextDecoder()
        .decode(await file.slice(0, 1024).arrayBuffer())
        .includes("%PDF-")
    ) {
      errors.push(`${file.name} 不是有效的 PDF 文件`);
      continue;
    }
    if (!workspaceLoaded) {
      errors.push("请先连接本地服务再上传附件");
      break;
    }
    let stored;
    try {
      stored = await uploadStoredAttachment(type, file, materialLabel);
    } catch (e) {
      errors.push(e.message);
      continue;
    }
    if (!t.multiple) {
      for (const a of state.attachments.filter((a) => a.type === type))
        files.delete(a.id);
      state.attachments = state.attachments.filter((a) => a.type !== type);
    }
    // Re-selecting an unavailable attachment replaces its metadata, rather than duplicating it.

    const id = stored.id;
    files.set(id, file);
    state.attachments.push(stored);
    accepted++;
    if (!t.multiple) break;
  }
  save();
  render();
  toast(
    errors.length
      ? `${accepted ? `已添加 ${accepted} 份；` : ""}${errors[0]}${errors.length > 1 ? `（共 ${errors.length} 份未添加）` : ""}`
      : `已选择 ${accepted} 份材料。`,
  );
}
function setPath(path, value) {
  if (path.startsWith("profile.")) profileEdited = true;
  if (path.startsWith("model.")) modelEdited = true;
  const parts = path.split(".");
  let obj = state;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (obj[key] == null) obj[key] = /^\d+$/.test(parts[i + 1]) ? [] : {};
    obj = obj[key];
  }
  obj[parts.at(-1)] =
    path === "applicationCount" ? (value === "" ? "" : Number(value)) : value;
  if (path.startsWith("profile.") && parts.at(-1) === "dateRange")
    obj._pdfDateRange = true;
  if (path.startsWith("profile.")) syncDerivedProfile(state.profile, path);
  queueSave();
}

document.addEventListener("input", (e) => {
  if (e.target.matches("[data-bind]"))
    setPath(e.target.dataset.bind, e.target.value);
  if (e.target.id === "other-material-name")
    $("#attachment-other").disabled = !e.target.value.trim();
  if (e.target.id === "wechat-token") wechatToken = e.target.value;
  if (e.target.id === "api-key") {
    apiKey = e.target.value;
    modelEdited = true;
  }
});
document.addEventListener("change", (e) => {
  const input = e.target;
  if (input.id === "wechat-token") {
    bindWechat();
    return;
  }
  if (input.name === "selected-browser") {
    state.selectedBrowser = input.value;
    state.pluginReadyFor = "";
    save();
    render();
    return;
  }
  if (input.id === "tailored-resume") {
    state.resumePlan = input.checked ? "template" : "upload";
    profileEdited = true;
    save();
    render();
    return;
  }
  if (input.dataset.resumeSection) {
    const key = input.dataset.resumeSection;
    state.resumeSections = input.checked
      ? [...new Set([...state.resumeSections, key])]
      : state.resumeSections.filter((k) => k !== key);
    profileEdited = true;
    save();
    render();
    return;
  }
  if (input.name === "resume-plan" || input.name === "resume-template") {
    profileEdited = true;
    state[input.name === "resume-plan" ? "resumePlan" : "resumeTemplate"] =
      input.value;
    save();
    render();
    return;
  }
  if (input.dataset.attachment)
    addAttachments(input.dataset.attachment, input.files).catch(() =>
      toast("材料读取失败，请重新选择文件。"),
    );
});
const actions = {
  "edit-module": (b) => openModule(b.dataset.module),
  "edit-custom-module": (b) => openCustomModule(Number(b.dataset.index)),
  "finish-module": finishModule,
  "add-custom-module": () => {
    state.profile.customModules ??= [];
    if (state.profile.customModules.length >= 20)
      return toast("最多添加20个自定义模块。");
    state.profile.customModules.push({ name: "", content: "" });
    if (!state.resumeSections.includes("custom"))
      state.resumeSections.push("custom");
    profileEdited = true;
    save();
    render();
    openCustomModule(state.profile.customModules.length - 1);
  },
  "remove-custom-module": (b) => {
    state.profile.customModules.splice(Number(b.dataset.index), 1);
    if (!state.profile.customModules.length)
      state.resumeSections = state.resumeSections.filter((k) => k !== "custom");
    profileEdited = true;
    finishModule();
  },
  "connect-browser": () => browserAction("connect"),
  "use-browser-helper": () => { window.location.href = "/?connection=helper#browser"; },
  "copy-browser-address": () => navigator.clipboard.writeText(`${state.selectedBrowser}://inspect/#remote-debugging`).then(() => toast("授权地址已复制，请粘贴到浏览器地址栏。")),
  "copy-helper-link": () => navigator.clipboard.writeText(helperLaunchUrl).then(() => toast("连接链接已复制，请粘贴到助手窗口。")),
  "browser-setup": () => browserAction("open", "setup"),
  "check-browser": () => browserAction("check"),
  "disconnect-browser": () => browserAction("disconnect"),
  "test-tata-program": testTataProgram,
  "test-wechat": testWechat,
  "application-history": showApplicationHistory,
  "open-book": () => go("intro"),
  "assistant-launch": launchAssistant,
  "assistant-close": () => {
    assistantOpen = false;
    render();
  },
  "assistant-confirm": confirmPreferences,
  "assistant-edit": () => {
    assistantStage = "preferences";
    assistantError = "";
    render();
    $(".assistant-body").scrollTop = 0;
  },
  "assistant-search": () => submitAssistant("search"),
  "assistant-companies": () => {
    assistantStage = "companies";
    assistantError = "";
    render();
    $(".assistant-body").scrollTop = 0;
  },
  "assistant-submit-companies": () => submitAssistant("companies"),
  "assistant-new-round": launchAssistant,
  "model-settings": () =>
    modal(
      "AI 服务设置",
      `${modelView()}<div class="form-actions">${button("保存配置", "save-chapter", "primary")}</div>`,
    ),
  "save-chapter": saveChapter,
  "pair-phone": pairPhone,
  "check-model": () =>
    toast(
      modelReady()
        ? "格式完整，保存后可用于 Agent。"
        : "请检查 API 地址、模型名称和密钥。",
    ),
  "preview-template": (b) =>
    previewTemplate(b.dataset.template || state.resumeTemplate),
  "remove-attachment": (b) => removeStoredAttachment(b.dataset.id),
  "copy-phone": () =>
    navigator.clipboard
      .writeText(pairLink)
      .then(() => toast("链接已复制"))
      .catch(() => toast("请手动复制上方链接。")),
  "revoke-phone": async () => {
    phoneStatus = await api("/api/phone/revoke", { method: "POST" });
    pairLink = "";
    render();
  },
  "task-companies": () => {
    go("launch");
    $("#queue-results")?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  },
  "complete-task": () => controlTask("complete"),
  "close-modal": finishModule,
  "add-pdf-entry": (b) => {
    const rows = (state.profile[b.dataset.group] ??= []);
    if (!rows.length) rows.push({});
    rows.push({});
    profileEdited = true;
    save();
    if ($("#modal-root").dataset.module)
      openModule($("#modal-root").dataset.module);
    else render();
  },
  "remove-pdf-entry": (b) => {
    state.profile[b.dataset.group].splice(Number(b.dataset.index), 1);
    profileEdited = true;
    save();
    if ($("#modal-root").dataset.module)
      openModule($("#modal-root").dataset.module);
    else render();
  },
};
for (const name of ["pause", "continue", "stop", "skip", "focus", "resume"])
  actions[name + "-task"] = () => controlTask(name);
document.addEventListener("click", (e) => {
  const routeButton = e.target.closest("[data-route]");
  if (routeButton) {
    e.preventDefault();
    go(routeButton.dataset.route);
    return;
  }
  const target = e.target.closest("[data-action]"),
    handler = target && actions[target.dataset.action];
  if (handler)
    Promise.resolve()
      .then(() => handler(target))
      .catch((error) => toast(error.message));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("#modal-root").children.length) finishModule();
    else if (assistantOpen) {
      assistantOpen = false;
      render();
      $(".robot-start").focus();
    }
  }
  if (e.key === "Tab" && assistantOpen && !$("#modal-root").children.length) {
    const nodes = [
      ...document.querySelectorAll(
        ".assistant-panel button:not(:disabled), .assistant-panel input, .assistant-panel textarea, .assistant-panel select, .assistant-panel a[href], .assistant-panel summary",
      ),
    ].filter((x) => x.getClientRects().length);
    const first = nodes[0],
      last = nodes.at(-1);
    if (
      e.shiftKey &&
      (document.activeElement === first ||
        !$(".assistant-panel").contains(document.activeElement))
    ) {
      e.preventDefault();
      last?.focus();
    } else if (
      !e.shiftKey &&
      (document.activeElement === last ||
        !$(".assistant-panel").contains(document.activeElement))
    ) {
      e.preventDefault();
      first?.focus();
    }
  }
});
save();
render();
syncTask();
