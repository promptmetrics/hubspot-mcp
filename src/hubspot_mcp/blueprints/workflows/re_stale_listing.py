from __future__ import annotations
from typing import Any
from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint

def _build(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "ui_path": "Settings > Automation > Workflows > Create workflow",
        "object_type": "Listing-based",
        "enrollment": {
            "type": "PROPERTY_BASED",
            "filter_branch": {
                "filterBranchType": "OR",
                "filterBranches": [{
                    "filterBranchType": "AND",
                    "filters": [
                        {"filterType": "PROPERTY", "property": "listing_status", "operation": {"operationType": "ENUMERATION", "operator": "IS_ANY_OF", "values": ["Active"], "includeObjectsWithNoValueSet": False}},
                        {"filterType": "PROPERTY", "property": "days_on_market", "operation": {"operationType": "NUMBER", "operator": "IS_GREATER_THAN", "value": 21, "includeObjectsWithNoValueSet": False}},
                        {"filterType": "PROPERTY", "property": "total_showings_count", "operation": {"operationType": "NUMBER", "operator": "IS_EQUAL_TO", "value": 0, "includeObjectsWithNoValueSet": False}},
                    ]
                }]
            },
        },
        "actions": [
            {"step": 1, "ui_action": "Create task", "fields": {"Title": "Listing stale — review pricing and marketing", "Due date": "{{timestamp + 1d}}", "Assigned to": "{{listing.listing_agent}}", "Priority": "High", "Notes": "Listing has been active >21 days with zero showings. Review pricing and marketing strategy."}},
            {"step": 2, "ui_action": "Set property value", "fields": {"Property": "price_alert_sent", "Value": "true"}},
        ],
        "prerequisites": ["Native listings object is active", "Properties 'days_on_market', 'total_showings_count', 'price_alert_sent' exist"],
        "validation": ["Create test listing with status=Active, DOM>21, showings=0", "Verify task created and price_alert_sent set to true"],
    }

register_blueprint(WorkflowBlueprint(name="re_stale_listing", description="Alert when an active listing is stale (>21 DOM, zero showings).", tags=["real-estate", "listing", "stale", "alert"], parameter_schema={}, build=_build))
