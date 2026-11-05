// Offline checks of the real page controller. No browser, model or user data is used.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as schema from "../frontend/public/profile-schema.js";
import * as derived from "../frontend/public/profile-derived.js";
import * as scene from "../frontend/public/book-scene.js";

// Compact schema declarations must still preserve saved and legacy profile data.
const migrated = schema.normalizeProfile({
  basic: { name: "Saved candidate", customValue: "keep" },
  contact: { github: "https://example.invalid" },
  education: [{ school: "Saved school", customValue: "keep" }],
  skills: [{ name: "Python", level: "熟悉" }],
  campus: [{ type: "社会实践", organization: "Saved organization" }],
  research: [{ type: "论文", title: "Saved paper" }],
  customModules: [{ name: "Portfolio", content: "Saved content" }],
});
assert.equal(migrated.basic.customValue, "keep");
assert.equal(migrated.education[0].customValue, "keep");
assert.equal(migrated.contact.website, "https://example.invalid");
assert.match(migrated.skillsSummary.technical, /Python/);
assert.equal(migrated.social[0].organization, "Saved organization");
assert.equal(migrated.papers[0].title, "Saved paper");
assert.equal(migrated.customModules[0].content, "Saved content");

const source = fs
  .readFileSync(new URL("../frontend/public/app.js", import.meta.url), "utf8")
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];\s*/gm, "")
  .replace(/save\(\);\s*render\(\);\s*syncTask\(\);\s*$/, "");
const nodes = new Map();
const element = (selector) => {
  if (!nodes.has(selector))
    nodes.set(selector, {
      innerHTML: "",
      textContent: "",
      hidden: false,
      dataset: {},
      scrollTop: 0,
      scrollIntoView() {},
      focus() {},
      classList: { add() {}, remove() {} },
    });
  return nodes.get(selector);
};
const context = vm.createContext({
  ...schema,
  ...derived,
  ...scene,
  assert,
  URL,
  console,
  document: {
    querySelector: element,
    querySelectorAll: () => [],
    addEventListener() {},
  },
  window: { addEventListener() {}, scrollTo() {} },
  location: { hash: "", pathname: "/", search: "" },
  history: { replaceState() {} },
  localStorage: { getItem: () => null, setItem() {} },
  navigator: {},
  setTimeout: () => 1,
  clearTimeout() {},
});
vm.runInContext(source, context);
await vm.runInContext(
  String.raw`(async () => {
  // Keep rendering and transport isolated, while exercising the real transition functions.
  render = () => {};
  renderTaskUI = () => {};
  toast = () => {};
  syncTask = async () => {};
  saveRemoteProfile = async () => {};
  const requests = [];
  api = async (path, options) => {
    requests.push({path, body:JSON.parse(options.body)});
    return {id:'offline',active:true,state:'RUNNING',companies:[],current:0};
  };
  serviceOnline = true; agentAvailable = true; autonomousTaskAvailable = true;
  storedModel = true; savedModelUrl = state.model.baseUrl;
  workspaceRevision = 17;
  launchAssistant();
  assert.equal(assistantStage, 'gate');
  assert.deepEqual(preparationChecks().filter(x=>!x.ok).map(x=>x.id), ['browser','plugin','materials','phone']);
  await submitAssistant('search');
  assert.equal(requests.length, 0, 'Incomplete setup must never launch a task');
  browserConnections.edge = {connected:true};
  state.pluginReadyFor = 'edge'; phoneStatus = {connected:true};
  state.attachments = [{id:'resume',type:'resume',name:'test.pdf',stored:true}];
  files.set('resume', {});
  launchAssistant(); assert.equal(assistantStage, 'preferences');
  state.applicationCount = 0;
  confirmPreferences(); assert.equal(assistantStage, 'preferences');
  assert.match(assistantError, /1 至 200/);
  state.applicationCount = 3;
  state.preferences.keywords = 'AI 应用开发';
  state.preferences.companyBlacklist = '不要投的企业';
  confirmPreferences(); assert.equal(assistantStage, 'confirm');
  assert.match(assistantView(), /不要投的企业/);
  assert.match(assistantView(), /test.pdf/);
  await submitAssistant('search');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/agent/tasks');
  assert.equal(requests[0].body.source, 'search');
  assert.equal(requests[0].body.count, 3);
  assert.equal(requests[0].body.requirements.companyBlacklist, '不要投的企业');
  assert.equal(requests[0].body.workspaceRevision, 17);
  assert.equal(assistantStage, 'progress');
  activeTask = null;
  state.targetLines = '示例科技，产品经理方向\nhttps://example.com/jobs/123';
  await submitAssistant('companies');
  assert.equal(requests[1].body.source, 'companies');
  assert.equal(requests[1].body.count, 2);
  assert.equal(requests[1].body.targets[0].company, '示例科技，产品经理方向');
  assert.equal(requests[1].body.targets[1].url, 'https://example.com/jobs/123');
  activeTask = null;
  state.resumePlan = 'template'; state.resumeSections = ['summary'];
  assert.equal(preparationChecks().find(x=>x.id==='materials').ok, false);
  state.profile.basic.name = '测试'; state.profile.contact.phone = '13800000000';
  state.profile.contact.email = 'test@example.invalid'; state.profile.extra.evaluation = '测试描述';
  assert.equal(preparationChecks().find(x=>x.id==='materials').ok, true);
  assert.equal(bookSteps.map(x=>x.id).join(','), 'browser,plugin,materials,phone');
  go('materials'); assert.equal(bookPage, 'materials'); assert.equal(assistantOpen, false);
  go('cover'); assert.equal(bookPage, 'cover');
  console.log('PASS: preparation checks, both task routes, payloads, resume readiness and book navigation');
})()`,
  context,
);
