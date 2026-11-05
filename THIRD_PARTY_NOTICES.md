# 第三方组件声明

Offerly 使用开源组件构建求职任务应用。业务编排、岗位与简历服务、连接助手和手机协作代码与依赖共同组成产品；下列第三方能力不作为 Offerly 独立原创能力宣称。

## 浏览器自动化运行时

项目包含 [Browser Use](https://github.com/browser-use/browser-use) 的固定版本源码，位于 `vendor/browser-use/`。固定提交、文件摘要及本地修改记录保存在该目录的 `UPSTREAM.json`。其 MIT 许可证保存在 `vendor/browser-use/LICENSE`，原始版权声明保留。当前登记的运行时本地修改为面向求职任务的中文系统提示词。

## 其他开源依赖

Python 依赖及锁定版本见 `pyproject.toml` 和 `uv.lock`，岗位采集依赖见 `frontend/job-search/package.json` 与 `package-lock.json`。各组件的版权和许可证归其作者，分发与使用应遵守相应条款。

可选的 cloudflared 工具不包含在本仓库中，使用者可从 [Cloudflare 官方项目](https://github.com/cloudflare/cloudflared) 获取。缺少该工具时，本地启动脚本不会自动建立对应公网手机隧道。

## 外部服务与插件

DeepSeek、Tavily、塔塔网申与可选公众号通知属于外部服务或插件。Offerly 不包含这些服务的服务端实现，也不分发塔塔插件源码。用户需要遵守各自服务条款；外部服务的费用、可用性、授权与站点支持范围由其运营方决定。

仓库不包含用户账号、API 密钥、简历附件、招聘网站登录状态或投递记录。
