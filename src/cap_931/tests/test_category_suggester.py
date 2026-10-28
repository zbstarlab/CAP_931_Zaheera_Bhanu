import pytest

from cap_931.category_suggester import (
    CategorySuggestionError,
    suggest_product_category,
)
from cap_931.llm_client import LLMError


def test_valid_category_suggestion_uses_untrusted_bounded_context():
    captured = {}

    def generator(system, user):
        captured["system"] = system
        captured["user"] = user
        return {"category": "AI Sales Intelligence"}

    category = suggest_product_category(
        "SalesIntel AI", "Source-grounded account preparation", generator
    )

    assert category == "AI Sales Intelligence"
    assert "untrusted" in captured["system"]
    assert "SalesIntel AI" in captured["user"]


@pytest.mark.parametrize(
    "response",
    [
        {"category": "Software Platform"},
        {"category": "Software"},
        {"category": "A category with far too many words for this field"},
        {"category": "AI Sales Intelligence", "explanation": "extra"},
        {},
    ],
)
def test_invalid_category_response_is_rejected(response):
    with pytest.raises(CategorySuggestionError, match="usable product category"):
        suggest_product_category("Product", "Value", lambda *_: response)


def test_category_api_error_remains_safe_for_ui_handling():
    def unavailable(*_):
        raise LLMError("Groq request failed.")

    with pytest.raises(LLMError, match="Groq request failed"):
        suggest_product_category("Product", "Value", unavailable)


def test_category_requires_product_and_value_proposition():
    with pytest.raises(CategorySuggestionError, match="product name"):
        suggest_product_category("", "", lambda *_: {})
