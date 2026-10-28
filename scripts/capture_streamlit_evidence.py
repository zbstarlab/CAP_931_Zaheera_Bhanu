"""Capture genuine Streamlit evidence from a running local application.

Run with:
    uv run --with playwright python scripts/capture_streamlit_evidence.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOTS = ROOT / "docs" / "screenshots"
DOCS = ROOT / "docs"
EVIDENCE = DOCS / "evidence"
CHROME = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)


def viewport_screenshot(page, text: str, filename: str) -> None:
    """Scroll to authentic page content and capture the visible browser viewport."""
    headings = page.locator("h1, h2, h3").filter(has_text=text)
    locator = headings.first if headings.count() else page.get_by_text(
        text, exact=False
    ).first
    locator.wait_for(state="visible", timeout=180_000)
    locator.evaluate(
        "element => {"
        "const scroller = element.closest('[data-testid=stMain]') || "
        "document.scrollingElement;"
        "const top = element.getBoundingClientRect().top - "
        "scroller.getBoundingClientRect().top - 24;"
        "scroller.scrollTop += top;"
        "}"
    )
    page.wait_for_timeout(750)
    page.screenshot(path=SCREENSHOTS / filename)


def main() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not CHROME.exists():
        raise RuntimeError(f"Google Chrome was not found at {CHROME}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(CHROME),
            headless=True,
            args=["--no-sandbox"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:8501", wait_until="networkidle")
        page.get_by_text("SalesIntel AI", exact=True).wait_for()
        page.get_by_role("button", name="Research Account").click()
        page.get_by_text("Product Name is required.").wait_for()
        page.screenshot(path=SCREENSHOTS / "03-input-validation.png", full_page=True)

        page.reload(wait_until="networkidle")
        page.get_by_label("Product Name").fill("SalesIntel AI")
        page.get_by_label("Prospective Company URL").fill("https://www.ibm.com/")
        page.get_by_label("Competitor URLs (optional, one per line)").fill(
            "https://www.salesforce.com/"
        )
        page.get_by_label("Target Customer").fill("Chief Revenue Officer")
        page.get_by_label("Value Proposition").fill(
            "Helps sales teams research prospective customers and prepare for "
            "meetings with source-backed company, competitor, and leadership "
            "information."
        )
        page.locator('input[type="file"]').set_input_files(
            ROOT / "artifacts" / "demo_product_overview.txt"
        )
        page.get_by_role("button", name="Suggest Category").click()
        page.get_by_text("Suggested category:", exact=False).wait_for(
            state="visible", timeout=120_000
        )
        viewport_screenshot(
            page, "Suggested category:", "11-category-suggestion.png"
        )
        page.get_by_role("button", name="Use suggested category").click()
        page.wait_for_timeout(1_000)
        category = page.get_by_label("Product Category")
        if not category.input_value().strip():
            raise RuntimeError("The category suggestion was not accepted.")
        category.scroll_into_view_if_needed()
        page.screenshot(path=SCREENSHOTS / "12-category-accepted.png")
        category.fill("AI Sales Research")
        page.get_by_text("SalesIntel AI", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=SCREENSHOTS / "02-streamlit-inputs.png", full_page=True)
        page.get_by_role("button", name="Research Account").click()

        page.get_by_role(
            "button", name="Download one-page sales intelligence PDF"
        ).wait_for(state="visible", timeout=240_000)
        (EVIDENCE / "representative_research_run.txt").write_text(
            page.locator("body").inner_text(), encoding="utf-8"
        )

        viewport_screenshot(page, "Retrieved evidence", "04-source-discovery.png")
        first_expander = page.locator('[data-testid="stExpander"]').first
        first_expander.locator("summary").click()
        viewport_screenshot(page, "Retrieved evidence", "04-source-details.png")
        first_expander.locator("summary").click()
        viewport_screenshot(page, "Agent research results", "05-agent-results.png")
        viewport_screenshot(
            page, "Competitor research", "05-agent-results-continued.png"
        )
        viewport_screenshot(page, "Product Name", "06-product-upload.png")
        viewport_screenshot(
            page,
            "Meeting Preparation",
            "07-pdf-download.png",
        )

        with page.expect_download(timeout=30_000) as download_info:
            page.get_by_role(
                "button", name="Download one-page sales intelligence PDF"
            ).click()
        download_info.value.save_as(DOCS / "sales_intelligence_brief.pdf")
        browser.close()


if __name__ == "__main__":
    main()
