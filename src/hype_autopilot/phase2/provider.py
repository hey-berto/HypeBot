from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from hype_autopilot.phase2.config import Phase2Config
from hype_autopilot.phase2.credentials import load_phase2_openai_environment
from hype_autopilot.phase2.models import ProviderResponse, structured_output_model


class ProviderError(RuntimeError):
    pass


class ProviderTimeout(ProviderError):
    pass


class LLMProvider(Protocol):
    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse: ...


def output_json_schema(
    output_schema_version: str = "LLM_OUTPUT_V1",
) -> dict[str, Any]:
    schema = structured_output_model(output_schema_version).model_json_schema()

    def strict(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)

    strict(schema)
    return schema


@dataclass(frozen=True)
class OpenAIResponsesProvider:
    model: str
    model_version: str
    reasoning_effort: str
    input_cost_per_million_usd: float
    cached_input_cost_per_million_usd: float
    output_cost_per_million_usd: float
    output_schema_version: str = "LLM_OUTPUT_V1"
    api_key_env: str = "OPENAI_API_KEY"
    endpoint: str = "https://api.openai.com/v1/responses"

    def invoke(
        self, *, prompt: str, snapshot_json: str, timeout_seconds: int
    ) -> ProviderResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ProviderError(f"{self.api_key_env} is not configured")
        started = datetime.now(UTC)
        body = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "store": False,
            "tools": [],
            "input": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": snapshot_json},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": (
                        "llm_v1_decision"
                        if self.output_schema_version == "LLM_OUTPUT_V1"
                        else "llm_v2_decision"
                    ),
                    "strict": True,
                    "schema": output_json_schema(self.output_schema_version),
                }
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderTimeout("OpenAI request timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout("OpenAI request timed out") from exc
            raise ProviderError("OpenAI request failed") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                "OpenAI returned an unreadable response envelope"
            ) from exc
        ended = datetime.now(UTC)
        try:
            output = payload["output"]
            tool_calls = sum(
                1 for item in output if item.get("type") not in {"message", "reasoning"}
            )
            text_items = [
                part["text"]
                for item in output
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            ]
            raw_output = "".join(text_items)
            usage = payload.get("usage", {})
            input_details = usage.get("input_tokens_details", {})
            input_tokens = int(usage.get("input_tokens", 0))
            cached_tokens = int(input_details.get("cached_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cost = (
                (input_tokens - cached_tokens) * self.input_cost_per_million_usd
                + cached_tokens * self.cached_input_cost_per_million_usd
                + output_tokens * self.output_cost_per_million_usd
            ) / 1_000_000
            return ProviderResponse(
                raw_output=raw_output,
                model=str(payload.get("model", self.model)),
                model_version=self.model_version,
                request_started_at=started,
                request_ended_at=ended,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                tool_calls_count=tool_calls,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("OpenAI response was missing required fields") from exc


def openai_provider_from_config(
    config: Phase2Config, *, workspace_root: str
) -> OpenAIResponsesProvider:
    if not load_phase2_openai_environment(workspace_root):
        raise ProviderError(
            "OPENAI_API_KEY is not configured in the Phase 2 environment"
        )
    return OpenAIResponsesProvider(
        model=config.model,
        model_version=config.model_version,
        reasoning_effort=config.reasoning_effort,
        input_cost_per_million_usd=config.input_cost_per_million_usd,
        cached_input_cost_per_million_usd=config.cached_input_cost_per_million_usd,
        output_cost_per_million_usd=config.output_cost_per_million_usd,
        output_schema_version=config.output_schema_version,
    )
