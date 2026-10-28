from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.testing.v1 import AppTest

from cap_931.schemas import AgentResult, EvidenceExcerpt, Finding


def test_streamlit_form_produces_downloadable_one_page_pdf(monkeypatch):
    source = {
        "source_id": "S1",
        "url": "https://prospect.example/news",
        "title": "Prospect news",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "content": "Prospect announced a public data initiative.",
        "source_type": "prospect",
    }
    results = {
        "company": AgentResult(
            agent="company",
            findings=[
                Finding(
                    claim="Prospect announced a public data initiative.",
                    source_ids=["S1"],
                    evidence=[
                        EvidenceExcerpt(
                            source_id="S1",
                            excerpt="Prospect announced a public data initiative.",
                        )
                    ],
                    relevance="Relevant product fit.",
                )
            ],
        ),
        "competitor": AgentResult(agent="competitor"),
        "leadership": AgentResult(agent="leadership"),
    }
    captured_pdfs = []
    captured_requests = []
    original_store = MemoryMediaFileStorage.load_and_get_id

    def capture_download(storage, data, mimetype, kind, filename=None):
        if mimetype == "application/pdf" and isinstance(data, bytes):
            captured_pdfs.append(data)
        return original_store(storage, data, mimetype, kind, filename)

    def capture_agents(request, sources):
        captured_requests.append(request)
        return results

    monkeypatch.setattr(
        "cap_931.validators.validate_public_url", lambda url: url.strip()
    )
    monkeypatch.setattr(
        "cap_931.web_research.research_companies",
        lambda company_url, competitors: ([source], []),
    )
    monkeypatch.setattr("cap_931.agents.run_all_agents", capture_agents)
    monkeypatch.setattr(MemoryMediaFileStorage, "load_and_get_id", capture_download)

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()
    for widget, value in zip(
        app.text_input,
        ["Example Analytics", "https://prospect.example", "Data", "CTO"],
        strict=True,
    ):
        widget.set_value(value)
    app.text_area[0].set_value("")
    app.text_area[1].set_value("Faster insight from public information.")
    app.file_uploader[0].set_value(
        ("overview.txt", b"Seller product supports rapid analysis.", "text/plain")
    )
    next(
        button for button in app.button if button.label == "Research Account"
    ).click().run()

    assert not app.exception
    assert len(app.get("download_button")) == 1
    assert any("Retrieved 1 public source" in item.value for item in app.markdown)
    assert app.expander[0].label.startswith("[Prospect] S1:")
    assert len(app.checkbox) == 1
    assert any(
        "Meeting Preparation" in item.value for item in app.get("subheader")
    )
    assert captured_requests[0]["product_document"]["content"] == (
        "Seller product supports rapid analysis."
    )
    assert captured_pdfs
    reader = PdfReader(BytesIO(captured_pdfs[-1]))
    assert len(reader.pages) == 1
    assert "SALES INTELLIGENCE BRIEF" in reader.pages[0].extract_text()
    assert "Meeting Preparation" in reader.pages[0].extract_text()

    app.checkbox[0].set_value(False).run()
    reviewed_reader = PdfReader(BytesIO(captured_pdfs[-1]))
    reviewed_text = reviewed_reader.pages[0].extract_text()
    assert "Prospect announced a public data initiative" not in reviewed_text
    assert "Not verified from the retrieved evidence" in reviewed_text


def test_category_suggestion_does_not_overwrite_without_acceptance(monkeypatch):
    monkeypatch.setattr(
        "cap_931.category_suggester.suggest_product_category",
        lambda product, value: "AI Sales Intelligence",
    )
    app = AppTest.from_file(
        Path(__file__).parents[1] / "app.py", default_timeout=10
    ).run()
    app.text_input[0].set_value("SalesIntel AI")
    app.text_input[2].set_value("Manual Category")
    app.text_area[1].set_value("Reduce research preparation time.")

    next(
        button for button in app.button if button.label == "Suggest Category"
    ).click().run()

    assert app.text_input[2].value == "Manual Category"
    assert any(
        "Suggested category: AI Sales Intelligence" in item.value
        for item in app.info
    )
    next(
        button
        for button in app.button
        if button.label == "Use suggested category"
    ).click().run()
    assert app.text_input[2].value == "AI Sales Intelligence"
