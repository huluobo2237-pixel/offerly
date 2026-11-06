# Offerly

使用用户自己的 Edge / Chrome，由 Agent 选岗、准备简历、导航申请流程，再通过塔塔填表，最后交用户检查并提交。

在线体验：[Offerly](https://8.133.190.12/)（首次使用需按页面引导运行 Windows 连接助手）。

完整架构、执行流程、部署及验证边界见 [技术文档](docs/TECHNICAL.md)；参赛提交可下载 [技术文档 PDF](docs/Offerly-技术文档.pdf)。

## 启动

双击 `start-offerly.cmd`，或运行 `./start-offerly.ps1`，打开 http://127.0.0.1:9189/。

- 默认手机端口 9190；检测到 `tools/cloudflared.exe` 时启用临时 HTTPS 手机入口。
- 仅局域网运行：`./start-offerly.ps1 -LocalPhone`。
- 更换端口：`./start-offerly.ps1 -Port 9289 -PhonePort 9290`。
- 首次部署：Python 3.13、uv、Node.js 22.19+，执行 `uv sync --frozen` 和 `npm --prefix frontend/job-search ci --ignore-scripts --no-audit --no-fund`。

正式 HTTPS 网站使用 `python -B -m frontend.serve --public-origin https://你的站点 --data-dir /var/lib/offerly/workspace --phone-ice-config /私有目录/phone-ice.json`，由反向代理统一转发到 9189。工作区按浏览器会话隔离，模型与搜索的服务器默认配置使用独立的 `model.json` 与 `job-search/search.json`，不要公开这些文件。跨网络手机协助需配置可用的 TURN 服务。

正式站第一步提供 Windows 连接助手下载。首次运行后注册 `offerly-connect` 唤起协议，以后从网站点击连接即可。助手仅主动连接网站，不开放公网浏览器端口；附件按原文件名传输，断开后清理本次临时文件。运行 `tools/build-connector.ps1` 可重新构建助手。

## 使用流程

翻开使用说明，完成连接浏览器、准备塔塔、准备资料和连接手机四步；点击机器人“开始投递”，在对话中确认岗位偏好、数量、黑名单及简历方案。

默认按偏好自动找岗，也可在机器人对话中提供企业名称或投递链接，每行一个。
自动找岗优先查询本地岗位库，候选不足再全网搜索。从结果中用 `confirm_job` 选齐 min(5, 剩余投递数) 个具体岗位，不足继续搜索，无需先逐个打开详情。凑齐后才依次打开企业官网、读取完整 JD，并用 `choose_job` 开始逐个申请；本批完成后再找下一批。Tavily 每个基础搜索请求最多取 20 条结果，已齐备批次不重复搜索。
申请入口必须来自企业官网或企业专属招聘站；牛客、BOSS、高校就业站等只作线索，需要另找官方入口。搜索摘要不会冒充完整 JD 入库。
全网线索先持久保存，后台用已有结果核实公开详情，不再消耗搜索额度；已核实岗位按链接去重入库。有效但不符合本轮偏好的岗位可用 `confirm_job(forBatch=false)` 只入公共库，不占批次名额。公共岗位库不保存用户的简历资料或匹配理由。
自带简历从 PDF 或图片提取选岗依据；模板方案使用用户勾选的简历模块。Agent 阅读岗位详情、登记 JD 后生成本岗 PDF。

进入申请表后先上传所需附件，等待上传与解析结束，再调用塔塔填表。用户通过手机完成登录、验证码、内容检查和最终提交。
“该岗位已投递完成”与“放弃该岗位”分别记入历史，然后继续下一目标；已处理岗位不重复选择。
微信提醒只在电脑端“连接手机”配置，换手机配对链接无需重新保存 token。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| `frontend/public/app.js`、`book-scene.js`、`book.css` | 说明书、机器人对话、配置保存、任务启动与进度 |
| `frontend/public/profile-schema.js`、`profile-derived.js` | 保存资料的结构兼容与派生值；不再渲染旧网申档案表单 |
| `frontend/backend.py`、`workspace.py`、`resumes.py` | HTTP 接口、资料与附件保存、共享 PDF 服务 |
| `offerly/services.py` | 连接应用服务与 Agent 的统一边界 |
| `offerly/orchestration.py` | 任务模型、简历读取、岗位搜索与登记、逐岗状态交接 |
| `offerly/agent.py` | Browser Use 运行适配、工具、人工协助与任务控制 |
| `offerly/model.py` | 结构化 Function Calling、响应校验与用量回传 |
| `offerly/browser_connection.py` | 用户浏览器授权连接、标签页隔离、CDP 转发与提交保护 |
| `offerly/tata_program.py` | 当前申请标签页唤起塔塔并点击一键填入 |
| `offerly/history.py` | 企业和岗位投递记录、防重复 |
| `frontend/phone.py`、`remote.py`、`public/browser-*.js` | 手机配对、WebRTC 画面和浏览器输入 |
| `frontend/wechat.py`、`tunnel.py` | 微信通知、HTTPS 手机入口 |
| `frontend/job_*.py`、`job-search/` | 岗位采集与共享岗位库；`admin.html` 为管理入口 |
| `vendor/browser-use/` | 固定版本的官方 Agent；来源与提示词修改记录在 `UPSTREAM.json` |
| `tests/` | 现有流程、塔塔触发、上传顺序、手机画面、人工交接和防重复回归 |

任务只有一个启动入口：`POST /api/agent/tasks`。任务控制使用 `/api/tasks/{id}/{action}`；旧的预选岗位启动接口已移除。
手机画面与 Agent 的 CDP 会话分别管理，查看连接关闭不会串用 Agent 会话；浏览器断开后停止新增模型请求。
手机设置与招聘网页分属不同权限边界，Agent 只能操作本轮投递标签页，不能关闭或接管个人标签页。

## 数据与维护

`data/` 保存个人资料、附件、生成的 PDF、岗位库、投递历史和 DPAPI 加密配置，仅供本机用户使用。清理代码不清理这些数据。
`.venv/` 和 `frontend/job-search/node_modules/` 是运行依赖；`.cache/` 是可重建的临时文件。
`output/` 保留已交付的简历等文件，不属于运行依赖。岗位库管理入口为 `/admin.html`；`frontend/catalog_server.py` 提供可独立部署的只读共享岗位接口。

Browser Use 固定为 0.13.14，中文系统提示词直接维护在 `vendor/browser-use/browser_use/agent/system_prompts/system_prompt.md`，不追加另一套提示词。
保留已配置的模型，每轮最多 100 次模型请求；Agent 在当前决策中按 JD 定制表述，通过生成工具直接传入内容和资料依据；工具不额外请求模型。手机勾选后直接显示本岗简历页面，预览无需下载PDF。

本地回归：`./.venv/Scripts/python.exe -B -m unittest discover -s tests -v`。
前端回归：`node tests/test_book_frontend.mjs`、`node tests/test_phone_keyboard.mjs`。
测试使用临时浏览器、本地页面和脚本化模型，不调用付费模型。真实招聘网站、塔塔服务和手机设备仍需实测。
格式化与静态检查仅针对自有代码，不处理 `vendor/`：`ruff format offerly frontend tests`、`ruff check --no-cache offerly frontend tests`。

本项目部分通用组件源自原项目，保留根目录 MIT LICENSE。
