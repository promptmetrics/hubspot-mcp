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
                        {"filterType": "PROPERTY", "property": "contingency_financing_deadline", "operation": {"operationType": "ALL_PROPERTY", "operator": "IS_KNOWN", "includeObjectsWithNoValueSet": False}},
                        {"filterType": "PROPERTY", "property": "dealstage", "operation": {"operationType": "ENUMERATION", "operator": "IS_ANY_OF", "values": ["undercontract"], "includeObjectsWithNoValueSet": False}},
                    ]
                }]
            },
        },
        "actions": [
            {"step": 1, "ui_action": "Create task", "fields": {"Title": "Confirm loan commitment", "Due date": "{{contingency_financing_deadline - 5d}}", "Assigned to": "{{deal.hubspot_owner_id}}", "Priority": "High"}},
            {"step": 2, "ui_action": "Delay until date", "fields": {"Delay until": "{{contingency_financing_deadline - 2d}}"}},
            {"step": 3, "ui_action": "If/then branch", "fields": {"Condition property": "financing_cleared", "Operator": "is not equal to any of", "Value": "true"}, "true_branch": [{"ui_action": "Create task", "fields": {"Title": "ESCALATE: Financing not cleared", "Assigned to": "Team lead", "Priority": "High"}}]},
        ],
        "prerequisites": ["Deal properties 'contingency_financing_deadline' and 'financing_cleared' exist", "Buyer Pipeline has stage 'Under Contract'"],
        "validation": ["Create test deal with financing_deadline and stage=Under Contract", "Verify task and escalation fire correctly"],
    }

register_blueprint(WorkflowBlueprint(name="re_buyer_financing_alert", description="Financing deadline reminder with escalation for buyer-side deals.", tags=["real-estate", "buyer", "contingency", "financing"], parameter_schema={}, build=_build))
