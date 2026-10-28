from cap_931.sales_tools import apply_human_review, generate_discovery_questions
from cap_931.schemas import AgentResult, EvidenceExcerpt, Finding


def supported_finding() -> Finding:
    return Finding(
        claim="Prospect announced a public data initiative.",
        source_ids=["S1"],
        evidence=[
            EvidenceExcerpt(
                source_id="S1",
                excerpt="Prospect announced a public data initiative.",
            )
        ],
        relevance="This may provide context for an analytics discussion.",
    )


def test_human_review_excludes_findings_but_preserves_gaps():
    results = {
        "company": AgentResult(
            agent="company",
            findings=[supported_finding()],
            gaps=["Purchasing criteria were not found."],
        ),
        "competitor": AgentResult(agent="competitor", gaps=["No competitor source."]),
        "leadership": AgentResult(agent="leadership", gaps=["No leadership source."]),
    }

    reviewed = apply_human_review(results, {"company": set()})

    assert not reviewed["company"].findings
    assert reviewed["company"].gaps == ["Purchasing criteria were not found."]
    assert reviewed["competitor"].gaps == ["No competitor source."]


def test_discovery_questions_use_verified_fact_and_are_labeled_as_questions():
    request = {
        "product_name": "SalesIntel AI",
        "product_category": "AI Sales Research",
        "target_customer": "Chief Revenue Officer",
        "value_proposition": "reduce preparation time",
    }
    results = {
        "company": AgentResult(agent="company", findings=[supported_finding()]),
        "competitor": AgentResult(agent="competitor", gaps=["No competitor source."]),
        "leadership": AgentResult(agent="leadership"),
    }

    questions = generate_discovery_questions(request, results)

    assert len(questions) == 3
    assert all(question.endswith("?") for question in questions)
    assert "A public source states" in questions[0]
    assert "not verified in public sources" in questions[2]


def test_discovery_questions_are_neutral_without_public_findings():
    request = {
        "product_name": "SalesIntel AI",
        "product_category": "AI Sales Research",
        "target_customer": "Chief Revenue Officer",
        "value_proposition": "reduce preparation time",
    }
    results = {
        role: AgentResult(agent=role, gaps=["No evidence."])
        for role in ("company", "competitor", "leadership")
    }

    questions = generate_discovery_questions(request, results)

    assert "What outcomes would Chief Revenue Officer want" in questions[0]
    assert "Prospect needs" not in " ".join(questions)
