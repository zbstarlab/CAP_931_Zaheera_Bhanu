import json
from pathlib import Path

import pytest

from cap_931.experiment_runner import _snapshot_payload
from cap_931.experiments import (
    ApproachRecord,
    ExperimentArtifact,
    ExperimentCaseRecord,
    GroundedManualScores,
    ManualScores,
    evaluate_mechanical,
    grounded_score_average,
    manual_score_average,
    prompt_hash,
    run_baseline,
    run_grounded_sales_agents,
    select_baseline_sources,
    serialize_artifact,
    stable_json_hash,
)
from cap_931.llm_client import LLMError
from cap_931.schemas import AgentResult, Finding

REQUEST = {
    "product_name": "Sales Research",
    "product_category": "Analytics",
    "company_url": "https://prospect.example",
    "competitor_urls": ["https://competitor.example"],
    "value_proposition": "Faster preparation",
    "target_customer": "CRO",
}
SOURCES = [
    {
        "source_id": "S1",
        "url": "https://prospect.example/strategy",
        "title": "Strategy",
        "content": "Prospect announced a data initiative.",
        "source_type": "prospect",
        "relevance_score": 10,
    },
    {
        "source_id": "S2",
        "url": "https://competitor.example/product",
        "title": "Competitor Product",
        "content": "Competitor offers an analytics product.",
        "source_type": "competitor",
        "relevance_score": 9,
    },
]


def valid_baseline_output():
    return {
        "company": {
            "findings": [
                {
                    "claim": "Prospect announced a data initiative.",
                    "source_ids": ["S1"],
                    "relevance": "Supports an analytics conversation.",
                }
            ],
            "gaps": [],
        },
        "competitor": {
            "findings": [
                {
                    "claim": "Competitor offers an analytics product.",
                    "source_ids": ["S2"],
                    "relevance": "Provides documented comparison evidence.",
                }
            ],
            "gaps": [],
        },
        "leadership": {"findings": [], "gaps": ["No leadership evidence."]},
    }


def test_baseline_schema_context_and_citation_validation():
    captured = {}

    def generator(system, user):
        captured["system"] = system
        captured["context"] = json.loads(user)
        return valid_baseline_output()

    results = run_baseline(REQUEST, SOURCES, generator)

    assert set(results) == {"company", "competitor", "leadership"}
    assert [source["source_id"] for source in captured["context"]["sources"]] == [
        "S1",
        "S2",
    ]
    assert "three sales" in captured["system"]
    assert results["company"].findings[0].source_ids == ["S1"]
    assert results["competitor"].findings[0].source_ids == ["S2"]


def test_baseline_removes_wrong_provenance_and_unknown_ids():
    raw = valid_baseline_output()
    raw["company"]["findings"] = [
        {
            "claim": "Competitor claim incorrectly assigned to prospect.",
            "source_ids": ["S2"],
            "relevance": "Invalid attribution.",
        },
        {
            "claim": "Unknown source claim must be removed.",
            "source_ids": ["S999"],
            "relevance": "Invalid citation.",
        },
    ]

    results = run_baseline(REQUEST, SOURCES, lambda *_: raw)

    assert not results["company"].findings
    assert "2 finding(s) omitted" in results["company"].gaps[-1]


def test_baseline_rejects_invalid_schema():
    with pytest.raises(LLMError, match="invalid response"):
        run_baseline(REQUEST, SOURCES, lambda *_: {"company": {}})


def test_baseline_without_sources_skips_generator():
    results = run_baseline(REQUEST, [], lambda *_: 1 / 0)

    assert all(result.gaps for result in results.values())
    assert all(not result.findings for result in results.values())


def test_baseline_source_union_is_bounded_and_contains_both_provenances():
    sources = [
        {
            **SOURCES[index % 2],
            "source_id": f"S{index + 1}",
            "relevance_score": 20 - index,
        }
        for index in range(8)
    ]

    selected = select_baseline_sources(sources)

    assert len(selected) == 4
    assert {source["source_type"] for source in selected} == {
        "prospect",
        "competitor",
    }


