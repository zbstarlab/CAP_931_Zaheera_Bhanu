"""Reproducible baseline comparison and evaluation helpers for Phase 7."""

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from .agents import (
    MAX_SOURCES,
    build_context,
    competitor_evidence_available,
    keep_supported_findings,
    normalize_evidence_text,
    select_sources_for_role,
)
from .llm_client import MODEL, LLMError, generate_json
from .prompts import (
    BASELINE_PROMPT,
    BASELINE_PROMPT_VERSION,
    GROUNDED_SALES_PROMPT_VERSION,
    GROUNDED_SALES_PROMPTS,
    HISTORICAL_SPECIALIZED_PROMPT_VERSION,
    HISTORICAL_SPECIALIZED_PROMPTS,
    PROMPTS,
    SPECIALIZED_PROMPT_VERSION,
)
from .schemas import AgentResult, EvidenceExcerpt, Finding

ROLES = ("company", "competitor", "leadership")
BASELINE_MAX_COMPLETION_TOKENS = 1_800

RUBRIC = {
    "factual_accuracy": {
        1: "Most claims are contradicted, fabricated, or unsupported.",
        2: "Multiple material claims are unsupported or misleading.",
        3: "Core claims are plausible, but support is mixed or includes inference.",
        4: "Claims are supported with only minor imprecision or cautious inference.",
        5: "Every claim is directly supported, or the output correctly abstains.",
    },
    "source_support": {
        1: "Citations are absent, invalid, or do not support most claims.",
        2: "Some citations resolve, but several do not support the attached claims.",
        3: "Most citations support part of each claim, with noticeable overreach.",
        4: "Citations generally support claims with only minor ambiguity.",
        5: "Every citation resolves and the cited excerpt directly supports its claim.",
    },
    "product_relevance": {
        1: "Output is unrelated to the product, buyer, or value proposition.",
        2: "Connections are mostly generic or speculative.",
        3: "Some findings are product-relevant, but others are generic.",
        4: "Most findings connect clearly to the product and target buyer.",
        5: "All findings make specific, evidence-aware product connections.",
    },
    "category_coverage": {
        1: "Required categories are mostly omitted without explaining evidence gaps.",
        2: "Only one category is useful; other categories are weak or unexplained.",
        3: "All categories appear, but useful evidence or gap handling is uneven.",
        4: "All categories have useful findings or clear evidence-gap explanations.",
        5: "All categories are thorough, distinct, and appropriately evidence-bounded.",
    },
    "sales_usefulness": {
        1: "Output would mislead or provide no practical preparation value.",
        2: "Output has limited actionable value and needs major verification.",
        3: "Output offers some useful preparation points but remains generic.",
        4: "Output is concise, trustworthy, and useful for a sales conversation.",
        5: "Output provides highly actionable, prioritized, source-grounded guidance.",
    },
}

GROUNDED_RUBRIC = {
    "factual_accuracy": "Are factual claims correct within the frozen excerpts?",
    "evidence_support": "Do the cited excerpts directly support the claims?",
    "product_relevance": "Are sales suggestions specific and evidence-aware?",
    "completeness": "Are facts, relevance, questions, and gaps all addressed?",
    "sales_usefulness": "Would the result help prepare an initial meeting?",
    "format_adherence": "Does the output follow the requested separated structure?",
    "unsupported_assumptions": (
        "Does the result avoid unstated needs, intent, relationships, or priorities?"
    ),
}


class BaselineCategory(BaseModel):
    """One category in the single-call baseline response."""

    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=5)


class BaselineOutput(BaseModel):
    """Strict three-category schema expected from the baseline prompt."""

    model_config = ConfigDict(extra="forbid")

    company: BaselineCategory
    competitor: BaselineCategory
    leadership: BaselineCategory


class GroundedFact(BaseModel):
    """A fact kept separate from its possible sales relevance."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(pattern=r"^F[1-4]$")
    claim: str = Field(min_length=8, max_length=450)
    source_ids: list[str] = Field(min_length=1, max_length=4)
    evidence: list[EvidenceExcerpt] = Field(min_length=1, max_length=4)


class GroundedRelevance(BaseModel):
    """An explicitly advisory interpretation linked to one verified fact."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(pattern=r"^F[1-4]$")
    suggestion: str = Field(min_length=3, max_length=300)


