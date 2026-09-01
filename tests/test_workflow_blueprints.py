import pytest

from hubspot_mcp.agents.workflows import get_workflows_agent_prompt
from hubspot_mcp.blueprints.workflows import (
    WorkflowBlueprint,
    build_blueprint_context,
    get_blueprint,
    list_blueprints,
    register_blueprint,
)
from hubspot_mcp.blueprints.workflows.converter import blueprint_to_v4_payload


def build_welcome_email(params):
    return get_blueprint("welcome_email").build(dict(params))


def build_lead_scoring(params):
    return get_blueprint("lead_scoring").build(dict(params))


def build_deal_stage_task(params):
    return get_blueprint("deal_stage_task").build(dict(params))


def build_re_engagement(params):
    return get_blueprint("re_engagement").build(dict(params))


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    import hubspot_mcp.blueprints.workflows as reg

    original = dict(reg._BLUEPRINT_REGISTRY)
    yield
    reg._BLUEPRINT_REGISTRY.clear()
    reg._BLUEPRINT_REGISTRY.update(original)


def test_list_blueprints_has_starter_set():
    names = {bp.name for bp in list_blueprints()}
    assert names >= {"welcome_email", "lead_scoring", "deal_stage_task", "re_engagement"}


def test_get_blueprint_found():
    bp = get_blueprint("welcome_email")
    assert bp is not None
    assert bp.name == "welcome_email"
    assert "welcome" in bp.description.lower()


def test_get_blueprint_missing():
    assert get_blueprint("nonexistent") is None


def test_register_blueprint():
    bp = WorkflowBlueprint(
        name="test_bp",
        description="A test blueprint.",
        tags=["test"],
        parameter_schema={"foo": {"type": "string", "default": "bar"}},
        build=lambda p: {"name": p.get("foo", "bar")},
    )
    register_blueprint(bp)
    assert get_blueprint("test_bp") is bp


class TestWelcomeEmailBlueprint:
    def test_build_defaults(self):
        payload = build_welcome_email({})
        assert payload["object_type"] == "Contact-based"
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["ui_action"] == "Send internal email notification"
        assert payload["actions"][0]["fields"]["Subject"] == "Welcome!"
        assert payload["enrollment"]["type"] == "EVENT_BASED"

    def test_build_custom_params(self):
        payload = build_welcome_email({
            "delay_hours": 2,
            "subject": "Hello!",
            "body": "Welcome aboard.",
        })
        assert payload["object_type"] == "Contact-based"
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["ui_action"] == "Delay"
        assert payload["actions"][0]["fields"]["Delay for"] == "2 hours"
        assert payload["actions"][1]["ui_action"] == "Send internal email notification"
        assert payload["actions"][1]["fields"]["Subject"] == "Hello!"


class TestLeadScoringBlueprint:
    def test_build_defaults(self):
        payload = build_lead_scoring({})
        assert payload["object_type"] == "Contact-based"
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["ui_action"] == "Set property value"
        assert payload["actions"][1]["ui_action"] == "If/then branch"

    def test_build_no_threshold(self):
        payload = build_lead_scoring({"threshold": 0})
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["ui_action"] == "Set property value"

    def test_build_custom_params(self):
        payload = build_lead_scoring({
            "score_property": "mycustomscore",
            "increment": 5,
            "threshold": 100,
        })
        set_action = payload["actions"][0]
        assert set_action["fields"]["Property"] == "mycustomscore"
        assert set_action["fields"]["Value"] == "5"
        assert payload["enrollment"]["type"] == "PROPERTY_BASED"


class TestDealStageTaskBlueprint:
    def test_build_defaults(self):
        payload = build_deal_stage_task({})
        assert payload["object_type"] == "Deal-based"
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["ui_action"] == "Create task"
        assert payload["enrollment"]["type"] == "PROPERTY_BASED"

    def test_build_custom_params(self):
        payload = build_deal_stage_task({
            "stage": "closedwon",
            "task_title": "Close celebration",
            "due_days": 3,
        })
        fb = payload["enrollment"]["filter_branch"]["filterBranches"][0]["filters"][0]
        assert fb["property"] == "dealstage"
        assert fb["operation"]["values"] == ["closedwon"]
        task = payload["actions"][0]["fields"]
        assert task["Title"] == "Close celebration"
        assert task["Due date"] == "{timestamp + 3d}"


class TestReEngagementBlueprint:
    def test_build_defaults(self):
        payload = build_re_engagement({})
        assert payload["object_type"] == "Contact-based"
        assert len(payload["actions"]) == 2
        assert payload["actions"][0]["ui_action"] == "Delay"
        assert payload["actions"][0]["fields"]["Delay for"] == "90 days"
        assert payload["actions"][1]["fields"]["Subject"] == "We miss you!"

    def test_build_custom_params(self):
        payload = build_re_engagement({
            "inactive_days": 60,
            "subject": "Come back!",
            "body": "We have new features.",
        })
        assert payload["object_type"] == "Contact-based"
        assert payload["actions"][0]["fields"]["Delay for"] == "60 days"
        assert payload["actions"][1]["fields"]["Body"] == "We have new features."


class TestBlueprintContext:
    def test_build_blueprint_context_includes_all(self):
        ctx = build_blueprint_context()
        assert "welcome_email" in ctx
        assert "lead_scoring" in ctx
        assert "deal_stage_task" in ctx
        assert "re_engagement" in ctx
        assert "Before building a workflow from scratch" in ctx

    def test_build_blueprint_context_shows_parameters(self):
        ctx = build_blueprint_context()
        assert "delay_hours" in ctx
        assert "score_property" in ctx


class TestWorkflowsAgentPrompt:
    def test_prompt_includes_blueprint_context(self):
        prompt = get_workflows_agent_prompt()
        assert "Workflow Blueprints" in prompt.system_prompt
        assert "welcome_email" in prompt.system_prompt
        assert "deal_stage_task" in prompt.system_prompt

    def test_prompt_still_has_tools(self):
        prompt = get_workflows_agent_prompt()
        assert "hubspot_create_workflow" in prompt.system_prompt


class TestConverterV4Shape:
    def test_event_based_contact_payload_shape(self):
        payload = blueprint_to_v4_payload(
            {
                "name": "X",
                "object_type": "Contact-based",
                "enrollment": {"type": "EVENT_BASED", "trigger": "Contact is created"},
                "actions": [],
            }
        )
        assert payload["flowType"] == "WORKFLOW"
        assert payload["objectTypeId"] == "0-1"
        assert payload["type"] == "CONTACT_FLOW"
        assert payload["isEnabled"] is False
        ec = payload["enrollmentCriteria"]
        assert ec["type"] == "EVENT_BASED"
        assert ec["eventFilterBranches"][0]["eventTypeId"] == "4-1463224"
        assert ec["eventFilterBranches"][0]["operator"] == "HAS_COMPLETED"

    def test_list_based_payload_has_list_filter_branch(self):
        payload = blueprint_to_v4_payload(
            {
                "name": "Y",
                "object_type": "Deal-based",
                "enrollment": {"type": "LIST_BASED", "filter_branch": {"filters": []}},
                "actions": [],
            }
        )
        assert payload["objectTypeId"] == "0-3"
        assert payload["type"] == "PLATFORM_FLOW"
        assert payload["enrollmentCriteria"]["type"] == "LIST_BASED"
        assert "listFilterBranch" in payload["enrollmentCriteria"]