def test_mechanical_metrics_do_not_claim_factual_support():
    results = {
        "company": AgentResult(
            agent="company",
            findings=[
                Finding(
                    claim="Plausible claim with a valid identifier.",
                    source_ids=["S1"],
                    relevance="Potentially relevant.",
                )
            ],
        ),
        "competitor": AgentResult(
            agent="competitor",
            findings=[
                Finding(
                    claim="Claim with an unknown identifier.",
                    source_ids=["S999"],
                    relevance="Cannot be trusted.",
                )
            ],
        ),
        "leadership": AgentResult(agent="leadership", gaps=["No evidence."]),
    }

    metrics = evaluate_mechanical(results, SOURCES)

    assert metrics.schema_compliant
    assert metrics.citation_reference_count == 2
    assert metrics.valid_citation_reference_count == 1
    assert metrics.citation_id_validity_rate == 0.5
    assert not hasattr(metrics, "factual_accuracy")


def test_manual_score_calculation_and_bounds():
    scores = ManualScores(
        case_id="case",
        approach="baseline",
        reviewer="manual-review",
        factual_accuracy=5,
        source_support=4,
        product_relevance=3,
        category_coverage=2,
        sales_usefulness=1,
    )

    assert manual_score_average(scores) == 3.0
    with pytest.raises(ValueError):
        ManualScores(
            case_id="case",
            approach="baseline",
            reviewer="manual-review",
            factual_accuracy=6,
            source_support=4,
            product_relevance=3,
            category_coverage=2,
            sales_usefulness=1,
        )


def test_hashing_and_result_serialization_are_reproducible():
    assert stable_json_hash({"b": 2, "a": 1}) == stable_json_hash({"a": 1, "b": 2})
    metrics = evaluate_mechanical(
        {role: AgentResult(agent=role, gaps=["No evidence."]) for role in ("company", "competitor", "leadership")},
        [],
    )
    approach = ApproachRecord(
        approach="baseline",
        prompt_version="baseline-v1.0",
        prompt_sha256=prompt_hash("baseline"),
        model="openai/gpt-oss-20b",
        settings={"temperature": 0.1},
        selected_source_ids=[],
        raw_model_outputs=[],
        output={role: AgentResult(agent=role, gaps=["No evidence."]) for role in ("company", "competitor", "leadership")},
        api_latency_seconds=0,
        wall_time_seconds=0,
        usage={},
        mechanical=metrics,
    )
    artifact = ExperimentArtifact(
        schema_version="1.0",
        created_at="2026-01-01T00:00:00+00:00",
        cases=[
            ExperimentCaseRecord(
                case_id="case",
                description="Blocked case",
                source_snapshot_sha256=stable_json_hash([]),
                request=REQUEST,
                source_ids=[],
                retrieval_warnings=["blocked"],
                approaches=[approach],
            )
        ],
    )

    serialized = serialize_artifact(artifact)
    restored = ExperimentArtifact.model_validate_json(serialized)

    assert restored == artifact
    assert serialize_artifact(restored) == serialized


def test_recorded_experiment_artifacts_are_valid_and_hash_matched():
    project_root = Path(__file__).resolve().parents[3]
    artifact_dir = project_root / "artifacts" / "experiments"
    snapshots = json.loads((artifact_dir / "source_snapshots.json").read_text())
    results = ExperimentArtifact.model_validate_json(
        (artifact_dir / "model_runs.json").read_text()
    )
    manual = json.loads((artifact_dir / "manual_scores.json").read_text())

    for case in snapshots["cases"]:
        assert stable_json_hash(_snapshot_payload(case)) == case[
            "source_snapshot_sha256"
        ]
    for score in manual["scores"]:
        validated = ManualScores.model_validate(score)
        assert manual_score_average(validated) == score["average"]

    ibm = next(
        case
        for case in results.cases
        if case.case_id == "ibm_with_blocked_oracle_competitor"
    )
    final_baseline = [
        run for run in ibm.approaches if run.approach == "baseline"
    ][-1]
    specialized = next(
        run for run in ibm.approaches if run.approach == "specialized"
    )
    assert not final_baseline.errors
    assert final_baseline.prompt_sha256 == prompt_hash("baseline")
    assert specialized.prompt_sha256 == prompt_hash("specialized")


