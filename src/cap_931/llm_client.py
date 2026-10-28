"""Groq adapter used by the CAP 931 research agents."""
import json
import os
from dataclasses import dataclass
from time import perf_counter

from dotenv import load_dotenv

load_dotenv()
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
DEFAULT_MAX_COMPLETION_TOKENS = 1_200

class LLMError(RuntimeError):
    """The model request or response could not be used."""


@dataclass(frozen=True)
class GenerationResult:
    """Parsed JSON plus non-sensitive timing and token measurements."""

    value: dict
    latency_seconds: float
    usage: dict[str, int]


def _usage_dict(usage: object) -> dict[str, int]:
    fields = ("prompt_tokens", "completion_tokens", "total_tokens")
    values = {}
    for field in fields:
        value = (
            usage.get(field)
            if isinstance(usage, dict)
            else getattr(usage, field, None)
        )
        if isinstance(value, int):
            values[field] = value
    return values


def generate_json_with_metrics(
    system_prompt: str,
    user_prompt: str,
    *,
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
) -> GenerationResult:
    """Ask Groq for JSON and retain non-sensitive usage measurements."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is missing. Set it in the local .env file.")
    try:
        from groq import Groq
        client = Groq(api_key=key, timeout=40.0, max_retries=2)
        started = perf_counter()
        reply = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            reasoning_effort="low",
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_object"},
        )
        latency = perf_counter() - started
        content = reply.choices[0].message.content or ""
        value = json.loads(content)
        if not isinstance(value, dict):
            raise LLMError("The model did not return a JSON object.")
        return GenerationResult(
            value=value,
            latency_seconds=latency,
            usage=_usage_dict(getattr(reply, "usage", None)),
        )
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise LLMError("The model response was not valid JSON.") from exc
    except LLMError:
        raise
    except Exception as exc:
        # Do not echo exceptions that could include request contents or secrets.
        if getattr(exc, "status_code", None) == 429:
            raise LLMError("Groq rate limit reached.") from exc
        raise LLMError("Groq request failed. Check your connection, key, model and rate limits.") from exc


def generate_json(system_prompt: str, user_prompt: str) -> dict:
    """Ask Groq for JSON. Preserve the original agent-generator interface."""
    return generate_json_with_metrics(system_prompt, user_prompt).value
