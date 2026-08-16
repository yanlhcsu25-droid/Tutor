import json

import pytest

from calculus_agent.knowledge.classification import (
    classify_knowledge_points,
    suggest_question_knowledge,
)
from test_knowledge_classification import FakeBackend, _published_question


def _valid_payload(session, question, **updates):
    candidate_id = suggest_question_knowledge(session, question)[0]["knowledge_node_id"]
    payload = {
        "primary_knowledge_point_id": candidate_id,
        "secondary_knowledge_point_ids": [],
        "confidence": 0.8,
        "needs_review": False,
        "reason": "结构化测试",
    }
    payload.update(updates)
    return payload


def _tool_response(arguments):
    return {
        "message": {
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "submit_knowledge_classification",
                    "arguments": arguments,
                },
            }],
        },
        "finish_reason": "tool_calls",
    }


def _classify(session, response=None, error=None):
    question = _published_question(session)
    backend = FakeBackend(response=response, error=error)
    result = classify_knowledge_points(session, question, backend=backend)
    return result, backend, question


class SequenceBackend(FakeBackend):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)

    def complete(self, messages, tools, *, tool_choice="auto", response_format=None):
        self.tool_choice = tool_choice
        self.response_format = response_format
        return self.responses.pop(0)


def test_tool_call_arguments_accepts_legal_object(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    backend = FakeBackend(response=_tool_response(payload))
    result = classify_knowledge_points(session, question, backend=backend)
    assert result["provenance"] == "llm_suggested"
    assert result["llm_raw_response_type"] == "tool_calls"
    assert backend.response_format["type"] == "json_schema"


@pytest.mark.parametrize("confidence", [0, 1])
def test_schema_accepts_confidence_boundaries(session, confidence):
    question = _published_question(session)
    payload = _valid_payload(
        session,
        question,
        primary_knowledge_point_id=None,
        secondary_knowledge_point_ids=[],
        confidence=confidence,
    )
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(payload))
    )
    assert result["provenance"] == "llm_suggested"
    assert result["primary_knowledge_point"] is None
    assert result["secondary_knowledge_points"] == []
    assert result["confidence"] == confidence


def test_content_accepts_pure_json(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    response = {"message": {"content": json.dumps(payload)}}
    result = classify_knowledge_points(session, question, backend=FakeBackend(response=response))
    assert result["provenance"] == "llm_suggested"
    assert result["llm_raw_response_type"] == "content"


def test_content_accepts_fenced_json(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    response = {"message": {"content": f"```json\n{json.dumps(payload)}\n```"}}
    result = classify_knowledge_points(session, question, backend=FakeBackend(response=response))
    assert result["provenance"] == "llm_suggested"


def test_content_accepts_short_preface_then_json(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    response = {"message": {"content": f"结果如下：\n{json.dumps(payload)}"}}
    result = classify_knowledge_points(session, question, backend=FakeBackend(response=response))
    assert result["provenance"] == "llm_suggested"


def test_parsed_structured_response_precedes_content(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    response = {"message": {"parsed": payload, "content": "not json"}}
    result = classify_knowledge_points(session, question, backend=FakeBackend(response=response))
    assert result["provenance"] == "llm_suggested"
    assert result["llm_raw_response_type"] == "parsed"


def test_tool_calls_take_priority_over_empty_content(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(json.dumps(payload)))
    )
    assert result["provenance"] == "llm_suggested"
    assert result["llm_raw_response_type"] == "tool_calls"


def test_siliconflow_content_tool_serialization_is_structured(session):
    question = _published_question(session)
    candidate_id = suggest_question_knowledge(session, question)[0]["knowledge_node_id"]
    content = f"""<|begin_of_box|>submit_knowledge_classification
<arg_key>primary_knowledge_point_id</arg_key>
<arg_value>{candidate_id}</arg_value>
<arg_key>secondary_knowledge_point_ids</arg_key>
<arg_value>[]</arg_value>
<arg_key>confidence</arg_key>
<arg_value>0.9</arg_value>
<arg_key>needs_review</arg_key>
<arg_value>false</arg_value>
<arg_key>reason</arg_key>
<arg_value>固定工具序列化格式</arg_value>
</tool_call>"""
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response={"message": {"content": content}})
    )
    assert result["provenance"] == "llm_suggested"
    assert result["llm_raw_response_type"] == "content_tool_call"


def test_structured_failure_retries_once(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    backend = SequenceBackend([
        {"message": {"content": "not-json"}},
        {"message": {"content": json.dumps(payload)}},
    ])
    result = classify_knowledge_points(session, question, backend=backend)
    assert result["provenance"] == "llm_suggested"
    assert result["llm_attempt_count"] == 2


def test_invalid_tool_arguments_has_json_decode_reason(session):
    result, _backend, _question = _classify(session, response=_tool_response("{bad json"))
    assert result["provenance"] == "rule_fallback"
    assert result["fallback_reason"] == "json_decode_error"
    assert result["llm_raw_response_type"] == "tool_calls"


def test_missing_field_has_schema_detail(session):
    question = _published_question(session)
    payload = _valid_payload(session, question)
    payload.pop("reason")
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(payload))
    )
    assert result["fallback_reason"] == "schema_validation_error"
    assert {"field": "reason", "category": "missing_field"} in result["schema_validation_errors"]


def test_confidence_string_is_invalid_type(session):
    question = _published_question(session)
    payload = _valid_payload(session, question, confidence="0.8")
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(payload))
    )
    assert result["fallback_reason"] == "schema_validation_error"
    assert {"field": "confidence", "category": "invalid_type"} in result["schema_validation_errors"]


def test_secondary_string_is_not_list(session):
    question = _published_question(session)
    payload = _valid_payload(session, question, secondary_knowledge_point_ids="not-a-list")
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(payload))
    )
    assert result["fallback_reason"] == "schema_validation_error"
    assert {"field": "secondary_knowledge_point_ids", "category": "secondary_not_list"} in result["schema_validation_errors"]


def test_invalid_null_has_schema_detail(session):
    question = _published_question(session)
    payload = _valid_payload(session, question, secondary_knowledge_point_ids=None)
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(response=_tool_response(payload))
    )
    assert result["fallback_reason"] == "schema_validation_error"
    assert {"field": "secondary_knowledge_point_ids", "category": "invalid_null"} in result["schema_validation_errors"]


def test_timeout_has_specific_fallback_reason(session):
    result, _backend, _question = _classify(session, error=TimeoutError("timeout"))
    assert result["provenance"] == "rule_fallback"
    assert result["fallback_reason"] == "llm_timeout"


def test_api_error_has_specific_fallback_reason(session):
    result, _backend, _question = _classify(session, error=OSError("api unavailable"))
    assert result["provenance"] == "rule_fallback"
    assert result["fallback_reason"] == "api_error"


@pytest.mark.parametrize("response", [{}, {"message": {}}, {"message": {"content": ""}}])
def test_empty_response_has_specific_fallback_reason(session, response):
    result, _backend, _question = _classify(session, response=response)
    assert result["provenance"] == "rule_fallback"
    assert result["fallback_reason"] == "empty_response"
