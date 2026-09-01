from __future__ import annotations

import json

from hype_autopilot.phase2.credentials import load_phase2_openai_environment
from hype_autopilot.phase2.provider import OpenAIResponsesProvider


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_env_loader_reads_ignored_workspace_key_without_override(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=synthetic-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert load_phase2_openai_environment(tmp_path)
    assert __import__("os").environ["OPENAI_API_KEY"] == "synthetic-key"
    monkeypatch.setenv("OPENAI_API_KEY", "explicit-key")
    assert load_phase2_openai_environment(tmp_path)
    assert __import__("os").environ["OPENAI_API_KEY"] == "explicit-key"


def test_openai_provider_sends_zero_tools_strict_schema_and_accounts_cost(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHttpResponse(
            {
                "model": "gpt-5.6-terra",
                "output": [
                    {"type": "reasoning"},
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"decision":"NO_TRADE"}'}
                        ],
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 30,
                },
            }
        )

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(
        model="gpt-5.6-terra",
        model_version="gpt-5.6-terra",
        reasoning_effort="medium",
        input_cost_per_million_usd=2,
        cached_input_cost_per_million_usd=0.2,
        output_cost_per_million_usd=12,
    )
    response = provider.invoke(prompt="prompt", snapshot_json="{}", timeout_seconds=60)
    body = captured["body"]
    assert body["tools"] == []
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert captured["timeout"] == 60
    assert response.tool_calls_count == 0
    assert response.raw_output == '{"decision":"NO_TRADE"}'
    assert response.cost_usd == (80 * 2 + 20 * 0.2 + 30 * 12) / 1_000_000
