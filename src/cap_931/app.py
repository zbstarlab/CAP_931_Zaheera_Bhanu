"""CAP 931 Streamlit UI: web retrieval + three Groq research roles."""

import streamlit as st

from cap_931.agents import run_all_agents
from cap_931.category_suggester import (
    CategorySuggestionError,
    suggest_product_category,
)
from cap_931.document_parser import DocumentError, parse_product_document
from cap_931.llm_client import LLMError
from cap_931.report_generator import create_sales_pdf
from cap_931.sales_tools import apply_human_review, generate_discovery_questions
from cap_931.validators import (
    ValidationError,
    parse_competitor_urls,
    validate_public_url,
)
from cap_931.web_research import research_companies


def _accept_category_suggestion() -> None:
    suggestion = st.session_state.get("category_suggestion")
    if suggestion:
        st.session_state["product_category"] = suggestion
        del st.session_state["category_suggestion"]


def _render_sources(sources: list[dict]) -> None:
    st.subheader("Retrieved evidence")
    st.write(f"Retrieved {len(sources)} public source(s).")
    for source in sources:
        source_label = source.get("source_type", "unknown").title()
        format_label = source.get("content_type", "html").upper()
        with st.expander(
            f"[{source_label}] {source['source_id']}: "
            f"{source['title']} ({format_label})"
        ):
            st.write(source["url"])
            metadata = f"Retrieved: {source['retrieved_at']}"
            if source.get("publication_date"):
                metadata += f" | Published: {source['publication_date']}"
            st.caption(metadata)
            st.text(source["content"][:2000])


def _render_results(bundle: dict) -> None:
    request = bundle["request"]
    sources = bundle["sources"]
    results = bundle["results"]
    run_id = bundle["run_id"]

    st.subheader("Agent research results")
    st.caption(
        "Verified findings passed source-ID, provenance, and exact-excerpt checks. "
        "Sales relevance is a suggestion, not a verified company fact."
    )
    included: dict[str, set[int]] = {}
    for role, analysis in results.items():
        st.markdown(f"### {role.title()} research")
        if not analysis.findings:
            st.info("No source-backed findings returned for this role.")
        included[role] = set()
        for index, finding in enumerate(analysis.findings):
            st.write(f"**Verified fact:** {finding.claim}")
            for evidence in finding.evidence:
                st.write(
                    f'**Supporting excerpt ({evidence.source_id}):** '
                    f'“{evidence.excerpt}”'
                )
            st.write(f"**Possible sales relevance:** {finding.relevance}")
            st.caption("Source IDs: " + ", ".join(finding.source_ids))
            if st.checkbox(
                "Include this verified finding in the PDF",
                value=True,
                key=f"review_{run_id}_{role}_{index}",
            ):
                included[role].add(index)
        if analysis.gaps:
            st.warning("Evidence gaps: " + " | ".join(analysis.gaps))

    reviewed = apply_human_review(results, included)
    questions = generate_discovery_questions(request, reviewed)
    st.subheader("Meeting Preparation")
    st.caption(
        "Suggested questions for an initial conversation; they are not verified "
        "facts about the prospect."
    )
    for question in questions:
        st.write(f"- {question}")

    pdf_data = create_sales_pdf(request, reviewed, sources, questions)
    st.download_button(
        "Download one-page sales intelligence PDF",
        data=pdf_data,
        file_name="sales_intelligence_brief.pdf",
        mime="application/pdf",
        use_container_width=True,
    )


st.set_page_config(page_title="SalesIntel AI", page_icon="📊", layout="wide")
st.title("SalesIntel AI")
st.caption("Source-grounded, lightweight multi-agent sales research prototype")

with st.form("research_form"):
    left, right = st.columns(2)
    with left:
        product_name = st.text_input(
            "Product Name",
            placeholder="Cloud Data Platform",
            key="product_name",
        )
        company_url = st.text_input(
            "Prospective Company URL",
            placeholder="https://example.com",
            key="company_url",
        )
        product_category = st.text_input(
            "Product Category",
            placeholder="Data Analytics",
            key="product_category",
        )
    with right:
        competitors = st.text_area(
            "Competitor URLs (optional, one per line)", key="competitors"
        )
        target_customer = st.text_input(
            "Target Customer",
            placeholder="Chief Technology Officer",
            key="target_customer",
        )
        value_proposition = st.text_area(
            "Value Proposition", key="value_proposition"
        )
    document = st.file_uploader(
        "Optional product overview (PDF or TXT)",
        type=["pdf", "txt"],
        key="product_document_upload",
    )
    suggest_requested = st.form_submit_button("Suggest Category")
    submitted = st.form_submit_button(
        "Research Account", use_container_width=True
    )

if suggest_requested:
    try:
        st.session_state["category_suggestion"] = suggest_product_category(
            product_name, value_proposition
        )
    except (CategorySuggestionError, LLMError) as exc:
        st.error(str(exc))

if st.session_state.get("category_suggestion"):
    st.info(
        "Suggested category: " + st.session_state["category_suggestion"]
    )
    st.button(
        "Use suggested category",
        on_click=_accept_category_suggestion,
    )

if submitted:
    st.session_state.pop("research_bundle", None)
    errors = []
    parsed_document = None
    for name, value in [
        ("Product Name", product_name),
        ("Product Category", product_category),
        ("Target Customer", target_customer),
        ("Value Proposition", value_proposition),
    ]:
        if not value.strip():
            errors.append(f"{name} is required.")
    try:
        validated_company = validate_public_url(company_url)
    except ValidationError as exc:
        errors.append(str(exc))
        validated_company = None
    try:
        competitor_urls = parse_competitor_urls(competitors)
    except ValidationError as exc:
        errors.append(str(exc))
        competitor_urls = []
    if document is not None:
        try:
            parsed_document = parse_product_document(
                document.name, document.getvalue()
            )
        except DocumentError as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            st.error(error)
    else:
        request = {
            "product_name": product_name,
            "company_url": validated_company,
            "product_category": product_category,
            "competitor_urls": competitor_urls,
            "value_proposition": value_proposition,
            "target_customer": target_customer,
        }
        if parsed_document is not None:
            request["product_document"] = parsed_document.model_dump()
        with st.spinner("Retrieving public website content..."):
            sources, warnings = research_companies(
                validated_company, competitor_urls
            )
        results = None
        if sources:
            try:
                with st.spinner("Running three Groq research roles..."):
                    results = run_all_agents(request, sources)
            except LLMError as exc:
                st.error(str(exc))
        run_id = int(st.session_state.get("research_run_id", 0)) + 1
        st.session_state["research_run_id"] = run_id
        st.session_state["research_bundle"] = {
            "request": request,
            "sources": sources,
            "warnings": warnings,
            "results": results,
            "parsed_document": parsed_document,
            "run_id": run_id,
        }

bundle = st.session_state.get("research_bundle")
if bundle:
    parsed_document = bundle["parsed_document"]
    if parsed_document is not None:
        st.success(
            f"Product document parsed: {parsed_document.filename} "
            f"({len(parsed_document.content):,} text characters)."
        )
        if parsed_document.truncated:
            st.warning(
                "The product document text was shortened to fit the model context limit."
            )
    for warning in bundle["warnings"]:
        st.warning(warning)
    _render_sources(bundle["sources"])
    if not bundle["sources"]:
        st.error("No accessible webpages were retrieved. Try another public website.")
    elif bundle["results"] is not None:
        try:
            _render_results(bundle)
        except ValueError as exc:
            st.error(f"Could not create the one-page PDF: {exc}")
