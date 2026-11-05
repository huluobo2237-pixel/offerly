"""Real SDK request serialization and AgentOutput validation without paid calls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI
from pydantic import BaseModel
from browser_use.agent.views import AgentOutput
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import SystemMessage, UserMessage
from browser_use import Tools
from offerly.model import AgentDeepSeek
from offerly.orchestration import GenerateResume
from offerly.services import ApplicationServices


class Empty(BaseModel):
    pass


class ModelProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_flash_alias_explicitly_disables_thinking_for_forced_tools(self):
        for name in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"):
            with self.subTest(model=name):
                model = AgentDeepSeek(model=name, thinking=False)
                self.assertEqual(
                    model._request_kwargs()["extra_body"]["thinking"],
                    {"type": "disabled"},
                )

    async def request(self, content, *, summary=False):
        tools = Tools()

        @tools.action("Read candidate profile", param_model=Empty)
        async def read_candidate_profile(params: Empty):
            pass

        @tools.action("Generate per-job resume", param_model=GenerateResume)
        async def generate_resume(params: GenerateResume):
            pass

        output = AgentOutput.type_with_custom_actions(
            tools.registry.create_action_model()
        )
        sent = []

        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "unchanged-model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": content if summary else None,
                                **(
                                    {}
                                    if summary
                                    else {
                                        "tool_calls": [
                                            {
                                                "id": "offline-call",
                                                "type": "function",
                                                "function": {
                                                    "name": output.__name__,
                                                    "arguments": content,
                                                },
                                            }
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 30,
                    },
                },
            )

        client = AsyncOpenAI(
            api_key="offline-only",
            base_url="https://example.invalid",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        model = AgentDeepSeek(model="unchanged-model", api_key="offline-only")
        usage = []
        with (
            patch.object(AgentDeepSeek, "_client", return_value=client),
            patch.object(AgentDeepSeek, "usage_received", side_effect=usage.append),
        ):
            messages = [
                SystemMessage(content="JSON protocol"),
                UserMessage(content="Read profile"),
            ]
            if summary:
                with tempfile.TemporaryDirectory() as directory:
                    owner = ApplicationServices(Path(directory)).agent
                    owner.job = {"progress": {"timeline": []}}
                    metered = owner.make_model(
                        {"model": "unchanged-model", "apiKey": "offline-only"}
                    )
                    result = await metered.ainvoke(messages)
                    self.assertEqual(owner.request_count, 1)
                    self.assertEqual(owner.prompt_tokens, 20)
            else:
                result = await model.ainvoke(messages, output_format=output)
        return result, sent, usage

    async def test_complete_json_output_uses_content_without_function_wrapper(self):
        result, sent, usage = await self.request(
            json.dumps({"action": [{"read_candidate_profile": {}}]})
        )
        self.assertEqual(len(result.completion.action), 1)
        self.assertNotIn("response_format", sent[0])
        self.assertEqual(
            sent[0]["tool_choice"]["function"]["name"],
            sent[0]["tools"][0]["function"]["name"],
        )
        self.assertEqual(sent[0]["model"], "unchanged-model")
        self.assertEqual(usage[0].total_tokens, 30)
        self.assertEqual(sent[0]["messages"][0]["content"], "JSON protocol")

    async def test_empty_objects_and_missing_actions_are_not_executed(self):
        for content in ["{}", '{"action":[]}', '{"action":[{"unknown_tool":{}}]}']:
            with self.subTest(content=content), self.assertRaises(ModelProviderError):
                await self.request(content)

    async def test_unambiguous_envelopes_are_repaired_without_a_second_request(self):
        for payload in [
            {"read_candidate_profile": {}},
            {"action": {"read_candidate_profile": {}}},
            {
                "rewrites": {
                    "summary:0": [
                        {
                            "text": "Relevant experience",
                            "sources": [
                                {"id": "summary:0", "quote": "Original experience"}
                            ],
                        }
                    ]
                }
            },
        ]:
            with self.subTest(payload=payload):
                result, sent, _ = await self.request(json.dumps(payload))
                self.assertEqual(len(sent), 1)
                self.assertEqual(len(result.completion.action), 1)

    async def test_parameter_errors_are_not_repaired_or_hidden(self):
        with self.assertRaisesRegex(ModelProviderError, r"generate_resume\.rewrites"):
            await self.request(
                '{"action":[{"generate_resume":{"rewrites":{"summary:0":"invalid bullet structure"}}}]}'
            )

    async def test_advertised_schema_accepts_a_nonempty_resume_rewrite(self):
        action = {
            "generate_resume": {
                "order": {"summary": ["summary:0"]},
                "rewrites": {
                    "summary:0": [
                        {
                            "text": "Relevant experience",
                            "sources": [
                                {"id": "summary:0", "quote": "Original experience"}
                            ],
                        }
                    ]
                },
                "focus": ["API development"],
            }
        }
        payload = {
            "evaluation_previous_goal": "Selected the job.",
            "memory": "The candidate has API experience.",
            "next_goal": "Generate the tailored resume.",
            "action": [action],
        }
        result, sent, _ = await self.request(json.dumps(payload))
        schema = sent[0]["tools"][0]["function"]["parameters"]
        Draft202012Validator(schema).validate(payload)
        self.assertEqual(
            result.completion.action[0].model_dump(exclude_unset=True), action
        )

    async def test_plain_summary_is_metered_without_action_validation(self):
        result, sent, _ = await self.request(
            "Verified the selected role.", summary=True
        )
        self.assertEqual(result.completion, "Verified the selected role.")
        self.assertNotIn("response_format", sent[0])

    async def test_invalid_configuration_stops_without_another_request(self):
        for status in (400, 401, 402, 403, 404, 422):
            with self.subTest(status=status):
                await self.assert_fatal_error_stops(status)

    async def assert_fatal_error_stops(self, status):
        with tempfile.TemporaryDirectory() as directory:
            owner = ApplicationServices(Path(directory)).agent
            owner.job = {"progress": {"timeline": []}}
            owner.agent = Mock()
            model = owner.make_model(
                {"model": "unchanged-model", "apiKey": "offline-only"}
            )
            provider = AsyncMock(
                side_effect=ModelProviderError("接口配置错误", status_code=status)
            )
            with patch.object(AgentDeepSeek, "ainvoke", provider):
                for _ in range(2):
                    with self.assertRaisesRegex(ModelProviderError, "接口配置错误"):
                        await model.ainvoke([UserMessage(content="Generate a resume")])
            provider.assert_awaited_once()
            owner.agent.stop.assert_called_once()
            self.assertEqual(owner.request_count, 1)
