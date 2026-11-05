# Tutor Workflow Eval 300 v2

## Validation

- Cases: 300
- Unique IDs: 300
- Dev: 200
- Holdout Candidate: 100
- Invalid status-as-user turns: 0
- Missing expected.status: 0
- Acceptance/expected status conflicts: 0
- Missing difficulty/primary_risk: 0
- Cases with non-empty required_tools: 213

## Difficulty

- Easy: 90
- Medium: 118
- Hard: 92

## Categories

{
  "generation": 70,
  "pending": 45,
  "paper": 80,
  "teaching": 45,
  "ambiguity": 20,
  "adversarial": 10,
  "multi_turn": 30
}

## Important

The 100 holdout cases are still `holdout_candidate`, not frozen holdout.
Run deterministic dataset audit and human semantic review before any real-model smoke/freeze.
