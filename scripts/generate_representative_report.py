"""Run the final bounded IBM scenario and save its reviewed one-page PDF."""

from pathlib import Path

from pypdf import PdfReader

from cap_931.agents import run_all_agents
from cap_931.report_generator import create_sales_pdf
from cap_931.sales_tools import apply_human_review, generate_discovery_questions
from cap_931.web_research import research_companies

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
EVIDENCE = DOCS / "evidence"
PDF_PATH = DOCS / "sales_intelligence_brief.pdf"
RUN_PATH = EVIDENCE / "representative_research_run.txt"

REQUEST = {
    "product_name": "SalesIntel AI",
    "company_url": "https://www.ibm.com/",
    "product_category": "AI Sales Research",
    "competitor_urls": ["https://www.salesforce.com/"],
    "target_customer": "Chief Revenue Officer",
    "value_proposition": (
        "Helps sales teams research prospective customers and prepare for "
        "meetings with source-backed company, competitor, and leadership "
        "information."
    ),
}


def main() -> None:
    """Retrieve public evidence, run three roles, and save reproducible artifacts."""
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    sources, warnings = research_companies(
        REQUEST["company_url"], REQUEST["competitor_urls"]
    )
    if not sources:
        raise RuntimeError("No readable public source was retrieved.")

    results = run_all_agents(REQUEST, sources)
    included = {
        role: set(range(len(result.findings)))
        for role, result in results.items()
    }
    reviewed = apply_human_review(results, included)
    questions = generate_discovery_questions(REQUEST, reviewed)
    pdf_data = create_sales_pdf(REQUEST, reviewed, sources, questions)
    PDF_PATH.write_bytes(pdf_data)

    page_count = len(PdfReader(PDF_PATH).pages)
    if page_count != 1:
        raise RuntimeError(f"Representative report has {page_count} pages.")

    lines = [
        "CAP 931 representative research run",
        "Scenario: SalesIntel AI for IBM; Salesforce competitor; Chief Revenue Officer",
        f"Retrieved sources: {len(sources)}",
    ]
    for source in sources:
        lines.append(
            f"SOURCE {source['source_id']} [{source['source_type']}] "
            f"{source['title']} | {source['url']}"
        )
    for warning in warnings:
        lines.append(f"WARNING {warning}")
    for role, result in reviewed.items():
        lines.append(f"ROLE {role}")
        for finding in result.findings:
            lines.append(f"VERIFIED {finding.claim}")
            lines.append(f"SOURCE IDS {', '.join(finding.source_ids)}")
            for evidence in finding.evidence:
                lines.append(
                    f"EVIDENCE {evidence.source_id}: {evidence.excerpt}"
                )
            lines.append(f"POSSIBLE RELEVANCE {finding.relevance}")
        for gap in result.gaps:
            lines.append(f"GAP {gap}")
    lines.append("MEETING PREPARATION (SUGGESTED QUESTIONS)")
    lines.extend(f"QUESTION {question}" for question in questions)
    lines.append(f"PDF pages: {page_count}")
    RUN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Retrieved {len(sources)} source(s).")
    for source in sources:
        print(f"SUCCESS {source['source_id']} {source['url']}")
    for warning in warnings:
        print(f"WARNING {warning}")
    print(
        "Validated findings:",
        sum(len(result.findings) for result in reviewed.values()),
    )
    print(f"Suggested questions: {len(questions)}")
    print(f"PDF: {PDF_PATH} ({page_count} page)")


if __name__ == "__main__":
    main()
