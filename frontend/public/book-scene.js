export const bookSteps = [
  {
    id: "browser",
    number: "一",
    title: "连接浏览器",
    note: "从你熟悉的浏览器开始。",
    body: "连接你正在使用的 Edge 或 Chrome，保留已登录的账号和安装好的插件。",
    tip: "首次连接需要在浏览器里允许远程调试。之后，Offerly 会在这里显示连接状态。",
    mark: "01",
  },
  {
    id: "plugin",
    number: "二",
    title: "准备塔塔",
    note: "把重复填写，交给插件。",
    body: "安装塔塔网申，登录并填写自己的资料。Agent 进入申请表后，会调用它帮你填表。",
    tip: "点击“测试塔塔”，在本地小表单里试填一次。测试成功，就可以继续了。",
    mark: "02",
  },
  {
    id: "materials",
    number: "三",
    title: "准备资料",
    note: "让每一次申请，有备而来。",
    body: "准备好企业可能要求的附件。你可以使用自己的简历，也可以让 Agent 结合岗位 JD 生成专属简历。",
    tip: "照片、成绩单和其他材料按需添加。生成专属简历时，点击模块补充资料即可。",
    mark: "03",
  },
  {
    id: "phone",
    number: "四",
    title: "连接手机",
    note: "需要你时，我们再碰个面。",
    body: "用手机打开连接链接。当遇到登录、验证或需要检查申请时，你可以在手机上接手。",
    tip: "配置好弹窗提醒，就不用一直盯着电脑。最终的申请内容，请由你检查并确认提交。",
    mark: "04",
  },
];

export function robotArt() {
  return `<svg class="robot-art" viewBox="0 0 260 230" fill="none" aria-hidden="true">
  <defs><linearGradient id="robot-shell" x1="76" y1="38" x2="195" y2="190" gradientUnits="userSpaceOnUse"><stop stop-color="#fffef7"/><stop offset="1" stop-color="#cadbd3"/></linearGradient><linearGradient id="robot-screen" x1="84" y1="70" x2="174" y2="118" gradientUnits="userSpaceOnUse"><stop stop-color="#204c46"/><stop offset="1" stop-color="#102d29"/></linearGradient></defs>
  <ellipse cx="133" cy="214" rx="108" ry="10" fill="#344b3d" opacity=".12"/>
  <path d="M105 177v30m46-30v30" stroke="#687f70" stroke-width="16" stroke-linecap="round"/><path d="M90 208h23m31 0h23" stroke="#214c43" stroke-width="13" stroke-linecap="round"/>
  <rect x="88" y="129" width="80" height="58" rx="24" fill="url(#robot-shell)" stroke="#8ca69a" stroke-width="2"/>
  <path d="M94 144 67 169m94-25 28 25" stroke="#d6e2d8" stroke-width="17" stroke-linecap="round"/><circle cx="67" cy="168" r="10" fill="#e9eee3" stroke="#8ca69a" stroke-width="2"/><circle cx="189" cy="168" r="10" fill="#e9eee3" stroke="#8ca69a" stroke-width="2"/>
  <path d="M129 44V28" stroke="#6c8b78" stroke-width="4"/><circle cx="129" cy="24" r="7" fill="#d1a061"/><path d="m211 52 3-9m5 19 9-3M49 72l-9-5" stroke="#bc9b66" stroke-width="3" stroke-linecap="round"/>
  <rect x="66" y="73" width="15" height="34" rx="7" fill="#a5bbac"/><rect x="178" y="73" width="15" height="34" rx="7" fill="#a5bbac"/>
  <rect x="76" y="44" width="108" height="89" rx="34" fill="url(#robot-shell)" stroke="#86a190" stroke-width="2"/>
  <rect x="86" y="62" width="88" height="56" rx="23" fill="url(#robot-screen)"/><path d="M104 85q7-11 14 0m23 0q7-11 14 0" stroke="#d9edc9" stroke-width="5" stroke-linecap="round"/><path d="M121 101q9 6 18 0" stroke="#d9edc9" stroke-width="3" stroke-linecap="round"/>
  <circle cx="129" cy="145" r="5" fill="#d0a263"/><path d="M32 190h196" stroke="#8b7255" stroke-width="7" stroke-linecap="round"/>
  <path d="m67 145 17 46h105l-17-46z" fill="#45675b" stroke="#264c41" stroke-width="2"/><path d="m74 149 14 36h92l-13-36z" fill="#668b78"/><path d="M84 191h114" stroke="#c7b591" stroke-width="5" stroke-linecap="round"/><path d="m123 164 4-7 8 1 4 7-5 6-8-1z" fill="#d8e3c7"/>
  </svg>`;
}

