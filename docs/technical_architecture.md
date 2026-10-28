# Technical Architecture

## Purpose

SalesIntel AI is a local Streamlit application. It helps a salesperson collect
public company information before a meeting and download a one-page sales
brief.

I kept the design small so I could understand and explain it. The three
research roles are focused Groq calls controlled by Python. They are not
independent agents that make their own plans.

## How the application works

1. The user enters product and company information in Streamlit.
2. Python checks the required fields and URLs.
3. The app reads an optional PDF or TXT product overview.
4. It retrieves a limited number of public company pages.
5. It labels every source as prospect or competitor evidence.
6. Groq runs company, competitor, and leadership research prompts.
7. Python checks the response format, source IDs, and evidence excerpts.
8. The user reviews the findings and downloads a one-page PDF.

## Main files

- `app.py` builds the Streamlit interface and runs the workflow.
- `validators.py` checks inputs and public URLs.
- `document_parser.py` reads PDF and TXT product documents.
- `web_research.py` retrieves public HTML and PDF sources.
- `prompts.py` contains the research prompts.
- `agents.py` selects evidence and runs the three research roles.
- `llm_client.py` connects to Groq.
- `schemas.py` defines the expected response formats.
- `sales_tools.py` handles review choices and meeting questions.
- `report_generator.py` creates the one-page PDF.
- `experiments.py` supports the saved prompt comparisons.

## Inputs and documents

The form has six fields: product name, prospective company URL, product
category, competitor URLs, target customer, and value proposition. Competitor
URLs are optional. The other five fields are required.

The category helper makes a short suggestion from the product name and value
proposition. It does not replace the user's category unless the user accepts
it.

One PDF or TXT product overview can be uploaded. The file is limited to 5 MB,
and the extracted text is limited to 8,000 characters. Empty, encrypted,
broken, image-only, and unsupported files are rejected. Scanned PDFs require
OCR, which is not part of this project.

Uploaded text is seller-provided context. It cannot be used as public evidence
about the prospect or a competitor.

## Public research and source checks

`research_companies()` keeps prospect and competitor sites separate.
`research_site()` retrieves the submitted page and a few useful links from the
same company domain. It gives priority to news, strategy, leadership,
investor, product, career, and technology pages.

Every destination is checked before retrieval. The code rejects local and
private addresses, limits redirects to three, stops redirect loops, respects
`robots.txt`, and limits page size, requests, and extracted text. It does not
bypass CAPTCHAs, login pages, paywalls, or anti-bot blocks.

Each successful source has an ID, title, URL, retrieval time, source type,
extracted text, and an optional publication date. Failed pages become warnings
and are not sent to Groq.

Company and leadership research use prospect sources. Competitor research can
use both source types, but competitor information cannot become a verified fact
about the prospect.

## Groq prompts and validation

The project uses `openai/gpt-oss-20b` through Groq. The prompts tell the model
to use only the supplied evidence and report missing information instead of
guessing.

Pydantic checks the JSON response shape. Each retained finding needs an allowed
source ID and an excerpt that appears in that source after safe whitespace
normalization. This proves that the excerpt exists, but it does not prove that
the excerpt supports every part of the claim. Human review is still needed.

## Review and PDF

The app shows verified findings, excerpts, possible sales relevance, evidence
gaps, and suggested meeting questions. The user can leave out a supported
finding before export. An unsupported finding cannot be changed into a verified
fact through the review screen.

ReportLab creates the sales brief, and pypdf checks that it has exactly one
page. The brief contains company, competitor, and leadership sections, meeting
questions, evidence gaps, and source links.

## Testing and limitations

The automated tests cover input checks, redirects, blocked pages, document
uploads, source separation, prompt responses, evidence excerpts, human review,
and PDF output. Mock responses are used so the tests do not depend on live
websites or Groq.

Important limits are:

- Some websites block automated requests or need JavaScript.
- The app stays on company-owned domains and does not search the whole web.
- AI findings still need human review.
- The app has no login, shared cache, background jobs, or monitoring.
- The prompt experiment was too small for broad conclusions.

The [deployment plan](deployment_plan.md) explains what a production version
would need. The [experiment notes](experiments.md) contain the saved prompt
results.
