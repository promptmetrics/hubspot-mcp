from __future__ import annotations
from typing import Any
from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint

def _build(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Contact-based",
        "enrollment": {
            "type": "PROPERTY_BASED",
            "filter_branch": {
                "filterBranchType": "OR",
                "filterBranches": [{
                    "filterBranchType": "AND",
                    "filters": [
                        {"filterType": "PROPERTY", "property": "hubspot_owner_id", "operation": {"operationType": "ALL_PROPERTY", "operator": "IS_UNKNOWN", "includeObjectsWithNoValueSet": True}},
                    ]
                }]
            },
        },
        "actions": [
            {"step": 1, "ui_action": "Rotate leads", "fields": {"Rotate to": "<select team>"}},
            {"step": 2, "ui_action": "Create task", "fields": {"Title": "New lead auto-assigned — confirm contact within 5 min", "Due date": "{{timestamp + 5m}}", "Assigned to": "{{contact.hubspot_owner_id}}", "Priority": "High", "Notes": "This unassigned lead was auto-routed. Confirm contact within 5 minutes for optimal conversion."}},
        ],
        "prerequisites": ["Team with contact owners configured for round-robin"],
        "validation": ["Create test contact with no owner", "Verify contact gets assigned via round-robin and task created"],
    }

register_blueprint(WorkflowBlueprint(name="re_hygiene_unassigned", description="Round-robin assignment and 5-minute confirmation task for unassigned contacts.", tags=["real-estate", "hygiene", "lead-routing", "unassigned"], parameter_schema={}, build=_build))
