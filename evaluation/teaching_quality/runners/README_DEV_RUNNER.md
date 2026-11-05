# Teaching Quality Dev Runner (diagnostic)

This runner calls the **actual** `run_teacher_agent()` entrypoint, using the project's configured SiliconFlow model. It never executes Blind cases. The 24 Dev cases are selected from the frozen split with case-file SHA checks.

From the repository root:

```bash
uv run python evaluation/teaching_quality/runners/run_dev_eval.py --list
uv run python evaluation/teaching_quality/runners/run_dev_eval.py --smoke --execute --grade
```

After reviewing the 1-case smoke result, to capture all 24 Dev case slots:

```bash
uv run python evaluation/teaching_quality/runners/run_dev_eval.py --all --execute --grade
```

Requires a configured `SILICONFLOW_API_KEY`. The `--execute` switch is deliberate: it permits paid model calls. `--grade` additionally invokes the Semantic and Behavioral Judge where genuine output artifacts exist. `--list` does **not** invoke any model.

## Important validity limits

- The uploaded source bundle contained only `fixtures/README.md`, not importable controlled curriculum or question-bank fixtures. The runner does **not** invent those records or inject `case.setup` as fake database state.
- By default, **9 paper_quality Dev cases are SKIPPED_UNSEEDED_FIXTURE**. With `--all`, the remaining 15 consultation / teaching-design cases are executed. Use `--allow-unseeded-paper` only for exploratory paper-agent runs, never for official quality scores.
- Consultation requests are sent verbatim. Setup metadata is supplied to the grader but not injected into the Agent, so not all setup conditions are guaranteed to exist in the database. For teaching-design cases, the real runtime's `TeachingDesignRead.content` differs from the benchmark's `teaching_design.lessons`, `teaching_design.minutes`, and related expected paths. Missing fields are marked `NOT_FULLY_SCORABLE`, not fabricated or silently marked PASS/FAIL.
- Generated questions normally require preview and explicit confirmation. The runner does **not** send a synthetic confirmation turn. Drafts and pending actions are captured without falsely asserting that a paper exists.
- `official_benchmark: false` is always saved until controlled fixtures and output adapters are ready. Treat results as an integration diagnostic, not a publishable 24-case pass rate.
- Each case gets a **separate SQLite clone** (or empty SQLite for one-case smoke without source DB); the source database is never modified by this runner. Reports and per-case SQLite files are written under `reports/dev_eval_runs/`. Copying a large source database can use considerable disk space. Ancillary non-SQLite integrations are not sandboxed.
- Use `--output-dir <new-path>` to select a fresh output directory; existing output directories are not overwritten. The report is checkpointed after each case, including errors.

Send back the `REPORT=.../report.json` file after the smoke test. It contains Agent output, tool trace, the quality assessment if available, and explicit unscorable reasons.
