"""Browser Use Function Calling with native schemas and usage accounting."""

import json
import logging
from typing import get_args

from openai import APIError, RateLimitError
from pydantic import ValidationError
from browser_use.llm.deepseek.chat import ChatDeepSeek
from browser_use.llm.deepseek.serializer import DeepSeekMessageSerializer
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage

logger = logging.getLogger(__name__)


def describe_validation_error(error):
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    # The action union reports errors from every unrelated tool before the
    # selected tool's actual parameter errors.
    parameters = [
        issue
        for issue in errors
        if issue["loc"][:1] == ("action",)
        and len(issue["loc"]) >= 4
        and not (
            len(issue["loc"]) == 4 and issue["type"] in ("missing", "extra_forbidden")
        )
    ]
    return "; ".join(
        ".".join(map(str, issue["loc"])) + ":" + issue["type"]
        for issue in (parameters or errors)[:3]
    )[:700]


def validate_agent_output(content, output_format):
    """Repair only an unambiguous JSON envelope; keep all parameter validation."""
    data = json.loads(content)
    action_type = get_args(output_format.model_fields["action"].annotation)[0]
    variants = (
        get_args(action_type.model_fields["root"].annotation)
        if "root" in action_type.model_fields
        else (action_type,)
    )
    known = {name for variant in variants for name in variant.model_fields}
    if isinstance(data, dict):
        if isinstance(data.get("action"), dict):
            data = {**data, "action": [data["action"]]}
        elif "action" not in data:
            tools = set(data) & known
            metadata = set(output_format.model_fields) - {"action"}
            if len(tools) == 1 and set(data) <= tools | metadata:
                name = tools.pop()
                data = {
                    **{k: v for k, v in data.items() if k != name},
                    "action": [{name: data[name]}],
                }
            elif (
                "generate_resume" in known
                and data.get("rewrites")
                and set(data) <= {"order", "rewrites", "focus"}
            ):
                data = {"action": [{"generate_resume": data}]}
    parsed = output_format.model_validate(data)
    if not parsed.action:
        raise ValueError("action 不能为空")
    return parsed


class AgentDeepSeek(ChatDeepSeek):
    """Use the upstream AgentOutput function envelope without closing map fields."""

    def _supports_thinking(self):
        # The current public Flash alias no longer contains the v4 prefix.
        return self.model.lower() == "deepseek-flash" or super()._supports_thinking()

    def usage_received(self, usage):
        pass

    async def ainvoke(self, messages, output_format=None, **kwargs):
        payload = DeepSeekMessageSerializer.serialize_messages(messages)
        options = self._request_kwargs()
        if output_format is not None:
            # Keep map-valued resume fields open in the function schema.
            schema = output_format.model_json_schema()
            options["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": output_format.__name__,
                        "description": "Return the complete AgentOutput with executable actions.",
                        "parameters": schema,
                    },
                }
            ]
            options["tool_choice"] = {
                "type": "function",
                "function": {"name": output_format.__name__},
            }
        try:
            async with self._client() as client:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=payload,
                    **options,
                )
        except RateLimitError:
            raise ModelRateLimitError(
                "模型服务限流，请稍后继续。", model=self.name
            ) from None
        except APIError as exc:
            status = getattr(exc, "status_code", None) or 502
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, dict) else {}
            message = str(error.get("message", "")) if isinstance(error, dict) else ""
            if status == 400 and "Thinking mode does not support this tool_choice" in message:
                reason = "模型请求参数冲突（400）：思考模式不支持指定工具调用，请检查服务端思考模式配置。"
            else:
                reason = {
                    400: "模型请求参数不兼容（400），请检查服务端接口配置。",
                    401: "模型密钥无效或已失效（401），请更新模型配置。",
                    402: "DeepSeek 余额不足（402），充值后可交还 Agent 继续。",
                    403: "模型接口拒绝访问（403），请检查密钥权限。",
                    404: "模型或接口地址不存在（404），请检查模型名称与接口地址。",
                    422: "模型请求参数校验失败（422），请检查服务端接口配置。",
                }.get(status, f"模型服务请求失败（HTTP {status}），请稍后继续。")
            logger.warning("DeepSeek request rejected: status=%s", status)
            raise ModelProviderError(
                reason,
                status_code=status,
                model=self.name,
            ) from None
        raw_usage = response.usage
        usage = (
            ChatInvokeUsage(
                prompt_tokens=raw_usage.prompt_tokens,
                completion_tokens=raw_usage.completion_tokens,
                total_tokens=raw_usage.total_tokens,
                prompt_cached_tokens=getattr(
                    raw_usage, "prompt_cache_hit_tokens", None
                ),
                prompt_cache_creation_tokens=None,
                prompt_image_tokens=None,
            )
            if raw_usage
            else None
        )
        if usage:
            self.usage_received(usage)
        choice = response.choices[0]
        content = choice.message.content or ""
        if output_format is None:
            return ChatInvokeCompletion(
                completion=content, usage=usage, stop_reason=choice.finish_reason
            )
        try:
            calls = choice.message.tool_calls or []
            if len(calls) != 1 or calls[0].function.name != output_format.__name__:
                raise ValueError("缺少完整 AgentOutput 工具调用")
            content = calls[0].function.arguments
            parsed = validate_agent_output(content, output_format)
        except (ValidationError, ValueError) as exc:
            details = (
                describe_validation_error(exc)
                if isinstance(exc, ValidationError)
                else str(exc)[:200]
            )
            logger.warning(
                "DeepSeek AgentOutput rejected: finish=%s, chars=%s, validation=%s",
                choice.finish_reason,
                len(content),
                details,
            )
            raise ModelProviderError(
                "模型动作格式错误，本次未执行浏览器动作："
                + details
                + "。请按当前工具的 JSON Schema 修正对应字段后再试。",
                model=self.name,
            ) from None
        return ChatInvokeCompletion(
            completion=parsed, usage=usage, stop_reason=choice.finish_reason
        )