def test_grounded_variant_separates_facts_relevance_questions_and_gaps():
    def generator(system, user):
        if "named people" in system:
            return {
                "verified_facts": [],
                "possible_sales_relevance": [],
                "suggested_discovery_questions": [
                    "Who should be included in an initial evaluation?"
                ],
                "missing_evidence": ["No named leadership evidence."],
            }
        source_id = (
            "S2" if "Analyze explicit competitor mentions" in system else "S1"
        )
        content = (
            "Competitor offers an analytics product."
            if source_id == "S2"
            else "Prospect announced a data initiative."
        )
        return {
            "verified_facts": [
                {
                    "fact_id": "F1",
                    "claim": content,
                    "source_ids": [source_id],
                    "evidence": [{"source_id": source_id, "excerpt": content}],
                }
            ],
            "possible_sales_relevance": [
                {
                    "fact_id": "F1",
                    "suggestion": "This may be useful context for discovery.",
                }
            ],
            "suggested_discovery_questions": [
                "How should this public information be interpreted?"
            ],
            "missing_evidence": [],
        }

    results = run_grounded_sales_agents(REQUEST, SOURCES, generator)
    metrics = evaluate_mechanical(results, SOURCES)

    assert set(results) == {"company", "competitor", "leadership"}
    assert results["company"].findings[0].evidence[0].source_id == "S1"
    assert results["competitor"].findings[0].source_ids == ["S2"]
    assert results["leadership"].gaps == ["No named leadership evidence."]
    assert metrics.evidence_excerpt_validity_rate == 1.0
    assert metrics.suggested_question_count == 3


def test_grounded_variant_rejects_mismatched_relevance_structure():
    raw = {
        "verified_facts": [
            {
                "fact_id": "F1",
                "claim": "Prospect announced a data initiative.",
                "source_ids": ["S1"],
                "evidence": [
                    {
                        "source_id": "S1",
                        "excerpt": "Prospect announced a data initiative.",
                    }
                ],
            }
        ],
        "possible_sales_relevance": [],
        "suggested_discovery_questions": [],
        "missing_evidence": [],
    }

    with pytest.raises(LLMError, match="grounded-sales"):
        run_grounded_sales_agents(REQUEST, SOURCES, lambda *_: raw)


def test_grounded_manual_score_average_and_prompt_versions():
    scores = GroundedManualScores(
        case_id="case",
        approach="grounded_sales",
        reviewer="manual-review",
        factual_accuracy=5,
        evidence_support=5,
        product_relevance=4,
        completeness=4,
        sales_usefulness=4,
        format_adherence=5,
        unsupported_assumptions=5,
    )

    assert grounded_score_average(scores) == 4.57
    assert prompt_hash("specialized") != prompt_hash("specialized_v3")
    assert prompt_hash("specialized_v3") != prompt_hash("grounded_sales")


def test_final_prompt_artifacts_are_valid_and_historical_results_unchanged():
    project_root = Path(__file__).resolve().parents[3]
    artifact_dir = project_root / "artifacts" / "experiments"
    comparison = ExperimentArtifact.model_validate_json(
        (artifact_dir / "final_prompt_comparison.json").read_text()
    )
    manual = json.loads(
        (artifact_dir / "final_prompt_manual_scores.json").read_text()
    )

    ibm = next(
        case
        for case in comparison.cases
        if case.case_id == "ibm_with_blocked_oracle_competitor"
    )
    latest = {
        approach: [run for run in ibm.approaches if run.approach == approach][
            -1
        ]
        for approach in ("specialized_v3", "grounded_sales")
    }
    assert latest["specialized_v3"].prompt_sha256 == prompt_hash(
        "specialized_v3"
    )
    assert latest["grounded_sales"].prompt_sha256 == prompt_hash(
        "grounded_sales"
    )
    assert all(not run.errors for run in latest.values())
    for score in manual["scores"]:
        validated = GroundedManualScores.model_validate(
            {**score, "reviewer": manual["reviewer"]}
        )
        assert grounded_score_average(validated) == score["average"]

    historical = ExperimentArtifact.model_validate_json(
        (artifact_dir / "model_runs.json").read_text()
    )
    historical_ibm = next(
        case
        for case in historical.cases
        if case.case_id == "ibm_with_blocked_oracle_competitor"
    )
    historical_specialized = next(
        run
        for run in historical_ibm.approaches
        if run.approach == "specialized"
    )
    assert historical_specialized.prompt_version == "specialized-v2.1"
    assert historical_specialized.usage["total_tokens"] == 4890
