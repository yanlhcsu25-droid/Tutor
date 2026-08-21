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
