"""Provider tests. Nothing here starts a model or touches the network.

`OllamaProvider` and `HostedProvider` are exercised through `respx`, which intercepts
httpx; the one test that needs a real local model is marked `live` and excluded from the
default run (plan section 29).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from evergrove_agent.config import Settings
from evergrove_agent.llm import (
    FakeProvider,
    HostedProvider,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    OllamaProvider,
    ToolSpec,
    build_provider,
)
from evergrove_agent.llm.fake_provider import ExhaustedScript
from evergrove_agent.llm.hosted_provider import to_gemini_schema
from evergrove_agent.schemas import FocusPreparationReport


class TestFakeProvider:
    async def test_returns_scripted_responses_in_order(self) -> None:
        provider = FakeProvider(["first", "second", "third"])

        first = await provider.generate([Message(role="user", content="go")])
        second = await provider.generate([Message(role="user", content="go")])
        third = await provider.generate([Message(role="user", content="go")])

        assert [first.text, second.text, third.text] == ["first", "second", "third"]
        assert provider.remaining == 0

    async def test_raises_once_the_script_runs_out(self) -> None:
        provider = FakeProvider(["only one"])
        await provider.generate([Message(role="user", content="go")])

        with pytest.raises(ExhaustedScript):
            await provider.generate([Message(role="user", content="go")])

    async def test_records_what_it_was_asked(self) -> None:
        provider = FakeProvider(["{}"])

        await provider.generate(
            [Message(role="user", content="prepare a session")],
            schema=FocusPreparationReport,
            temperature=0.0,
        )

        assert provider.calls[0].schema_name == "FocusPreparationReport"
        assert provider.calls[0].messages[0].content == "prepare a session"

    async def test_can_replay_a_prepared_response_object(self) -> None:
        scripted = LLMResponse(text="{}", model="m", provider="fake", duration_ms=7)
        provider = FakeProvider([scripted])

        assert (await provider.generate([])).duration_ms == 7

    def test_satisfies_the_provider_protocol(self) -> None:
        assert isinstance(FakeProvider([]), LLMProvider)


class TestOllamaProvider:
    @respx.mock
    async def test_sends_the_json_schema_for_constrained_decoding(
        self, settings: Settings
    ) -> None:
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(
                200, json={"model": "qwen3:4b", "message": {"content": "{}"}, "done_reason": "stop"}
            )
        )
        provider = OllamaProvider(settings)

        response = await provider.generate(
            [Message(role="user", content="prepare")],
            schema=FocusPreparationReport,
            temperature=0.0,
        )

        sent: dict[str, Any] = json.loads(route.calls.last.request.content)
        assert sent["format"] == FocusPreparationReport.model_json_schema()
        assert sent["stream"] is False
        assert sent["options"] == {"temperature": 0.0, "num_ctx": settings.num_ctx}
        assert sent["keep_alive"] == settings.local_keep_alive
        assert response.provider == "ollama"

    @respx.mock
    async def test_passes_tools_through(self, settings: Settings) -> None:
        route = respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(200, json={"message": {"content": ""}})
        )
        tool = ToolSpec(name="web_search", description="Search the web", parameters={"type": "object"})

        await OllamaProvider(settings).generate([], tools=[tool])

        sent = json.loads(route.calls.last.request.content)
        assert sent["tools"][0]["function"]["name"] == "web_search"

    @respx.mock
    async def test_reads_tool_calls_back(self, settings: Settings) -> None:
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "web_search", "arguments": {"query": "btree"}}}
                        ],
                    }
                },
            )
        )

        response = await OllamaProvider(settings).generate([])

        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].arguments == {"query": "btree"}

    @respx.mock
    async def test_an_unreachable_server_is_an_llm_error(self, settings: Settings) -> None:
        respx.post("http://localhost:11434/api/chat").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        with pytest.raises(LLMError, match="could not reach Ollama"):
            await OllamaProvider(settings).generate([])

    @respx.mock
    async def test_a_missing_model_explains_how_to_pull_it(self, settings: Settings) -> None:
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=httpx.Response(404, text='{"error":"model not found"}')
        )

        with pytest.raises(LLMError, match="ollama pull"):
            await OllamaProvider(settings).generate([])

    @pytest.mark.live
    async def test_produces_a_valid_report_from_a_real_model(self, settings: Settings) -> None:
        """Requires a running Ollama with the configured model. Run with `-m live`."""
        from evergrove_agent.llm.prompts import render_prompt

        prompt = render_prompt(
            "finalise",
            task_title="Learn PostgreSQL indexing",
            task_description="(none given)",
            session_minutes=25,
            max_topics=5,
            research_context="No research was performed.",
        )

        response = await OllamaProvider(settings).generate(
            [Message(role="user", content=prompt)],
            schema=FocusPreparationReport,
        )

        FocusPreparationReport.model_validate_json(response.text)


class TestHostedProvider:
    async def test_refuses_to_run_without_a_key(self, settings: Settings) -> None:
        with pytest.raises(LLMError, match="GOOGLE_API_KEY"):
            await HostedProvider(settings).generate([])

    @respx.mock
    async def test_sends_the_key_as_a_header_and_the_schema_as_response_schema(self) -> None:
        settings = Settings(_env_file=None, google_api_key="test-key")
        route = respx.post(
            f"{settings.hosted_api_base}/models/{settings.hosted_model}:generateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}
                    ],
                    "modelVersion": settings.hosted_model,
                },
            )
        )

        response = await HostedProvider(settings).generate(
            [Message(role="system", content="be terse"), Message(role="user", content="prepare")],
            schema=FocusPreparationReport,
        )

        request = route.calls.last.request
        sent = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "test-key" not in str(request.url)  # never in a query string
        assert sent["systemInstruction"]["parts"][0]["text"] == "be terse"
        assert sent["generationConfig"]["responseMimeType"] == "application/json"
        assert sent["generationConfig"]["responseSchema"]["type"] == "object"
        assert response.provider == "hosted"
        assert response.finish_reason == "STOP"

    def test_api_key_is_not_printed_by_repr(self) -> None:
        settings = Settings(_env_file=None, google_api_key="super-secret")

        assert "super-secret" not in repr(settings)


class TestGeminiSchemaTranslation:
    """Gemini takes an OpenAPI subset, not full JSON Schema, so ours has to be translated."""

    def test_inlines_refs_and_drops_unsupported_keywords(self) -> None:
        translated = to_gemini_schema(FocusPreparationReport.model_json_schema())
        resource = translated["properties"]["resources"]["items"]

        assert "$defs" not in translated
        assert "$ref" not in json.dumps(translated)
        assert "additionalProperties" not in json.dumps(translated)
        assert resource["properties"]["url"]["type"] == "string"
        assert "format" not in resource["properties"]["url"]  # `uri` is not supported

    def test_collapses_optional_fields_into_nullable(self) -> None:
        translated = to_gemini_schema(FocusPreparationReport.model_json_schema())

        assert translated["properties"]["practice"]["nullable"] is True
        assert translated["properties"]["practice"]["type"] == "object"

    def test_keeps_enums_and_list_bounds(self) -> None:
        translated = to_gemini_schema(FocusPreparationReport.model_json_schema())
        topics = translated["properties"]["topics_to_cover"]
        authority = translated["properties"]["resources"]["items"]["properties"]["authority"]

        assert (topics["minItems"], topics["maxItems"]) == (2, 8)
        assert "official" in authority["enum"]

    def test_keeps_date_time_format(self) -> None:
        translated = to_gemini_schema(FocusPreparationReport.model_json_schema())

        assert translated["properties"]["generated_at"]["format"] == "date-time"


class TestBuildProvider:
    def test_local_is_the_default(self, settings: Settings) -> None:
        assert isinstance(build_provider("supervisor", settings), OllamaProvider)

    def test_role_can_be_pointed_at_the_hosted_provider(self) -> None:
        settings = Settings(_env_file=None, appraiser_provider="hosted")

        assert isinstance(build_provider("appraiser", settings), HostedProvider)
        assert isinstance(build_provider("researcher", settings), OllamaProvider)

    def test_override_wins_over_configuration(self, settings: Settings) -> None:
        assert isinstance(build_provider("supervisor", settings, override="hosted"), HostedProvider)

    def test_an_unvalidated_provider_name_is_refused(self, settings: Settings) -> None:
        """The seam `Settings`' own validation cannot cover (Day 5 T3).

        `ProviderName` catches a typo when `Settings` is constructed, but `model_copy(update=)`
        and `setattr` both skip validation — and those are precisely how `tools/cli.py` and
        `main.py` apply their overrides. This is the factory refusing to invent a default for a
        value nobody configured, so the failure surfaces here rather than as a local run that
        the operator believed was hosted.
        """
        unvalidated = settings.model_copy(update={"appraiser_provider": "hostd"})

        with pytest.raises(ValueError, match="hostd"):
            build_provider("appraiser", unvalidated)
