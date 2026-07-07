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
            "list_name": "[RE] Customers for Anniversary",
        },
        "actions": [
            {"step": 1, "ui_action": "Delay until date", "fields": {"Delay until": "{{contact.anniversary_date}}"}},
            {"step": 2, "ui_action": "Send marketing email", "fields": {"Email": "Happy Home-iversary!", "content_id": "<create email first>"}},
            {"step": 3, "ui_action": "Create task", "fields": {"Title": "Send anniversary card/gift", "Due date": "{{timestamp + 3d}}", "Assigned to": "{{contact.hubspot_owner_id}}", "Priority": "Low"}},
        ],
        "prerequisites": ["Static list '[RE] Customers for Anniversary' exists", "Contact property 'anniversary_date' exists", "Marketing email template created"],
        "validation": ["Add test contact with anniversary_date=today to the list", "Verify email sends and task created"],
    }

register_blueprint(WorkflowBlueprint(name="re_anniversary_touch", description="Annual anniversary email and gift task for past clients.", tags=["real-estate", "anniversary", "retention", "email"], parameter_schema={}, build=_build))
