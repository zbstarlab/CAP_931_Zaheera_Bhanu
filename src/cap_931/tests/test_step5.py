from io import BytesIO

from pypdf import PdfReader

from cap_931.agents import run_agent
from cap_931.report_generator import _short, create_sales_pdf
from cap_931.schemas import AgentResult, EvidenceExcerpt, Finding
from cap_931.web_research import looks_blocked


def test_access_denial_is_not_evidence():
    assert looks_blocked('Blocked', 'Your request has been blocked. Your current User-Agent string appears to be from an automated process')
    assert not looks_blocked('Services', 'A company platform for analytics')


def test_company_agent_does_not_use_competitor_only_source():
    sources = [{'source_id':'S2','url':'https://competitor.example','title':'Other',
                'content':'Platform description', 'source_type':'competitor'}]
    def should_not_call(*args):
        raise AssertionError('Must not call model without prospect evidence')
    result = run_agent('company', {}, sources, generator=should_not_call)
    assert not result.findings
    assert result.gaps


def test_pdf_one_page_and_source_link():
    request = {'company_url':'https://prospect.example', 'product_name':'Analytics',
               'product_category':'Data','target_customer':'CTO','value_proposition':'Fast insights'}
    results = {'company':AgentResult(agent='company',findings=[Finding(claim='Prospect announced a public initiative.', source_ids=['S1'], evidence=[EvidenceExcerpt(source_id='S1', excerpt='Prospect announced a public initiative.')], relevance='Product fit')]),
               'competitor': AgentResult(agent='competitor'),
               'leadership': AgentResult(agent='leadership')}
    sources = [{'source_id':'S1','url':'https://prospect.example/news','title':'News'}]
    data = create_sales_pdf(request, results, sources)
    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) == 1
    assert 'Company Strategy' in reader.pages[0].extract_text()
    assert 'Meeting Preparation' in reader.pages[0].extract_text()
    assert '/Annots' in reader.pages[0]


def test_pdf_text_normalizes_unsupported_hyphens():
    assert _short("fault\u2011tolerant") == "fault-tolerant"


def test_pdf_with_reviewed_findings_and_questions_stays_one_page():
    request = {
        'company_url': 'https://prospect.example',
        'product_name': 'Sales Research',
        'product_category': 'AI Sales Intelligence',
        'target_customer': 'Chief Revenue Officer',
        'value_proposition': 'Faster evidence review',
    }
    sources = []
    results = {}
    for role in ('company', 'competitor', 'leadership'):
        findings = []
        for _ in range(2):
            source_id = f'S{len(sources) + 1}'
            excerpt = 'Public evidence supporting this factual statement.'
            findings.append(Finding(
                claim='A concise verified factual statement for meeting preparation.',
                source_ids=[source_id],
                evidence=[EvidenceExcerpt(source_id=source_id, excerpt=excerpt)],
                relevance='This may provide context for a sales conversation.',
            ))
            sources.append({
                'source_id': source_id,
                'url': f'https://prospect.example/{source_id}',
                'title': source_id,
            })
        results[role] = AgentResult(
            agent=role,
            findings=findings,
            gaps=['One important evidence gap remains.'],
        )

    data = create_sales_pdf(
        request,
        results,
        sources,
        [
            'What outcomes should an initial evaluation measure?',
            'Which constraints should be discussed before a pilot?',
            'Who should participate in the evaluation conversation?',
        ],
    )

    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert 'Possible sales relevance' in text
    assert 'Suggested Questions' in text
