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
                "filterBranches": [
                    {
                        "filterBranchType": "AND",
                        "filters": [
                            {
                                "filterType": "PROPERTY",
                                "property": "price_range_max",
                                "operation": {
                                    "operationType": "ALL_PROPERTY",
                                    "operator": "IS_KNOWN",
                                    "includeObjectsWithNoValueSet": False,
                                },
                            }
                        ],
                    },
                    {
                        "filterBranchType": "AND",
                        "filters": [
                            {
                                "filterType": "PROPERTY",
                                "property": "preferred_neighborhoods",
                                "operation": {
                                    "operationType": "ALL_PROPERTY",
                                    "operator": "IS_KNOWN",
                                    "includeObjectsWithNoValueSet": False,
                                },
                            }
                        ],
                    },
                ],
            },
        },
        "actions": [
            {
                "step": 1,
                "ui_action": "Delay",
                "fields": {"Delay for": "1 hour"},
            },
            {
                "step": 2,
                "ui_action": "Create task",
                "fields": {
                    "Title": "Send matching listings to {{contact.firstname}}",
                    "Due date": "{{timestamp + 1d}}",
                    "Assigned to": "{{contact.hubspot_owner_id}}",
                    "Priority": "Medium",
                    "Notes": "Buyer criteria updated. Pull matching active listings and send curated list.",
                },
            },
        ],
        "prerequisites": [
            "Contact properties 'price_range_max' and 'preferred_neighborhoods' are active",
            "Active Listing records exist with matching fields",
        ],
        "validation": [
            "Update a test contact with price_range_max or preferred_neighborhoods",
            "Verify task appears after 1-hour delay",
        ],
    }


register_blueprint(
    WorkflowBlueprint(
        name="re_buyer_criteria_match",
        description="Agent digest task when buyer search criteria are captured.",
        tags=["real-estate", "buyer", "task", "digest"],
        parameter_schema={},
        build=_build,
    )
)
