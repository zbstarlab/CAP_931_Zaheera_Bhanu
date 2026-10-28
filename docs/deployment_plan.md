# Deployment Plan

## Current status

SalesIntel AI is a tested local prototype. It is not deployed. It currently
runs with Streamlit, reads the Groq key from a local `.env` file, and processes
one research request inside the application.

A production version would need more security and reliability work before real
company documents or multiple users could use it.

## Simple hosting plan

The first version could run in a Python 3.12 container on a managed hosting
service. The service would use HTTPS and the same launch command:

```bash
uv run streamlit run src/cap_931/app.py
```

The deployment should use `uv.lock`, run the tests before release, and keep the
previous working version available in case a rollback is needed.

## API keys and user access

The Groq key should be stored in the hosting service's secret manager. It must
not be placed in the code, container image, GitHub repository, browser, or
logs. Development and production should use different keys.

The current app has no login. A production pilot should allow only approved
users. Saved reports must also be kept separate for each user or organization.

## Safe website retrieval

The current code checks URLs, DNS results, redirects, ports, domains, page
sizes, and request time. It blocks private, local, and loopback addresses and
respects `robots.txt`.

Python checks cannot completely prevent DNS rebinding, where a hostname changes
its address after validation. Production should also use restricted outbound
network access that blocks private networks, cloud metadata addresses, and
unapproved ports. Every redirect should still be checked again.

Blocked sites should remain warnings. The app should never bypass CAPTCHAs,
logins, paywalls, or other access controls.

## Usage and reliability

Production should limit research jobs, downloads, Groq calls, and tokens for
each user. Temporary provider errors can receive a small number of retries.
Repeated failures should stop new calls for a short time and show a clear
message.

A short-lived cache could reduce repeated website downloads. Cached sources
must keep their URL, retrieval time, and source label. Uploaded product text
should not be placed in a shared cache.

If only a few people use the app, one small service may be enough. A queue and
separate workers would only be needed if several research jobs must run at the
same time.

## Privacy

Product documents may contain private information. Users should be told that a
limited excerpt may be sent to Groq.

The safest plan is to process uploads in memory and remove them when the request
ends. If temporary storage is needed, files should be encrypted, separated by
user, scanned for malware, and deleted automatically after a short time.

## Logging and maintenance

Production logs should record request IDs, timing, source counts, failures,
security blocks, and token use. They should not store API keys, full uploaded
documents, full website text, or sensitive URL query values.

Useful alerts would include repeated provider failures, unusual destinations,
high error rates, and exhausted token or request limits.

Dependency and prompt changes should be tested before release. Prompt changes
should receive a new version so their results can be compared with earlier
versions.

The required checks are:

```bash
uv run pytest -v
uv run ruff check .
uv run python -m compileall -q src
```

## Cost

The saved experiments record tokens but do not contain a dollar cost, so I did
not create one. Real costs would include hosting, Groq usage, network traffic,
monitoring, login services, and storage.

A small single service would be simpler and cheaper. Extra workers, queues, and
monitoring would improve reliability but would also add cost and maintenance.

## Deployment stages

1. Finish the local report, screenshots, tests, and credential checks.
2. Create a private test deployment with HTTPS, managed secrets, and restricted
   network access.
3. Add login, privacy rules, usage limits, monitoring, and a small group of
   approved pilot users.
4. Add more workers or storage only if the pilot shows that they are needed.

The application should not be described as production-ready until these steps
are completed and tested.
