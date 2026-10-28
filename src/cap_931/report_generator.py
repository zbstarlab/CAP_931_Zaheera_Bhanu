"""Compact, source-attributed, one-page CAP 931 sales brief."""
from io import BytesIO
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate

from .sales_tools import generate_discovery_questions


def _short(text, limit=260):
    text = str(text).translate(str.maketrans({
        '\u00a0': ' ',
        '\u2010': '-',
        '\u2011': '-',
        '\u2012': '-',
        '\u202f': ' ',
    }))
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(' ', 1)[0] + '…'


def create_sales_pdf(
    request: dict,
    results: dict,
    sources: list[dict],
    questions: list[str] | None = None,
) -> bytes:
    """Create exactly one page, failing if content overflows rather than hiding it."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(612, 792), leftMargin=44,
                            rightMargin=44, topMargin=36, bottomMargin=35,
                            title='Sales Intelligence Brief')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BriefTitleCustom', parent=styles['Title'],
                              fontName='Helvetica-Bold', fontSize=15, leading=18,
                              textColor=colors.HexColor('#15304A'), spaceAfter=6))
    styles.add(ParagraphStyle(name='BriefHeadingCustom', parent=styles['Heading2'],
                              fontName='Helvetica-Bold', fontSize=10, leading=12,
                              spaceBefore=9, spaceAfter=3, textColor=colors.HexColor('#15304A')))
    styles.add(ParagraphStyle(name='BriefBodyCustom', parent=styles['BodyText'],
                              fontSize=8, leading=10.5, spaceAfter=4, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name='BriefSmallCustom', parent=styles['BodyText'],
                              fontSize=7, leading=8.5, spaceAfter=3))
    story = [Paragraph('SALES INTELLIGENCE BRIEF', styles['BriefTitleCustom'])]
    for label, key in [('Prospect', 'company_url'), ('Product', 'product_name'),
                       ('Category', 'product_category'), ('Target', 'target_customer')]:
        story.append(Paragraph(f'<b>{label}:</b> {escape(_short(request.get(key, "Not provided"), 90))}',
                               styles['BriefSmallCustom']))
    headings = [('company', 'Company Strategy'), ('competitor', 'Competitor Intelligence'),
                ('leadership', 'Leadership Intelligence')]
    cited = set()
    for role, heading in headings:
        section = [Paragraph(heading, styles['BriefHeadingCustom'])]
        result = results.get(role)
        findings = getattr(result, 'findings', []) if result is not None else []
        for finding in findings[:2]:
            refs = ', '.join(finding.source_ids)
            cited.update(finding.source_ids)
            section.append(Paragraph(f'<b>Verified:</b> {escape(_short(finding.claim, 180))} '
                                     f'<font color="#365a78">[{escape(refs)}]</font>',
                                     styles['BriefBodyCustom']))
            if finding.evidence:
                section.append(Paragraph(
                    '<b>Evidence:</b> “' + escape(
                        _short(finding.evidence[0].excerpt, 105)
                    ) + '”',
                    styles['BriefSmallCustom'],
                ))
            section.append(Paragraph(
                '<b>Possible sales relevance:</b> '
                + escape(_short(finding.relevance, 100)),
                styles['BriefSmallCustom'],
            ))
        if not findings:
            section.append(Paragraph('Not verified from the retrieved evidence.', styles['BriefBodyCustom']))
        if result is not None and getattr(result, 'gaps', None):
            section.append(Paragraph('<i>Evidence gap: ' + escape(_short(result.gaps[0], 115)) + '</i>',
                                     styles['BriefSmallCustom']))
        story.append(KeepTogether(section))
    story.append(Paragraph(
        'Meeting Preparation (Suggested Questions)', styles['BriefHeadingCustom']
    ))
    prepared_questions = questions or generate_discovery_questions(request, results)
    for question in prepared_questions[:3]:
        story.append(Paragraph(
            '&bull; ' + escape(_short(question, 155)),
            styles['BriefSmallCustom'],
        ))
    story.append(Paragraph('Sources', styles['BriefHeadingCustom']))
    relevant = [s for s in sources if s['source_id'] in cited]
    if not relevant:
        story.append(Paragraph('No findings supported by retrieved sources.', styles['BriefSmallCustom']))
    for source in relevant[:6]:
        url = source['url']
        if not url.startswith(('https://', 'http://')):
            continue
        story.append(Paragraph(f"[{escape(source['source_id'])}] "
                               f"<link href=\"{escape(url, {'\"': '&quot;'})}\">"
                               f"{escape(_short(url, 88))}</link>", styles['BriefSmallCustom']))
    doc.build(story)
    data = buffer.getvalue()
    if len(PdfReader(BytesIO(data)).pages) != 1:
        raise ValueError('Report exceeded one page. Reduce input or shorten findings.')
    return data
