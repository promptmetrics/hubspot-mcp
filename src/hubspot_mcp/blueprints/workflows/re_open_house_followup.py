from __future__ import annotations
from typing import Any
from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint

def _build(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Contact-based",
        "enrollment": {
            "type": "LIST_BASED",
            "trigger": "Contact is added to list",
            "list_name": "[RE] Open House Attendees",
        },
        "actions": [
            {"step": 1, "ui_action": "Delay", "fields": {"Delay for": "2 hours"}},
            {"step": 2, "ui_action": "Send marketing email", "fields": {"Email": "Thanks for visiting {{open_house.address}}", "content_id": "<create email first>"}},
            {"step": 3, "ui_action": "Delay", "fields": {"Delay for": "2 days"}},
            {"step": 4, "ui_action": "Create task", "fields": {"Title": "Follow up with open house attendee {{contact.firstname}}", "Due date": "{{timestamp + 1d}}", "Assigned to": "{{contact.hubspot_owner_id}}", "Priority": "Medium"}},
        ],
        "prerequisites": ["Static list '[RE] Open House Attendees' exists", "Marketing email template created"],
        "validation": ["Add a test contact to the list", "Verify email sends after 2h delay", "Verify follow-up task after 2 days"],
    }

register_blueprint(WorkflowBlueprint(name="re_open_house_followup", description="Open house attendee follow-up: thank-you email then task after 2 days.", tags=["real-estate", "open-house", "follow-up", "email"], parameter_schema={}, build=_build))
