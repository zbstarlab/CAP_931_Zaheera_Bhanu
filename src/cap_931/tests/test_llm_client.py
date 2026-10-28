from types import SimpleNamespace

import groq

from cap_931.llm_client import generate_json, generate_json_with_metrics


def test_groq_request_uses_bounded_low_reasoning_json_mode(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"findings": [], "gaps": []}')
            usage = SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)], usage=usage
            )

    class FakeGroq:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(groq, "Groq", FakeGroq)

    result = generate_json("system", "user")

    assert result == {"findings": [], "gaps": []}
    assert captured["reasoning_effort"] == "low"
    assert captured["max_completion_tokens"] == 1200
    assert captured["response_format"] == {"type": "json_object"}

    measured = generate_json_with_metrics(
        "system", "user", max_completion_tokens=1800
    )
    assert measured.value == {"findings": [], "gaps": []}
    assert measured.latency_seconds >= 0
    assert measured.usage == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }
    assert captured["max_completion_tokens"] == 1800
