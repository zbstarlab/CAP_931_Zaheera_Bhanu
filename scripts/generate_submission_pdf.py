"""Build the final local capstone report PDF from the Markdown report.

Run with:
    uv run --with markdown --with playwright python scripts/generate_submission_pdf.py
"""

import html
import tempfile
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "report.md"
OUTPUT = ROOT / "docs" / "CAP931_Zaheera_Report.pdf"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

CSS = """
@page { size: Letter; margin: 0.65in 0.62in 0.72in; }
html { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
body { color: #202634; font: 10.2pt/1.42 Arial, Helvetica, sans-serif; }
h1 { color: #15304a; font-size: 24pt; margin: 0 0 18pt; }
h2 { color: #15304a; font-size: 16pt; margin: 22pt 0 8pt;
     break-after: avoid-page; }
h3 { color: #244865; font-size: 12pt; margin: 14pt 0 6pt;
     break-after: avoid-page; }
p, li { orphans: 3; widows: 3; }
a { color: #145b88; text-decoration: none; }
blockquote { margin: 12pt 0; padding: 10pt 13pt; background: #fff4d8;
             border-left: 4pt solid #c88a16; break-inside: avoid-page; }
code { font: 8.7pt Menlo, Consolas, monospace; background: #edf1f5;
       padding: 1pt 2pt; }
pre { font: 8pt/1.35 Menlo, Consolas, monospace; white-space: pre-wrap;
      overflow-wrap: anywhere; padding: 9pt; color: #f2f4f8;
      background: #172033; border-radius: 4pt; break-inside: avoid-page; }
pre code { background: transparent; padding: 0; }
table { width: 100%; border-collapse: collapse; margin: 9pt 0 13pt;
        font-size: 8.2pt; break-inside: avoid-page; }
th, td { border: 0.6pt solid #b9c4d0; padding: 4.5pt; vertical-align: top; }
th { background: #e6edf4; color: #15304a; }
img { display: block; width: 100%; max-width: 100%; max-height: 8.55in;
      height: auto; object-fit: contain; margin: 8pt auto 18pt;
      break-inside: avoid-page; }
p:has(> img[alt="Opened one-page sales report"]) {
      break-before: page; break-after: page; }
hr { border: 0; border-top: 0.8pt solid #c8d1db; }
body > h2 { break-before: page; }
body > h2:first-of-type { break-before: auto; }
.cover { height: 8.45in; display: flex; flex-direction: column;
         justify-content: center; text-align: center; break-after: page; }
.cover h1 { font-size: 25pt; margin-bottom: 18pt; }
.cover p { font-size: 12pt; margin: 4pt; }
.contents { min-height: 8.1in; break-after: page; }
.contents h1 { margin-bottom: 24pt; }
.contents li { margin: 9pt 0; font-size: 11pt; }
blockquote { background: #f5f5f5; border-left-color: #666; }
"""


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    report_text = REPORT.read_text(encoding="utf-8")
    overview_marker = "## Overview"
    if overview_marker not in report_text:
        raise RuntimeError("The report must contain an Overview section.")
    report_text = overview_marker + report_text.split(overview_marker, 1)[1]
    report_html = markdown.markdown(
        report_text,
        extensions=["extra", "tables", "fenced_code", "toc"],
    )
    base_url = REPORT.parent.as_uri() + "/"
    cover = """
    <section class="cover">
      <h1>CAP 931: SalesIntel AI</h1>
      <p>Building an AI Sales Assistant</p>
      <p><strong>Zaheera Bhanu Shanawaz Ahamed</strong></p>
      <p>CAP 931 Capstone Project</p>
      <p>October 27, 2026</p>
    </section>
    """
    contents = """
    <section class="contents">
      <h1>Contents</h1>
      <ol>
        <li>Overview</li>
        <li>Steps</li>
        <li>Conclusion</li>
        <li>Appendix</li>
      </ol>
    </section>
    """
    document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<base href='{html.escape(base_url, quote=True)}'><style>{CSS}</style>"
        f"</head><body>{cover}{contents}{report_html}</body></html>"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    ) as temporary:
        temporary.write(document)
        temporary_path = Path(temporary.name)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(CHROME), headless=True, args=["--no-sandbox"]
            )
            page = browser.new_page()
            page.goto(temporary_path.as_uri(), wait_until="networkidle")
            page.emulate_media(media="print")
            page.pdf(
                path=OUTPUT,
                format="Letter",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    "<div style='width:100%;font:8px Arial;color:#667085;"
                    "text-align:center'>Zaheera Bhanu Shanawaz Ahamed | "
                    "<span class='pageNumber'></span></div>"
                ),
                margin={
                    "top": "0.65in",
                    "right": "0.62in",
                    "bottom": "0.72in",
                    "left": "0.62in",
                },
            )
            browser.close()
    finally:
        temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
