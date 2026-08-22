from calculus_agent.agent.context_builder import AgentContextBuilder


def test_llm_workspace_projection_excludes_runtime_internals():
    source = {
        "working_memory": {
            "active_task": {
                "type": "teaching_planning",
                "status": "drafted",
                "target_topic": "极限",
                "curriculum_semantic_matches": ["large"],
            },
            "generation_summary": {"internal": "large"},
        },
        "active_teaching_design": {
            "version_id": "v1",
            "status": "awaiting_confirmation",
            "metadata": {"audit": "internal"},
            "content": {
                "scope_names": ["第一章"],
                "objective": "掌握极限",
                "knowledge_plan": [
                    {"name": "极限", "role": "required", "priority": 5}
                ],
                "assessment_plan": {"total_score": 100},
                "evidence_refs": [{"ref_id": "internal"}],
            },
        },
    }

    projected = AgentContextBuilder.project_workspace(source)

    assert projected["working_memory"] == {
        "active_task": {
            "type": "teaching_planning",
            "status": "drafted",
            "target_topic": "极限",
        }
    }
    assert projected["active_teaching_design"] == {
        "version_id": "v1",
        "status": "awaiting_confirmation",
        "scope": ["第一章"],
        "goal": "掌握极限",
        "key_knowledge": [{"name": "极限", "role": "required", "priority": 5}],
    }
    assert source["working_memory"]["generation_summary"] == {"internal": "large"}


def test_llm_workspace_projection_includes_compact_learning_context():
    projected = AgentContextBuilder.project_workspace({
        "working_memory": {
            "active_task": {"type": "information_request", "status": "completed"},
            "active_learning_context": {
                "scope_names": ["第三章 微分中值定理与导数的应用"],
                "knowledge_names": ["洛必达法则"],
                "learning_need": "学生洛必达法则老算错",
                "evidence_refs": [{"ref_id": "large-internal-evidence"}],
                "source_run_id": "run-internal",
            },
        },
        "active_teaching_design": None,
    })

    assert projected["working_memory"]["active_learning_context"] == {
        "scope_names": ["第三章 微分中值定理与导数的应用"],
        "knowledge_names": ["洛必达法则"],
        "learning_need": "学生洛必达法则老算错",
    }
