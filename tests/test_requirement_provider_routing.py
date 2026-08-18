from calculus_agent import api
from calculus_agent.api import _requirement_parser
from calculus_agent.config import Settings
from fastapi import HTTPException
import pytest

from calculus_agent.requirements.parser import OpenAICompatibleRequirementParser
from calculus_agent.papers.workflow import save_blueprint
from calculus_agent.schemas import NaturalLanguagePaperRequest, PaperBlueprint, SectionRequirement


def test_blueprint_parser_rejects_missing_siliconflow_key(monkeypatch):
    monkeypatch.delenv("CALCULUS_AGENT_SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    with pytest.raises(HTTPException) as error:
        _requirement_parser(settings)
    assert error.value.status_code == 503


def test_blueprint_parser_uses_only_siliconflow():
    settings = Settings(
        _env_file=None,
        bailian_api_key=None,
        SILICONFLOW_API_KEY="test-key",
        SILICONFLOW_BASE_URL="https://silicon.example/v1",
        SILICONFLOW_AGENT_MODEL="cloud-test",
        SILICONFLOW_TIMEOUT_SECONDS=55,
    )
    parser = _requirement_parser(settings)
    assert isinstance(parser, OpenAICompatibleRequirementParser)
    assert parser.base_url == "https://silicon.example/v1"
    assert parser.model == "cloud-test"
    assert parser.timeout == 55


def test_identical_requirement_reuses_local_cache(session, monkeypatch):
    calls = 0

    class FakeParser:
        def parse(self, requirement):
            nonlocal calls
            calls += 1
            return PaperBlueprint(total_questions=1, total_score=5)

    monkeypatch.setattr(api, "_requirement_parser", lambda settings: FakeParser())
    settings = Settings(_env_file=None, bailian_api_key=None, siliconflow_api_key=None)
    request = NaturalLanguagePaperRequest(requirement="生成一题测试卷")
    first = api.parse_blueprint(request, session, settings)
    second = api.parse_blueprint(request, session, settings)
    assert calls == 1
    assert first.cached is False
    assert second.cached is True
    assert first.blueprint_id != second.blueprint_id


def test_conversation_modification_uses_saved_blueprint_as_base(session, monkeypatch):
    base = save_blueprint(session, PaperBlueprint(
        total_questions=10,
        total_score=70,
        sections=[
            SectionRequirement(question_type="选择题", count=3, score_per_question=5, total_score=15),
            SectionRequirement(question_type="填空题", count=3, score_per_question=5, total_score=15),
            SectionRequirement(question_type="计算题", count=4, score_per_question=10, total_score=40),
        ],
    ))
    monkeypatch.setattr(
        api, "_requirement_parser",
        lambda settings: (_ for _ in ()).throw(AssertionError("增量修改不应重新调用模型")),
    )
    settings = Settings(_env_file=None, siliconflow_api_key=None)
    result = api.parse_blueprint(
        NaturalLanguagePaperRequest(
            requirement="加入3道证明题", base_blueprint_id=base.blueprint_id
        ),
        session,
        settings,
    )
    assert result.blueprint.total_questions == 13
    assert result.blueprint.question_type_counts == {
        "选择题": 3, "填空题": 3, "计算题": 4, "证明题": 3,
    }


def test_ambiguous_conversation_returns_clarification_without_model_key(
    session,
    monkeypatch,
):
    monkeypatch.delenv("CALCULUS_AGENT_SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    base = save_blueprint(session, PaperBlueprint(
        total_questions=2,
        total_score=10,
        sections=[
            SectionRequirement(question_type="选择题", count=2, score_per_question=5, total_score=10),
        ],
    ))
    settings = Settings(_env_file=None, siliconflow_api_key=None)

    result = api.parse_blueprint(
        NaturalLanguagePaperRequest(
            requirement="感觉还是不太合适",
            base_blueprint_id=base.blueprint_id,
        ),
        session,
        settings,
    )

    assert result.blueprint_id == base.blueprint_id
    assert result.needs_clarification is True
    assert "请说明" in (result.agent_message or "")
