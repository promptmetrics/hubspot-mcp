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
                        {"filterType": "PROPERTY", "property": "dealstage", "operation": {"operationType": "ENUMERATION", "operator": "IS_ANY_OF", "values": ["activebuyer"], "includeObjectsWithNoValueSet": False}},
                        {"filterType": "PROPERTY", "property": "last_showing_date", "operation": {"operationType": "ROLLING_DATE_RANGE", "operator": "IS_MORE_THAN_X_DAYS_AGO", "numberOfDays": 14, "includeObjectsWithNoValueSet": False, "requiresTimeZoneConversion": False}},
                    ]
                }]
            },
        },
        "actions": [
            {"step": 1, "ui_action": "Create task", "fields": {"Title": "Re-engage buyer — no showings in 14 days", "Due date": "{{timestamp + 1d}}", "Assigned to": "{{deal.hubspot_owner_id}}", "Priority": "Medium", "Notes": "Buyer deal has had no showings in 14 days. Re-engage or adjust search criteria."}},
            {"step": 2, "ui_action": "Send internal email notification", "fields": {"Send to": "{{deal.hubspot_owner_id}}", "Subject": "Stale buyer deal: {{deal.dealname}}", "Body": "This buyer deal has had no showings in 14 days. Consider re-engaging the client."}},
        ],
        "prerequisites": ["Buyer Pipeline has stage 'Active Buyer'", "Deal property 'last_showing_date' exists"],
        "validation": ["Create test deal with stage=Active Buyer and last_showing_date > 14 days ago", "Verify task and email are created"],
    }

register_blueprint(WorkflowBlueprint(name="re_stale_buyer_deal", description="Alert when a buyer deal has no showings for 14 days.", tags=["real-estate", "buyer", "stale", "alert"], parameter_schema={}, build=_build))
