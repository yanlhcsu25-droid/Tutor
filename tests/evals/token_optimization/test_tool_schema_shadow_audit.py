from tests.evals.token_optimization.tool_schema_shadow_audit import audit


def test_shadow_audit_covers_historical_read_without_model_or_database():
    turn = {
        "turn": 1,
        "user": "当前试卷有多少道题？",
        "business_state_before": {
            "paper": {"paper": [{"id": "paper-1"}]},
            "pending": {
                "agent_pending_generation": [],
                "agent_pending_adjustment": [],
                "agent_pending_replacement": [],
            },
            "teaching_design": {"active_teaching_design": []},
        },
        "observability": {
            "model_calls": [{"span_id": "model-1"}],
            "tool_calls": [{"name": "read_paper", "output_json": {"ok": True}}],
        },
    }
    report = audit(
        {"metadata": {"git_sha": "fixture"}, "results": [{
            "case_id": "READ-1",
            "passed": True,
            "turns": [turn],
        }]},
        source_path="fixture.json",
        source_sha256="abc",
    )

    assert report["method"]["model_calls"] == 0
    assert report["overall"]["historical_required_tool_coverage"] == 1.0
    assert report["overall"]["unsafe_pruning_cases"] == 0
    assert report["overall"]["weighted_schema_chars_reduction"] > 0
