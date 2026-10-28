"""Validated internal shapes for research findings."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PRODUCT_DOCUMENT_CHARS = 8_000


class ProductDocument(BaseModel):
    """Bounded text extracted from an optional seller product document."""

    filename: str = Field(min_length=1, max_length=255)
    file_type: Literal["pdf", "txt"]
    content: str = Field(min_length=1, max_length=MAX_PRODUCT_DOCUMENT_CHARS)
    truncated: bool = False


class EvidenceExcerpt(BaseModel):
    """Exact, source-addressed text offered in support of one factual claim."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=2, max_length=20)
    excerpt: str = Field(min_length=8, max_length=320)


class Finding(BaseModel):
    """A verified fact plus clearly advisory sales relevance."""

    claim: str = Field(min_length=8, max_length=450)
    source_ids: list[str] = Field(min_length=1, max_length=4)
    evidence: list[EvidenceExcerpt] = Field(default_factory=list, max_length=4)
    relevance: str = Field(min_length=3, max_length=300)

    @field_validator("source_ids")
    @classmethod
    def no_duplicate_ids(cls, ids: list[str]) -> list[str]:
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source IDs are not allowed")
        return ids


class AgentResult(BaseModel):
    agent: str
    findings: list[Finding] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    suggested_questions: list[str] = Field(default_factory=list, max_length=3)
