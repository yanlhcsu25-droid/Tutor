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


def test_read_only_negation_does_not_trigger_paper_change_boundary():
    policy = ResponsePolicy()
    assert not policy.paper_change_intent("不要修改，检查当前卷有没有填空题。")
    assert not policy.paper_change_intent("只查看第2题的修改预览。")
    assert policy.paper_change_intent("不要修改分值，只删除第2题。")
    assert policy.paper_change_intent("删除第2题。")
    assert policy.paper_change_intent("删除第2题，只查看修改预览，不要应用。")


def test_question_type_summary_query_is_explicit_and_canonical():
    assert ResponsePolicy.question_type_summary_query("当前卷有几道选择题？") == "选择题"
    assert ResponsePolicy.question_type_summary_query("检查有没有填空题") == "填空题"
    assert ResponsePolicy.question_type_summary_query("有多少道解答题") == "计算题"
    assert ResponsePolicy.question_type_summary_query("查看选择题第2题") is None


def test_contextual_replacement_intent_requires_an_action_not_a_question():
    assert ResponsePolicy.contextual_replacement_intent("把这道题换掉")
    assert ResponsePolicy.contextual_replacement_intent("换一下刚才那题")
    assert not ResponsePolicy.contextual_replacement_intent("为什么这道题要换？")
    assert not ResponsePolicy.contextual_replacement_intent("这道题不要换")


def test_pending_pause_is_not_cancel():
    assert ResponsePolicy.pending_generation_pause_intent("先别生成，我还要再想想")
    assert ResponsePolicy.pending_generation_pause_intent("不要取消，只是先别确认")


def test_explicit_pending_cancel_is_not_pause():
    assert not ResponsePolicy.pending_generation_pause_intent("取消当前方案")
    assert not ResponsePolicy.pending_generation_pause_intent("先别生成了，取消这个方案")


def test_unrelated_negation_is_not_pending_pause():
    assert not ResponsePolicy.pending_generation_pause_intent("不要修改题型，只改总分")
    assert not ResponsePolicy.pending_generation_pause_intent("先降到90分，暂时不要确认")
