from __future__ import annotations
from typing import Any
from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint

def _build(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Custom object (Showings)",
        "enrollment": {
            "type": "EVENT_BASED",
            "trigger": "Showing record is created",
        },
        "actions": [
            {"step": 1, "ui_action": "Delay", "fields": {"Delay for": "2 hours"}},
            {"step": 2, "ui_action": "Create task", "fields": {"Title": "Collect buyer feedback from showing {{showing.id}}", "Due date": "{{timestamp + 4h}}", "Assigned to": "{{showing.showing_agent}}", "Priority": "Medium", "Notes": "Follow up with buyer to gather feedback after the showing."}},
        ],
        "prerequisites": ["Custom object 'Showings' is active", "Property 'showing_agent' exists on Showings"],
        "validation": ["Create a test Showing record", "Verify task appears 2 hours after showing datetime"],
    }

register_blueprint(WorkflowBlueprint(name="re_showing_feedback", description="Post-showing feedback task fired 2 hours after a showing is created.", tags=["real-estate", "showing", "feedback", "task"], parameter_schema={}, build=_build))
