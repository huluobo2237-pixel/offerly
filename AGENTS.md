# Offerly

- 本项目包含前端、岗位库、附件、简历 PDF、手机配对及通知服务。
- 新 Agent 位于 offerly/agent.py，通过 offerly/services.py 的 AgentBackend 边界接入。
- 仅使用 vendor/browser-use/ 内经校验的官方源码，不导入旧项目，不引用旧项目绝对路径。
- 按用户要求，直接维护 vendor/browser-use/browser_use/agent/system_prompts/system_prompt.md 的中文网申系统提示词，不使用 extend_system_message 追加。保留上游页面采集、工具机制、JSON 字段与参数规范；源码本地修改记录在 UPSTREAM.json。
- 使用 uv 管理 Python 依赖，保留 uv.lock；岗位采集依赖使用 npm ci。
- 内部输入输出使用 Pydantic v2。不要擅自更换已配置的模型名称。
- 用户数据和加密配置保存在 data/，不得提交到 Git 或打印密钥。
- 不生成无关报告、示例、截图或复制旧 Agent 的测试和运行产物。

- 任务只通过 /api/agent/tasks 启动；不要恢复旧的预选岗位执行器或旧档案页面。
- Page.screencastFrameAck 的 params.sessionId 是帧编号，不是 CDP 会话编号；会话权限校验使用外层 sessionId。
- 格式化和静态检查只针对自有代码，禁止批量格式化 vendor/；保存资料的兼容转换必须保留。
