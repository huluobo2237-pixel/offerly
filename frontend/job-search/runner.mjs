/** Read-only, bounded public recruitment queries. No browser session or model credentials. */
import { listSites, searchJobs, getJobDetail } from "jobhunt-cli";
import { createDecipheriv } from "node:crypto";
import { AsyncLocalStorage } from "node:async_hooks";

const names = {
  didi: "滴滴",
  kuaishou: "快手",
  bytedance: "字节跳动",
  meituan: "美团",
  xiaomi: "小米",
  tencent: "腾讯",
  baidu: "百度",
  jd: "京东",
  xiaohongshu: "小红书",
  bilibili: "哔哩哔哩",
  netease: "网易",
  ctrip: "携程",
  huawei: "华为",
  dji: "大疆",
  ant: "蚂蚁集团",
  dewu: "得物",
  mihoyo: "米哈游",
  minimax: "MiniMax",
  moonshot: "月之暗面",
  zhipu: "智谱",
  deepseek: "DeepSeek",
  taotian: "淘天集团",
  "taobao-shangou": "淘宝闪购",
  fliggy: "飞猪",
  "alibaba-intl": "阿里国际",
  aliyun: "阿里云",
  tongyi: "通义",
  dingtalk: "钉钉",
  quark: "夸克",
  thead: "平头哥",
  amap: "高德",
  cainiao: "菜鸟",
  hujing: "阿里大文娱",
  freshippo: "盒马",
  alihealth: "阿里健康",
  lingxi: "灵犀互娱",
};
// Official campus portals, separate from social recruiting sites.
const moka = [
  ["gigadevice", "兆易创新", 92215],
  ["3peakic", "思瑞浦", 67894],
  ["vwa", "大众中国", 142785],
  ["aftershokzhr", "韶音科技", 36940],
  ["bjhl", "高途", 102145],
  ["deeproute", "元戎启行", 6487],
  ["hangtianwuyuan", "航天五院总体设计部", 166339],
].map(([org, name, site]) => ({
  id: `moka-${org}`,
  name,
  org,
  site,
  supported_natures: ["campus"],
  url: `https://app.mokahr.com/campus-recruitment/${org}/${site}`,
}));
const sites = [
  ...listSites().map((s) => ({ ...s, name: names[s.id] || s.name })),
  ...moka,
];
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
let input = "";
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
// Extra tenants come from the current web search, not a fixed enterprise directory.
for (const candidate of request.extraSources || []) {
  try {
    if (
      !/^web_(moka|feishu)_[a-f0-9]{16}$/.test(candidate.id) ||
      typeof candidate.name !== "string"
    )
      continue;
    const url = new URL(candidate.url);
    if (url.protocol !== "https:" || url.username || url.password || url.port)
      continue;
    if (candidate.kind === "moka" && url.hostname === "app.mokahr.com") {
      const match = url.pathname.match(
        /^\/(campus-recruitment|campus_apply|social-recruitment|social_apply)\/([A-Za-z0-9_-]{1,80})\/(\d{1,12})$/,
      );
      if (match)
        sites.push({
          id: candidate.id,
          name: candidate.name,
          url: url.href.replace(/\/$/, ""),
          org: match[2],
          site: Number(match[3]),
          nature: candidate.nature,
          supported_natures: [candidate.nature],
        });
    } else if (
      candidate.kind === "feishu" &&
      /^[a-z0-9-]+\.jobs\.(feishu\.cn|f\.mioffice\.cn|larkoffice\.com)$/.test(
        url.hostname,
      ) &&
      /^\/[A-Za-z0-9_-]{1,80}$/.test(url.pathname)
    ) {
      sites.push({
        id: candidate.id,
        name: candidate.name,
        url: url.href.replace(/\/$/, ""),
        feishu: true,
        nature: candidate.nature,
        supported_natures: ["campus", "intern", "social"],
      });
    }
  } catch {}
}
if (request.action === "sources") {
  emit({
    sources: sites.map(({ id, name, supported_natures, url }) => ({
      id,
      name,
      natures: supported_natures,
      url,
    })),
  });
  process.exit(0);
}
const nativeFetch = globalThis.fetch;
const requestContext = new AsyncLocalStorage();
let requests = 0;
globalThis.fetch = (url, options = {}) => {
  if (++requests > 240) throw Error("REQUEST_BUDGET");
  const signal = AbortSignal.any(
    [
      options.signal,
      requestContext.getStore()?.signal,
      AbortSignal.timeout(8000),
    ].filter(Boolean),
  );
  return nativeFetch(url, { ...options, signal });
};
function clean(job) {
  const raw = job.raw || {};
  return {
    id: String(job.id || ""),
    title: String(job.name || ""),
    url: String(job.url || ""),
    city: String(job.location_names || ""),
    description: String(job.description || ""),
    requirement: String(job.requirement || ""),
    nature: job.nature_code,
    natureEvidence: String(
      raw.source_nature_name || raw.source_nature_code || job.nature_code || "",
    ),
    updatedAt: String(job.updated_at || ""),
  };
}
async function mokaJobs(site, offset = 0) {
  const response = await fetch(
    "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://app.mokahr.com",
        Referer: site.url + "/",
      },
      body: JSON.stringify({
        orgId: site.org,
        siteId: site.site,
        locale: "zh-CN",
        limit: 20,
        offset,
      }),
    },
  );
  if (!response.ok) throw Error(`HTTP_${response.status}`);
  let data = await response.json();
  if (typeof data.data === "string" && data.necromancer) {
    const key = Buffer.from(data.necromancer),
      dec = createDecipheriv(
        `aes-${key.length * 8}-cbc`,
        key,
        Buffer.from("de7c21ed8d6f50fe"),
      );
    data = JSON.parse(
      Buffer.concat([
        dec.update(Buffer.from(data.data, "base64")),
        dec.final(),
      ]).toString(),
    );
  }
  if (data.code !== 0 && data.success !== true) throw Error("SOURCE_CHANGED");
  if (!Array.isArray(data.data?.jobs)) throw Error("SOURCE_CHANGED");
  return {
    count: data.data.jobs.length,
    closedIds: data.data.jobs
      .filter((j) =>
        ["closed", "archived", "expired", "offline"].includes(j.status),
      )
      .map((j) => String(j.id)),
    jobs: data.data.jobs
      .filter((j) => j.status === "open")
      .map((j) => ({
        id: String(j.id),
        title: j.title,
        url: `${site.url}#/job/${j.id}`,
        city: (j.locations || [])
          .map((l) => l.cityName || l.city || l.label || l.name)
          .filter(Boolean)
          .join("、"),
        description: j.jobDescription || "",
        requirement: "",
        nature: site.nature || "campus",
        natureEvidence:
          (site.nature || "campus") === "campus"
            ? "官方校招频道"
            : "官方招聘频道",
        updatedAt: j.publishedAt || j.updatedAt || "",
      })),
  };
}
async function feishuJobs(site, term, nature, offset = 0) {
  const root = new URL(site.url),
    path = root.pathname.slice(1);
  const response = await fetch(root.origin + "/api/v1/search/job/posts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: root.origin,
      Referer: site.url + "/",
      "Portal-Channel": "office",
      "Portal-Platform": "pc",
      "website-path": path,
    },
    body: JSON.stringify({
      keyword: term,
      limit: 20,
      offset,
      portal_type: 6,
      job_category_id_list: [],
      location_code_list: [],
      subject_id_list: [],
      recruitment_id_list: [
        nature === "campus" ? 201 : nature === "intern" ? 301 : 101,
      ],
      job_function_id_list: [],
    }),
  });
  if (!response.ok) throw Error(`HTTP_${response.status}`);
  const data = await response.json();
  if (data.code !== 0 || !Array.isArray(data.data?.job_post_list))
    throw Error("SOURCE_CHANGED");
  return data.data.job_post_list.map((j) => {
    const code = String(j.recruit_type?.id || "");
    return {
      id: String(j.id),
      title: j.title,
      url: site.url + "/position/" + j.id + "/detail",
      city: (j.city_list || []).map((c) => c.name).join("、"),
      description: j.description || "",
      requirement: j.requirement || "",
      nature:
        code === "101"
          ? "social"
          : code === "301" || code === "202"
            ? "intern"
            : code === "201"
              ? "campus"
              : nature,
      natureEvidence: j.recruit_type?.name || nature,
      updatedAt: String(j.publish_time || ""),
    };
  });
}
async function scan(site) {
  const jobs = new Map(),
    closedIds = new Set();
  let warning = "",
    successful = 0;
  const pages = request.collectAll
    ? Math.max(1, Math.min(10, Number(request.pages) || 5))
    : 1;
  const add = (rows) => {
    let count = 0;
    for (const row of rows) {
      const job = site.org || site.feishu ? row : clean(row);
      if (job.id && !jobs.has(`${job.nature}:${job.id}`)) {
        jobs.set(`${job.nature}:${job.id}`, job);
        count++;
      }
    }
    return count;
  };
  const natures =
    request.nature === "all" ? site.supported_natures : [request.nature];
  for (const nature of natures) {
    if (!site.supported_natures.includes(nature)) continue;
    for (const term of site.org || request.collectAll ? [""] : request.terms) {
      try {
        let rows;
        if (site.org) {
          for (let page = 0; page < (request.collectAll ? pages : 5); page++) {
            const data = await mokaJobs(site, page * 20);
            successful++;
            const added = add(data.jobs);
            for (const id of data.closedIds) closedIds.add(id);
            if (data.count < 20 || !added) break;
            if (request.collectAll && page === pages - 1)
              warning = "PAGE_LIMIT";
          }
        } else {
          const size = site.feishu
            ? 20
            : Math.min(20, site.max_page_size || 20);
          for (let page = 1; page <= pages; page++) {
            rows = site.feishu
              ? await feishuJobs(site, term, nature, (page - 1) * 20)
              : await searchJobs(site.id, {
                  query: term,
                  nature,
                  limit: size,
                  page,
                });
            successful++;
            const added = add(rows);
            if (!added || rows.length < size) break;
            if (request.collectAll && page === pages) warning = "PAGE_LIMIT";
          }
        }
      } catch (error) {
        if (error.code === "EMPTY_RESULT") {
          successful++;
          continue;
        }
        warning = String(
          error.code || error.message || "SOURCE_UNAVAILABLE",
        ).slice(0, 80);
        // Do not repeatedly request a blocked, rate-limited, or changed endpoint.
        break;
      }
    }
  }
  // Some list endpoints omit JD. Fetch a small number of details, never an unbounded crawl.
  let detailCursor = 0;
  const details = [...jobs]
    .filter(
      ([, job]) =>
        (job.description + job.requirement).length < 80 &&
        !site.org &&
        !site.feishu,
    )
    .slice(0, request.collectAll ? 20 : 5);
  await Promise.all(
    Array.from({ length: 3 }, async () => {
      while (detailCursor < details.length) {
        const [key, job] = details[detailCursor++];
        try {
          jobs.set(
            key,
            clean(await getJobDetail(site.id, job.id, { nature: job.nature })),
          );
        } catch {
          warning = warning || "DETAIL_UNAVAILABLE";
        }
      }
    }),
  );
  return {
    source: site.id,
    company: site.name,
    status: warning ? (successful ? "partial" : "failed") : "ok",
    warning,
    jobs: [...jobs.values()],
    closedIds: [...closedIds],
  };
}
if (request.action !== "search") throw Error("UNKNOWN_ACTION");
const selected = sites.filter(
  (s) =>
    (request.nature === "all" ||
      s.supported_natures.includes(request.nature)) &&
    (!request.sources || request.sources.includes(s.id)),
);
emit({ type: "start", total: selected.length });
let cursor = 0;
await Promise.all(
  Array.from({ length: 4 }, async () => {
    while (cursor < selected.length) {
      const site = selected[cursor++];
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 24000);
      const result = await requestContext.run(
        { signal: controller.signal },
        () => scan(site),
      );
      clearTimeout(timer);
      emit({ type: "source", ...result });
    }
  }),
);
emit({ type: "done", requests });
process.exit(0);