class GroundedRoleOutput(BaseModel):
    """Separated structure required by the grounded-sales prompt variant."""

    model_config = ConfigDict(extra="forbid")

    verified_facts: list[GroundedFact] = Field(default_factory=list, max_length=4)
    possible_sales_relevance: list[GroundedRelevance] = Field(
        default_factory=list, max_length=4
    )
    suggested_discovery_questions: list[str] = Field(
        default_factory=list, max_length=3
    )
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)


class MechanicalEvaluation(BaseModel):
    """Properties that can be checked without judging claim entailment."""

    schema_compliant: bool
    finding_count: int
    citation_reference_count: int
    valid_citation_reference_count: int
    citation_id_validity_rate: float = Field(ge=0, le=1)
    provenance_violation_count: int
    categories_with_findings: int
    categories_with_explicit_gaps: int
    category_response_rate: float = Field(ge=0, le=1)
    evidence_excerpt_count: int = 0
    valid_evidence_excerpt_count: int = 0
    evidence_excerpt_validity_rate: float = Field(default=1.0, ge=0, le=1)
    findings_missing_evidence: int = 0
    suggested_question_count: int = 0


class ManualScores(BaseModel):
    """Human review scores; citation support is not inferred from ID validity."""

    case_id: str
    approach: Literal["baseline", "specialized"]
    reviewer: str
    factual_accuracy: int = Field(ge=1, le=5)
    source_support: int = Field(ge=1, le=5)
    product_relevance: int = Field(ge=1, le=5)
    category_coverage: int = Field(ge=1, le=5)
    sales_usefulness: int = Field(ge=1, le=5)
    unsupported_claims: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GroundedManualScores(BaseModel):
    """Seven-part manual review for the final prompt comparison."""

    case_id: str
    approach: Literal["specialized_v3", "grounded_sales"]
    reviewer: str
    factual_accuracy: int = Field(ge=1, le=5)
    evidence_support: int = Field(ge=1, le=5)
    product_relevance: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    sales_usefulness: int = Field(ge=1, le=5)
    format_adherence: int = Field(ge=1, le=5)
    unsupported_assumptions: int = Field(ge=1, le=5)
    unsupported_claims: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ApproachRecord(BaseModel):
    """Serializable measurements and outputs for one experimental approach."""

    approach: Literal[
        "baseline", "specialized", "specialized_v3", "grounded_sales"
    ]
    attempt: int = Field(default=1, ge=1)
    prompt_version: str
    prompt_sha256: str
    model: str
    settings: dict[str, Any]
    selected_source_ids: list[str]
    raw_model_outputs: list[dict]
    output: dict[str, AgentResult]
    api_latency_seconds: float = Field(ge=0)
    wall_time_seconds: float = Field(ge=0)
    usage: dict[str, int]
    mechanical: MechanicalEvaluation
    errors: list[str] = Field(default_factory=list)
    rate_limit_events: list[str] = Field(default_factory=list)


class ExperimentCaseRecord(BaseModel):
    """Complete reproducible result for one frozen input case."""

    case_id: str
    description: str
    source_snapshot_sha256: str
    request: dict[str, Any]
    source_ids: list[str]
    retrieval_warnings: list[str]
    approaches: list[ApproachRecord]


class ExperimentArtifact(BaseModel):
    """Top-level artifact written by the experiment runner."""

    schema_version: str
    created_at: str
    cases: list[ExperimentCaseRecord]


