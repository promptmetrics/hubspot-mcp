from __future__ import annotations
from typing import Any
from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint

def _build(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Deal-based",
        "enrollment": {
            "type": "PROPERTY_BASED",
            "filter_branch": {
                "filterBranchType": "OR",
                "filterBranches": [{
                    "filterBranchType": "AND",
                    "filters": [
                        {"filterType": "PROPERTY", "property": "contingency_inspection_deadline", "operation": {"operationType": "ALL_PROPERTY", "operator": "IS_KNOWN", "includeObjectsWithNoValueSet": False}},
                        {"filterType": "PROPERTY", "property": "dealstage", "operation": {"operationType": "ENUMERATION", "operator": "IS_ANY_OF", "values": ["undercontract"], "includeObjectsWithNoValueSet": False}},
                    ]
                }]
            },
        },
        "actions": [
            {"step": 1, "ui_action": "Create task", "fields": {"Title": "Schedule inspection", "Due date": "{{contingency_inspection_deadline - 3d}}", "Assigned to": "{{deal.hubspot_owner_id}}", "Priority": "High"}},
            {"step": 2, "ui_action": "Delay until date", "fields": {"Delay until": "{{contingency_inspection_deadline - 1d}}"}},
            {"step": 3, "ui_action": "If/then branch", "fields": {"Condition property": "inspection_completed", "Operator": "is not equal to any of", "Value": "true"}, "true_branch": [{"ui_action": "Create task", "fields": {"Title": "ESCALATE: Inspection not completed", "Assigned to": "Team lead", "Priority": "High"}}]},
        ],
        "prerequisites": ["Deal properties 'contingency_inspection_deadline' and 'inspection_completed' exist", "Buyer Pipeline has stage 'Under Contract'"],
        "validation": ["Create test deal with inspection_deadline and stage=Under Contract", "Verify task created 3 days before deadline", "Verify escalation fires 1 day before deadline if not completed"],
    }

register_blueprint(WorkflowBlueprint(name="re_buyer_inspection_alert", description="Inspection deadline reminder with escalation for buyer-side deals.", tags=["real-estate", "buyer", "contingency", "inspection"], parameter_schema={}, build=_build))
