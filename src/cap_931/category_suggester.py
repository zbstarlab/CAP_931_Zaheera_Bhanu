"""Bounded Groq category suggestion for the optional Streamlit helper."""

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .llm_client import generate_json
from .prompts import CATEGORY_PROMPT


class CategorySuggestionError(ValueError):
    """The category request or model response could not be safely used."""


class CategoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=3, max_length=60)

    @field_validator("category")
    @classmethod
    def specific_short_category(cls, value: str) -> str:
        value = " ".join(value.split()).strip(" .,:;!?")
        words = value.split()
        if not 2 <= len(words) <= 6:
            raise ValueError("Category must contain 2 to 6 words")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &/+\-]*", value):
            raise ValueError("Category contains unsupported characters")
        if value.lower() in {
            "business software",
            "business technology",
            "software platform",
            "technology solution",
        }:
            raise ValueError("Category is too broad")
        return value


def suggest_product_category(
    product_name: str,
    value_proposition: str,
    generator: Callable[[str, str], dict] = generate_json,
) -> str:
    """Return one validated suggestion without modifying the user's input."""
    product_name = product_name.strip()
    value_proposition = value_proposition.strip()
    if not product_name or not value_proposition:
        raise CategorySuggestionError(
            "Enter a product name and value proposition before requesting a category."
        )
    context = json.dumps(
        {
            "product_name": product_name[:120],
            "value_proposition": value_proposition[:600],
        },
        ensure_ascii=False,
    )
    try:
        response = CategoryResponse.model_validate(generator(CATEGORY_PROMPT, context))
    except (ValidationError, TypeError, ValueError) as exc:
        raise CategorySuggestionError(
            "The model did not return a usable product category."
        ) from exc
    return response.category
