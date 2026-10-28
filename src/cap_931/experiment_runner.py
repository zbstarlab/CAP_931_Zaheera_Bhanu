"""Capture frozen evidence and run the Phase 7 prompt comparison."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .agents import (
    MAX_CHARS_PER_SOURCE,
    MAX_SOURCES,
    run_all_agents,
    select_sources_for_role,
)
from .experiments import (
    BASELINE_MAX_COMPLETION_TOKENS,
    ROLES,
    ApproachRecord,
    ExperimentArtifact,
    ExperimentCaseRecord,
    aggregate_usage,
    approach_prompt_version,
    evaluate_mechanical,
    model_name,
    prompt_hash,
    run_baseline,
    run_grounded_sales_agents,
    select_baseline_sources,
    serialize_artifact,
    stable_json_hash,
)
from .llm_client import (
    DEFAULT_MAX_COMPLETION_TOKENS,
    GenerationResult,
    LLMError,
    generate_json_with_metrics,
)
from .prompts import HISTORICAL_SPECIALIZED_PROMPTS
from .web_research import research_companies

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = PROJECT_ROOT / "artifacts" / "experiments"
SNAPSHOT_PATH = ARTIFACT_DIR / "source_snapshots.json"
RESULT_PATH = ARTIFACT_DIR / "model_runs.json"
FINAL_RESULT_PATH = ARTIFACT_DIR / "final_prompt_comparison.json"

CASE_DEFINITIONS = (
    {
        "case_id": "ibm_with_blocked_oracle_competitor",
        "description": (
            "Accessible IBM prospect evidence with Oracle supplied as a competitor; "
            "Oracle previously returned an access block."
        ),
        "request": {
            "product_name": "Sales Intelligence Platform",
            "company_url": "https://www.ibm.com",
            "product_category": "AI Sales Research",
            "competitor_urls": ["https://www.oracle.com"],
            "value_proposition": (
                "Source-grounded account research that reduces manual preparation time."
            ),
            "target_customer": "Chief Revenue Officer",
        },
    },
    {
        "case_id": "oracle_blocked_prospect",
        "description": (
            "Oracle prospect case intended to measure behavior when public retrieval "
            "is blocked or incomplete."
        ),
        "request": {
            "product_name": "Sales Intelligence Platform",
            "company_url": "https://www.oracle.com",
            "product_category": "AI Sales Research",
            "competitor_urls": [],
            "value_proposition": (
                "Source-grounded account research that reduces manual preparation time."
            ),
            "target_customer": "Chief Revenue Officer",
        },
    },
)


class CallPacer:
    """Space live calls to reduce pressure on the Groq free-tier token window."""

    def __init__(self, minimum_interval_seconds: float) -> None:
        self.minimum_interval_seconds = minimum_interval_seconds
        self.last_call_started: float | None = None

    def wait(self) -> None:
        if self.last_call_started is not None:
            remaining = self.minimum_interval_seconds - (
                perf_counter() - self.last_call_started
            )
            if remaining > 0:
                time.sleep(remaining)
        self.last_call_started = perf_counter()


class RecordingGenerator:
    """Agent-compatible generator that records raw JSON and provider metrics."""

    def __init__(
        self,
        pacer: CallPacer,
        max_completion_tokens: int,
    ) -> None:
        self.pacer = pacer
        self.max_completion_tokens = max_completion_tokens
        self.responses: list[GenerationResult] = []

    def __call__(self, system_prompt: str, user_prompt: str) -> dict:
        self.pacer.wait()
        response = generate_json_with_metrics(
            system_prompt,
            user_prompt,
            max_completion_tokens=self.max_completion_tokens,
        )
        self.responses.append(response)
        return response.value


def _snapshot_payload(case: dict) -> dict:
    return {
        "case_id": case["case_id"],
        "request": case["request"],
        "retrieval_config": case["retrieval_config"],
        "retrieval_warnings": case["retrieval_warnings"],
        "sources": case["sources"],
    }


def capture_source_snapshots(path: Path = SNAPSHOT_PATH) -> dict:
    """Retrieve two cases and freeze only the exact bounded model excerpts."""
    captured_cases = []
    for definition in CASE_DEFINITIONS:
        request = definition["request"]
        sources, warnings = research_companies(
            request["company_url"],
            request["competitor_urls"],
            max_pages_per_site=3,
            max_total_sources=6,
        )
        bounded_sources = []
        for source in sources:
            full_content = source.get("content", "")
            bounded_sources.append(
                {
                    **source,
                    "content": full_content[:MAX_CHARS_PER_SOURCE],
                    "snapshot_excerpt": True,
                    "original_extracted_characters": len(full_content),
                    "full_extracted_content_sha256": stable_json_hash(full_content),
                }
            )
        case = {
            **definition,
            "captured_at": datetime.now(UTC).isoformat(),
            "retrieval_config": {
                "max_pages_per_site": 3,
                "max_total_sources": 6,
                "snapshot_chars_per_source": MAX_CHARS_PER_SOURCE,
            },
            "retrieval_warnings": warnings,
            "sources": bounded_sources,
        }
        case["source_snapshot_sha256"] = stable_json_hash(_snapshot_payload(case))
        captured_cases.append(case)

    artifact = {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat(),
        "copyright_note": (
            "Only bounded excerpts actually supplied to the model are stored; "
            "full webpages are not archived."
        ),
        "cases": captured_cases,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    return artifact


def _selected_ids(approach: str, sources: list[dict]) -> list[str]:
    if approach == "baseline":
        selected = select_baseline_sources(sources)
    else:
        selected = []
        seen = set()
        for role in ROLES:
            for source in select_sources_for_role(role, sources):
                source_id = str(source["source_id"])
                if source_id not in seen:
                    selected.append(source)
                    seen.add(source_id)
    return [str(source["source_id"]) for source in selected]


def _run_approach(
    approach: str,
    request: dict,
    sources: list[dict],
    pacer: CallPacer,
    *,
    attempt: int = 1,
) -> ApproachRecord:
    max_tokens = (
        BASELINE_MAX_COMPLETION_TOKENS
        if approach == "baseline"
        else DEFAULT_MAX_COMPLETION_TOKENS
    )
    recorder = RecordingGenerator(pacer, max_tokens)
    errors = []
    rate_events = []
    started = perf_counter()
    try:
        if approach == "baseline":
            output = run_baseline(request, sources, recorder)
        elif approach == "specialized":
            output = run_all_agents(
                request,
                sources,
                recorder,
                prompt_map=HISTORICAL_SPECIALIZED_PROMPTS,
                require_evidence=False,
            )
        elif approach == "specialized_v3":
            output = run_all_agents(request, sources, recorder)
        else:
            output = run_grounded_sales_agents(request, sources, recorder)
    except LLMError as exc:
        message = str(exc)
        errors.append(message)
        if "rate limit" in message.lower():
            rate_events.append(message)
        output = {}
    wall_time = perf_counter() - started
    usages = [response.usage for response in recorder.responses]
    api_latency = sum(response.latency_seconds for response in recorder.responses)
    mechanical = evaluate_mechanical(output, sources)
    return ApproachRecord(
        approach=approach,
        attempt=attempt,
        prompt_version=approach_prompt_version(approach),
        prompt_sha256=prompt_hash(approach),
        model=model_name(),
        settings={
            "temperature": 0.1,
            "reasoning_effort": "low",
            "max_completion_tokens_per_call": max_tokens,
            "maximum_source_count": MAX_SOURCES,
            "maximum_characters_per_source": MAX_CHARS_PER_SOURCE,
            "paced_interval_seconds": pacer.minimum_interval_seconds,
        },
        selected_source_ids=_selected_ids(approach, sources),
        raw_model_outputs=[response.value for response in recorder.responses],
        output=output,
        api_latency_seconds=round(api_latency, 6),
        wall_time_seconds=round(wall_time, 6),
        usage=aggregate_usage(usages),
        mechanical=mechanical,
        errors=errors,
        rate_limit_events=rate_events,
    )


def run_experiments(
    snapshot_path: Path = SNAPSHOT_PATH,
    result_path: Path = RESULT_PATH,
    *,
    minimum_interval_seconds: float = 20.0,
) -> ExperimentArtifact:
    """Run both approaches against immutable snapshots and write JSON results."""
    snapshot = json.loads(snapshot_path.read_text())
    pacer = CallPacer(minimum_interval_seconds)
    records = []
    for case in snapshot["cases"]:
        expected_hash = stable_json_hash(_snapshot_payload(case))
        if expected_hash != case["source_snapshot_sha256"]:
            raise ValueError(f"Source snapshot hash mismatch for {case['case_id']}")
        approaches = [
            _run_approach("baseline", case["request"], case["sources"], pacer),
            _run_approach("specialized", case["request"], case["sources"], pacer),
        ]
        records.append(
            ExperimentCaseRecord(
                case_id=case["case_id"],
                description=case["description"],
                source_snapshot_sha256=case["source_snapshot_sha256"],
                request=case["request"],
                source_ids=[source["source_id"] for source in case["sources"]],
                retrieval_warnings=case["retrieval_warnings"],
                approaches=approaches,
            )
        )
    artifact = ExperimentArtifact(
        schema_version="1.0",
        created_at=datetime.now(UTC).isoformat(),
        cases=records,
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(serialize_artifact(artifact) + "\n")
    return artifact


def retry_failed_baselines(
    snapshot_path: Path = SNAPSHOT_PATH,
    result_path: Path = RESULT_PATH,
    *,
    minimum_interval_seconds: float = 20.0,
) -> ExperimentArtifact:
    """Append a revised baseline attempt while preserving failed raw output."""
    snapshot = json.loads(snapshot_path.read_text())
    artifact = ExperimentArtifact.model_validate_json(result_path.read_text())
    snapshots_by_id = {case["case_id"]: case for case in snapshot["cases"]}
    pacer = CallPacer(minimum_interval_seconds)
    for case_record in artifact.cases:
        case = snapshots_by_id[case_record.case_id]
        baseline_runs = [
            run for run in case_record.approaches if run.approach == "baseline"
        ]
        if not baseline_runs or not baseline_runs[-1].errors or not case["sources"]:
            continue
        case_record.approaches.append(
            _run_approach(
                "baseline",
                case["request"],
                case["sources"],
                pacer,
                attempt=max(run.attempt for run in baseline_runs) + 1,
            )
        )
    result_path.write_text(serialize_artifact(artifact) + "\n")
    return artifact


def run_final_prompt_experiment(
    snapshot_path: Path = SNAPSHOT_PATH,
    result_path: Path = FINAL_RESULT_PATH,
    *,
    minimum_interval_seconds: float = 20.0,
) -> ExperimentArtifact:
    """Compare production specialized v3 with the separated grounded variant."""
    snapshot = json.loads(snapshot_path.read_text())
    pacer = CallPacer(minimum_interval_seconds)
    records = []
    for case in snapshot["cases"]:
        expected_hash = stable_json_hash(_snapshot_payload(case))
        if expected_hash != case["source_snapshot_sha256"]:
            raise ValueError(f"Source snapshot hash mismatch for {case['case_id']}")
        approaches = [
            _run_approach(
                "specialized_v3", case["request"], case["sources"], pacer
            ),
            _run_approach(
                "grounded_sales", case["request"], case["sources"], pacer
            ),
        ]
        records.append(
            ExperimentCaseRecord(
                case_id=case["case_id"],
                description=case["description"],
                source_snapshot_sha256=case["source_snapshot_sha256"],
                request=case["request"],
                source_ids=[source["source_id"] for source in case["sources"]],
                retrieval_warnings=case["retrieval_warnings"],
                approaches=approaches,
            )
        )
    artifact = ExperimentArtifact(
        schema_version="2.0",
        created_at=datetime.now(UTC).isoformat(),
        cases=records,
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(serialize_artifact(artifact) + "\n")
    return artifact


def retry_final_prompt_experiment(
    snapshot_path: Path = SNAPSHOT_PATH,
    result_path: Path = FINAL_RESULT_PATH,
    *,
    minimum_interval_seconds: float = 20.0,
) -> ExperimentArtifact:
    """Append revised final-variant attempts without deleting recorded failures."""
    snapshot = json.loads(snapshot_path.read_text())
    artifact = ExperimentArtifact.model_validate_json(result_path.read_text())
    snapshots_by_id = {case["case_id"]: case for case in snapshot["cases"]}
    pacer = CallPacer(minimum_interval_seconds)
    for case_record in artifact.cases:
        case = snapshots_by_id[case_record.case_id]
        if not case["sources"]:
            continue
        for approach in ("specialized_v3", "grounded_sales"):
            previous = [
                run
                for run in case_record.approaches
                if run.approach == approach
            ]
            case_record.approaches.append(
                _run_approach(
                    approach,
                    case["request"],
                    case["sources"],
                    pacer,
                    attempt=max((run.attempt for run in previous), default=0) + 1,
                )
            )
    result_path.write_text(serialize_artifact(artifact) + "\n")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "capture",
            "run",
            "retry-baseline",
            "run-grounded",
            "retry-grounded",
        ),
    )
    parser.add_argument("--interval", type=float, default=20.0)
    args = parser.parse_args()
    if args.command == "capture":
        artifact = capture_source_snapshots()
        print(
            json.dumps(
                {
                    "snapshot_path": str(SNAPSHOT_PATH),
                    "case_count": len(artifact["cases"]),
                    "source_counts": {
                        case["case_id"]: len(case["sources"])
                        for case in artifact["cases"]
                    },
                    "warning_counts": {
                        case["case_id"]: len(case["retrieval_warnings"])
                        for case in artifact["cases"]
                    },
                },
                indent=2,
            )
        )
    elif args.command == "run":
        artifact = run_experiments(minimum_interval_seconds=args.interval)
        print(
            json.dumps(
                {
                    "result_path": str(RESULT_PATH),
                    "case_count": len(artifact.cases),
                    "errors": {
                        f"{case.case_id}:{run.approach}": run.errors
                        for case in artifact.cases
                        for run in case.approaches
                    },
                },
                indent=2,
            )
        )
    elif args.command == "retry-baseline":
        artifact = retry_failed_baselines(minimum_interval_seconds=args.interval)
        print(
            json.dumps(
                {
                    "result_path": str(RESULT_PATH),
                    "baseline_attempts": {
                        case.case_id: len(
                            [
                                run
                                for run in case.approaches
                                if run.approach == "baseline"
                            ]
                        )
                        for case in artifact.cases
                    },
                    "latest_errors": {
                        case.case_id: [
                            run
                            for run in case.approaches
                            if run.approach == "baseline"
                        ][-1].errors
                        for case in artifact.cases
                    },
                },
                indent=2,
            )
        )
    elif args.command == "run-grounded":
        artifact = run_final_prompt_experiment(
            minimum_interval_seconds=args.interval
        )
        print(
            json.dumps(
                {
                    "result_path": str(FINAL_RESULT_PATH),
                    "case_count": len(artifact.cases),
                    "errors": {
                        f"{case.case_id}:{run.approach}": run.errors
                        for case in artifact.cases
                        for run in case.approaches
                    },
                },
                indent=2,
            )
        )
    else:
        artifact = retry_final_prompt_experiment(
            minimum_interval_seconds=args.interval
        )
        print(
            json.dumps(
                {
                    "result_path": str(FINAL_RESULT_PATH),
                    "attempts": {
                        f"{case.case_id}:{approach}": len(
                            [
                                run
                                for run in case.approaches
                                if run.approach == approach
                            ]
                        )
                        for case in artifact.cases
                        for approach in ("specialized_v3", "grounded_sales")
                    },
                    "latest_errors": {
                        f"{case.case_id}:{approach}": [
                            run
                            for run in case.approaches
                            if run.approach == approach
                        ][-1].errors
                        for case in artifact.cases
                        for approach in ("specialized_v3", "grounded_sales")
                    },
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
