from calculus_agent.runtime.response_policy import ResponsePolicy


def test_response_policy_detects_protocol_and_pending_lifecycle_gap():
    policy = ResponsePolicy()
    assert policy.contains_leaked_tool_protocol("答案</think><tool_call>")
    assert policy.requires_pending_paper_change_recheck(
        pending_adjustment=True,
        trace_calls=[{"tool_name": "read_paper"}],
        already_rechecked=False,
    )
    assert not policy.requires_pending_paper_change_recheck(
        pending_adjustment=True,
        trace_calls=[{"tool_name": "preview_paper_changes"}],
        already_rechecked=False,
    )


def test_response_policy_detects_paper_change_intent():
    assert ResponsePolicy().paper_change_intent("把第3题换简单一点")
    assert not ResponsePolicy().paper_change_intent("你好")
