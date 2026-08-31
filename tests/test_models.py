from hubspot_mcp.models import TaskIntent, RiskLevel, PlanStep, ExecutionPlan, PreviewResult, AgentResult


def test_task_intent_creation():
    intent = TaskIntent(
        intent_type="search_objects",
        target_object="contacts",
        description="find contacts in northeast",
        risk_level=RiskLevel.LOW,
        required_scopes=["crm.objects.contacts.read"],
    )
    assert intent.intent_type == "search_objects"
    assert intent.risk_level == RiskLevel.LOW


def test_execution_plan_creation():
    plan = ExecutionPlan(
        plan_id="plan-1",
        thread_id="thread-1",
        steps=[
            PlanStep(step_number=1, agent="ObjectsAgent", action="search contacts")
        ],
        overall_risk=RiskLevel.MEDIUM,
        rollback_available=True,
        estimated_duration_seconds=30,
    )
    assert plan.overall_risk == RiskLevel.MEDIUM


def test_preview_result_creation():
    result = PreviewResult(
        preview={"affected": [{"id": "1"}]},
        impact_count=1,
        risk_level=RiskLevel.DESTRUCTIVE,
        proposed_payload={"endpoint": "/crm/v3/objects/contacts/1"},
        original_values={"contacts": [{"id": "1", "email": "old@example.com"}]},
    )
    assert result.impact_count == 1
    assert result.risk_level == RiskLevel.DESTRUCTIVE
    assert result.informing_sources == []


def test_preview_result_informing_sources():
    result = PreviewResult(
        preview={"affected": [{"id": "1"}]},
        impact_count=1,
        risk_level=RiskLevel.MEDIUM,
        informing_sources=[
            {"source": "official", "trust_tier": "official", "title": "Docs", "url": "https://developers.hubspot.com/docs"},
        ],
    )
    assert len(result.informing_sources) == 1
    assert result.informing_sources[0]["source"] == "official"


def test_agent_result_creation():
    result = AgentResult(
        agent_name="ObjectsAgent",
        status="success",
        data={"count": 5},
    )
    assert result.status == "success"
    assert not result.retryable
