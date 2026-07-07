from __future__ import annotations

from typing import Any

from hubspot_mcp.blueprints.workflows import WorkflowBlueprint, register_blueprint


def _build(params: dict[str, Any]) -> dict[str, Any]:
    score_property = params.get("score_property", "hubspotscore")
    increment = params.get("increment", 10)
    threshold = params.get("threshold", 50)
    actions: list[dict[str, Any]] = [
        {
            "step": 1,
            "ui_action": "Set property value",
            "fields": {"Property": score_property, "Value": str(increment)},
        },
    ]
    if threshold:
        actions.append(
            {
                "step": 2,
                "ui_action": "If/then branch",
                "fields": {
                    "Condition property": score_property,
                    "Operator": "is greater than",
                    "Value": str(threshold),
                },
                "true_branch": [
                    {
                        "ui_action": "Set property value",
                        "fields": {
                            "Property": "lifecyclestage",
                            "Value": "marketingqualifiedlead",
                        },
                    }
                ],
            }
        )
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
                                "property": score_property,
                                "operation": {
                                    "operationType": "ALL_PROPERTY",
                                    "operator": "IS_KNOWN",
                                    "includeObjectsWithNoValueSet": False,
                                },
                            }
                        ],
                    }
                ],
            },
        },
        "actions": actions,
        "prerequisites": [],
        "validation": [
            "Create a test contact",
            "Verify score property is set",
            "Verify lifecycle stage promoted when threshold exceeded",
        ],
    }


register_blueprint(
    WorkflowBlueprint(
        name="lead_scoring",
        description="Increment a score property when a contact engages, and optionally promote lifecycle stage when a threshold is reached.",
        tags=["scoring", "contact", "automation"],
        parameter_schema={
            "name": {"type": "string", "default": "Lead Scoring", "description": "Workflow name"},
            "score_property": {"type": "string", "default": "hubspotscore", "description": "Contact property to increment"},
            "increment": {"type": "integer", "default": 10, "description": "Points to add per engagement"},
            "threshold": {"type": "integer", "default": 50, "description": "Score threshold to promote lifecycle stage (0 to disable)"},
        },
        build=_build,
    )
)
