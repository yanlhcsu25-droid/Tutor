# Tutor V3.2.1 Provider Resilience Report

## Change

The V3.2 infrastructure freeze was explicitly reopened to address provider rate limits and transient transport failures. Business Runtime, Prompt, Grader, Fixture, Holdout Dataset, and labels were not intentionally changed.

`BailianChatBackend` now retries only transient provider failures:

- HTTP 429;
- read timeout;
- timeout/connection errors wrapped by `URLError`;
- connection reset or remote disconnect.

The policy performs at most four retries, honors numeric `Retry-After`, otherwise waits 5, 10, 20, and 40 seconds, capped at 60 seconds. Other HTTP 4xx responses remain fail-fast. The eval runner waits five seconds between cases and records the delay in report metadata.

## Validation

- Full pytest: passed
- Ruff `src tests`: passed
- EVAL-056/057 timeout follow-up: **2/2 passed**
- Golden Smoke V1: **19/20**, with no provider failures; the remaining case is the intentionally preserved V1 pre-fail-closed lifecycle expectation
- Golden Smoke V2: **19/20** initially; the sole `RemoteDisconnected` case passed after adding connection retry coverage, giving **20/20 effective case coverage** across preserved reports
- HTTP 429 failures in the post-change targeted run: **0**

EVAL-059 remains the previously isolated ambiguous difficulty contract and did not cause Runtime tuning.

## Holdout integrity

- New manifest: `tests/evals/manifests/holdout_v3_2_1_69.yaml`
- Manifest SHA256: `ca8df1acfd8c28f7e7c896b5a447e0c09daf701f4b940b489c0f3ce665d1abab`
- Validation: 69 approved cases, valid component locks
- V3.1 baseline remains **25/69 = 36.23%**
- The second Official 69-case Holdout was **not run**
