# Prompt Experiments

## Purpose

I wanted to see whether separate research prompts produced better results than
one larger prompt. I later compared the final specialized prompts with a format
that separated facts, sales ideas, questions, and missing evidence.

I used the same saved sources for each comparison. I kept the model responses
as they were returned and did not edit them to improve the scores.

## Test cases

The evidence-rich case used IBM as the prospect and Oracle as the competitor.
Three IBM sources were available. Oracle returned HTTP 403, so Oracle content
was not used.

The second case used blocked Oracle as the prospect. It had no readable public
sources. Both approaches skipped Groq and returned evidence gaps instead of
inventing findings.

The saved source snapshots and model responses are in
`artifacts/experiments/`.

## Model settings

All comparisons used:

- Groq model `openai/gpt-oss-20b`
- Temperature 0.1
- Low reasoning effort
- JSON response mode
- The same saved evidence
- Up to four sources with bounded text

The calls were spaced out because of Groq free-tier limits. API latency measures
provider processing time. Wall time also includes the planned waiting time
between calls.

## Scoring

Python checked the response format, citation IDs, source type, exact excerpts,
findings, gaps, tokens, and latency.

I manually reviewed factual accuracy, evidence support, product relevance,
coverage, usefulness, format, and unsupported assumptions.

I used this scale:

| Score | Meaning |
|---:|---|
| 1 | Mostly unsupported or unusable |
| 2 | Several important problems |
| 3 | Mixed result that needs review |
| 4 | Mostly supported and useful |
| 5 | Fully supported or a correct evidence-based abstention |

A valid citation ID only proves that the source was retrieved. It does not
prove that the source supports the full claim. I still had to read the output
and excerpts.

## First comparison

Baseline v1.1 used one call for company, competitor, and leadership research.
Specialized v2.1 used separate prompts and role-specific evidence.

| Result | Baseline v1.1 | Specialized v2.1 |
|---|---:|---:|
| IBM and blocked Oracle manual score | 3.0 / 5 | 4.2 / 5 |
| Blocked Oracle manual score | 3.6 / 5 | 3.8 / 5 |
| Two-case mean | 3.3 / 5 | 4.0 / 5 |
| Evidence-rich case findings | 6 | 2 |
| Valid citation IDs | 100% | 100% |
| Evidence-rich case tokens | 2,158 | 4,890 |
| API latency | 1.190 s | 1.590 s |

The baseline returned more findings, but it inferred responsibilities and
strategy from leadership titles. It also described a company relationship that
was not stated in the supplied excerpt.

The specialized approach returned fewer findings, but those findings were
better supported. It used about 2.27 times the baseline tokens.

Baseline v1.0 also had a real schema failure. It placed competitor and
leadership data inside the company object. I added a clearer JSON example and a
rule that the categories could not be nested. Baseline v1.1 then passed.

The first experiment used 9,139 provider-reported tokens when the failed
attempt was included. Groq did not report a dollar cost, so I did not invent
one.

## Final comparison

Specialized v3.1 required a claim, source IDs, exact excerpts, and a separate
sales-relevance note.

Grounded-sales v1.1 separated verified facts, possible sales relevance,
suggested questions, and missing evidence.

| Result | Specialized v3.1 | Grounded-sales v1.1 |
|---|---:|---:|
| Manual score | 3.57 / 5 | 3.43 / 5 |
| Findings retained | 2 | 4 |
| Total tokens | 3,409 | 3,806 |
| API latency | 1.403 s | 1.536 s |
| Exact excerpt validation | 100% | 100% |
| Provenance violations | 0 | 0 |

Both blocked-case responses scored 3.86 out of 5 because they correctly
reported missing evidence.

The first attempts at the final prompts also failed validation. Specialized
v3.0 returned gap objects instead of strings. Grounded-sales v1.0 returned
question and gap objects and joined some excerpts together. I added clearer JSON
examples and required plain strings. The revised prompts passed without
weakening the citation checks.

## What I learned

Specialized v3.1 received a slightly higher manual score and used fewer tokens.
Grounded-sales v1.1 returned more findings and questions, but some findings had
support or category problems.

This showed me that more output does not always mean better research. Exact
excerpt matching is useful, but it cannot prove that an excerpt supports every
part of a claim. Human review is still important.

I kept specialized v3.1 for the application. Python creates the final meeting
questions from validated findings and evidence gaps.

## Limitations

- There were only two cases, and only one had usable evidence.
- Oracle blocked access, so there was no full competitor comparison.
- One person completed the manual scoring.
- Model output can change between runs.
- Different response formats needed different token limits.

The results do not prove that one prompt will always perform better. A larger
test should use more companies, an accessible competitor pair, and another
reviewer.

## Saved evidence

- [Source snapshots](../artifacts/experiments/source_snapshots.json)
- [First model runs](../artifacts/experiments/model_runs.json)
- [First manual scores](../artifacts/experiments/manual_scores.json)
- [Final comparison](../artifacts/experiments/final_prompt_comparison.json)
- [Final manual scores](../artifacts/experiments/final_prompt_manual_scores.json)

New provider calls are only needed if the experiment is intentionally repeated:

```bash
uv run python -m cap_931.experiment_runner capture
uv run python -m cap_931.experiment_runner run --interval 20
uv run python -m cap_931.experiment_runner retry-baseline --interval 20
```

The saved JSON files are the source of truth for the numbers in this document.