export function coverView() {
  return `<button class="manual-cover" data-action="open-book" aria-label="翻开使用说明"><span class="cover-spine" aria-hidden="true">OFFERLY · 使用说明</span><span class="cover-border" aria-hidden="true"></span><span class="cover-edition">OFFERLY HANDBOOK / 01</span><span class="cover-emblem" aria-hidden="true"><svg viewBox="0 0 100 100"><path d="M50 16v68M17 25q18-9 33 3 15-12 33-3v48q-18-8-33 4-15-12-33-4z"/><path d="M27 36q8-2 14 2m-14 9q8-2 14 2m18-11q6-4 14-2m-14 13q6-4 14-2"/></svg></span><span class="cover-title">使用说明</span><span class="cover-subtitle">把时间留给下一次机会。</span><span class="cover-rule" aria-hidden="true"></span><span class="cover-open">轻触翻开 ${'<span aria-hidden="true">↗</span>'}</span><span class="cover-foot">你的求职之旅，从这一页开始。</span></button>`;
}

export function storyLeft(page) {
  if (page === "intro")
    return `<div class="story-kicker">写在开始之前</div><h1 class="story-title">下一份机会，<br>一起慢慢翻开。</h1><p class="story-description">找岗位、看要求、填资料……<br>求职路上的重复工作，<br>可以交给 Offerly。</p><div class="intro-drawing" aria-hidden="true"><span class="drawing-orbit"></span><span class="drawing-paper"><i></i><i></i><i></i><b>你的下一站</b><span>↗</span></span><span class="drawing-star">✦</span></div><p class="story-footnote">你负责选择未来，<br>我们帮你准备每一次出发。</p>`;
  const s = bookSteps.find((s) => s.id === page);
  return `<div class="story-kicker">准备工作 / ${s.mark}</div><span class="story-numeral" aria-hidden="true">${s.mark}</span><h1 class="story-title">第${s.number}步：<br>${s.title}</h1><p class="story-tagline">${s.note}</p><p class="story-description">${s.body}</p><div class="story-margin-note"><span>一点小提示</span><p>${s.tip}</p></div><div class="story-step-dots" aria-label="配置进度">${bookSteps.map((x) => `<button data-route="${x.id}" class="${x.id === page ? "current" : ""}" aria-label="第${x.number}步：${x.title}">${x.mark}</button>`).join("")}</div>`;
}

export function introRight() {
  return `<div class="intro-copy"><span class="paper-eyebrow">认识 OFFERLY</span><h2>一位陪你投递的<br>求职助手。</h2><p>告诉它你想找什么样的工作。Offerly 会寻找岗位、浏览企业官网、准备简历，并借助塔塔填写申请表。</p><div class="intro-promises"><div><span>01</span><div><h3>先准备，后出发</h3><p>浏览器、插件、资料、手机，四步准备好。</p></div></div><div><span>02</span><div><h3>想投什么，和它聊聊</h3><p>点击右下角的机器人，确认岗位偏好，或告诉它想投的企业。</p></div></div><div><span>03</span><div><h3>重要的决定，留给你</h3><p>登录和验证时接手协助，申请填好后，由你检查并确认提交。</p></div></div></div><p class="intro-ending">翻到下一页，开始准备吧。<span aria-hidden="true">↘</span></p></div>`;
}
