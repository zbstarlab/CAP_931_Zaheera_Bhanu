"""Small deterministic helpers for meeting preparation and human review."""

from .schemas import AgentResult

ROLES = ("company", "competitor", "leadership")


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def apply_human_review(
    results: dict[str, AgentResult], included: dict[str, set[int]]
) -> dict[str, AgentResult]:
    """Keep selected validated findings while preserving every evidence gap."""
    reviewed = {}
    for role in ROLES:
        result = results.get(role, AgentResult(agent=role))
        selected_indexes = included.get(role, set(range(len(result.findings))))
        reviewed[role] = result.model_copy(
            update={
                "findings": [
                    finding
                    for index, finding in enumerate(result.findings)
                    if index in selected_indexes
                ]
            }
        )
    return reviewed


def generate_discovery_questions(
    request: dict, results: dict[str, AgentResult]
) -> list[str]:
    """Create up to three neutral suggestions without another model call."""
    product = _short(request.get("product_name") or "our product", 60)
    category = _short(request.get("product_category") or "this product category", 60)
    target = _short(request.get("target_customer") or "your team", 60)
    value = _short(request.get("value_proposition") or "the proposed outcomes", 100)
    findings = [
        finding
        for role in ROLES
        for finding in results.get(role, AgentResult(agent=role)).findings
    ]
    gaps = [
        gap
        for role in ROLES
        for gap in results.get(role, AgentResult(agent=role)).gaps
    ]

    questions = []
    if findings:
        fact = _short(findings[0].claim, 120)
        questions.append(
            f'A public source states, "{fact}" What context should we understand '
            f"before discussing {category}?"
        )
    else:
        questions.append(
            f"What outcomes would {target} want a {category} solution such as "
            f"{product} to improve?"
        )

    questions.append(
        f"How would {target} evaluate whether {value.rstrip('.')} is valuable?"
    )
    if gaps:
        questions.append(
            "Which current priorities, constraints, and decision criteria should "
            "we understand because they were not verified in public sources?"
        )
    else:
        questions.append(
            f"What would a useful first evaluation of {product} need to include?"
        )
    return questions[:3]
