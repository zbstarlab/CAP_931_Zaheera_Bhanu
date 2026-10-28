# SalesIntel AI

SalesIntel AI is my CAP 931 capstone project. I built it to help a salesperson
research a company before a meeting. The app gathers a small amount of public
information, checks the AI response against its sources, and creates a one-page
sales brief.

This is a local student project, not a production system. A person should still
review the results before using them.

## Main features

- Streamlit form with six sales inputs
- Optional PDF or TXT product overview
- Optional product-category suggestion
- Public website and PDF research
- Company strategy, competitor, and leadership research roles
- Source links and supporting excerpts
- Suggested meeting questions
- Checkboxes for excluding findings before export
- One-page PDF download

Competitor URLs are shown on the form but are optional. The other five fields
are required.

## Tools

- Python 3.12 and uv
- Streamlit
- Groq with `openai/gpt-oss-20b`
- Requests and BeautifulSoup
- Pydantic
- pypdf and ReportLab
- pytest and Ruff

The three research roles are separate model calls managed by Python. They are
not independent or fully autonomous agents.

## Project folders

| Folder | Purpose |
|---|---|
| `src/cap_931/` | Application code |
| `src/cap_931/tests/` | Automated tests |
| `artifacts/experiments/` | Saved prompt experiment results |
| `docs/` | Report, PDFs, screenshots, and supporting documents |
| `scripts/` | Helpers for creating evidence and reports |

## Setup

Install the project:

```bash
uv sync
```

Create a local `.env` file:

```dotenv
GROQ_API_KEY=your_key_here
GROQ_MODEL=openai/gpt-oss-20b
```

The `.env` file is ignored by Git. The real API key must not appear in code,
screenshots, experiment files, or documentation.

## Run the app

```bash
uv run streamlit run src/cap_931/app.py
```

Open the local address shown by Streamlit, usually `http://localhost:8501`.

## How the app works

1. The user enters product and company information.
2. Python checks the required fields and URLs.
3. The app reads an optional product document as seller-provided context.
4. It retrieves a limited number of public company pages.
5. Groq runs the three research roles.
6. Pydantic checks the response format.
7. Python checks source IDs and exact excerpts.
8. The user reviews the findings and downloads the PDF.

Company and leadership findings must use prospect sources. Competitor sources
stay separate so they cannot be reported as facts about the prospect.

## Research limits

The retriever checks every URL and redirect, stops redirect loops, stays on the
approved company domain, and respects `robots.txt`. It does not bypass login
pages, CAPTCHAs, paywalls, or anti-bot blocks.

- HTML downloads: 3 MB
- Public PDFs: 5 MB and 20 pages
- Product uploads: 5 MB and 8,000 extracted characters

Scanned PDFs need OCR, which is outside this project. Some sites, including
Microsoft and Oracle during testing, may block automated requests. Blocked
pages are shown as warnings and are not used as evidence.

## Tests

```bash
uv run pytest -v
uv run ruff check .
uv run python -m compileall -q src
```

Latest local result:

- 98 tests passed
- Ruff passed
- Python compilation passed

The automated tests use mock web and Groq responses. Live websites can change.

## Prompt experiment

I compared a single prompt with separate role prompts. I later compared the
final specialized prompt with a grounded-sales format.

| Approach | Manual score | Findings | Tokens | API latency |
|---|---:|---:|---:|---:|
| Specialized v3.1 | 3.57 / 5 | 2 | 3,409 | 1.403 s |
| Grounded-sales v1.1 | 3.43 / 5 | 4 | 3,806 | 1.536 s |

The grounded-sales prompt returned more findings, but the specialized prompt
received a slightly higher score and used fewer tokens. This was a small test,
so it does not prove that one prompt will always be better.

See [Prompt experiments](docs/experiments.md) for the saved results.

## Known limits

- Some sites block automated access or need JavaScript.
- The app does not search the full public web.
- A matching excerpt does not prove that the full claim is correct.
- Model answers can change between runs.
- The app is not deployed and has no user login.

## Project documents

- [KB report source](docs/report.md)
- [KB report PDF](docs/CAP931_Zaheera_Report.pdf)
- [Sales intelligence brief](docs/sales_intelligence_brief.pdf)
- [Technical architecture](docs/technical_architecture.md)
- [Prompt experiments](docs/experiments.md)
- [Deployment plan](docs/deployment_plan.md)
- [Final test results](docs/evidence/final_test_results.txt)

## GitHub repository

The project is available at:

[github.com/zbstarlab/CAP_931_Zaheera_Bhanu](https://github.com/zbstarlab/CAP_931_Zaheera_Bhanu)

The current repository branch is `master`.

## Final submission checks

1. Check that the screenshots in the report are readable.
2. Use the official Per Scholas form if the instructor provides a separate one.
3. Run the tests, Ruff, compilation, and a final credential check.
4. Commit and push any final documentation updates.

```bash
git add .
git commit -m "Update final CAP 931 documentation"
git push origin master
```
