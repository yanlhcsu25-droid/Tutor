# Official Holdout V3.1 — First Run

## Sealed result

- Denominator: **69**
- Passed: **25**
- Failed: **44**
- Raw pass rate: **36.23%**
- Formal run status: **complete**
- Retries: **none**

This is the immutable first official result. Provider failures remain failures in the 69-case denominator; no adjusted pass rate is reported.

## Locked provenance

- Git SHA: `e855f32d83d4ec14bb30aef97015d33626844756` (clean detached worktree)
- Authorized manifest SHA256: `8a2f369e0c605fffb99047e49af3158f81dde177e9569a95b838df8c1725b152`
- Dataset file SHA256: `2f3c9c48936a8208a53f850194d15118be9601e380e1249726a42cc140e394ad`
- Provider/model: `siliconflow` / `Qwen/Qwen3.5-35B-A3B`
- Adapter/protocol: `BailianChatBackend` / `openai_compatible`
- Temperature: 0
- Max tool rounds: 8
- Provider timeout: 120 seconds
- Provider retries: 0

## Raw artifacts

- Report: `tests/evals/reports/official-holdout-v3.1-69-first-20261005T132623Z.json`
  - SHA256: `f935c71c228d6fda3d64b2fa4634ac94faf9349aa1b79107c94c464308943aaa`
- Log: `tests/evals/reports/official-holdout-v3.1-69-first-20261005T132623Z.log`
  - SHA256: `bad8c9097fb3c96a91a1425bcb13c6424c6b065177c0e17854ffad2f56167bb8`

## Failure taxonomy

Of the 44 raw failures:

- Provider HTTP 429: **13**
- Provider timeout: **2**
- Non-infrastructure automatic `clarification_contract`: **26**
- Non-infrastructure automatic `model_reasoning`: **2**
- Non-infrastructure automatic `side_effect_violation`: **1**

These labels do not alter the official score. `confirmed_grader_gaps` is empty at archive time; future analysis may identify gaps, but it cannot rewrite this first result.

## Usage and latency

- Model calls: **140**
- Tool calls / rounds: **75 / 75**
- Usage available: **42/69 cases**
- Usage missing: **27/69 cases**; missing usage remains unknown, not zero
- Recorded tokens: **545,230**
  - Input: 529,661
  - Output: 15,569
  - Cached: 22,272
- Latency: median 4,984 ms; p95 34,369 ms; max 128,805 ms

Automatic grader pass is not equivalent to human-confirmed Task Success.
