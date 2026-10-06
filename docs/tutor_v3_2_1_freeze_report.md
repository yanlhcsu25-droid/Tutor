# Tutor V3.2.1 Freeze Report

Tutor V3.2.1 is frozen with provider resilience enabled.

- Infrastructure checkpoint: `b9635dd56005739a9825cf26e77a2a02f82bbf65`
- Acceptance evidence commit: `6c46c044acf5bd53d6e37a7438b8e7b7a7e27970`
- Holdout manifest: `tests/evals/manifests/holdout_v3_2_1_69.yaml`
- Holdout manifest SHA256: `ca8df1acfd8c28f7e7c896b5a447e0c09daf701f4b940b489c0f3ce665d1abab`
- Freeze manifest: `evaluations/tutor_v3_2_1_freeze_manifest.json`
- Freeze manifest SHA256: `4c65cb0efa50b666364db3287096b4327fade5c554abcc09b051b6cbfc477b6b`

Frozen transport policy: four retries for HTTP 429, timeout, and connection errors; 5-second exponential base, 60-second cap, numeric `Retry-After` honored, and 5 seconds between eval cases. Other HTTP 4xx responses fail immediately.

Full pytest and Ruff passed. Post-change Golden Smoke had no HTTP 429 failures; transient timeout/disconnect cases passed targeted retry. V3.1 remains **25/69 = 36.23%**.

No second Official Holdout was run. Runtime, Prompt, Grader, Fixture, Dataset, labels, retry policy, and pacing policy are locked pending explicit authorization.