def stable_json_hash(value: object) -> str:
    """Hash canonical JSON so frozen inputs and prompts can be verified."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_hash(approach: str) -> str:
    """Hash the exact prompt configuration used by an approach."""
    prompts = {
        "baseline": BASELINE_PROMPT,
        "specialized": HISTORICAL_SPECIALIZED_PROMPTS,
        "specialized_v3": PROMPTS,
        "grounded_sales": GROUNDED_SALES_PROMPTS,
    }
    if approach not in prompts:
        raise ValueError(f"Unknown experiment approach: {approach}")
    prompt = prompts[approach]
    return stable_json_hash(prompt)


def select_baseline_sources(sources: list[dict]) -> list[dict]:
    """Build a fair union of evidence available to the three specialists."""
    role_sources = {role: select_sources_for_role(role, sources) for role in ROLES}
    selected = []
    seen = set()
    for rank in range(MAX_SOURCES):
        for role in ROLES:
            candidates = role_sources[role]
            if rank >= len(candidates):
                continue
            source = candidates[rank]
            source_id = str(source["source_id"])
            if source_id in seen:
                continue
            selected.append(source)
            seen.add(source_id)
            if len(selected) == MAX_SOURCES:
                return selected
    return selected


def _allowed_ids(role: str, sources: list[dict]) -> set[str]:
    if role in {"company", "leadership"} and any(
        "source_type" in source for source in sources
    ):
        return {
            str(source["source_id"])
            for source in sources
            if source.get("source_type") == "prospect"
        }
    return {str(source["source_id"]) for source in sources}


def run_baseline(
    request: dict,
    sources: list[dict],
    generator: Callable[[str, str], dict] = generate_json,
) -> dict[str, AgentResult]:
    """Run all categories in one model call with strict citation validation."""
    selected = select_baseline_sources(sources)
    if not selected:
        return {
            role: AgentResult(
                agent=role,
                findings=[],
                gaps=["No public website sources were retrieved."],
            )
            for role in ROLES
        }
    try:
        raw = generator(BASELINE_PROMPT, build_context(request, selected))
        validated = BaselineOutput.model_validate(raw)
    except (SchemaError, TypeError, ValueError) as exc:
        raise LLMError("Baseline prompt produced an invalid response.") from exc

    results = {}
    for role in ROLES:
        category = getattr(validated, role)
        allowed = _allowed_ids(role, selected)
        findings = [
            finding
            for finding in category.findings
            if set(finding.source_ids) <= allowed
        ]
        discarded = len(category.findings) - len(findings)
        gaps = list(category.gaps)
        if discarded:
            gaps.append(
                f"{discarded} finding(s) omitted due to invalid source attribution."
            )
        results[role] = AgentResult(
            agent=role,
            findings=findings,
            gaps=gaps[:5],
        )
    return results


def run_grounded_sales_agents(
    request: dict,
    sources: list[dict],
    generator: Callable[[str, str], dict] = generate_json,
) -> dict[str, AgentResult]:
    """Run three role calls with facts, suggestions, questions, and gaps separated."""
    results = {}
    for role in ROLES:
        role_sources = select_sources_for_role(role, sources)
        if role == "competitor" and not competitor_evidence_available(
            request, role_sources
        ):
            results[role] = AgentResult(
                agent=role,
                gaps=[
                    (
                        "No retrieved competitor source or explicit prospect-source "
                        "competitor mention was available."
                    )
                ],
            )
            continue
        if not role_sources:
            evidence = "prospect" if role in {"company", "leadership"} else "website"
            results[role] = AgentResult(
                agent=role,
                gaps=[f"No {evidence} sources were retrieved."],
            )
            continue
        try:
            raw = generator(
                GROUNDED_SALES_PROMPTS[role],
                build_context(request, role_sources),
            )
            validated = GroundedRoleOutput.model_validate(raw)
            fact_ids = [fact.fact_id for fact in validated.verified_facts]
            relevance_ids = [
                item.fact_id for item in validated.possible_sales_relevance
            ]
            if len(fact_ids) != len(set(fact_ids)) or set(fact_ids) != set(
                relevance_ids
            ):
                raise ValueError("Every fact needs one matching relevance item")
            if len(relevance_ids) != len(set(relevance_ids)):
                raise ValueError("Relevance fact IDs must be unique")
        except (SchemaError, TypeError, ValueError) as exc:
            raise LLMError(
                f"{role} grounded-sales prompt produced an invalid response."
            ) from exc

        relevance = {
            item.fact_id: item.suggestion
            for item in validated.possible_sales_relevance
        }
        findings = [
            Finding(
                claim=fact.claim,
                source_ids=fact.source_ids,
                evidence=fact.evidence,
                relevance=relevance[fact.fact_id],
            )
            for fact in validated.verified_facts
        ]
        confirmed, discarded = keep_supported_findings(findings, role_sources)
        gaps = [
            gap.strip() for gap in validated.missing_evidence if gap.strip()
        ]
        if discarded:
            gaps.append(
                f"{discarded} fact(s) omitted because a citation or exact "
                "supporting excerpt could not be validated."
            )
        results[role] = AgentResult(
            agent=role,
            findings=confirmed,
            gaps=gaps[:5],
            suggested_questions=validated.suggested_discovery_questions,
        )
    return results


def evaluate_mechanical(
    results: dict[str, AgentResult], sources: list[dict]
) -> MechanicalEvaluation:
    """Measure schema, citation-ID, provenance, and category response properties."""
    source_types = {
        str(source["source_id"]): source.get("source_type", "unspecified")
        for source in sources
    }
    citation_count = 0
    valid_count = 0
    provenance_violations = 0
    finding_count = 0
    categories_with_findings = 0
    categories_with_gaps = 0
    responded = 0
    excerpt_count = 0
    valid_excerpt_count = 0
    findings_missing_evidence = 0
    question_count = 0
    source_content = {
        str(source["source_id"]): normalize_evidence_text(source.get("content", ""))
        for source in sources
    }
    for role in ROLES:
        result = results.get(role)
        if result is None:
            continue
        if result.findings or result.gaps:
            responded += 1
        if result.findings:
            categories_with_findings += 1
        if result.gaps:
            categories_with_gaps += 1
        question_count += len(result.suggested_questions)
        finding_count += len(result.findings)
        for finding in result.findings:
            if not finding.evidence:
                findings_missing_evidence += 1
            excerpt_count += len(finding.evidence)
            for evidence in finding.evidence:
                excerpt = normalize_evidence_text(evidence.excerpt)
                if excerpt and excerpt in source_content.get(evidence.source_id, ""):
                    valid_excerpt_count += 1
            for source_id in finding.source_ids:
                citation_count += 1
                if source_id in source_types:
                    valid_count += 1
                    if (
                        role in {"company", "leadership"}
                        and source_types[source_id] == "competitor"
                    ):
                        provenance_violations += 1
    validity = valid_count / citation_count if citation_count else 1.0
    excerpt_validity = (
        valid_excerpt_count / excerpt_count if excerpt_count else 1.0
    )
    return MechanicalEvaluation(
        schema_compliant=set(results) == set(ROLES)
        and all(isinstance(result, AgentResult) for result in results.values()),
        finding_count=finding_count,
        citation_reference_count=citation_count,
        valid_citation_reference_count=valid_count,
        citation_id_validity_rate=validity,
        provenance_violation_count=provenance_violations,
        categories_with_findings=categories_with_findings,
        categories_with_explicit_gaps=categories_with_gaps,
        category_response_rate=responded / len(ROLES),
        evidence_excerpt_count=excerpt_count,
        valid_evidence_excerpt_count=valid_excerpt_count,
        evidence_excerpt_validity_rate=excerpt_validity,
        findings_missing_evidence=findings_missing_evidence,
        suggested_question_count=question_count,
    )


def manual_score_average(scores: ManualScores) -> float:
    """Return the unweighted mean of the five documented manual criteria."""
    values = (
        scores.factual_accuracy,
        scores.source_support,
        scores.product_relevance,
        scores.category_coverage,
        scores.sales_usefulness,
    )
    return round(sum(values) / len(values), 2)


def grounded_score_average(scores: GroundedManualScores) -> float:
    """Return the unweighted mean of the seven final-comparison criteria."""
    values = (
        scores.factual_accuracy,
        scores.evidence_support,
        scores.product_relevance,
        scores.completeness,
        scores.sales_usefulness,
        scores.format_adherence,
        scores.unsupported_assumptions,
    )
    return round(sum(values) / len(values), 2)


def aggregate_usage(usages: list[dict[str, int]]) -> dict[str, int]:
    """Sum provider token fields that were actually returned."""
    totals: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return totals


def serialize_artifact(artifact: ExperimentArtifact) -> str:
    """Produce deterministic, human-readable experiment JSON."""
    return artifact.model_dump_json(indent=2)


def approach_prompt_version(approach: str) -> str:
    versions = {
        "baseline": BASELINE_PROMPT_VERSION,
        "specialized": HISTORICAL_SPECIALIZED_PROMPT_VERSION,
        "specialized_v3": SPECIALIZED_PROMPT_VERSION,
        "grounded_sales": GROUNDED_SALES_PROMPT_VERSION,
    }
    if approach not in versions:
        raise ValueError(f"Unknown experiment approach: {approach}")
    return versions[approach]


def model_name() -> str:
    return MODEL
